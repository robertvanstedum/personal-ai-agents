"""
Design/Dev Agent — Level 1
Traffic cop, memory builder, and CoS communications bridge for the mini-moi build process.
Port 8771. Run under launchd as com.user.devagent.

Phase 4 learnings applied:
- sys.path fix from day one (not as a hotfix)
- Event debouncing (10-second window — don't fire on every intermediate write)
- Graceful failure at every external call
- isinstance/size checks on all file reads

Cost guards (xAI spend incident, 2026-09-26 — see
_working/xai-spend-investigation-2026-09-26.md): the watcher once sent every
new .py/.json file under _working/ (worktrees, virtualenvs, archives) to a
reasoning model — about 18,000 paid calls in three days. Now:
- excluded directories are dropped before anything else (watch_exclude_dirs)
- only spec_*/build_plan_* markdown ever reaches a model; code and config
  files are classified by filename rules
- paid calls are capped per minute and per day; over the cap an event is
  marked "unclassified: rate-limited" and dropped, never queued
- identical content is classified once per process
- one JSON receipt line per paid call in logs/devagent_model_calls.jsonl
- the watcher refuses to start on the repo root or a home directory
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
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import PurePosixPath

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
_pending_events_lock = threading.Lock()
_state = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "events_processed": 0,
    "docs_archived": 0,
    "model_calls": 0,
    "model_calls_skipped_rate_limit": 0,
}
_state_lock = threading.Lock()


# ── Cost guards ───────────────────────────────────────────────────────────────
# Defaults live here; every value can be overridden in devagent_config.json.

DEFAULT_WATCH_EXCLUDE_DIRS = (
    "worktrees", ".venv", "venv", "env", "site-packages", "node_modules",
    "__pycache__", ".git", ".pytest_cache", "deployments", "m0-preservation",
    "tour-capture", "guild-prototype-evidence", "dist", "build",
)
DEFAULT_DEBOUNCE_SECONDS = 10
DEFAULT_CLASSIFY_MODEL = "grok-4.3"
DEFAULT_CLASSIFY_REASONING_EFFORT = "none"   # Spec 159 economy tier: grok-4.3, effort none
DEFAULT_MODEL_CALLS_PER_MINUTE = 6
DEFAULT_MODEL_CALLS_PER_DAY = 60
DEFAULT_MODEL_CALL_LOG = "logs/devagent_model_calls.jsonl"

# Code and config files never reach a model — these rules classify them.
_RULE_DOC_TYPES = {".py": "code", ".sql": "code", ".json": "config"}

_classified_by_hash: dict = {}   # content hash → model classification (process lifetime)
_classified_by_hash_lock = threading.Lock()
_receipt_lock = threading.Lock()


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


def _rel(path) -> str:
    """Path relative to the repo root when inside it, else absolute (posix)."""
    p = Path(os.path.abspath(str(path)))
    try:
        return p.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return p.as_posix()


def _exclude_dirs() -> set:
    return set(cfg("watch_exclude_dirs", DEFAULT_WATCH_EXCLUDE_DIRS))


def is_excluded(path: str) -> bool:
    """True if any directory component of the path is excluded.
    Checked relative to the repo root, so a checkout that itself lives under
    _working/worktrees/ does not exclude everything."""
    dirs = PurePosixPath(_rel(path)).parts[:-1]
    excluded = _exclude_dirs()
    if any(part in excluded for part in dirs):
        return True
    # Never watch trash or archive subdirs (read-only zones)
    for skip in ("_working/trash", "_working/archive"):
        if skip in path:
            return True
    return False


def _model_eligible(path: str) -> bool:
    """Only design documents (spec_*/build_plan_* markdown) may reach a model."""
    p = Path(path)
    return p.suffix == ".md" and (p.name.startswith("spec_") or p.name.startswith("build_plan_"))


def classify_by_rules(file_path: str) -> dict:
    """Filename/path classification for code and config files. No model call.
    None of these types are spec-track, so process_doc gives them zero footprint."""
    p = Path(file_path)
    doc_type = _RULE_DOC_TYPES.get(p.suffix, "unknown")
    return {
        "doc_type": doc_type, "summary": f"{doc_type.capitalize()} file: {p.name}",
        "agent_source": "unknown", "spec_title": None,
        "has_dod": False, "has_commit": False, "referenced_files": [],
    }


def _receipt_path() -> Path:
    return BASE_DIR / cfg("model_call_log", DEFAULT_MODEL_CALL_LOG)


class ModelCallBudget:
    """Per-minute and per-day ceilings on paid classification calls.

    try_acquire() reserves one call or refuses it. A refused call is dropped,
    never queued. Limits are read from config on every call. The day is the UTC
    date; seed_from_receipts() counts today's durable reservations at startup so
    a launchd KeepAlive restart does not hand out a fresh daily allowance. A
    reservation is written (and fsynced) before every paid request, so a crash
    or a failed outcome write after the request cannot lower the count.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._recent: deque = deque()
        self._day = None
        self._day_count = 0
        self._warned_minute = False
        self._warned_day = False

    @staticmethod
    def _utc_day(now: float) -> str:
        return datetime.fromtimestamp(now, timezone.utc).date().isoformat()

    def try_acquire(self, now: float | None = None) -> str | None:
        """Reserve a call. Returns None if allowed, else the limit hit: "minute" or "day"."""
        now = time.time() if now is None else now
        per_min = int(cfg("model_calls_per_minute", DEFAULT_MODEL_CALLS_PER_MINUTE))
        per_day = int(cfg("model_calls_per_day", DEFAULT_MODEL_CALLS_PER_DAY))
        day = self._utc_day(now)
        with self._lock:
            if day != self._day:
                self._day, self._day_count, self._warned_day = day, 0, False
            while self._recent and now - self._recent[0] >= 60:
                self._recent.popleft()
            if self._day_count >= per_day:
                if not self._warned_day:
                    self._warned_day = True
                    log.warning("Model daily cap reached (%d calls, UTC %s) — "
                                "classification skipped until tomorrow", per_day, day)
                return "day"
            if len(self._recent) >= per_min:
                if not self._warned_minute:
                    self._warned_minute = True
                    log.warning("Model per-minute limit reached (%d/min) — "
                                "classification skipped, not queued", per_min)
                return "minute"
            self._recent.append(now)
            self._day_count += 1
            self._warned_minute = False
            return None

    def calls_today(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            return self._day_count if self._day == self._utc_day(now) else 0

    def seed_from_receipts(self, path: Path, now: float | None = None) -> int:
        """Count today's (UTC) paid calls toward the daily cap.

        Counts reservation lines (written before each request). Outcome lines
        are not counted again. Legacy lines without a "kind" (written before
        reservations existed) each stand for one call and are counted."""
        now = time.time() if now is None else now
        day = self._utc_day(now)
        count = 0
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                        if not str(rec.get("ts", "")).startswith(day):
                            continue
                        if rec.get("kind", "reservation") == "reservation":
                            count += 1
                    except Exception:
                        continue
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("Could not read model receipts %s: %s", path, e)
        with self._lock:
            self._day, self._day_count = day, count
        return count


_budget = ModelCallBudget()


def _int_or_none(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _append_durable(record: dict) -> bool:
    """Append one JSON line to the receipt log and fsync it. False on any failure."""
    try:
        path = _receipt_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _receipt_lock, open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except Exception as e:
        log.warning("Model receipt write failed: %s", e)
        return False


def _reserve_call(file_path: str, model: str, effort: str) -> str | None:
    """Durably record a paid call BEFORE it is made. None means: do not call."""
    reservation_id = uuid.uuid4().hex
    ok = _append_durable({
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": "reservation",
        "id": reservation_id,
        "path": _rel(file_path),
        "model": model,
        "reasoning_effort": effort,
    })
    return reservation_id if ok else None


def _write_receipt(file_path: str, model: str, effort: str, usage, outcome: str,
                   reservation_id: str | None = None) -> None:
    """Outcome line for a reserved paid call. Never includes file content.
    A failure here is logged only: the reservation already counts the call."""
    details = getattr(usage, "completion_tokens_details", None)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": "outcome",
        "reservation_id": reservation_id,
        "path": _rel(file_path),
        "model": model,
        "reasoning_effort": effort,
        "input_tokens": _int_or_none(getattr(usage, "prompt_tokens", None)),
        "output_tokens": _int_or_none(getattr(usage, "completion_tokens", None)),
        "reasoning_tokens": _int_or_none(getattr(details, "reasoning_tokens", None)),
        "outcome": outcome,
    }
    _append_durable(record)


# ── File watcher ──────────────────────────────────────────────────────────────

class DesignDocHandler(FileSystemEventHandler):

    def _should_watch(self, path: str) -> bool:
        # Exclusions first: worktrees, virtualenvs, archives, build output.
        if is_excluded(path):
            return False
        p = Path(path)
        exts = cfg("watch_extensions", [".md", ".json", ".sql", ".py"])
        if p.name.startswith("."):
            return False
        if not any(path.endswith(e) for e in exts):
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
        if not event.is_directory:
            self._handle(event.src_path, "doc_created")

    def on_modified(self, event):
        if not event.is_directory:
            self._handle(event.src_path, "doc_modified")

    def _handle(self, path: str, event_type: str) -> None:
        if not self._should_watch(path):
            return
        if not _model_eligible(path):
            # Code/config: filename rules only — no read, no timer, no model.
            cl = classify_by_rules(path)
            log.debug("Rule-classified (no model): %s → %s", Path(path).name, cl["doc_type"])
            return
        self._debounce(path, event_type)

    def _debounce(self, path: str, event_type: str) -> None:
        """
        Batch rapid modification events into one notification.
        Files fire multiple modification events during a single save.
        10-second window (debounce_seconds) collapses them into one.

        After the timer fires, we remove the path from _pending_events so
        future events don't call cancel() on an already-fired timer (which is a
        no-op but leaves dead references and allows a new timer to start unchecked).
        Content-hash dedup in process_doc() catches any identical re-fires that
        slip through after the timer clears.
        """
        secs = cfg("debounce_seconds", DEFAULT_DEBOUNCE_SECONDS)

        def _fire():
            with _pending_events_lock:
                if _pending_events.get(path) is t:
                    _pending_events.pop(path, None)
            process_doc(path, event_type)

        with _pending_events_lock:
            if path in _pending_events:
                _pending_events[path].cancel()
            t = threading.Timer(secs, _fire)
            _pending_events[path] = t
        t.start()


# ── Core processor ────────────────────────────────────────────────────────────

def process_doc(file_path: str, event_type: str) -> dict | None:
    """Classify → completeness check → maybe archive superseded → log → memory → notify.
    Returns the classification, or None if the file was skipped before classifying."""
    path = Path(file_path)
    if is_excluded(file_path):
        return None

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

    cl = classify_doc(file_path, content, content_hash)
    doc_type    = cl["doc_type"]
    summary     = cl["summary"]
    agent_source = cl["agent_source"]
    spec_title  = cl["spec_title"]
    if cl.get("rate_limited"):
        # Dropped, not queued. Forget the hash so a later save can try again.
        with _last_processed_lock:
            if _last_processed.get(file_path) == content_hash:
                _last_processed.pop(file_path, None)
        log.debug("Unclassified (rate-limited): %s", path.name)
        return cl
    log.info("Classified: %s → %s | %s", path.name, doc_type, summary)

    # Only spec-track docs (handoff / spec / design) get logged, notified, and
    # written to memory. Plans, notes, release docs, build outputs, config —
    # no design_log row, no Telegram ping, no memory entry. Zero footprint.
    if doc_type not in ("handoff", "spec", "design"):
        log.debug("Non-spec-track (%s) — skipping log/notify/memory: %s", doc_type, path.name)
        return cl

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
    return cl


# ── Classification ────────────────────────────────────────────────────────────

def classify_doc(file_path: str, content: str, content_hash: str | None = None) -> dict:
    """LLM classify — doc type, summary, completeness data (one call).
    Returns dict with: doc_type, summary, agent_source, spec_title,
    has_dod, has_commit, referenced_files.
    Only spec_*/build_plan_* markdown reaches the model; anything else is
    classified by filename rules. Identical content reuses the earlier result.
    Over the per-minute or daily limit the result is "unclassified" with
    rate_limited=True and no call is made.
    Uses xAI (platform convention). Falls back to filename-pattern heuristic.
    """
    if not _model_eligible(file_path):
        return classify_by_rules(file_path)

    if content_hash is None:
        content_hash = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()[:16]
    with _classified_by_hash_lock:
        cached = _classified_by_hash.get(content_hash)
    if cached is not None:
        log.debug("Identical content already classified — no model call: %s", Path(file_path).name)
        return dict(cached)

    limit = _budget.try_acquire()
    if limit:
        with _state_lock:
            _state["model_calls_skipped_rate_limit"] += 1
        return {
            "doc_type": "unclassified", "summary": "unclassified: rate-limited",
            "agent_source": "unknown", "spec_title": None,
            "has_dod": False, "has_commit": False, "referenced_files": [],
            "rate_limited": True, "limit": limit,
        }

    data = _call_classify_model(file_path, content)
    if data is not None:
        _lower = content.lower()
        result = {
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
        with _classified_by_hash_lock:
            _classified_by_hash[content_hash] = result
        return dict(result)

    return _classify_by_filename(file_path, content)


def _call_classify_model(file_path: str, content: str) -> dict | None:
    """One paid classification call. A durable reservation is written before the
    request (no reservation, no call); an outcome line follows whenever it is sent.
    Returns the parsed JSON dict, or None on any failure."""
    model = cfg("classify_model", DEFAULT_CLASSIFY_MODEL)
    effort = cfg("classify_reasoning_effort", DEFAULT_CLASSIFY_REASONING_EFFORT)
    try:
        client = OpenAI(
            api_key=keyring.get_password("xai", "api_key"),
            base_url="https://api.x.ai/v1",
        )
    except Exception as e:
        log.debug("LLM client unavailable (%s) — using filename heuristic", e)
        return None

    # Fail closed: no durable reservation, no paid request.
    reservation_id = _reserve_call(file_path, model, effort)
    if reservation_id is None:
        with _state_lock:
            _state["model_calls_refused_unrecorded"] = _state.get("model_calls_refused_unrecorded", 0) + 1
        log.error("Could not record a model-call reservation in %s — no paid call made "
                  "(fail closed); using filename heuristic", _receipt_path())
        return None

    resp = None
    outcome = "ok"
    try:
        with _state_lock:
            _state["model_calls"] += 1
        resp = client.chat.completions.create(
            model=model,
            reasoning_effort=effort,
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
        return data
    except Exception as e:
        outcome = ("unparseable_response" if resp is not None
                   else f"error: {type(e).__name__}")
        log.debug("LLM classify failed (%s) — using filename heuristic", e)
        return None
    finally:
        _write_receipt(file_path, model, effort, getattr(resp, "usage", None), outcome,
                       reservation_id)


def _classify_by_filename(file_path: str, content: str) -> dict:
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
                _cos_event_url(),
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


def _cos_event_url() -> str:
    """CoS /event endpoint from env COS_BACKEND (same default as minimoi_portal/config.py)."""
    return f"{os.environ.get('COS_BACKEND', 'http://localhost:8769').rstrip('/')}/event"


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
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
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
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
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
    snap["model_calls_today"] = _budget.calls_today()
    snap["model_limits"] = {
        "per_minute": cfg("model_calls_per_minute", DEFAULT_MODEL_CALLS_PER_MINUTE),
        "per_day": cfg("model_calls_per_day", DEFAULT_MODEL_CALLS_PER_DAY),
    }
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
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
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

def _unsafe_watch_path(full_path: Path) -> str | None:
    """Reason a resolved watch path must not be watched, or None if it is fine."""
    if BASE_DIR == full_path or BASE_DIR.is_relative_to(full_path):
        return "is the repo root or contains it"
    home = Path.home().resolve()
    if full_path == home or full_path.parent in (Path("/Users"), Path("/home")):
        return "is a home directory"
    return None


def _log_effective_settings(resolved: list[Path]) -> None:
    log.info("Watch paths: %s", ", ".join(str(p) for p in resolved) or "(none)")
    log.info("Excluded dirs: %s (plus _working/trash, _working/archive, decision-records)",
             ", ".join(sorted(_exclude_dirs())))
    log.info("Model: %s, reasoning_effort=%s — spec_*/build_plan_* .md only; "
             "limits %s/min, %s/day (%d used today, UTC); debounce %ss; receipts → %s",
             cfg("classify_model", DEFAULT_CLASSIFY_MODEL),
             cfg("classify_reasoning_effort", DEFAULT_CLASSIFY_REASONING_EFFORT),
             cfg("model_calls_per_minute", DEFAULT_MODEL_CALLS_PER_MINUTE),
             cfg("model_calls_per_day", DEFAULT_MODEL_CALLS_PER_DAY),
             _budget.calls_today(),
             cfg("debounce_seconds", DEFAULT_DEBOUNCE_SECONDS),
             _receipt_path())


def _start_watcher() -> bool:
    """Start the watchdog observer and block. Returns False if it refuses to start."""
    watch_paths = cfg("watch_paths", ["_working/"])
    resolved = [(BASE_DIR / wp).resolve() for wp in watch_paths]
    _log_effective_settings(resolved)
    for raw, full_path in zip(watch_paths, resolved):
        reason = _unsafe_watch_path(full_path)
        if reason:
            log.error("Refusing to start watcher: watch path %r resolves to %s, which %s. "
                      "Watch a subdirectory such as _working/ instead.", raw, full_path, reason)
            return False

    observer = Observer()
    scheduled = 0
    for watch_path, full_path in zip(watch_paths, resolved):
        if full_path.exists():
            observer.schedule(DesignDocHandler(), str(full_path), recursive=True)
            log.info("Watching: %s", full_path)
            scheduled += 1
        else:
            log.warning("Watch path does not exist — skipping: %s", full_path)

    if scheduled == 0:
        log.error("No valid watch paths — watcher not started")
        return False

    observer.start()
    log.info("Watchdog observer started (%d path(s))", scheduled)
    try:
        while True:
            time.sleep(1)
    except Exception:
        observer.stop()
    observer.join()
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    PORT = int(os.environ.get("PORT", 8771))
    load_config()
    seeded = _budget.seed_from_receipts(_receipt_path())
    if seeded:
        log.info("Daily model cap: %d call(s) already made today (from receipts)", seeded)

    print(f"""
🔍  Design/Dev Agent starting on port {PORT}…
   /status       — agent state and memory usage
   /health       — liveness probe
   /archive-spec — POST {{spec_file}} to move file from _working/ to archive/
   /start-build  — POST {{spec_file}} to flip status → in_build
   Watching: {cfg('watch_paths', ['_working/'])}
   Autonomy level: {cfg('autonomy_level', 1)}
   Debounce: {cfg('debounce_seconds', DEFAULT_DEBOUNCE_SECONDS)}s
   Model: {cfg('classify_model', DEFAULT_CLASSIFY_MODEL)} (effort {cfg('classify_reasoning_effort', DEFAULT_CLASSIFY_REASONING_EFFORT)})
   Model call limits: {cfg('model_calls_per_minute', DEFAULT_MODEL_CALLS_PER_MINUTE)}/min, {cfg('model_calls_per_day', DEFAULT_MODEL_CALLS_PER_DAY)}/day
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
