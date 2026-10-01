"""Master Craftsman's turn trace, file first: one JSON line per turn.

The portal times every turn itself (start to end, around the backend call) and
appends one line to ``mc_turns.jsonl`` next to the build queue (the Guild data
folder, already writable by the portal). A kept reply's line carries its note's
request id, so the Shop floor can show "Done in 1.2s" under that reply after a
reload. Nothing in the note store changes.

Tokens and cost are NOT here: OpenClaw 2026.9.6's compat API reports zeros.
Each line has an empty ``usage`` slot, to be filled from the gateway's own
per-call usage record (the usage-record PR), joined by ``turn_id`` or by MC's
spend delta (MC takes one turn at a time, so the delta is exact).

Never blocking, never failing a turn: a write or read problem is logged and
ignored; the footer is simply not shown.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

FILE_NAME = "mc_turns.jsonl"
READ_TAIL_BYTES = 512 * 1024          # the recent turns only; old replies simply show no footer
SHOWN_KINDS = ("openclaw",)           # a live MC reply; a stub reply gets no footer

_LOCK = threading.Lock()
_log = logging.getLogger("guild_ui.mc")


def _window(line: dict, duration_ms: int):
    """(start, end) of a traced turn as aware datetimes, or None."""
    def parse(v):
        try:
            d = datetime.fromisoformat(str(v))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    end = parse(line.get("ended_at")) or parse(line.get("at"))
    if end is None:
        return None
    start = parse(line.get("started_at")) or (end - timedelta(milliseconds=duration_ms))
    if not line.get("ended_at"):
        end = end + timedelta(seconds=1)          # an older line: "at" is to the second
    return start, end


def done_text(duration_ms: int) -> str:
    seconds = max(0, duration_ms) / 1000
    return f"Done in {seconds:.1f}s" if seconds < 10 else f"Done in {round(seconds)}s"


class TurnLog:
    def __init__(self, folder: str | None):
        self.path = os.path.join(folder, FILE_NAME) if folder else None

    def record(self, *, turn_id: str, status: str, backend_kind: str | None, duration_ms: int,
               reply_request_id: str | None = None, failure_class: str | None = None,
               mode: str | None = None) -> None:
        """One line per turn. ``mode`` is "stream" for a streamed turn, whose
        interrupted and stopped turns get a line too (streaming spec v0.3 §6),
        with no reply id; ``duration_ms`` is then the time to the final text."""
        if not self.path:
            return
        ended = datetime.now(timezone.utc)
        line = {"turn_id": turn_id, "at": ended.isoformat(timespec="seconds"),
                # The turn's window, to the millisecond (usage-record U3: MC's gateway
                # calls inside it are this turn's, since MC takes one turn at a time).
                "started_at": (ended - timedelta(milliseconds=int(duration_ms))).isoformat(timespec="milliseconds"),
                "ended_at": ended.isoformat(timespec="milliseconds"),
                "status": status, "failure_class": failure_class, "backend_kind": backend_kind,
                "duration_ms": int(duration_ms), "reply_request_id": reply_request_id,
                "usage": None}
        if mode:
            line["mode"] = mode
        try:
            with _LOCK:
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as f:
                    f.write(json.dumps(line, sort_keys=True) + "\n")
        except OSError as exc:
            _log.warning("mc turn %s: turn trace not written (%s)", turn_id, type(exc).__name__)

    def turns_for(self, request_ids) -> dict[str, dict]:
        """{reply request id: {"duration_ms", "done_text", "usage"}} for live replies."""
        wanted = {r for r in request_ids if r}
        if not self.path or not wanted:
            return {}
        try:
            with open(self.path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - READ_TAIL_BYTES))
                raw = f.read().decode("utf-8", errors="replace")
        except OSError:
            return {}
        found = {}
        for text in raw.splitlines():
            try:
                line = json.loads(text)
            except ValueError:
                continue                      # a partial first line of the tail, or a torn write
            rid = line.get("reply_request_id") if isinstance(line, dict) else None
            if rid in wanted and line.get("status") == "answered" and line.get("backend_kind") in SHOWN_KINDS:
                ms = line.get("duration_ms")
                if isinstance(ms, int):
                    found[rid] = {"duration_ms": ms, "done_text": done_text(ms), "usage": line.get("usage"),
                                  "window": _window(line, ms), "turn_id": line.get("turn_id")}
        return found

    def annotate(self, notes):
        """Add ``turn`` to each kept MC reply that has a trace; others get none.
        Output tokens are joined from the usage store (usage-record U3)."""
        if not notes:
            return notes
        turns = self.turns_for(n.get("request_id") for n in notes
                               if isinstance(n, dict) and str(n.get("request_id") or "").startswith("mc-"))
        if turns:
            from .usage_join import add_tokens
            add_tokens(turns)
        for n in notes:
            if isinstance(n, dict) and n.get("request_id") in turns:
                n["turn"] = turns[n["request_id"]]
        return notes


def turn_log_of(services) -> TurnLog:
    store = getattr(services, "store", None)
    return TurnLog(getattr(store, "folder", None) if store is not None and getattr(store, "path", None) else None)


__all__ = ["TurnLog", "turn_log_of", "done_text", "FILE_NAME"]
