"""The generic turn-log writer shared by CoS and Master Craftsman (v0.5.1 §8).

One line per turn goes to ``<root>/YYYY/YYYY-MM-DD.jsonl``, named by Robert's
local day, with UTC times inside; folders 0700, files 0600. Callers decide *what*
is written and *whether* (Private, switched off, unset directory); this module
decides *how*, and is the only place that knows the file layout.

Rules, each tested in ``tests/agent_turns/test_writer.py`` (and, for CoS, in
``tests/cos/test_turn_log.py``, which must stay green unchanged):
- **Whole lines under a lock.** ``append_record`` takes an exclusive ``flock``,
  writes one line, and first closes a crash-truncated tail with a newline and a
  ``log_gap`` line.
- **Never fatal, fixed codes.** ``save_record`` returns ``(saved, code)`` with
  codes ``saved`` / ``disk_low`` / ``disk_write_failed`` / ``internal`` and never
  raises. Nothing about an error (name, text, exception message) leaves it.
- **Visible failure.** ``_status/<container>.json`` keeps the last success time,
  the last failure time and a fixed failure code; no text. Writing it never fails
  a turn.
- **Headroom.** Below the free-space floor (5 GB, or a fraction of the disk when
  one is configured, or an unreadable disk) a write is skipped with ``disk_low``.
  Nothing is ever deleted to make room (v0.5.1 §4).
- **Scrubbed before it is kept.** ``cap_and_scrub`` caps text (8,000 characters
  for what Robert said, 16,000 for the reply) and then runs the payment scrub
  and the credential guard; it reports whether anything changed.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import socket
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from utils.credential_scrub import scrub as scrub_credentials
from utils.payment_scrub import scrub as scrub_payment

SCHEMA_VERSION = 1
MAX_USER_TEXT = 8_000
MAX_REPLY = 16_000
STATUS_DIR = "_status"
DEFAULT_MIN_FREE_BYTES = 5 * 1024 ** 3

# Fixed outcome codes (no error text ever leaves this module).
SAVED = "saved"
DISK_LOW = "disk_low"
WRITE_FAILED = "disk_write_failed"
INTERNAL = "internal"
FAILURES = frozenset({DISK_LOW, WRITE_FAILED, INTERNAL})


def local_day(now: datetime) -> datetime:
    """Robert's local time for ``now``: ``AGENT_TURNS_TIMEZONE``, else ``COS_AGENT_TIMEZONE``, else Chicago."""
    name = os.environ.get("AGENT_TURNS_TIMEZONE") or os.environ.get("COS_AGENT_TIMEZONE") or "America/Chicago"
    try:
        tz = ZoneInfo(name)
    except Exception:
        tz = ZoneInfo("America/Chicago")
    return now.astimezone(tz)


def append_record(root: Path, record: dict, now: datetime) -> Path:
    """One whole line under an exclusive lock; a crash-truncated tail is
    closed with a newline and a log_gap line first (Spec 160)."""
    day = local_day(now)
    folder = root / f"{day:%Y}"
    for path in (root, folder):
        path.mkdir(mode=0o700, exist_ok=True)   # new folders only; an existing mount keeps its mode
    target = folder / f"{day:%Y-%m-%d}.jsonl"
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        size = os.fstat(fd).st_size
        prefix = ""
        if size:
            os.lseek(fd, size - 1, os.SEEK_SET)
            if os.read(fd, 1) != b"\n":
                prefix = "\n" + json.dumps({"record_type": "log_gap", "reason": "truncated_tail"}) + "\n"
        os.write(fd, (prefix + line).encode("utf-8"))
    finally:
        os.close(fd)
    return target


def container_name(env_var: str, default: str) -> str:
    """The status-file name: ``$env_var``, else the hostname, else ``default``; filename-safe."""
    raw = os.environ.get(env_var) or socket.gethostname() or default
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", raw)[:64] or default


def disk_low(root: Path, *, floor_env: str, fraction_env: str) -> bool:
    """True when free space is below the floor. An unreadable disk counts as low."""
    try:
        usage = shutil.disk_usage(root if root.exists() else root.parent)
    except OSError:
        return True
    floor = int(os.environ.get(floor_env, DEFAULT_MIN_FREE_BYTES))
    if usage.free < floor:
        return True
    fraction = os.environ.get(fraction_env)
    return bool(fraction) and usage.total > 0 and usage.free / usage.total < float(fraction)


def cap_and_scrub(text: str, limit: int) -> tuple[str, bool]:
    text = (text or "")[:limit]
    scrubbed = scrub_payment(text)
    scrubbed, creds = scrub_credentials(scrubbed)
    return scrubbed, scrubbed != text or creds


def write_status(root: Path, container: str, *, ok: bool, code: str | None, now: datetime) -> None:
    """The per-container status file; any failure here is swallowed."""
    try:
        folder = root / STATUS_DIR
        folder.mkdir(mode=0o700, exist_ok=True)
        path = folder / f"{container}.json"
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        except (OSError, ValueError):
            state = {}
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        state["schema_version"] = SCHEMA_VERSION
        state["container"] = container
        if ok:
            state["last_success_at"] = stamp
        else:
            state["last_failure_at"] = stamp
            state["last_failure_code"] = code if code in FAILURES else INTERNAL
        tmp = folder / f".{container}.tmp-{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except Exception:
        pass


def save_record(root: Path, build: Callable[[], dict], *, now: datetime, container: str, log_tag: str,
                floor_env: str, fraction_env: str,
                append: Callable[[Path, dict, datetime], object] | None = None) -> tuple[bool, str]:
    """Headroom check, build, append, status. Returns (saved, code); never raises.

    ``build`` runs after the headroom check so a skipped write costs nothing, and
    any exception from it is an ``internal`` failure. ``append`` defaults to
    ``append_record`` (callers pass their own module attribute so tests can swap it).
    """
    put = append or append_record
    try:
        if disk_low(root, floor_env=floor_env, fraction_env=fraction_env):
            write_status(root, container, ok=False, code=DISK_LOW, now=now)
            return False, DISK_LOW
        put(root, build(), now)
    except OSError:
        write_status(root, container, ok=False, code=WRITE_FAILED, now=now)
        print(f"[{log_tag}] saved=false code={WRITE_FAILED}", flush=True)
        return False, WRITE_FAILED
    except Exception:
        write_status(root, container, ok=False, code=INTERNAL, now=now)
        print(f"[{log_tag}] saved=false code={INTERNAL}", flush=True)
        return False, INTERNAL
    write_status(root, container, ok=True, code=None, now=now)
    return True, SAVED
