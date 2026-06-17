"""
Design/Dev Agent — Level 1
Traffic cop, memory builder, and CoS communications bridge for the mini-moi build process.
Port 8770. Run under launchd as com.user.devagent.

Phase 4 learnings applied:
- sys.path fix from day one (not as a hotfix)
- Event debouncing (5-second window — don't fire on every intermediate write)
- Graceful failure at every external call
- isinstance/size checks on all file reads
"""

import sys
from pathlib import Path

# sys.path fix — must be before any domain imports (Phase 4 launchd lesson)
BASE_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BASE_DIR))

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import keyring
import requests
from flask import Flask, jsonify, request
from openai import OpenAI
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [devagent] %(levelname)s %(message)s"
)
log = logging.getLogger("devagent")

app = Flask(__name__)
_cfg: dict = {}
_pending_events: dict = {}     # debounce: path → threading.Timer
_last_processed: dict = {}     # dedup guard: path → content hash of last logged version
_last_processed_lock = threading.Lock()
_state = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "events_processed": 0,
    "docs_archived": 0,
}
_state_lock = threading.Lock()


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> None:
    global _cfg
    path = BASE_DIR / "domains/guild/config/devagent_config.json"
    try:
        _cfg = json.loads(path.read_text()).get("design_dev", {})
        log.info("Config loaded from %s", path)
    except Exception as e:
        log.error("Config load failed: %s — using defaults", e)
        _cfg = {}


def cfg(key, default=None):
    return _cfg.get(key, default)


# ── File watcher ──────────────────────────────────────────────────────────────

class DesignDocHandler(FileSystemEventHandler):

    def _should_watch(self, path: str) -> bool:
        p = Path(path)
        exts = cfg("watch_extensions", [".md", ".json", ".sql", ".py"])
        if p.name.startswith("."):
            return False
        if not any(path.endswith(e) for e in exts):
            return False
        # Never watch trash or archive subdirs (read-only zones)
        for skip in ("_working/trash", "_working/archive"):
            if skip in path:
                return False
        # .md allowlist — only spec_* and build_plan_* are build items.
        # Session summaries, handoffs, approach docs, DR files, etc. are not.
        if p.suffix == ".md":
            if not (p.name.startswith("spec_") or p.name.startswith("build_plan_")):
                return False
        # Decision records are reasoning artifacts, not queue items
        if "decision-records" in path:
            return False
        return True

    def on_created(self, event):
        if not event.is_directory and self._should_watch(event.src_path):
            self._debounce(event.src_path, "doc_created")

    def on_modified(self, event):
        if not event.is_directory and self._should_watch(event.src_path):
            self._debounce(event.src_path, "doc_modified")

    def _debounce(self, path: str, event_type: str) -> None:
        """
        Batch rapid modification events into one notification.
        Files fire multiple modification events during a single save.
        5-second window collapses them into one.

        After the timer fires, we remove the path from _pending_events so
        future events don't call cancel() on an already-fired timer (which is a
        no-op but leaves dead references and allows a new timer to start unchecked).
        Content-hash dedup in process_doc() catches any identical re-fires that
        slip through after the timer clears.
        """
        secs = cfg("debounce_seconds", 5)
        if path in _pending_events:
            _pending_events[path].cancel()

        def _fire():
            _pending_events.pop(path, None)
            process_doc(path, event_type)

        t = threading.Timer(secs, _fire)
        _pending_events[path] = t
        t.start()


# ── Core processor ────────────────────────────────────────────────────────────

def process_doc(file_path: str, event_type: str) -> None:
    """Classify → completeness check → maybe archive superseded → log → memory → notify."""
    path = Path(file_path)

    # Size check before read (Phase 4 lesson)
    max_bytes = cfg("max_file_size_bytes", 102400)
    try:
        if path.stat().st_size > max_bytes:
            log.debug("Skipping oversized file: %s", path.name)
            return
    except Exception:
        return

    try:
        content = path.read_text(encoding="utf-8", errors="ignore")[:2000]
    except Exception:
        return

    # Dedup guard — skip if the file content hasn't changed since the last time
    # we logged this path. Handles editors that fire multiple on_modified events
    # for a single save (autosave chunk writes, metadata flushes, atomic renames).
    # No time window needed: if the fingerprint is new, it's a real change; if it
    # matches, we've already logged this exact content regardless of how many
    # events fired.
    content_hash = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()[:16]
    with _last_processed_lock:
        if _last_processed.get(file_path) == content_hash:
            log.debug("Skipping duplicate: %s (content unchanged since last log)", path.name)
            return
        _last_processed[file_path] = content_hash

    cl = classify_doc(file_path, content)
    doc_type    = cl["doc_type"]
    summary     = cl["summary"]
    agent_source = cl["agent_source"]
    spec_title  = cl["spec_title"]
    log.info("Classified: %s → %s | %s", path.name, doc_type, summary)

    # Only spec-track docs (handoff / spec / design) get logged, notified, and
    # written to memory. Plans, notes, release docs, build outputs, config —
    # no design_log row, no Telegram ping, no memory entry. Zero footprint.
    if doc_type not in ("handoff", "spec", "design"):
        log.debug("Non-spec-track (%s) — skipping log/notify/memory: %s", doc_type, path.name)
        return

    # Completeness check for handoff/spec/design docs
    build_status = None
    completeness_failures: list[str] = []
    if doc_type in ("handoff", "spec", "design"):
        build_status, completeness_failures = _check_completeness(cl)
        log.info("Completeness: %s → %s", path.name, build_status)
        if completeness_failures:
            _notify_incomplete(file_path, completeness_failures)

    spec_file = path.name if doc_type in ("handoff", "spec", "design") else None

    maybe_archive_superseded(file_path, doc_type)
    log_to_db(event_type, file_path, doc_type, summary, agent_source,
              spec_file=spec_file, spec_title=spec_title, build_status=build_status,
              completeness_failures=completeness_failures or None)
    append_to_memory(file_path, event_type, summary)
    notify_parallel(file_path, event_type, doc_type, summary)

    with _state_lock:
        _state["events_processed"] += 1
        _state["last_event"] = {
            "file": path.name,
            "type": event_type,
            "doc_type": doc_type,
            "build_status": build_status,
            "at": datetime.now(timezone.utc).isoformat(),
        }


# ── Classification ────────────────────────────────────────────────────────────

def classify_doc(file_path: str, content: str) -> dict:
    """LLM classify — doc type, summary, completeness data (one call).
    Returns dict with: doc_type, summary, agent_source, spec_title,
    has_dod, has_commit, referenced_files.
    Uses xAI (platform convention). Falls back to filename-pattern heuristic.
    """
    try:
        client = OpenAI(
            api_key=keyring.get_password("xai", "api_key"),
            base_url="https://api.x.ai/v1",
        )
        resp = client.chat.completions.create(
            model="grok-4-1-fast-reasoning",
            messages=[{"role": "user", "content": (
                "Classify this mini-moi project document. "
                "Return JSON only, no other text.\n\n"
                "Required fields:\n"
                '  "doc_type": "handoff|spec|design|build_output|review|config|archive"\n'
                '  "summary": "one sentence max 20 words"\n'
                '  "agent_source": "claude_ai|claude_code|openclaw|grok|robert|unknown"\n\n'
                "Also extract for build tracking:\n"
                "  1. spec_title: document title from top-level # heading. "
                "Strip any leading 'Handoff —', 'Build Spec —', 'Design —' prefix. "
                "Null if not a spec/handoff/design doc.\n"
                "  2. referenced_files: list of every _working/ path mentioned anywhere "
                "in this document (e.g. '_working/spec_foo.md'). Empty list if none.\n\n"
                '{"doc_type":"...","summary":"...","agent_source":"...",'
                '"spec_title":null,"referenced_files":[]}\n\n'
                f"Filename: {Path(file_path).name}\n"
                f"Content preview:\n{content[:3000]}"
            )}],
            max_tokens=300,
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "") if resp.choices else "{}"
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("non-dict response")
        _lower = content.lower()
        return {
            "doc_type":         data.get("doc_type", "unknown"),
            "summary":          data.get("summary", Path(file_path).name),
            "agent_source":     data.get("agent_source", "unknown"),
            "spec_title":       data.get("spec_title"),
            # Direct search on full content — structural check, no LLM needed
            "has_dod":          "## definition of done" in _lower,
            "has_commit":       "## commit" in _lower,
            "referenced_files": data.get("referenced_files", [])
                                if isinstance(data.get("referenced_files"), list) else [],
        }
    except Exception as e:
        log.debug("LLM classify failed (%s) — using filename heuristic", e)

    # Filename heuristic fallback — no LLM needed for obvious cases
    # has_dod / has_commit always use direct search regardless of LLM availability
    name = Path(file_path).name.lower()
    _lower = content.lower()
    _dod     = "## definition of done" in _lower
    _commit  = "## commit" in _lower
    if "handoff" in name:
        return {"doc_type": "handoff",      "summary": f"Handoff doc: {Path(file_path).name}",
                "agent_source": "unknown",  "spec_title": None,
                "has_dod": _dod, "has_commit": _commit, "referenced_files": []}
    if "spec" in name or "final" in name:
        return {"doc_type": "spec",         "summary": f"Spec: {Path(file_path).name}",
                "agent_source": "unknown",  "spec_title": None,
                "has_dod": _dod, "has_commit": _commit, "referenced_files": []}
    if "design" in name:
        return {"doc_type": "design",       "summary": f"Design doc: {Path(file_path).name}",
                "agent_source": "unknown",  "spec_title": None,
                "has_dod": _dod, "has_commit": _commit, "referenced_files": []}
    if "review" in name or "build_log" in name:
        return {"doc_type": "build_output", "summary": f"Review: {Path(file_path).name}",
                "agent_source": "unknown",  "spec_title": None,
                "has_dod": _dod, "has_commit": _commit, "referenced_files": []}
    return {
        "doc_type": "unknown", "summary": f"New doc: {Path(file_path).name}",
        "agent_source": "unknown", "spec_title": None,
        "has_dod": _dod, "has_commit": _commit, "referenced_files": [],
    }


def _check_completeness(classification: dict) -> tuple[str, list[str]]:
    """
    Completeness check for handoff/spec/design docs.
    Returns (status, failures) where status is 'spec_ready' or 'incomplete',
    and failures is a list of human-readable failure reasons.
    """
    failures = []
    if not classification.get("has_dod"):
        failures.append("missing Definition of Done section")
    if not classification.get("has_commit"):
        failures.append("missing Commit section")
    # File existence check (reuses check_handoff_gaps logic)
    for ref in classification.get("referenced_files", []):
        ref_path = BASE_DIR / ref if not os.path.isabs(ref) else Path(ref)
        if not ref_path.exists():
            failures.append(f"referenced file not found: {ref}")
    return ("design" if failures else "spec_ready"), failures


def _notify_incomplete(file_path: str, failures: list[str]) -> None:
    """Notify Robert via Telegram when a spec needs design work (missing DoD or Commit)."""
    bullets = "\n".join(f"  • {f}" for f in failures)
    msg = (
        f"📐 <b>Spec needs design work:</b> {Path(file_path).name}\n"
        f"Missing sections:\n{bullets}\n"
        "Add DoD + Commit section, or set status manually in /guild/build/queue"
    )
    _send_telegram(msg)


# ── _working/ management ──────────────────────────────────────────────────────

def maybe_archive_superseded(file_path: str, doc_type: str) -> None:
    """
    If a newer version of the same base slug exists in _working/, move the older
    one to _working/archive/YYYY-MM/. Safe — reversible move, not delete.
    Only acts on handoff/spec/design doc types.
    """
    if doc_type not in ("handoff", "spec", "design"):
        return
    try:
        path = Path(file_path)
        working = path.parent
        if not str(working).endswith("_working"):
            return

        stem = path.stem
        parts = stem.split("_")
        if len(parts) < 3:
            return
        slug_prefix = "_".join(parts[:3])

        siblings = [
            f for f in working.glob(f"{slug_prefix}*.md")
            if f != path and f.name != path.name
        ]
        for older in siblings:
            archive_dir = working / "archive" / datetime.now().strftime("%Y-%m")
            archive_dir.mkdir(parents=True, exist_ok=True)
            dest = archive_dir / older.name
            older.rename(dest)
            log.info("Archived superseded doc: %s → %s", older.name, archive_dir)
            log_to_db("doc_superseded", str(older), "handoff",
                      f"Superseded by {path.name}", "design_dev")
            with _state_lock:
                _state["docs_archived"] += 1
    except Exception as e:
        log.debug("Archive superseded failed: %s", e)


# ── Notifications (parallel) ──────────────────────────────────────────────────

def notify_parallel(file_path: str, event_type: str, doc_type: str, summary: str) -> None:
    """
    Notify Robert (Telegram) AND CoS (/event) simultaneously.
    CoS is not a secondary step — it receives the same info Robert does.
    """
    rule = _get_escalation_rule(event_type, summary)

    def _to_robert():
        if not cfg("telegram_notify", True):
            return
        msg = (
            f"📋 <b>Design/Dev:</b> {event_type.replace('_', ' ')}\n"
            f"{Path(file_path).name}\n"
            f"{summary}"
        )
        _send_telegram(msg)

    def _to_cos():
        if not cfg("flag_to_cos", True):
            return
        try:
            requests.post(
                "http://localhost:8769/event",
                json={
                    "source": "design_dev",
                    "event_type": event_type,
                    "file_path": str(file_path),
                    "doc_type": doc_type,
                    "summary": summary,
                    "escalation_rule": rule,
                },
                timeout=2,
            )
        except Exception:
            # CoS unavailable — write directly to agenda file
            _write_agenda_direct(event_type, file_path, summary)

    t1 = threading.Thread(target=_to_robert, daemon=True)
    t2 = threading.Thread(target=_to_cos, daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)


def _get_escalation_rule(event_type: str, summary: str) -> dict:
    rules = cfg("escalation_rules", {})
    lower = summary.lower()
    if "conflict" in lower or "contradicts" in lower:
        return rules.get("doc_conflict", {"cos_action": "flag_to_robert", "priority": "high"})
    if "failed" in lower or "error" in lower:
        return rules.get("build_failed", {"cos_action": "flag_to_robert", "priority": "high"})
    return rules.get(event_type, {"cos_action": "log_only", "priority": "info"})


# ── Telegram ──────────────────────────────────────────────────────────────────

def _send_telegram(text: str) -> None:
    try:
        token   = keyring.get_password("telegram", "bot_token")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "8379221702")
        if token:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=5,
            )
    except Exception:
        pass


# ── Memory ────────────────────────────────────────────────────────────────────

def append_to_memory(file_path: str, event_type: str, summary: str) -> None:
    memory_path = BASE_DIR / cfg("memory_path", "data/guild/memory/devagent_memory.md")
    cap = cfg("memory_hard_cap_chars", 8000)
    entry = (
        f"\n{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC: "
        f"[{event_type}] {Path(file_path).name} — {summary}"
    )
    try:
        current = memory_path.read_text() if memory_path.exists() else ""
        if len(current) + len(entry) > cap - 500:
            entry += "\n⚠️ Memory approaching cap — distillation needed"
            _write_agenda_direct("memory_cap_approaching",
                                 str(memory_path), "devagent memory approaching 8k cap")
        memory_path.write_text(current + entry)
    except Exception as e:
        log.debug("Memory write failed: %s", e)


# ── DB ────────────────────────────────────────────────────────────────────────

def log_to_db(event_type: str, file_path: str, doc_type: str,
              summary: str, agent_source: str,
              spec_file: str | None = None,
              spec_title: str | None = None,
              build_status: str | None = None,
              completeness_failures: list[str] | None = None) -> None:
    try:
        import psycopg2
        conn = psycopg2.connect(
            dbname="personal_agents", user="minimoi",
            password="simple123", host="localhost", port=5432
        )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO guild.design_log "
                "(event_type, file_path, doc_type, summary, agent_source, "
                " spec_file, spec_title, status, last_transition_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW()) "
                "RETURNING id",
                (event_type, str(file_path), doc_type, summary, agent_source,
                 spec_file, spec_title, build_status)
            )
            row = cur.fetchone()
            log_id = row[0] if row else None

            # Write initial transition row for tracked docs
            if log_id and build_status:
                reason = "; ".join(completeness_failures) if completeness_failures else None
                cur.execute(
                    "INSERT INTO guild.design_log_transitions "
                    "(design_log_id, from_status, to_status, triggered_by, reason) "
                    "VALUES (%s, NULL, %s, 'design_dev', %s)",
                    (log_id, build_status, reason)
                )
        conn.commit()
        conn.close()
    except Exception:
        pass   # DB down — memory file is the fallback


def _log_transition(design_log_id: int, from_status: str | None,
                    to_status: str, triggered_by: str,
                    reason: str | None = None) -> None:
    """Write a single row to guild.design_log_transitions."""
    try:
        import psycopg2
        conn = psycopg2.connect(
            dbname="personal_agents", user="minimoi",
            password="simple123", host="localhost", port=5432
        )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO guild.design_log_transitions "
                "(design_log_id, from_status, to_status, triggered_by, reason) "
                "VALUES (%s,%s,%s,%s,%s)",
                (design_log_id, from_status, to_status, triggered_by, reason)
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _write_agenda_direct(event_type: str, file_path: str, summary: str) -> None:
    """Write to cos_agenda JSON file directly when CoS endpoint is unavailable."""
    agenda_file = BASE_DIR / "data/guild/cos_agenda.json"
    try:
        agenda_file.parent.mkdir(parents=True, exist_ok=True)
        existing: list = []
        if agenda_file.exists():
            try:
                existing = json.loads(agenda_file.read_text())
            except Exception:
                pass
        existing.append({
            "domain": "design_dev",
            "description": f"[{event_type}] {Path(file_path).name}: {summary}",
            "confidence": 0.8,
            "loop_name": "devagent_watcher",
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        agenda_file.write_text(json.dumps(existing, indent=2))
    except Exception:
        pass


# ── Private repo check ────────────────────────────────────────────────────────

def check_private_push_needed() -> None:
    """
    Check if archive files have uncommitted changes.
    Level 1: log + notify only. Claude Code executes the actual push.
    """
    try:
        import subprocess
        result = subprocess.run(
            ["git", "status", "--porcelain", "_working/archive/"],
            capture_output=True, text=True, cwd=str(BASE_DIR)
        )
        if result.stdout.strip():
            notify_parallel(
                "_working/archive/", "private_repo_stale",
                "config", "Uncommitted archive files — private push needed"
            )
    except Exception:
        pass


# ── Flask endpoints ───────────────────────────────────────────────────────────

@app.route("/status")
def status():
    memory_path = BASE_DIR / cfg("memory_path", "data/guild/memory/devagent_memory.md")
    memory_size = len(memory_path.read_text()) if memory_path.exists() else 0
    with _state_lock:
        snap = dict(_state)
    snap["uptime_seconds"] = int(
        (datetime.now(timezone.utc) -
         datetime.fromisoformat(snap["started_at"])).total_seconds()
    )
    snap["agent"] = "design_dev"
    snap["autonomy_level"] = cfg("autonomy_level", 1)
    snap["flag_threshold"] = cfg("flag_threshold", "low")
    snap["watching"] = cfg("watch_paths", [])
    snap["memory_chars"] = memory_size
    snap["memory_cap"] = cfg("memory_hard_cap_chars", 8000)
    return jsonify(snap)


@app.route("/health")
def health():
    return jsonify({"ok": True})


@app.route("/event", methods=["POST"])
def receive_external_event():
    """Receive events from other agents — wired for future use."""
    return jsonify({"received": True})


@app.route("/archive-spec", methods=["POST"])
def archive_spec():
    """
    Move a spec file from _working/ to _working/archive/YYYY-MM/.
    Called by the portal when a spec's status flips to done or deferred.
    Non-fatal if the file is already gone (already archived, moved manually, etc.).
    """
    data = request.get_json(silent=True) or {}
    spec_file = data.get("spec_file")
    if not spec_file:
        return jsonify({"error": "spec_file required"}), 400

    src = BASE_DIR / "_working" / spec_file
    if not src.exists():
        log.info("archive-spec: file not in _working/ (already archived or never there): %s", spec_file)
        return jsonify({"archived": False, "reason": "file not found in _working/"})

    archive_dir = BASE_DIR / "_working" / "archive" / datetime.now().strftime("%Y-%m")
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / spec_file
    try:
        src.rename(dest)
        log.info("archive-spec: %s → archive/%s/", spec_file, datetime.now().strftime("%Y-%m"))
        return jsonify({"archived": True, "dest": str(dest.relative_to(BASE_DIR))})
    except Exception as e:
        log.error("archive-spec failed: %s", e)
        return jsonify({"error": str(e), "archived": False}), 500


@app.route("/start-build", methods=["POST"])
def start_build():
    """
    Signal that Claude Code has started work on a spec.
    Flips status → in_build and writes a transition row.
    Called once at the start of each build: scripts/start_build.sh <spec-filename>
    """
    data = request.get_json(silent=True) or {}
    spec_file = data.get("spec_file")
    if not spec_file:
        return jsonify({"error": "spec_file required"}), 400

    try:
        import psycopg2
        conn = psycopg2.connect(
            dbname="personal_agents", user="minimoi",
            password="simple123", host="localhost", port=5432
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status FROM guild.design_log "
                "WHERE spec_file = %s ORDER BY id DESC LIMIT 1",
                (spec_file,)
            )
            row = cur.fetchone()
            if not row:
                conn.close()
                return jsonify({"error": f"spec_file not found in design_log: {spec_file}"}), 404

            log_id, current_status = row
            cur.execute(
                "UPDATE guild.design_log SET status='in_build', "
                "last_transition_at=NOW() WHERE id=%s",
                (log_id,)
            )
            cur.execute(
                "INSERT INTO guild.design_log_transitions "
                "(design_log_id, from_status, to_status, triggered_by) "
                "VALUES (%s,%s,'in_build','claude_code')",
                (log_id, current_status)
            )
        conn.commit()
        conn.close()
        log.info("start-build: %s → in_build (was: %s)", spec_file, current_status)
        return jsonify({"status": "in_build", "spec_file": spec_file,
                        "previous_status": current_status})
    except Exception as e:
        log.error("start-build error: %s", e)
        return jsonify({"error": str(e)}), 500


# ── Watcher thread ────────────────────────────────────────────────────────────

def _start_watcher() -> None:
    observer = Observer()
    watch_paths = cfg("watch_paths", ["_working/"])
    scheduled = 0
    for watch_path in watch_paths:
        full_path = BASE_DIR / watch_path
        if full_path.exists():
            observer.schedule(DesignDocHandler(), str(full_path), recursive=True)
            log.info("Watching: %s", full_path)
            scheduled += 1
        else:
            log.warning("Watch path does not exist — skipping: %s", full_path)

    if scheduled == 0:
        log.error("No valid watch paths — watcher not started")
        return

    observer.start()
    log.info("Watchdog observer started (%d path(s))", scheduled)
    try:
        while True:
            time.sleep(1)
    except Exception:
        observer.stop()
    observer.join()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    PORT = int(os.environ.get("PORT", 8770))
    load_config()

    print(f"""
🔍  Design/Dev Agent starting on port {PORT}…
   /status       — agent state and memory usage
   /health       — liveness probe
   /archive-spec — POST {{spec_file}} to move file from _working/ to archive/
   /start-build  — POST {{spec_file}} to flip status → in_build
   Watching: {cfg('watch_paths', ['_working/'])}
   Autonomy level: {cfg('autonomy_level', 1)}
   Debounce: {cfg('debounce_seconds', 5)}s
""")

    # Start file watcher in background thread
    watcher_thread = threading.Thread(
        target=_start_watcher, daemon=True, name="devagent-watcher"
    )
    watcher_thread.start()
    print("   Thread started: devagent-watcher")
    print("   State: running\n")

    app.run(host="localhost", port=PORT, debug=False)


if __name__ == "__main__":
    main()
