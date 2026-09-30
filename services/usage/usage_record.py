"""MiniMoi's standard usage record, v1: one JSON line per model or tool call.

Design: planning-studio/initiatives/INIT-2026-0007-interaction-vision/documents/
USAGE_RECORD_DESIGN_NOTE_2026-09-29.md (approved). Stdlib only, so every
emitter can load it: the gateway's recorder (one emitter among several; LiteLLM
is not the core), and the direct-call helper (Tavily, CoS's Grok backend).

* No content and no secrets: unknown fields are refused, and any value that
  looks like a key, token or bearer credential is refused.
* Unknown numbers are null, never 0-as-unknown.
* File first: ``$MINIMOI_USAGE_DIR/usage-YYYY-MM.jsonl``, mode 600, append
  only, one ``os.write`` per line (under 4 KB, O_APPEND), so writers in
  several containers never interleave.
* Fire and forget: ``record()`` hands the line to one daemon thread through a
  small bounded queue; a full queue drops the line with a warning; nothing
  ever raises into the caller. With MINIMOI_USAGE_DIR unset (production today)
  nothing is written at all.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import uuid
from datetime import datetime, timezone

VERSION = 1
MAX_LINE_BYTES = 4000
QUEUE_MAX = 256

# "runtime-stream" is a streamed run's own usage (streaming spec v0.3 §4; it replaces the
# reserved "openclaw-stream" name: the runtime appears only in `route`).
EMITTER_RE = re.compile(r"^(gateway|runtime-stream|helper:[a-z0-9_.-]{1,40})$")
STATUSES = ("ok", "error", "refused")
KINDS = ("model", "search", "speech")
# "unrecorded-abort": a streamed run MiniMoi aborted (Stop or a limit). The
# gateway (LiteLLM 1.93.1) logs nothing for a client-cancelled stream, so the
# run's cost is unknown, and possibly billed (streaming S1, staging 2026-09-29).
COST_SOURCES = ("provider", "price_table", "none", "unrecorded-abort")

FIELDS = {
    "v", "record_id", "occurred_at", "env", "emitter", "actor", "kind", "route", "provider", "model",
    "deployment_id", "fallback_position", "status", "http_status", "error_class", "latency_ms",
    "input_tokens", "output_tokens", "cached_tokens", "units", "cost_usd", "cost_source", "key_ref",
    "correlation_id",
}
REQUIRED_TEXT = ("occurred_at", "env", "emitter", "actor", "kind", "route", "status", "cost_source")
OPTIONAL_TEXT = ("provider", "model", "deployment_id", "error_class", "key_ref", "correlation_id")
NUMBERS = ("latency_ms", "cost_usd")
COUNTS = ("input_tokens", "output_tokens", "cached_tokens", "fallback_position", "http_status")

# Anything credential-shaped is refused, wherever it appears.
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|xai-[A-Za-z0-9]{16,}|tvly-[A-Za-z0-9]{8,}|bearer\s+\S+"
                        r"|-----BEGIN|eyJ[A-Za-z0-9_\-]{20,}\.)", re.IGNORECASE)
_SECRET_ENV_RE = re.compile(r"(_KEY|_TOKEN|_SECRET|_PASSWORD|DATABASE_URL)$")

_log = logging.getLogger("minimoi.usage")


def _secret_values() -> set[str]:
    return {v for k, v in os.environ.items() if _SECRET_ENV_RE.search(k) and v and len(v) >= 8}


def _check_text(name: str, value, secrets: set[str], required: bool) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"usage record {name} is required")
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError(f"usage record {name} must be short text")
    if _SECRET_RE.search(value) or value in secrets or any(s in value for s in secrets if len(s) >= 12):
        raise ValueError(f"usage record {name} looks like a credential; refused")
    return value.strip()


def validate(payload: dict) -> dict:
    """A normalized v1 record, or ValueError. Never echoes a refused value."""
    if not isinstance(payload, dict):
        raise ValueError("usage record must be an object")
    unknown = set(payload) - FIELDS
    if unknown:
        raise ValueError(f"usage record has unknown fields: {sorted(unknown)}")
    secrets = _secret_values()
    out: dict = {"v": VERSION}
    out["record_id"] = str(uuid.UUID(str(payload.get("record_id") or uuid.uuid4())))
    for name in REQUIRED_TEXT:
        out[name] = _check_text(name, payload.get(name), secrets, True)
    for name in OPTIONAL_TEXT:
        out[name] = _check_text(name, payload.get(name), secrets, False)
    if not EMITTER_RE.match(out["emitter"]):
        raise ValueError("usage record emitter must be gateway, runtime-stream or helper:<name>")
    if out["status"] not in STATUSES:
        raise ValueError(f"usage record status must be one of {STATUSES}")
    if out["kind"] not in KINDS:
        raise ValueError(f"usage record kind must be one of {KINDS}")
    if out["cost_source"] not in COST_SOURCES:
        raise ValueError(f"usage record cost_source must be one of {COST_SOURCES}")
    for name in NUMBERS:
        value = payload.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
            raise ValueError(f"usage record {name} must be a non-negative number or null")
        out[name] = round(float(value), 9) if value is not None else None
    for name in COUNTS:
        value = payload.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError(f"usage record {name} must be a non-negative integer or null")
        out[name] = value
    units = payload.get("units")
    if units is not None:
        if not isinstance(units, dict) or len(units) > 4 or not all(
                isinstance(k, str) and re.fullmatch(r"[a-z_]{1,24}", k) and isinstance(v, (int, float))
                and not isinstance(v, bool) and v >= 0 for k, v in units.items()):
            raise ValueError("usage record units must be a small {name: non-negative number} map")
    out["units"] = units
    line = json.dumps(out, sort_keys=True, separators=(",", ":"))
    if len(line.encode("utf-8")) > MAX_LINE_BYTES:
        raise ValueError("usage record is too long")
    return out


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def usage_dir() -> str | None:
    return os.environ.get("MINIMOI_USAGE_DIR") or None


def env_name() -> str:
    return os.environ.get("MINIMOI_ENV") or "unknown"


def append(record: dict, folder: str | None = None) -> None:
    """Validate and append one line now (raises on a bad record or a write error)."""
    folder = folder or usage_dir()
    if not folder:
        return
    rec = validate(record)
    month = rec["occurred_at"][:7] if re.match(r"^\d{4}-\d{2}", rec["occurred_at"]) else now_iso()[:7]
    path = os.path.join(folder, f"usage-{month}.jsonl")
    data = (json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


class _Writer:
    def __init__(self):
        self.q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()

    def put(self, record: dict, folder: str | None) -> bool:
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, name="minimoi-usage-writer", daemon=True)
                self.thread.start()
        try:
            self.q.put_nowait((record, folder))
            return True
        except queue.Full:
            _log.warning("usage record dropped: the writer queue is full")
            return False

    def _run(self):
        while True:
            record, folder = self.q.get()
            try:
                append(record, folder)
            except Exception as exc:          # never raise anywhere: log the class only, never the record
                _log.warning("usage record not written (%s)", type(exc).__name__)
            finally:
                self.q.task_done()

    def flush(self, timeout: float = 5.0) -> None:
        """For tests and shutdown: wait until queued lines are written."""
        done = threading.Event()

        def waiter():
            self.q.join()
            done.set()
        threading.Thread(target=waiter, daemon=True).start()
        done.wait(timeout)


_WRITER = _Writer()


def record(folder: str | None = None, **fields) -> bool:
    """Fire and forget. Returns False when nothing was queued (no folder, a
    full queue, or an error); it never raises and never blocks on disk."""
    try:
        folder = folder or usage_dir()
        if not folder:
            return False
        fields.setdefault("occurred_at", now_iso())
        fields.setdefault("env", env_name())
        return _WRITER.put(dict(fields), folder)
    except Exception as exc:                  # pragma: no cover - belt and braces
        _log.warning("usage record not queued (%s)", type(exc).__name__)
        return False


def flush(timeout: float = 5.0) -> None:
    _WRITER.flush(timeout)


def read(folder: str, months: int = 2) -> list[dict]:
    """The most recent months' records (readers: reports, the portal footer).
    Torn or foreign lines are skipped."""
    out: list[dict] = []
    try:
        names = sorted(n for n in os.listdir(folder) if re.fullmatch(r"usage-\d{4}-\d{2}\.jsonl", n))[-months:]
    except OSError:
        return out
    for name in names:
        try:
            with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec, dict) and rec.get("v") == VERSION:
                        out.append(rec)
        except OSError:
            continue
    return out


# ── One folder per writer (the first-writer ownership fix) ─────────────────────
# Files are created 0600 by whoever writes first. The gateway runs as a non-root
# user and appends to the shared monthly file (usage-YYYY-MM.jsonl at the top of
# the store); every other writer (cos-bot, cos-scheduler, the portal) runs as
# root. Had one of them created the month's shared file first, the gateway could
# not append to it that month. So every writer but the gateway writes only into
# its own subfolder, <store>/<writer>/ (the same v1 files and format), and the
# shared file is only ever the gateway's. Readers use read_all(), which reads the
# top level and every writer's folder.

WRITER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
DEFAULT_WRITER = "direct"


def own_folder(writer: str | None = None, *, create: bool = True) -> str | None:
    """This writer's own folder: MINIMOI_USAGE_OWN_DIR if set, else
    $MINIMOI_USAGE_DIR/<writer>, where the writer is the argument, else
    MINIMOI_USAGE_WRITER, else "direct". None when there is no store (nothing
    is written). With ``create``, the folder is made (0700) when missing."""
    folder = os.environ.get("MINIMOI_USAGE_OWN_DIR") or None
    if folder is None:
        store = usage_dir()
        if not store:
            return None
        name = writer or os.environ.get("MINIMOI_USAGE_WRITER") or DEFAULT_WRITER
        if not WRITER_RE.match(name):
            raise ValueError("a usage writer's name is short lower-case text")
        folder = os.path.join(store, name)
    if create:
        os.makedirs(folder, mode=0o700, exist_ok=True)
    return folder


def read_all(folder: str, months: int = 2) -> list[dict]:
    """The most recent months' records of the whole store: the top level (the
    gateway's) and every writer's own folder. Torn or foreign lines are skipped."""
    out = read(folder, months)
    try:
        names = sorted(n for n in os.listdir(folder) if WRITER_RE.match(n) and os.path.isdir(os.path.join(folder, n)))
    except OSError:
        return out
    for name in names:
        out.extend(read(os.path.join(folder, name), months))
    return out


__all__ = ["validate", "append", "record", "flush", "read", "read_all", "own_folder", "now_iso", "usage_dir",
           "env_name", "FIELDS", "VERSION", "WRITER_RE"]
