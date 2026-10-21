"""Append-only, hash-chained lesson ledger on disk (file first; one file per owner and environment).

Every line is one JSON event carrying the SHA-256 of the line before it, so an edited, reordered or
missing line is detected rather than believed. Writers hold an exclusive lock for read-fold-append and
fsync the file and its directory before returning. A final line without its newline is an unfinished
append: it is cut off (under the lock) and was never acknowledged. Damage anywhere else holds the
ledger: reads raise ``LedgerDamaged`` and nothing is appended, so the caller reports "unknown".

The state is a fold of the events: adopted lessons (numbered L1, L2, … and never reused), revoked
lessons and proposals with their decisions. Only ``core.mc_lessons.commands`` writes here, from an
authenticated owner turn; no model output reaches it.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64
MAX_LINE = 4096
MAX_FILE = 4 * 1024 * 1024
EVENT_TYPES = ("proposed", "decided", "revoked")


class LedgerDamaged(Exception):
    """The ledger failed an integrity check; nothing may be read from or appended to it."""


@dataclass
class State:
    lessons: dict = field(default_factory=dict)        # no -> {text, sha256, adopted_at, revoked_at}
    proposals: dict = field(default_factory=dict)      # proposal_id -> event + {"decision": ...}
    turns: dict = field(default_factory=dict)          # turn_key -> the event that turn wrote
    next_no: int = 1
    head: str = GENESIS

    def active(self):
        return [(no, l) for no, l in sorted(self.lessons.items()) if l["revoked_at"] is None]


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def text_sha(text: str) -> str:
    return _sha(text.encode("utf-8"))


class LessonLedger:
    def __init__(self, root, *, principal: str, environment: str):
        root = Path(root)
        if not root.is_absolute() or environment not in ("dev", "prod") or not principal:
            raise ValueError("ledger_config_invalid")
        self.dir = root / environment / _sha(principal.encode())[:16]
        self.path, self.lock_path = self.dir / "lessons.jsonl", self.dir / "lessons.lock"

    # ── locking and reading ──
    @contextmanager
    def _locked(self, exclusive: bool):
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _raw(self) -> bytes:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return b""
        with os.fdopen(fd, "rb") as f:
            data = f.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise LedgerDamaged("ledger_too_large")
        return data

    def _fold(self, data: bytes) -> State:
        st = State()
        for line in data.split(b"\n")[:-1] if data else []:
            if len(line) > MAX_LINE:
                raise LedgerDamaged("line_too_long")
            try:
                ev = json.loads(line)
            except ValueError as exc:
                raise LedgerDamaged("line_unreadable") from exc
            if not isinstance(ev, dict) or ev.get("prev") != st.head or ev.get("type") not in EVENT_TYPES:
                raise LedgerDamaged("chain_broken")
            st.head = _sha(line)
            _apply(st, ev)
        return st

    def read(self) -> State:
        with self._locked(exclusive=False):
            data = self._raw()
            if data and not data.endswith(b"\n"):
                data = data[: data.rfind(b"\n") + 1]        # an unfinished append was never acknowledged
            return self._fold(data)

    # ── writing ──
    @contextmanager
    def writing(self):
        """Exclusive section: yields (state, append). ``append(event)`` writes durably and returns the new state."""
        with self._locked(exclusive=True):
            data = self._raw()
            if data and not data.endswith(b"\n"):
                keep = data.rfind(b"\n") + 1
                with open(self.path, "r+b") as f:
                    f.truncate(keep)
                    f.flush()
                    os.fsync(f.fileno())
                data = data[:keep]
            box = {"state": self._fold(data)}

            def append(event: dict) -> State:
                if event.get("type") not in EVENT_TYPES:
                    raise ValueError("event_type_invalid")
                ev = {**event, "prev": box["state"].head, "at": event.get("at") or _now()}
                line = json.dumps(ev, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(line) > MAX_LINE:
                    raise ValueError("event_too_large")
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                try:
                    os.write(fd, line + b"\n")
                    os.fsync(fd)
                finally:
                    os.close(fd)
                dfd = os.open(self.dir, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
                box["state"].head = _sha(line)
                _apply(box["state"], ev)
                return box["state"]
            yield box["state"], append


def _apply(st: State, ev: dict) -> None:
    kind = ev["type"]
    if kind == "proposed":
        st.proposals[ev["proposal_id"]] = {**ev, "decision": None}
    elif kind == "decided":
        p = st.proposals.get(ev["proposal_id"])
        if p is None or p["decision"] is not None:
            raise LedgerDamaged("decision_without_open_proposal")
        p["decision"] = ev["outcome"]
        if ev["outcome"] == "adopted":
            no = ev["lesson_no"]
            if no != st.next_no or text_sha(p["text"]) != ev["sha256"]:
                raise LedgerDamaged("adoption_inconsistent")
            st.lessons[no] = {"text": p["text"], "sha256": ev["sha256"], "adopted_at": ev["at"], "revoked_at": None}
            st.next_no = no + 1
    elif kind == "revoked":
        lesson = st.lessons.get(ev["lesson_no"])
        if lesson is None or lesson["revoked_at"] is not None:
            raise LedgerDamaged("revoke_inconsistent")
        lesson["revoked_at"] = ev["at"]
    if ev.get("turn_key"):
        st.turns[ev["turn_key"]] = ev


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
