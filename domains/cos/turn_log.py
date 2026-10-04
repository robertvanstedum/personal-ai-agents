"""CoS text-turn log (Spec 160 §2, M1): one line per Confer turn.

``record_turn`` is the one optional hook of ``ConferTurnService`` (like its
``save_note``). It appends a ``cos_turn`` record to the same daily file that
voice sessions use (``COS_TURNS_DIR/YYYY/YYYY-MM-DD.jsonl``, named by Robert's
local day, UTC times inside, 0600/0700), through ``voice_transcripts.append_record``
so whole-line appends, the file lock and the ``log_gap`` repair are shared. The
generic parts (append, status file, headroom, scrub) live in ``core/agent_turns``,
which Master Craftsman uses too (v0.5.1 §8); this module keeps the CoS-specific
part: Private, the switch, the record shape.

Rules, each tested in ``tests/cos/test_turn_log.py``:
- **Best effort, never fatal.** Every failure is caught and reported as a fixed
  code; the turn still answers. ``history_saved`` is ``True`` when a line was
  written, ``False`` when a write was attempted and failed, and ``None`` when
  nothing was meant to be written (Private, no turn log, switched off).
- **Private writes nothing.** Private is on if the stored mode says so, the mode
  cannot be read, or the request says so (``private_mode.read_mode`` fails to
  Private). Nothing about a Private turn is logged, not even its length.
- **The mode is latched when the turn starts** (Codex review of M1, 2026-10-04). ``begin`` reads the stored mode and
  its epoch before the backend runs; ``record_turn`` writes nothing if the turn started Private, if the mode could not
  be read then, or if the epoch differs now (any change in between, even Private on and off again). The request's own
  flag stays an extra way to say Private. This is the same epoch rule a voice session follows at Stop.
- **Scrubbed before it is kept.** The payment scrub (cards, IBANs, routing
  numbers, emails) and the credential guard run on both text fields, which are
  capped first (8,000 and 16,000 characters).
- **Visible failure.** Each container keeps ``_status/<container>.json`` with the
  last success time, the last failure time and a fixed failure code. Never a
  name, text or exception message.
- **Headroom.** A write below the free-space floor is skipped with ``disk_low``;
  nothing is ever deleted to make room (v0.5.1 §4).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Callable, NamedTuple

from core.agent_turns import writer as core_writer
from core.agent_turns.writer import (DEFAULT_MIN_FREE_BYTES, DISK_LOW, FAILURES, INTERNAL, MAX_REPLY,  # noqa: F401
                                     MAX_USER_TEXT, SAVED, SCHEMA_VERSION, STATUS_DIR, WRITE_FAILED)
from domains.cos import private_mode
from domains.cos.voice_transcripts import append_record

NO_TURN_LOG = "no_turn_log"
DISABLED = "disabled"
PRIVATE = "private"
MODE_CHANGED = "mode_changed"


class Latch(NamedTuple):
    """The mode as it was when the turn began."""
    private: bool
    epoch: str | None
    readable: bool


def begin(request) -> Latch | None:
    """Read the stored mode before the turn runs. None when there is nothing to latch (log off, no turn log)."""
    try:
        if not enabled():
            return None
        root = private_mode.turns_dir()
        if root is None:
            return None
        private_now, epoch = private_mode.read_mode(root, (getattr(request, "conversation_id", "") or "").strip() or "owner")
        return Latch(bool(private_now), epoch, True)
    except Exception:
        return Latch(True, None, False)          # fail closed: a turn whose mode could not be read starts as Private


def enabled() -> bool:
    """``COS_TURN_LOG_ENABLED=0`` switches the log off (the rollback of Spec 160 §11)."""
    return os.environ.get("COS_TURN_LOG_ENABLED", "1").strip() not in {"0", "false", "no", "off"}


def container_name() -> str:
    return core_writer.container_name("COS_CONTAINER_NAME", "cos")


def _cap_and_scrub(text: str, limit: int) -> tuple[str, bool]:
    return core_writer.cap_and_scrub(text, limit)


def _build_record(request, result, now: datetime) -> dict:
    user_text, user_changed = _cap_and_scrub(result.user_text, MAX_USER_TEXT)
    reply, reply_changed = _cap_and_scrub(result.reply, MAX_REPLY)
    record = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "cos_turn",
        "time": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "turn_id": result.turn_id,
        "conversation_id": result.conversation_id,
        "channel": result.channel,
        "backend_type": os.environ.get("COS_BACKEND_TYPE", "").strip().lower() or None,
        "backend_label": result.backend_label,
        "served_provider": result.served_provider,
        "served_model": result.model_label or None,
        "configured_primary": result.configured_primary,
        "fallback_position": result.fallback_position,
        "fallback_reason": result.fallback_reason,
        "receipt_id": result.turn_id,
        "user_text": user_text,
        "reply": reply,
        "sanitized": user_changed or reply_changed,
    }
    if result.operation is not None:
        record["operation"] = {k: result.operation.get(k) for k in ("type", "status", "storage")}
    return {k: v for k, v in record.items() if v is not None}


def record_turn(request, result, *, clock: Callable[[], datetime] | None = None,
                container: str | None = None, latch: Latch | None = None) -> tuple[bool | None, str]:
    """Append one turn. Returns (history_saved, code); never raises. ``latch`` is what ``begin`` read at the start.

    The final mode check and the append happen **inside one shared hold of the mode lock** (``private_mode.hold_mode``),
    the same lock ``set_private`` takes exclusively, so the mode cannot change between "was it Private?" and the write."""
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    try:
        if not enabled():
            return None, DISABLED
        root = private_mode.turns_dir()
        if root is None:
            return None, NO_TURN_LOG
        with private_mode.hold_mode(root):
            private_now, epoch_now = private_mode.read_mode(root, result.conversation_id)
            if private_now or getattr(request, "private", False):
                return None, PRIVATE
            if latch is not None:
                if latch.private or not latch.readable:
                    return None, PRIVATE         # the turn began Private (or its mode was unreadable): it stays unlogged
                if latch.epoch != epoch_now:
                    return None, MODE_CHANGED    # the mode moved while the turn ran, in either direction: log nothing
            return core_writer.save_record(
                root, lambda: _build_record(request, result, now), now=now, container=container or container_name(),
                log_tag="cos_turn_log", floor_env="COS_TURNS_MIN_FREE_BYTES", fraction_env="COS_TURNS_MIN_FREE_FRACTION",
                append=append_record)
    except Exception:
        return False, INTERNAL           # the mode could not be decided or locked: log nothing, say so


def recorder(*, clock: Callable[[], datetime] | None = None, container: str | None = None):
    """The ``record_turn`` callable ``ConferTurnService`` takes: (request, result[, latch]) -> history_saved.
    ``hook.begin`` is the matching ``begin_turn`` hook."""
    def hook(request, result, latch=None):
        saved, _ = record_turn(request, result, clock=clock, container=container, latch=latch)
        return saved
    hook.begin = begin
    return hook


__all__ = ["record_turn", "recorder", "begin", "Latch", "MODE_CHANGED", "enabled", "container_name", "SAVED", "NO_TURN_LOG",
           "DISABLED", "PRIVATE", "DISK_LOW", "WRITE_FAILED", "INTERNAL"]
