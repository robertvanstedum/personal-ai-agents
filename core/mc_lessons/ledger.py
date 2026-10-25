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
EVENT_TYPES = ("proposed", "decided", "revoked",
               # MC first light M1.3 (spec v1.2 §10.2): owner preferences kept from MC's tool, with provenance
               "preference_kept", "preference_revoked", "suggestion_recorded")


class LedgerDamaged(Exception):
    """The ledger failed an integrity check; nothing may be read from or appended to it."""


@dataclass
class State:
    lessons: dict = field(default_factory=dict)        # no -> {text, sha256, adopted_at, revoked_at, revision, ...}
    operations: dict = field(default_factory=dict)     # operation_id -> the preference event it wrote (idempotency)
    suggestions: dict = field(default_factory=dict)    # suggestion_id -> pending/adopted clarification record
    proposals: dict = field(default_factory=dict)      # proposal_id -> event + {"decision": ...}
    turns: dict = field(default_factory=dict)          # turn_key -> the event that turn wrote
    next_no: int = 1
    head: str = GENESIS
    count: int = 0
    numbered: bool = False        # events carry their line number ("n"): written since anchors existed

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
        self.anchor_path = self.dir / "lessons.head"

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
            if "n" in ev:
                if ev["n"] != st.count + 1:
                    raise LedgerDamaged("chain_broken")
                st.numbered = True
            st.head = _sha(line)
            st.count += 1
            _apply(st, ev)
        return st

    # ── the anchor: the acknowledged line count and head, so a lost LAST line is detected too ──
    def _anchor(self):
        try:
            fd = os.open(self.anchor_path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as f:
            raw = f.read(512)
        try:
            doc = json.loads(raw)
            count, head = doc["count"], doc["head"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LedgerDamaged("anchor_unreadable") from exc
        if type(count) is not int or count < 0 or not isinstance(head, str) or len(head) != 64:
            raise LedgerDamaged("anchor_unreadable")
        return count, head

    def _write_anchor(self, st: State) -> None:
        tmp = self.dir / "lessons.head.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            _write_all(fd, json.dumps({"count": st.count, "head": st.head}).encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self.anchor_path)
        _fsync_dir(self.dir)

    def _verified(self, data: bytes):
        """(state, bytes to keep). Complete lines must match the anchor exactly; one extra complete line is an
        append whose acknowledgement (the anchor) never happened, so it is dropped. Anything else holds the ledger.
        A ledger written before anchors existed (no anchor yet) is trusted once and anchored at its next write."""
        if data and not data.endswith(b"\n"):
            data = data[: data.rfind(b"\n") + 1]            # an unfinished append was never acknowledged
        anchor = self._anchor()
        if anchor is None:
            st = self._fold(data)
            if st.numbered:                               # anchored once: a missing anchor is damage, never legacy
                raise LedgerDamaged("anchor_missing")
            return st, len(data)
        count, head = anchor
        lines = data.split(b"\n")[:-1] if data else []
        if len(lines) == count + 1:
            # Only a genuine unacknowledged append is dropped: a valid event chained to the anchored head.
            try:
                extra = json.loads(lines[-1])
            except ValueError as exc:
                raise LedgerDamaged("line_unreadable") from exc
            if (not isinstance(extra, dict) or extra.get("type") not in EVENT_TYPES
                    or extra.get("prev") != (head if count else GENESIS) or extra.get("n") != count + 1):
                raise LedgerDamaged("chain_broken")
            data = data[: len(data) - len(lines[-1]) - 1]
        st = self._fold(data)
        if st.count != count or st.head != (head if count else GENESIS):
            raise LedgerDamaged("anchor_mismatch")
        return st, len(data)

    def read(self) -> State:
        with self._locked(exclusive=False):
            return self._verified(self._raw())[0]

    # ── writing ──
    @contextmanager
    def writing(self):
        """Exclusive section: yields (state, append). ``append(event)`` writes durably and returns the new state."""
        with self._locked(exclusive=True):
            data = self._raw()
            st, keep = self._verified(data)
            if keep != len(data):                           # cut what was never acknowledged
                with open(self.path, "r+b") as f:
                    f.truncate(keep)
                    f.flush()
                    os.fsync(f.fileno())
            if data and self._anchor() is None:
                self._write_anchor(st)                       # anchor a ledger from before anchors existed
            box = {"state": st}

            def append(event: dict) -> State:
                if event.get("type") not in EVENT_TYPES:
                    raise ValueError("event_type_invalid")
                ev = {**event, "prev": box["state"].head, "n": box["state"].count + 1, "at": event.get("at") or _now()}
                line = json.dumps(ev, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(line) > MAX_LINE:
                    raise ValueError("event_too_large")
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                try:
                    _write_all(fd, line + b"\n")
                    os.fsync(fd)
                finally:
                    os.close(fd)
                _fsync_dir(self.dir)
                box["state"].head = _sha(line)
                box["state"].count += 1
                _apply(box["state"], ev)
                self._write_anchor(box["state"])              # the acknowledgement point
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
            st.lessons[no] = {"text": p["text"], "sha256": ev["sha256"], "adopted_at": ev["at"], "revoked_at": None,
                              "revision": ev.get("n"), "kind": "lesson"}
            st.next_no = no + 1
    elif kind == "revoked":
        lesson = st.lessons.get(ev["lesson_no"])
        if lesson is None or lesson["revoked_at"] is not None:
            raise LedgerDamaged("revoke_inconsistent")
        lesson["revoked_at"] = ev["at"]
    elif kind == "preference_kept":
        no = ev["lesson_no"]
        if no != st.next_no or text_sha(ev["text"]) != ev["sha256"] or ev["operation_id"] in st.operations:
            raise LedgerDamaged("adoption_inconsistent")
        replaced = ev.get("supersedes")
        if replaced is not None:
            old = st.lessons.get(replaced)
            if old is None or old["revoked_at"] is not None:
                raise LedgerDamaged("supersede_inconsistent")
            old["revoked_at"], old["superseded_by"] = ev["at"], no
        suggestion = ev.get("from_suggestion")
        if suggestion is not None:
            pending = st.suggestions.get(suggestion)
            if pending is None or pending["state"] != "pending":
                raise LedgerDamaged("suggestion_inconsistent")
            pending["state"], pending["lesson_no"] = "adopted", no
        st.lessons[no] = {"text": ev["text"], "sha256": ev["sha256"], "adopted_at": ev["at"], "revoked_at": None,
                          "revision": ev.get("n"), "kind": "preference", "original": ev["original"],
                          "source": ev["source"], "operation_id": ev["operation_id"], "supersedes": replaced}
        st.next_no = no + 1
        st.operations[ev["operation_id"]] = ev
    elif kind == "preference_revoked":
        lesson = st.lessons.get(ev["lesson_no"])
        if (lesson is None or lesson["revoked_at"] is not None or lesson.get("revision") != ev["expected_revision"]
                or ev["operation_id"] in st.operations):
            raise LedgerDamaged("revoke_inconsistent")
        lesson["revoked_at"], lesson["revoked_by"] = ev["at"], ev["operation_id"]
        st.operations[ev["operation_id"]] = ev
    elif kind == "suggestion_recorded":
        if ev["suggestion_id"] in st.suggestions or ev["operation_id"] in st.operations:
            raise LedgerDamaged("suggestion_inconsistent")
        st.suggestions[ev["suggestion_id"]] = {**ev, "state": "pending"}
        st.operations[ev["operation_id"]] = ev
    if ev.get("turn_key"):
        st.turns[ev["turn_key"]] = ev


def _write_all(fd, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _fsync_dir(path) -> None:
    dfd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
