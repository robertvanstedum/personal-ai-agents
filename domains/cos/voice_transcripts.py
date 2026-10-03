"""Confer voice transcripts: one record per finished voice session.

The Confer page posts the session's turns here when voice stops
(``POST /ui/voice/transcript``). The record is appended to the CoS turn log of
Spec 160 (``data/cos-turns/YYYY/YYYY-MM-DD.jsonl``, named by Robert's local
day, UTC times inside, files 0600, folders 0700), as one whole line with
``record_type: "voice_session"`` and ``channel: "html_voice"``.

Nothing is written when:
- the turn log is not configured (``COS_TURNS_DIR`` unset): ``no_turn_log``;
- the conversation is Private (``domains/cos/private_mode.py``: ``_mode.json``
  says so, or cannot be read, or the request says so): ``private``;
- the mode changed during the session: the ``mode_epoch`` the voice bootstrap
  handed the page differs from the mode now, or is missing. The whole session
  then counts as Private (Spec 160 T6).
Nothing about a Private session's content is logged.

Text is scrubbed before it is kept: credentials (``utils/credential_scrub``,
Spec 160 §3.4) and card-like numbers (13 to 19 digits that pass the Luhn
check; the full payment scrub moves to ``utils`` with Spec 160). The body is
capped (``MAX_BODY``) before it is parsed. This module never imports
``chief_of_staff`` (which starts threads on import), so it is tested alone.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

from core.identity import resolve_user_id
from domains.cos import private_mode
from utils.credential_scrub import scrub as scrub_credentials
from utils.payment_scrub import scrub as scrub_payment

SCHEMA_VERSION = 1
MAX_TURNS = 200
MAX_TEXT = 4000
MAX_TOTAL = 60000
MAX_BODY = 300_000          # bytes, refused before the JSON is parsed
_CODE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
CONVERSATION_ID = "owner"
REMOVED = "[payment detail removed]"
_CARD_RUN = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def scrub_cards(text: str) -> tuple[str, bool]:
    """Remove card-like numbers; returns (text, changed)."""
    changed = False

    def repl(match: re.Match) -> str:
        nonlocal changed
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            changed = True
            return REMOVED
        return match.group(0)

    return _CARD_RUN.sub(repl, text), changed


turns_dir = private_mode.turns_dir
is_private = private_mode.is_private


def _local_day(now: datetime) -> datetime:
    try:
        tz = ZoneInfo(os.environ.get("COS_AGENT_TIMEZONE") or "America/Chicago")
    except Exception:
        tz = ZoneInfo("America/Chicago")
    return now.astimezone(tz)


def append_record(root: Path, record: dict, now: datetime) -> Path:
    """One whole line under an exclusive lock; a crash-truncated tail is
    closed with a newline and a log_gap line first (Spec 160)."""
    day = _local_day(now)
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


class InvalidTranscript(ValueError):
    pass


def _clean_turns(raw) -> tuple[list[dict], bool]:
    if not isinstance(raw, list):
        raise InvalidTranscript("turns must be a list")
    if len(raw) > MAX_TURNS:
        raise InvalidTranscript(f"at most {MAX_TURNS} turns")
    turns, sanitized, total = [], False, 0
    for item in raw:
        if not isinstance(item, dict):
            raise InvalidTranscript("each turn must be an object")
        speaker = item.get("speaker")
        if speaker not in ("user", "assistant"):
            raise InvalidTranscript("speaker must be user or assistant")
        text = item.get("text")
        if not isinstance(text, str):
            raise InvalidTranscript("text must be a string")
        text = text.strip()
        if not text:
            continue
        if len(text) > MAX_TEXT:
            raise InvalidTranscript(f"a turn holds at most {MAX_TEXT} characters")
        total += len(text)
        if total > MAX_TOTAL:
            raise InvalidTranscript("transcript too long")
        text, changed_cards = scrub_cards(text)
        before = text
        text = scrub_payment(text)                      # cards, IBANs, routing numbers, emails (Spec 160 §2)
        text, changed_creds = scrub_credentials(text)
        sanitized = sanitized or changed_cards or changed_creds or text != before
        turns.append({"speaker": speaker, "text": text, "completed": bool(item.get("completed", True))})
    return turns, sanitized


def _short(value, limit: int = 64) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:limit]


def create_voice_transcript_blueprint(*, clock=None) -> Blueprint:
    bp = Blueprint("cos_voice_transcripts", __name__)
    now_fn = clock or (lambda: datetime.now(timezone.utc))

    @bp.route("/ui/voice/transcript", methods=["POST"])
    def voice_transcript():
        if resolve_user_id(request) is None:
            return jsonify({"saved": False, "reason": "identity_required"}), 401
        if request.content_length is None or request.content_length > MAX_BODY:
            return jsonify({"saved": False, "reason": "too_large"}), 413
        if not request.is_json:
            return jsonify({"saved": False, "reason": "not_json"}), 400
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"saved": False, "reason": "invalid"}), 400
        root = turns_dir()
        if root is None:
            return jsonify({"saved": False, "reason": "no_turn_log"})
        private_now, epoch_now = private_mode.read_mode(root, CONVERSATION_ID)
        if body.get("private") is True or private_now:
            return jsonify({"saved": False, "reason": "private"})
        # The mode the session started under (from the voice bootstrap). A
        # change during the session, or no record of the start, makes the
        # whole session Private.
        if body.get("mode_epoch") != epoch_now:
            return jsonify({"saved": False, "reason": "mode_changed"})
        try:
            turns, sanitized = _clean_turns(body.get("turns"))
        except InvalidTranscript as exc:
            return jsonify({"saved": False, "reason": "invalid", "error": str(exc)}), 400
        if not turns:
            return jsonify({"saved": False, "reason": "empty"})
        now = now_fn()
        duration = body.get("duration_seconds")
        record = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "voice_session",
            "time": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "record_id": uuid.uuid4().hex,
            "conversation_id": CONVERSATION_ID,
            "channel": "html_voice",
            "session_id": _short(body.get("session_id")),
            "voice_provider": _short(body.get("provider"), 16),
            "reply_mode": "write" if body.get("reply_mode") == "write" else "speak",
            "partial": bool(body.get("partial")),
            "partial_reason": _short(body.get("partial_reason"), 40),
            "duration_seconds": round(float(duration), 1) if isinstance(duration, (int, float)) and duration >= 0 else None,
            "turns": turns,
            "sanitized": sanitized,
        }
        try:
            append_record(root, record, now)
        except OSError as exc:
            print(f"[cos_voice_transcript] saved=false reason=write_failed error={type(exc).__name__}", flush=True)
            return jsonify({"saved": False, "reason": "write_failed"}), 500
        return jsonify({"saved": True, "turns": len(turns)})

    return bp


__all__ = [
    "create_voice_transcript_blueprint", "is_private", "append_record",
    "scrub_cards", "turns_dir", "REMOVED",
]
