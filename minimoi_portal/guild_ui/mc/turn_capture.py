"""Master Craftsman's text-turn capture (memory build, v0.5.1 §8): one ``mc_turn``
line per answered, live turn, through the same writer CoS uses (``core/agent_turns``).

The file is ``MC_TURNS_DIR/YYYY/YYYY-MM-DD.jsonl`` (named by Robert's local day,
UTC times inside, 0600/0700), with ``_status/<container>.json`` beside it. It is
**not** ``mc_turns.jsonl``, MC's timing trace (mc/turn_log.py), which stays as it
is; that trace only learns whether this line was saved (``history_saved``).

Rules, each tested in ``tests/guild/shop_floor/test_mc_turn_capture.py``:
- **Opt-in.** ``MC_TURNS_DIR`` unset means nothing is written and ``history_saved``
  is absent. Only the staging overlay sets it; production never does.
- **Answered and live only.** A turn is written when it was answered by a real
  backend (``REAL_KINDS``: openclaw, grok) and its reply was kept. The stub, a
  failure, a partial or stopped stream and a reply that was not kept write nothing.
- **Once per turn.** The plain and the streaming endpoints call this once, at the
  point they keep the reply. A repeated or already-dispatched request never reaches it.
- **Off the record never gets here.** The write guard refuses it 409 before any
  read or write (security.py); there is no separate MC Private mode.
- **Best effort, never fatal (R6).** Every failure is caught and returned as a fixed
  code; the turn still answers with the same status and the same kept notes.
  ``history_saved`` is True when a line was written, False when a write was
  attempted and failed, None when nothing was meant to be written.
- **Scrubbed before it is kept.** ``user_text`` is the text that was sent (already
  scrubbed); both fields are capped, then payment-scrubbed and credential-guarded
  by the shared writer.
- **Visible failure and headroom** are the shared writer's: ``_status`` with fixed
  codes, and a ``disk_low`` skip below 5 GB (or ``MC_TURNS_MIN_FREE_FRACTION``).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.agent_turns import writer as core_writer
from core.agent_turns.writer import DISK_LOW, INTERNAL, MAX_REPLY, MAX_USER_TEXT, SAVED, WRITE_FAILED  # noqa: F401

from .backend import REAL_KINDS

ENV_DIR = "MC_TURNS_DIR"
RECORD_TYPE = "mc_turn"
CHANNEL = "shop_floor"
LOG_TAG = "mc_turn_log"
NO_TURN_LOG = "no_turn_log"
NOT_LIVE = "not_live"


def turns_dir() -> Path | None:
    value = os.environ.get(ENV_DIR, "").strip()
    return Path(value) if value else None


def container_name() -> str:
    return core_writer.container_name("MC_CONTAINER_NAME", "portal")


def _build_record(*, now: datetime, turn_id: str, conversation_id, note: dict, note_id: str, user_text: str,
                  reply_note: dict, backend_kind: str) -> dict:
    user, user_changed = core_writer.cap_and_scrub(user_text, MAX_USER_TEXT)
    reply, reply_changed = core_writer.cap_and_scrub(reply_note.get("text"), MAX_REPLY)
    record = {
        "schema_version": core_writer.SCHEMA_VERSION,
        "record_type": RECORD_TYPE,
        "time": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "turn_id": turn_id,
        "conversation_id": conversation_id,
        "channel": CHANNEL,
        "backend_type": backend_kind,
        "backend_label": reply_note.get("author_label"),
        "note_id": note.get("id"),
        "note_request_id": note_id,
        "reply_note_id": reply_note.get("id"),
        "reply_request_id": reply_note.get("request_id"),
        "user_text": user,
        "reply": reply,
        "sanitized": user_changed or reply_changed,
    }
    return {k: v for k, v in record.items() if v is not None}


def capture_turn(*, turn_id: str, conversation_id, note: dict, note_id: str, user_text: str, reply_note: dict,
                 backend_kind: str | None, clock: Callable[[], datetime] | None = None,
                 container: str | None = None, append=None) -> tuple[bool | None, str]:
    """Append one answered, live turn. Returns (history_saved, code); never raises."""
    try:
        root = turns_dir()
        if root is None:
            return None, NO_TURN_LOG
        if backend_kind not in REAL_KINDS:
            return None, NOT_LIVE
        now = (clock or (lambda: datetime.now(timezone.utc)))()
        name = container or container_name()
        return core_writer.save_record(
            root,
            lambda: _build_record(now=now, turn_id=turn_id, conversation_id=conversation_id, note=note or {},
                                  note_id=note_id, user_text=user_text, reply_note=reply_note,
                                  backend_kind=backend_kind),
            now=now, container=name, log_tag=LOG_TAG, floor_env="MC_TURNS_MIN_FREE_BYTES",
            fraction_env="MC_TURNS_MIN_FREE_FRACTION", append=append)
    except Exception:
        return False, INTERNAL


def capturer(*, turn_id: str, conversation_id, note: dict, note_id: str, user_text: str, backend_kind: str | None,
             clock: Callable[[], datetime] | None = None) -> Callable[[dict], bool | None]:
    """The hook both endpoints call with the kept reply: (reply_note) -> history_saved."""
    def hook(reply_note):
        saved, _ = capture_turn(turn_id=turn_id, conversation_id=conversation_id, note=note, note_id=note_id,
                                user_text=user_text, reply_note=reply_note, backend_kind=backend_kind, clock=clock)
        return saved
    return hook


__all__ = ["capture_turn", "capturer", "turns_dir", "container_name", "ENV_DIR", "RECORD_TYPE", "CHANNEL",
           "NO_TURN_LOG", "NOT_LIVE"]
