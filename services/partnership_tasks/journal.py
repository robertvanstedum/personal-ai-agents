"""The task service's single-writer journal (spec v1.1 A01, D05, E01, F01, F07, Q06).

It keeps the CoS store's pattern (domains/cos/store/writer.py) and the same file primitives (core/durable_files.py),
with three differences that the spec requires:

1. **Replay is the same in every writer process (B0-1).** The command index is read from the journal under the
   writer lock before every command and every receipt lookup, from the byte offset this process last read (a full
   rescan if the file shrank or was replaced). A second process therefore answers a repeated command with the first
   receipt, and refuses a reused id with another payload, exactly as the first process does.
2. **Several documents change under one intent, recovered forward.** The intent line carries each target path, the
   digest it must have before the write, and the record to write. Every intent left without a completed line is
   finished by whichever process next takes the lock: a document already at its intended digest stays, one still at
   its before-digest is written, and one at any other digest means someone wrote outside the journal, which holds
   the store as damaged. So a crash at any point of a promotion ends in the same request and the same task, never a
   second task and never a half-linked pair (E01).
3. **The journal is hash-chained.** Each line carries the digest of the line before it and of itself. A reader that
   finds a broken chain holds the store: nothing is written and every receipt answer is ``unknown``. The chain head
   can be exported somewhere the writer cannot rewrite; ``verify_head`` then proves the journal still reproduces it
   (Q06: detects edits, deletions and reordering up to the last exported head; a privileged rewrite after it is not
   detectable from these files alone).

The journal is the evidence record (Q01): the completed line carries the actor seat and origin from the caller's
authenticated binding, session and runtime identity, causal parent, basis, environment, scope and both times.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core import durable_files as fsio
from core.canonical_json import digest

GENESIS = "sha256:" + "0" * 64
JOURNAL = "journal/journal.jsonl"
LOCK = "journal/.lock"
_PATH = re.compile(r"^[a-z_]+/[A-Za-z0-9_-]+\.json$")


class JournalDamaged(Exception):
    """The journal or a document it covers failed an integrity check; the store is held."""


class Refused(Exception):
    """Not saved now. Refusals and conflicts are not journalled: the same id may be accepted later once its
    preconditions hold (E03 allows a same-id retry of a definitely-absent operation). ``code`` is fixed vocabulary."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code, self.detail = code, detail


class Conflict(Exception):
    def __init__(self, current_revision: int | None):
        super().__init__("conflict")
        self.current_revision = current_revision


@dataclass
class Write:
    path: str                  # relative to the store root
    record: dict


@dataclass
class Plan:
    """What one accepted command writes. ``revision`` is the target's new revision; ``evidence`` is merged into the
    completed line (actor, origin, session, runtime, causal parent, basis, scope, times, targets)."""
    writes: list[Write]
    revision: int
    kind: str
    evidence: dict = field(default_factory=dict)


def _hashed(row: dict, prev: str) -> dict:
    row = dict(row, prev_hash=prev)
    row["hash"] = digest(row)
    return row


class Journal:
    """One per store folder. Safe for several threads and several processes on one host (flock + thread lock)."""

    def __init__(self, root: Path | str, *, result_schema: str, clock: Callable[[], str],
                 doc_dirs: tuple[str, ...] = ()):
        self.root = Path(root)
        self._doc_dirs = tuple(doc_dirs)      # the folders whose documents this journal accounts for (Q06 scan)
        self._schema = result_schema
        self._clock = clock
        self._thread_lock = threading.Lock()
        self._reset_view()

    def _reset_view(self) -> None:
        self._offset, self._ino, self._seq, self._last = 0, None, 0, GENESIS
        self._index: dict[str, tuple[str, dict]] = {}
        self._pending: dict[str, dict] = {}
        self._intents: dict[str, dict] = {}
        self._docs: dict[str, str] = {}          # path -> digest of its last journalled record (Q06 document check)
        self._damaged: str | None = None
        self._verified = False

    # ---- public --------------------------------------------------------------------------------------------------

    def run(self, command_id: str, payload_digest: str, plan: Callable[[], Plan]) -> dict:
        """Apply one command under the lock: replay, refusal, conflict, or a new accepted receipt."""
        with self._locked():
            try:
                self._refresh()
            except (JournalDamaged, OSError):
                return self._result(command_id, "failed", code="store_unavailable")
            seen = self._index.get(command_id)
            if seen is not None:
                if seen[0] != payload_digest:
                    return self._result(command_id, "refused", code="command_id_reused")
                return dict(seen[1], replayed=True)
            try:
                p = plan()
            except JournalDamaged:
                return self._result(command_id, "failed", code="store_unavailable")
            except Refused as exc:
                return self._result(command_id, "refused", code=exc.code, **({"detail": exc.detail} if exc.detail else {}))
            except Conflict as exc:
                return self._result(command_id, "conflict", current_revision=exc.current_revision)
            try:
                return self._apply(command_id, payload_digest, p)
            except JournalDamaged:
                return self._result(command_id, "failed", code="store_unavailable")       # stays held
            except OSError:
                # The intent may be on disk: the next process to take the lock finishes it forward, and a receipt
                # lookup for this id answers from the journal. Never reported as saved here.
                self._reset_view()
                return self._result(command_id, "failed", code="store_unavailable")

    def receipt(self, command_id: str, payload_digest: str | None = None) -> dict:
        """recorded / different / absent / unknown, read from the journal under the lock (never from memory alone)."""
        with self._locked():
            try:
                self._refresh()
            except (JournalDamaged, OSError):
                return {"schema": "minimoi.partnership.receipt_lookup/1", "command_id": command_id,
                        "answer": "unknown", "result": None}
            seen = self._index.get(command_id)
            if seen is None:
                answer, result = "absent", None
            elif payload_digest is not None and seen[0] != payload_digest:
                answer, result = "different", None
            else:
                answer, result = "recorded", seen[1]
            return {"schema": "minimoi.partnership.receipt_lookup/1", "command_id": command_id,
                    "answer": answer, "result": result}

    def recover(self) -> dict:
        """Rescan the journal from the start, finish any intent a dead writer left and verify every document against
        it. Also the operator's way back after a repair: a held store stays held until this or a restart."""
        with self._locked():
            self._reset_view()
            return self._refresh()

    def head(self) -> dict:
        with self._locked():
            self._refresh()
            return {"seq": self._seq, "hash": self._last}

    def verify_head(self, head: dict) -> bool:
        """True when the journal still contains the exported line ``head['seq']`` with ``head['hash']``."""
        with self._locked():
            self._refresh()
            if head["seq"] == 0:
                return head["hash"] == GENESIS
            for row in fsio.read_stream(self.root / JOURNAL):
                if row.get("seq") == head["seq"]:
                    return row.get("hash") == head["hash"]
            return False

    def entries(self) -> list[dict]:
        """Every journal line, chain-verified (raises JournalDamaged)."""
        with self._locked():
            self._refresh()
            rows = fsio.read_stream(self.root / JOURNAL)
        return rows

    def read(self, rel: str) -> dict | None:
        """One document as it is on disk now. Only for use inside ``run``'s plan or ``view``: there the lock is held and
        every intent is finished, so no half of a multi-document operation is ever seen (E01). Every read is checked
        against the journal (Q06): a document that is not exactly what the journal last wrote holds the store."""
        doc = fsio.read_doc(self.root / rel)
        have = None if doc is None else digest(doc)
        if have != self._docs.get(rel):
            self._damaged = f"{rel} does not match the journal"
            raise JournalDamaged(self._damaged)
        return doc

    def entries_unlocked(self) -> list[dict]:
        """The journal lines, for use inside ``view`` only (the lock is held and the chain was just verified)."""
        return fsio.read_stream(self.root / JOURNAL)

    def view(self, fn: Callable[[], object]):
        """Run a read under the lock after finishing any intent a dead writer left, so a reader never sees half of a
        multi-document operation (E01). Raises JournalDamaged when the store is held."""
        with self._locked():
            self._refresh()
            return fn()

    # ---- under the lock -----------------------------------------------------------------------------------------

    @contextmanager
    def _locked(self):
        with self._thread_lock:
            fsio.ensure_dir(self.root / "journal")
            fd = os.open(self.root / LOCK, os.O_RDWR | os.O_CREAT, fsio.FILE_MODE)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    def _refresh(self) -> dict:
        """Bring this process's view up to the journal on disk, then finish any intent a dead writer left."""
        counts = {"quarantined": 0, "recovered": 0}
        path = self.root / JOURNAL
        if self._damaged:
            raise JournalDamaged(self._damaged)
        if _last_byte(path) not in (None, b"\n"):
            counts["quarantined"] = fsio.quarantine_torn_tail(path, self.root / "journal")
        try:
            st = os.stat(path)
        except FileNotFoundError:
            if self._offset:
                self._damaged = "journal disappeared"
                raise JournalDamaged(self._damaged)
            self._verify_documents()
            return counts
        if st.st_ino != self._ino or st.st_size < self._offset:
            self._reset_view()
            self._ino = st.st_ino
        with open(path, "rb") as fh:
            fh.seek(self._offset)
            data = fh.read()
        consumed = 0
        for line in data.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                break
            consumed += len(line)
            try:
                row = json.loads(line)
            except ValueError:
                self._damaged = f"unreadable line after seq {self._seq}"
                raise JournalDamaged(self._damaged)
            self._fold(row)
        self._offset += consumed
        for cid in list(self._pending):
            self._finish(self._pending[cid])
            counts["recovered"] += 1
        if not self._verified:
            self._verify_documents()
        return counts

    def _verify_documents(self) -> None:
        """Q06: every document in the store must be exactly what the journal last wrote there, and every journalled
        document must exist. Run on each process's first view and on ``recover``. Catches an edit after completion,
        a document with no journal record (for instance after the journal's last lines were cut off) and a missing
        journal beside existing documents."""
        on_disk = {}
        for path in (p for d in self._doc_dirs for p in (self.root / d).glob("*.json")):
            if path.name.startswith("."):
                continue
            rel = f"{path.parent.name}/{path.name}"
            doc = fsio.read_doc(path)
            on_disk[rel] = None if doc is None else digest(doc)
        for rel, have in on_disk.items():
            if self._docs.get(rel) != have:
                self._damaged = f"{rel} does not match the journal"
                raise JournalDamaged(self._damaged)
        for rel in self._docs:
            if rel not in on_disk:
                self._damaged = f"{rel} is journalled but missing"
                raise JournalDamaged(self._damaged)
        self._verified = True

    def _fold(self, row: dict) -> None:
        body = {k: v for k, v in row.items() if k != "hash"}
        if row.get("seq") != self._seq + 1 or row.get("prev_hash") != self._last or digest(body) != row.get("hash"):
            self._damaged = f"chain broken at seq {self._seq + 1}"
            raise JournalDamaged(self._damaged)
        self._seq, self._last = row["seq"], row["hash"]
        cid, kind = row.get("command_id"), row.get("t")
        if kind == "intent":
            self._pending[cid] = row
            self._intents[cid] = row
        elif kind == "completed":
            self._pending.pop(cid, None)
            for w in self._intents.pop(cid, {"writes": []})["writes"]:
                self._docs[w["path"]] = digest(w["record"])
            self._index[cid] = (row["payload_digest"], self._accepted(cid, row["revision"], row["receipt_id"]))
        elif kind == "abandoned":
            self._pending.pop(cid, None)
        else:
            self._damaged = f"unknown line type at seq {self._seq}"
            raise JournalDamaged(self._damaged)

    def _append(self, row: dict) -> dict:
        row = _hashed(dict(row, seq=self._seq + 1), self._last)
        fsio.append_line(self.root / JOURNAL, row)
        st = os.stat(self.root / JOURNAL)
        self._ino = st.st_ino
        self._offset = st.st_size
        self._seq, self._last = row["seq"], row["hash"]
        return row

    def _apply(self, command_id: str, payload_digest: str, plan: Plan) -> dict:
        paths = [w.path for w in plan.writes]
        if (not paths or len(set(paths)) != len(paths) or not all(_PATH.match(p) for p in paths)
                or (self._doc_dirs and not all(p.split("/")[0] in self._doc_dirs for p in paths))):
            raise ValueError("plan paths must be unique store-relative document paths")
        writes = []
        for w in plan.writes:
            current = fsio.read_doc(self.root / w.path)
            before = None if current is None else digest(current)
            if before != self._docs.get(w.path):
                self._damaged = f"{w.path} changed outside the journal"
                raise JournalDamaged(self._damaged)
            writes.append({"path": w.path, "before": before, "record": w.record})
        receipt_id = "rcpt-" + secrets.token_hex(8)
        intent = self._append({"t": "intent", "command_id": command_id, "payload_digest": payload_digest,
                               "kind": plan.kind, "writes": writes, "evidence": plan.evidence,
                               "receipt_id": receipt_id, "revision": plan.revision, "recorded_at": self._clock()})
        self._pending[command_id] = intent
        self._intents[command_id] = intent
        return self._finish(intent, recovered=False)

    def _finish(self, intent: dict, *, recovered: bool = True) -> dict:
        """Roll one intent forward to completion, verifying every document by read-back."""
        for w in intent["writes"]:
            path = self.root / w["path"]
            want = digest(w["record"])
            current = fsio.read_doc(path)
            have = None if current is None else digest(current)
            if have == want:
                continue
            if have != w["before"]:
                self._damaged = f"{w['path']} changed outside the journal (intent seq {intent['seq']})"
                raise JournalDamaged(self._damaged)
            fsio.write_doc(path, w["record"])
            landed = fsio.read_doc(path)
            if landed is None or digest(landed) != want:
                raise OSError("read-back mismatch")
        self._intents.pop(intent["command_id"], None)
        for w in intent["writes"]:
            self._docs[w["path"]] = digest(w["record"])
        self._append(dict(intent.get("evidence") or {}, t="completed", command_id=intent["command_id"],
                                      payload_digest=intent["payload_digest"], kind=intent["kind"],
                                      receipt_id=intent["receipt_id"], revision=intent["revision"],
                                      recovered=recovered, recorded_at=self._clock()))
        self._pending.pop(intent["command_id"], None)
        result = self._accepted(intent["command_id"], intent["revision"], intent["receipt_id"])
        self._index[intent["command_id"]] = (intent["payload_digest"], result)
        return result

    def _accepted(self, command_id: str, revision: int, receipt_id: str) -> dict:
        return self._result(command_id, "accepted", revision=revision, receipt_id=receipt_id, replayed=False)

    def _result(self, command_id: str, outcome: str, **extra) -> dict:
        return dict({"schema": self._schema, "command_id": command_id, "outcome": outcome}, **extra)


def _last_byte(path: Path) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                return None
            fh.seek(-1, os.SEEK_END)
            return fh.read(1)
    except FileNotFoundError:
        return None
