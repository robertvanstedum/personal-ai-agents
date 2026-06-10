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
_pending_events: dict = {}   # debounce: path → threading.Timer
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
        """
        secs = cfg("debounce_seconds", 5)
        if path in _pending_events:
            _pending_events[path].cancel()
        t = threading.Timer(secs, lambda: process_doc(path, event_type))
        _pending_events[path] = t
        t.start()


# ── Core processor ────────────────────────────────────────────────────────────

def process_doc(file_path: str, event_type: str) -> None:
    """Classify → maybe archive superseded → log → memory → notify Robert + CoS."""
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

    doc_type, summary, agent_source = classify_doc(file_path, content)
    log.info("Processed: %s → %s | %s", path.name, doc_type, summary)

    maybe_archive_superseded(file_path, doc_type)
    log_to_db(event_type, file_path, doc_type, summary, agent_source)
    append_to_memory(file_path, event_type, summary)
    notify_parallel(file_path, event_type, doc_type, summary)

    with _state_lock:
        _state["events_processed"] += 1
        _state["last_event"] = {
            "file": path.name,
            "type": event_type,
            "doc_type": doc_type,
            "at": datetime.now(timezone.utc).isoformat(),
        }


# ── Classification ────────────────────────────────────────────────────────────

def classify_doc(file_path: str, content: str) -> tuple[str, str, str]:
    """LLM classify — doc type and one-sentence summary.
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
                "Return JSON only, no other text:\n"
                '{"doc_type":"handoff|spec|design|build_output|review|config|archive",'
                '"summary":"one sentence max 20 words",'
                '"agent_source":"claude_ai|claude_code|openclaw|grok|robert|unknown"}\n\n'
                f"Filename: {Path(file_path).name}\n"
                f"Content preview:\n{content[:500]}"
            )}],
            max_tokens=150,
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "") if resp.choices else "{}"
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        # isinstance checks — Phase 4 lesson
        return (
            data.get("doc_type", "unknown") if isinstance(data, dict) else "unknown",
            data.get("summary", Path(file_path).name) if isinstance(data, dict) else Path(file_path).name,
            data.get("agent_source", "unknown") if isinstance(data, dict) else "unknown",
        )
    except Exception as e:
        log.debug("LLM classify failed (%s) — using filename heuristic", e)

    # Filename heuristic fallback — no LLM needed for obvious cases
    name = Path(file_path).name.lower()
    if "handoff" in name:
        return "handoff",      f"Handoff doc: {Path(file_path).name}", "unknown"
    if "spec" in name or "final" in name:
        return "spec",         f"Spec: {Path(file_path).name}",        "unknown"
    if "design" in name:
        return "design",       f"Design doc: {Path(file_path).name}",  "unknown"
    if "review" in name or "build_log" in name:
        return "build_output", f"Review: {Path(file_path).name}",      "unknown"
    return "unknown", f"New doc: {Path(file_path).name}", "unknown"


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
              summary: str, agent_source: str) -> None:
    try:
        import psycopg2
        conn = psycopg2.connect(
            dbname="personal_agents", user="minimoi",
            password="simple123", host="localhost", port=5432
        )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO guild.design_log "
                "(event_type, file_path, doc_type, summary, agent_source) "
                "VALUES (%s,%s,%s,%s,%s)",
                (event_type, str(file_path), doc_type, summary, agent_source)
            )
        conn.commit()
        conn.close()
    except Exception:
        pass   # DB down — memory file is the fallback


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
   /status  — agent state and memory usage
   /health  — liveness probe
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
