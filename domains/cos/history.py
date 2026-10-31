"""The canonical CoS history: every Confer conversation, kept and reloadable (spec v0.3 section 4.19, C5 and U6).

Robert, 30 Oct: "when I tab off to MC, all of the text on CoS dissappears when I come back. Plus, there's no history of
chats like in MC." This is the record that fixes both. The CoS backend (minimoi-cos-scheduler) is its only writer;
the agent reads the folder read-only. The records are contracts/cos/v1/conversation.schema.json, validated on every
write.

Layout, under <COS_CONVERSATIONS_DIR>/history/ (production: /opt/minimoi/cos/conversations/history/):

    conversations/<conversation_id>.json   the conversation's metadata, written once
    turns/<conversation_id>.jsonl          every state of every turn, appended; a turn is its last line
    notes/<conversation_id>.jsonl          proactive stream notes, one per note_id
    selection/<owner>.<device>.json        the conversation selected per owner and device, held server-side
    .lock, quarantine-*.bin                writer-private

The rules:
- **A send is idempotent by the client's turn_id.** The same turn_id with the same text returns the turn as it stands;
  with different text, or in another conversation, it is refused.
- **"Saved" is true only after the durable acknowledgment:** the pending turn is fsynced and read back before begin()
  returns, and durable_ack_at says when.
- **A reply lands on its own turn,** found by conversation and turn id, never on whatever conversation is selected
  now. A reply to a turn that had already failed makes it ``recovered``.
- **On start,** a torn last line is quarantined, and a turn still pending from an earlier process is marked failed
  with ``interrupted``: the process that would have answered it is gone (the scheduler is a single process).
- **The monthly recall log** (domains/cos/conversation_log.py) becomes a projection of this history, written after
  each answer and marked ``projection``. The import of the old monthly logs skips those lines.
- **The "Before 1.2" conversation** (cos-before-1-2) is the read-only import of the monthly logs written before the
  history existed.
- **Private** conversations are kept, receive no proactive notes, and are never shelf-ingested.

The ``robert_source`` verifier (section 4.2) lives here too: a span is Robert's own only when it is a verbatim part of
one of his turns in this history. Turn refs are ``{"type": "cos.turn", "id": "<conversation_id>:<turn_id>"}``.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping
from uuid import UUID

from domains.cos.store import contracts, fsio
from domains.cos.store.canonical import digest

BEFORE_1_2 = "cos-before-1-2"
# The conversation Confer has always used ("owner") becomes the first canonical conversation, titled "CoS". Its id is
# fixed so every process derives the same one, and it keeps the backend session key "owner", so nothing restarts.
DEFAULT_CONVERSATION_ID = "cosc-" + hashlib.sha256(b"minimoi.cos.owner").hexdigest()[:24]
DEFAULT_TITLE = "CoS"
LEGACY_BACKEND_SESSION = "owner"
MAX_REPLY_CHARS = 40_000
PAGE_LIMIT = 100

_CONVERSATION_ID = re.compile(r"^cosc-[0-9a-f]{12,32}$|^cos-before-1-2$")
_TURN_ID = re.compile(r"^t-[0-9a-f]{12,32}$")
_DEVICE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MONTHLY_LOG = re.compile(r"^[0-9]{4}-[0-9]{2}\.jsonl$")
_TERMINAL = ("answered", "recovered")


class HistoryRefused(Exception):
    """Nothing was written; ``code`` is fixed vocabulary for the caller and the UI."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _now() -> str:
    # Milliseconds, so turns and conversations started within one second still order correctly.
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def turn_id_for(client_id: str | None) -> str:
    """The canonical turn id for a client's id: ``t-<hex>`` as sent, a UUID's hex, or a fresh one when none is sent."""
    value = (client_id or "").strip()
    if _TURN_ID.match(value):
        return value
    if value:
        try:
            return "t-" + UUID(value).hex
        except ValueError:
            pass
    return "t-" + secrets.token_hex(16)


def backend_session(conversation_id: str) -> str:
    """The id the CoS backend keys its session by: "owner" for the first conversation, the conversation's id after."""
    return LEGACY_BACKEND_SESSION if conversation_id == DEFAULT_CONVERSATION_ID else conversation_id


def turn_ref(conversation_id: str, turn_id: str) -> dict:
    return {"type": "cos.turn", "id": f"{conversation_id}:{turn_id}"}


def _fold(rows: list[dict]) -> dict[str, dict]:
    """turn_id -> the turn as it stands (its last line), in the order turns were first written."""
    turns: dict[str, dict] = {}
    for row in rows:
        turns[row["turn_id"]] = row
    return turns


def _bounded_reply(reply: str) -> str:
    if len(reply) <= MAX_REPLY_CHARS:
        return reply
    marker = f"\n\n[Cut here: the full reply was {len(reply):,} characters.]"
    return reply[: MAX_REPLY_CHARS - len(marker)] + marker


class History:
    """One per conversations folder, in the CoS backend process only."""

    def __init__(self, conversations_dir: Path | str, environment: str, *, clock: Callable[[], str] = _now):
        if environment not in ("dev", "staging", "prod"):
            raise ValueError("environment must be dev, staging or prod")
        self.base = Path(conversations_dir)
        self.root = self.base / "history"
        self.environment = environment
        self._clock = clock
        self._thread_lock = threading.Lock()
        self._needs_recovery = True

    # ---- conversations -----------------------------------------------------------------------------------------

    def ensure_default(self) -> dict:
        with self._locked():
            return self._ensure_conversation(DEFAULT_CONVERSATION_ID, DEFAULT_TITLE, private=False)

    def create(self, title: str, *, private: bool = False) -> dict:
        title = " ".join(str(title or "").split())[:120] or "New conversation"
        with self._locked():
            return self._ensure_conversation("cosc-" + secrets.token_hex(12), title, private=bool(private))

    def conversation(self, conversation_id: str) -> dict | None:
        if not _CONVERSATION_ID.match(conversation_id or ""):
            return None
        return fsio.read_doc(self._conversation_path(conversation_id))

    def conversations(self, *, limit: int = 50, cursor: str | None = None) -> dict:
        """Newest activity first. ``cursor`` is the opaque ``next`` of the page before."""
        limit = max(1, min(int(limit), PAGE_LIMIT))
        offset = int(cursor) if cursor and cursor.isdigit() else 0
        rows = []
        for path in sorted((self.root / "conversations").glob("*.json")):
            doc = fsio.read_doc(path)
            if doc is None:
                continue
            turns = _fold(fsio.read_stream(self._turns_path(doc["conversation_id"])))
            last = max((t.get("answered_at") or t["created_at"] for t in turns.values()), default=doc["created_at"])
            rows.append({**doc, "turn_count": len(turns), "last_activity_at": last})
        rows.sort(key=lambda r: (_instant(r["last_activity_at"]), r["conversation_id"]), reverse=True)
        page = rows[offset: offset + limit]
        more = offset + limit < len(rows)
        return {"items": page, "next": str(offset + limit) if more else None}

    # ---- turns -------------------------------------------------------------------------------------------------

    def begin(self, send: dict) -> tuple[dict, bool]:
        """Save Robert's turn durably before anything else happens. Returns (turn, replayed)."""
        if contracts.errors("conversation", "send", send):
            raise HistoryRefused("payload_invalid")
        cid, tid = send["conversation_id"], send["turn_id"]
        with self._locked():
            conversation = fsio.read_doc(self._conversation_path(cid))
            if conversation is None:
                raise HistoryRefused("conversation_not_found")
            if conversation["read_only"]:
                raise HistoryRefused("read_only")
            existing = self._find_locked(tid)
            if existing is not None:
                if existing["conversation_id"] != cid or existing["user_text"] != send["text"]:
                    raise HistoryRefused("turn_id_reused")
                return existing, True
            turns = _fold(fsio.read_stream(self._turns_path(cid)))
            now = self._clock()
            turn = {"schema": "minimoi.cos.turn/1", "conversation_id": cid, "turn_id": tid, "seq": len(turns) + 1,
                    "status": "pending", "user_text": send["text"], "reply_text": None, "created_at": now,
                    "durable_ack_at": now, "answered_at": None, "picture_revision": None,
                    "commitment_receipts": [], "failure_code": None}
            return self._append_turn_locked(turn), False

    def answer(self, conversation_id: str, turn_id: str, reply: str, *, picture_revision: str | None = None,
               commitment_receipts: list[str] | tuple = ()) -> dict:
        """Put the reply on its own turn. A failed turn that is answered after all becomes ``recovered``."""
        reply = _bounded_reply(str(reply or ""))
        with self._locked():
            current = self._turn_locked(conversation_id, turn_id)
            if current["status"] in _TERMINAL:
                if current["reply_text"] == reply:
                    return current
                raise HistoryRefused("transition_not_allowed")
            status = "answered" if current["status"] == "pending" else "recovered"
            turn = dict(current, status=status, reply_text=reply, answered_at=self._clock(),
                        picture_revision=picture_revision if picture_revision is not None else current["picture_revision"],
                        commitment_receipts=list(commitment_receipts) or current["commitment_receipts"])
            if status == "answered":
                turn["failure_code"] = None
            return self._append_turn_locked(turn)

    def fail(self, conversation_id: str, turn_id: str, code: str) -> dict:
        with self._locked():
            current = self._turn_locked(conversation_id, turn_id)
            if current["status"] == "failed":
                return current
            if current["status"] != "pending":
                raise HistoryRefused("transition_not_allowed")
            return self._append_turn_locked(dict(current, status="failed", failure_code=code))

    def turn(self, conversation_id: str, turn_id: str) -> dict | None:
        if not _CONVERSATION_ID.match(conversation_id or ""):
            return None
        return _fold(fsio.read_stream(self._turns_path(conversation_id))).get(turn_id)

    def turns(self, conversation_id: str, *, before_seq: int | None = None, limit: int = 50) -> dict:
        """One page of a conversation, oldest first; ``next_before_seq`` fetches the page before it."""
        if self.conversation(conversation_id) is None:
            raise HistoryRefused("conversation_not_found")
        limit = max(1, min(int(limit), PAGE_LIMIT))
        rows = sorted(_fold(fsio.read_stream(self._turns_path(conversation_id))).values(), key=lambda t: t["seq"])
        if before_seq is not None:
            rows = [t for t in rows if t["seq"] < before_seq]
        page = rows[-limit:]
        more = len(rows) > len(page)
        notes = [n for n in fsio.read_stream(self._notes_path(conversation_id))]
        if page:
            low = page[0]["created_at"] if more else None
            notes = [n for n in notes if low is None or _instant(n["created_at"]) >= _instant(low)]
        return {"conversation_id": conversation_id, "items": page, "notes": notes,
                "next_before_seq": page[0]["seq"] if more else None}

    # ---- proactive notes ---------------------------------------------------------------------------------------

    def add_note(self, note: dict) -> tuple[dict, bool]:
        """A briefing, nudge, incident or consultation note. One per note_id; never into a Private conversation."""
        if contracts.errors("conversation", "stream_note", note):
            raise HistoryRefused("payload_invalid")
        cid = note["conversation_id"]
        with self._locked():
            conversation = fsio.read_doc(self._conversation_path(cid))
            if conversation is None:
                raise HistoryRefused("conversation_not_found")
            if conversation["private"]:
                raise HistoryRefused("private_conversation")
            path = self._notes_path(cid)
            for row in fsio.read_stream(path):
                if row["note_id"] == note["note_id"]:
                    if digest(row) != digest(note):
                        raise HistoryRefused("note_id_reused")
                    return row, True
            self._append_verified(path, note)
            return note, False

    # ---- selection -----------------------------------------------------------------------------------------------

    def select(self, owner: object, device: str | None, conversation_id: str) -> dict:
        owner_key = _owner_key(owner)
        if self.conversation(conversation_id) is None:
            raise HistoryRefused("conversation_not_found")
        record = {"owner": owner_key, "device": _device(device), "conversation_id": conversation_id,
                  "at": self._clock()}
        with self._locked():
            fsio.write_doc(self._selection_path(owner, device), record)
        return record

    def selected(self, owner: object, device: str | None) -> str:
        """The owner's selected conversation on this device, else the first conversation."""
        try:
            record = fsio.read_doc(self._selection_path(owner, device))
        except OSError:
            record = None
        cid = (record or {}).get("conversation_id")
        if cid and self.conversation(cid) is not None:
            return cid
        return self.ensure_default()["conversation_id"]

    # ---- the robert_source verifier (section 4.2) ----------------------------------------------------------------

    def verify_robert_source(self, ref: dict, text: str) -> bool:
        """True only when ``text`` is a verbatim span of Robert's words in the turn ``ref`` names."""
        if not isinstance(ref, dict) or ref.get("type") != "cos.turn" or not isinstance(text, str) or not text.strip():
            return False
        cid, _, tid = str(ref.get("id", "")).partition(":")
        try:
            turn = self.turn(cid, tid)
        except OSError:
            return False
        return turn is not None and text in turn["user_text"]

    # ---- recovery ------------------------------------------------------------------------------------------------

    def recover(self) -> dict:
        with self._locked():
            return dict(self._last_recovery)

    def _recover_locked(self) -> dict:
        counts = {"quarantined": 0, "interrupted": 0}
        fsio.ensure_dir(self.root)
        for folder in ("turns", "notes"):
            for path in sorted((self.root / folder).glob("*.jsonl")):
                counts["quarantined"] += fsio.quarantine_torn_tail(path, self.root)
        for path in sorted((self.root / "turns").glob("*.jsonl")):
            for turn in _fold(fsio.read_stream(path)).values():
                if turn["status"] == "pending":
                    self._append_turn_locked(dict(turn, status="failed", failure_code="interrupted"))
                    counts["interrupted"] += 1
        self._last_recovery = counts
        self._needs_recovery = False
        return counts

    # ---- the legacy import (section 4.19: "Before 1.2") ----------------------------------------------------------

    def import_monthly_logs(self, log_dir: Path | str | None = None) -> dict:
        """Import the monthly recall logs as the read-only "Before 1.2" conversation. Safe to re-run: each line maps
        to one fixed turn id, already-imported turns are skipped, and projection lines of this history are ignored."""
        folder = Path(log_dir) if log_dir else self.base
        lines = []
        for path in sorted(folder.iterdir()):
            if not _MONTHLY_LOG.match(path.name):
                continue
            for row in fsio.read_stream(path):
                if isinstance(row, dict) and not row.get("projection"):
                    lines.append(row)
        lines.sort(key=lambda r: _instant(r.get("at") or ""))
        counts = {"read": len(lines), "imported": 0, "already": 0, "skipped_empty": 0}
        with self._locked():
            created = lines[0]["at"] if lines and _is_timestamp(lines[0].get("at")) else self._clock()
            self._ensure_conversation(BEFORE_1_2, "Before 1.2", private=False, read_only=True, created_at=created)
            path = self._turns_path(BEFORE_1_2)
            turns = _fold(fsio.read_stream(path))
            for row in lines:
                tid = _legacy_turn_id(row)
                if tid in turns:
                    counts["already"] += 1
                    continue
                words, at = str(row.get("robert") or ""), row.get("at")
                if not words.strip() or not _is_timestamp(at):
                    counts["skipped_empty"] += 1
                    continue
                turn = {"schema": "minimoi.cos.turn/1", "conversation_id": BEFORE_1_2, "turn_id": tid,
                        "seq": len(turns) + 1, "status": "answered", "user_text": words[:20_000],
                        "reply_text": _bounded_reply(str(row.get("cos") or "")), "created_at": at,
                        "durable_ack_at": at, "answered_at": at, "picture_revision": None,
                        "commitment_receipts": [], "failure_code": None}
                turns[tid] = self._append_turn_locked(turn)
                counts["imported"] += 1
        return counts

    # ---- internals -----------------------------------------------------------------------------------------------

    @contextmanager
    def _locked(self):
        with self._thread_lock:
            fsio.ensure_dir(self.root)
            fd = os.open(self.root / ".lock", os.O_RDWR | os.O_CREAT, fsio.FILE_MODE)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                if self._needs_recovery:
                    self._recover_locked()
                yield
            finally:
                os.close(fd)

    def _ensure_conversation(self, cid: str, title: str, *, private: bool, read_only: bool = False,
                             created_at: str | None = None) -> dict:
        path = self._conversation_path(cid)
        existing = fsio.read_doc(path)
        if existing is not None:
            return existing
        doc = {"schema": "minimoi.cos.conversation/1", "conversation_id": cid, "title": title, "private": private,
               "created_at": created_at or self._clock(), "read_only": read_only, "data_scope": "personal",
               "environment": self.environment}
        if contracts.errors("conversation", "conversation", doc):
            raise HistoryRefused("payload_invalid")
        fsio.write_doc(path, doc)
        if fsio.read_doc(path) != doc:
            raise OSError("conversation read-back mismatch")
        return doc

    def _turn_locked(self, conversation_id: str, turn_id: str) -> dict:
        if not _CONVERSATION_ID.match(conversation_id or ""):
            raise HistoryRefused("conversation_not_found")
        current = _fold(fsio.read_stream(self._turns_path(conversation_id))).get(turn_id)
        if current is None:
            raise HistoryRefused("turn_not_found")
        return current

    def _find_locked(self, turn_id: str) -> dict | None:
        for path in (self.root / "turns").glob("*.jsonl"):
            found = _fold(fsio.read_stream(path)).get(turn_id)
            if found is not None:
                return found
        return None

    def _append_turn_locked(self, turn: dict) -> dict:
        if contracts.errors("conversation", "turn", turn):
            raise HistoryRefused("payload_invalid")
        self._append_verified(self._turns_path(turn["conversation_id"]), turn)
        return turn

    def _append_verified(self, path: Path, row: dict) -> None:
        fsio.append_line(path, row)
        rows = fsio.read_stream(path)
        if not rows or digest(rows[-1]) != digest(row):
            self._needs_recovery = True
            raise OSError("history read-back mismatch")

    def _conversation_path(self, cid: str) -> Path:
        return self.root / "conversations" / f"{cid}.json"

    def _turns_path(self, cid: str) -> Path:
        return self.root / "turns" / f"{cid}.jsonl"

    def _notes_path(self, cid: str) -> Path:
        return self.root / "notes" / f"{cid}.jsonl"

    def _selection_path(self, owner: object, device: str | None) -> Path:
        return self.root / "selection" / f"{_owner_key(owner)}.{_device(device)}.json"


def _owner_key(owner: object) -> str:
    if owner is None or str(owner) == "":
        raise HistoryRefused("identity_required")
    return hashlib.sha256(str(owner).encode("utf-8")).hexdigest()[:16]


def _device(device: str | None) -> str:
    return device if isinstance(device, str) and _DEVICE.match(device) else "default"


def _legacy_turn_id(row: dict) -> str:
    raw = str(row.get("turn_id") or "")
    try:
        return "t-" + UUID(raw).hex
    except ValueError:
        return "t-" + hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:32]


def _is_timestamp(value: object) -> bool:
    return isinstance(value, str) and not contracts.errors("common", "timestamp", value)


def _instant(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def from_environ(environ: Mapping[str, str]) -> History | None:
    """The history is on only when the conversations folder exists and COS_STORE_ENVIRONMENT names this environment,
    the same switch that turns the CoS store on. Otherwise nothing changes and the recall log is written as before."""
    folder = environ.get("COS_CONVERSATIONS_DIR") or ""
    environment = (environ.get("COS_STORE_ENVIRONMENT") or "").strip()
    if not folder or not os.path.isabs(folder) or not os.path.isdir(folder):
        return None
    if environment not in ("dev", "staging", "prod"):
        return None
    return History(folder, environment)
