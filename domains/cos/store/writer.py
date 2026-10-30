"""The CoS store's only writer: a verified save with a receipt (spec v0.3 section 4.5).

It extends the Guild Build Queue's pattern (domains/guild/queue_store.py). Under one exclusive lock (a sidecar file
in journal/, plus a thread lock), a command goes:

    validate against contracts/cos/v1 (shape, scope, environment, payload digest)
    -> a repeated command_id with the same digest returns the first receipt; a different digest is refused
    -> check expected_revision, and the transition (occurrences and episodes are folded from their events)
    -> append an intent line to the journal
    -> write: a document by temp file + fsync + rename; a stream by one appended, fsynced line
    -> read back and compare the record's digest
    -> append a completed line with the receipt

Only then is the answer "accepted", and only then may the UI say "saved" or CoS say "I'll follow up".

On start, a torn last line in the journal or a stream is moved to journal/quarantine-*.bin, and every intent without a
completed line is resolved: if its record is on disk with the intended digest it completes (recovered), otherwise it
is abandoned. Files are 0640 and folders 0750, so the agent's group can read and nothing but this writer can write.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import secrets
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from domains.cos.store import contracts
from domains.cos.store.canonical import digest
from domains.cos.store.fold import (
    Episode,
    Occurrence,
    apply_episode,
    apply_occurrence,
    episode_refusal,
    occurrence_refusal,
)

FILE_MODE, DIR_MODE = 0o640, 0o750
MAX_COMMAND_BYTES = 256 * 1024

# kind -> (layout, target type, record definition in store-records)
_KINDS = {
    "goal.put": ("doc", "cos.goal", "goal"),
    "plan.put": ("doc", "cos.plan", "plan"),
    "obligation.define": ("doc", "cos.obligation", "obligation_definition"),
    "commitment.open": ("doc", "cos.obligation", "obligation_definition"),
    "meeting.link": ("doc", "cos.meeting", "meeting_link"),
    "obligation.event": ("stream", "cos.occurrence", "obligation_event"),
    "episode.event": ("stream", "cos.episode", "episode_event"),
    "decision.record": ("stream", "cos.decision", "decision"),
    "correction.record": ("stream", "cos.correction", "correction"),
}
_DOC_DIRS = {"cos.goal": "goals", "cos.plan": "plans", "cos.obligation": "obligations/definitions", "cos.meeting": "meetings"}
_STREAMS = {"cos.occurrence": "obligations/events.jsonl", "cos.episode": "obligations/events.jsonl",
            "cos.decision": "decisions.jsonl", "cos.correction": "corrections.jsonl"}
_STREAM_FILES = sorted(set(_STREAMS.values()))
_JOURNAL = "journal/journal.jsonl"
_LOCK = "journal/.lock"


class StoreRefused(Exception):
    """The command was not saved and never will be under this id; ``code`` is fixed vocabulary."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


class _Conflict(Exception):
    def __init__(self, current_revision: int):
        super().__init__("conflict")
        self.current_revision = current_revision


@dataclass
class _Plan:
    layout: str            # "doc" | "stream"
    path: Path
    record: dict
    revision: int          # the document's new revision, or the stream's revision after the append


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_all(fd: int, data: bytes) -> None:
    """A short write is progress, not completion; zero progress is an error. (The Vault's fsio does the same; the
    store keeps its own copy so the CoS images take no dependency on the Vault runtime.)"""
    remaining = memoryview(data)
    while remaining:
        try:
            written = os.write(fd, remaining)
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError(errno.EIO, "write made no progress")
        remaining = remaining[written:]


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _robert_source_spans(node: object) -> Iterator[dict]:
    if isinstance(node, dict):
        if node.get("provenance") == "robert_source" and "text" in node:
            yield node
        for value in node.values():
            yield from _robert_source_spans(value)
    elif isinstance(node, list):
        for value in node:
            yield from _robert_source_spans(value)


class StoreWriter:
    """One per store folder, in the CoS backend process only.

    ``verify_robert_source(turn_ref, text)`` must return True only when ``text`` is a verbatim span of that turn of
    Robert's. Without a verifier, nothing can be stored as ``robert_source``: the server sets that class, never the
    model, so an unverifiable claim is refused rather than trusted.
    """

    def __init__(self, root: Path | str, environment: str, *,
                 verify_robert_source: Callable[[dict, str], bool] | None = None,
                 clock: Callable[[], str] = _now):
        if environment not in ("dev", "staging", "prod"):
            raise ValueError("environment must be dev, staging or prod")
        self.root = Path(root)
        self.environment = environment
        self._verify = verify_robert_source
        self._clock = clock
        self._thread_lock = threading.Lock()
        self._index: dict[str, tuple[str, dict]] = {}
        self._recovered = False
        self._needs_recovery = True

    # ---- public --------------------------------------------------------------------------------------------------

    def submit(self, command: dict) -> dict:
        """Apply one command; always returns a store_result (contracts/cos/v1/store-command.schema.json)."""
        command_id = command.get("command_id") if isinstance(command, dict) else None
        if not isinstance(command_id, str):
            command_id = "unknown"
        with self._thread_lock, self._file_lock():
            try:
                if self._needs_recovery:
                    self._recover_locked()
                return self._submit_locked(command)
            except StoreRefused as exc:
                return {"schema": "minimoi.cos.store_result/1", "command_id": command_id,
                        "outcome": "refused", "code": exc.code}
            except _Conflict as exc:
                return {"schema": "minimoi.cos.store_result/1", "command_id": command_id,
                        "outcome": "conflict", "current_revision": exc.current_revision}
            except OSError:
                self._needs_recovery = True
                return {"schema": "minimoi.cos.store_result/1", "command_id": command_id,
                        "outcome": "failed", "code": "store_unavailable"}

    def recover(self) -> dict:
        """Run recovery now (it also runs before the first command). Returns counts for the operator log."""
        with self._thread_lock, self._file_lock():
            return self._recover_locked()

    def get(self, target_type: str, target_id: str) -> dict | None:
        path = self._doc_path(target_type, target_id)
        return self._read_doc(path)

    def rows(self, stream_type: str) -> list[dict]:
        return self._read_stream(self.root / _STREAMS[stream_type])

    def occurrence(self, occurrence_id: str) -> Occurrence:
        occ = Occurrence(occurrence_id)
        for row in self._read_stream(self.root / _STREAMS["cos.occurrence"]):
            if row.get("schema") == "minimoi.cos.obligation_event/1" and row.get("occurrence_id") == occurrence_id:
                apply_occurrence(occ, row)
        return occ

    def episode(self, episode_id: str) -> Episode:
        ep = Episode(episode_id)
        for row in self._read_stream(self.root / _STREAMS["cos.episode"]):
            if row.get("schema") == "minimoi.cos.episode_event/1" and row.get("episode_id") == episode_id:
                apply_episode(ep, row)
        return ep

    # ---- the command path ---------------------------------------------------------------------------------------

    def _submit_locked(self, command: dict) -> dict:
        if not isinstance(command, dict):
            raise StoreRefused("payload_invalid")
        if len(json.dumps(command, ensure_ascii=False).encode("utf-8")) > MAX_COMMAND_BYTES:
            raise StoreRefused("payload_invalid", "too_large")
        if contracts.errors("store-command", "command", command):
            raise StoreRefused("payload_invalid")
        payload = command["payload"]
        if command["environment"] != self.environment or payload.get("environment") != self.environment:
            raise StoreRefused("wrong_environment")
        if command["data_scope"] != "personal" or payload.get("data_scope") != "personal":
            raise StoreRefused("wrong_scope")
        if digest(payload) != command["payload_digest"]:
            raise StoreRefused("payload_digest_mismatch")

        seen = self._index.get(command["command_id"])
        if seen is not None:
            if seen[0] != command["payload_digest"]:
                raise StoreRefused("command_id_reused")
            return dict(seen[1], replayed=True)

        plan = self._plan(command)
        receipt_id = "rcpt-" + secrets.token_hex(8)
        record_digest = digest(plan.record)
        self._journal({"t": "intent", "command_id": command["command_id"], "payload_digest": command["payload_digest"],
                       "kind": command["kind"], "target": command["target"], "layout": plan.layout,
                       "path": str(plan.path.relative_to(self.root)), "record_digest": record_digest,
                       "receipt_id": receipt_id, "revision": plan.revision, "at": self._clock()})
        if plan.layout == "doc":
            self._write_doc(plan.path, plan.record)
            landed = self._read_doc(plan.path)
            ok = landed is not None and digest(landed) == record_digest
        else:
            self._append_line(plan.path, plan.record)
            rows = self._read_stream(plan.path)
            ok = bool(rows) and digest(rows[-1]) == record_digest
        if not ok:
            self._needs_recovery = True
            return {"schema": "minimoi.cos.store_result/1", "command_id": command["command_id"],
                    "outcome": "failed", "code": "read_back_mismatch"}
        result = {"schema": "minimoi.cos.store_result/1", "command_id": command["command_id"], "outcome": "accepted",
                  "revision": plan.revision, "receipt_id": receipt_id, "replayed": False}
        self._journal({"t": "completed", "command_id": command["command_id"],
                       "payload_digest": command["payload_digest"], "receipt_id": receipt_id,
                       "revision": plan.revision, "at": self._clock()})
        self._index[command["command_id"]] = (command["payload_digest"], result)
        return result

    def _plan(self, command: dict) -> _Plan:
        kind = command["kind"]
        layout, target_type, definition = _KINDS[kind]
        payload = command["payload"]
        target = command["target"]
        if target["type"] != target_type:
            raise StoreRefused("payload_invalid", "target_type")
        if contracts.errors("store-records", definition, payload):
            raise StoreRefused("payload_invalid")
        if command["origin"] == "owner_turn" and not command.get("basis_ref"):
            raise StoreRefused("payload_invalid", "owner_turn_without_basis")
        for span in _robert_source_spans(payload):
            if self._verify is None or not self._verify(span["turn_ref"], span["text"]):
                raise StoreRefused("robert_source_unverified")
        if layout == "doc":
            return self._plan_doc(command, target_type)
        return self._plan_stream(command, target_type)

    def _plan_doc(self, command: dict, target_type: str) -> _Plan:
        payload = command["payload"]
        key = payload["occurrence_id"] if target_type == "cos.meeting" else payload["id"]
        if command["target"]["id"] != key:
            raise StoreRefused("payload_invalid", "target_id")
        path = self._doc_path(target_type, key)
        current = self._read_doc(path)
        current_rev = None if current is None else int(current.get("revision", 0))
        expected = command["expected_revision"]
        if command["kind"] == "commitment.open" and current is not None:
            raise StoreRefused("transition_not_allowed", "commitment_exists")
        if payload.get("must_not_miss") is True and command["origin"] != "owner_turn":
            raise StoreRefused("not_authorized", "must_not_miss_needs_robert")
        if target_type == "cos.meeting":
            # Written once, at the standup's close: ids only, and Rooms owns the record (section 4.13).
            if current is not None:
                raise _Conflict(1)
            if expected is not None:
                raise _Conflict(0)
            return _Plan("doc", path, dict(payload), 1)
        if current_rev is None and expected is not None:
            raise _Conflict(0)
        if current_rev is not None and expected != current_rev:
            raise _Conflict(current_rev)
        revision = (current_rev or 0) + 1
        return _Plan("doc", path, dict(payload, revision=revision), revision)

    def _plan_stream(self, command: dict, target_type: str) -> _Plan:
        payload = command["payload"]
        path = self.root / _STREAMS[target_type]
        rows = self._read_stream(path)
        current = len(rows)
        if command["expected_revision"] != current:
            raise _Conflict(current)
        if target_type in ("cos.occurrence", "cos.episode"):
            if payload["revision_before"] != current:
                raise _Conflict(current)
            if any(r.get("event_id") == payload["event_id"] for r in rows):
                raise StoreRefused("transition_not_allowed", "event_id_reused")
        if target_type == "cos.occurrence":
            occ_id = payload["occurrence_id"]
            if command["target"]["id"] != occ_id:
                raise StoreRefused("payload_invalid", "target_id")
            definition_id = occ_id.split("@", 1)[0]
            definition = self._read_doc(self._doc_path("cos.obligation", definition_id))
            if definition is None:
                raise StoreRefused("transition_not_allowed", "no_definition")
            if definition.get("state") != "active" and payload["type"] not in ("cancelled", "missed", "reopened"):
                raise StoreRefused("transition_not_allowed", "definition_not_active")
            occ = Occurrence(occ_id)
            for row in rows:
                if row.get("schema") == "minimoi.cos.obligation_event/1" and row.get("occurrence_id") == occ_id:
                    apply_occurrence(occ, row)
            code = occurrence_refusal(occ, payload)
            if code:
                raise StoreRefused("transition_not_allowed", code)
        elif target_type == "cos.episode":
            ep_id = payload["episode_id"]
            if command["target"]["id"] != ep_id:
                raise StoreRefused("payload_invalid", "target_id")
            ep = Episode(ep_id)
            for row in rows:
                if row.get("schema") == "minimoi.cos.episode_event/1" and row.get("episode_id") == ep_id:
                    apply_episode(ep, row)
            code = episode_refusal(ep, payload)
            if code:
                raise StoreRefused("transition_not_allowed", code)
        else:
            if command["target"]["id"] != payload["id"]:
                raise StoreRefused("payload_invalid", "target_id")
            if any(r.get("id") == payload["id"] for r in rows):
                raise StoreRefused("transition_not_allowed", "id_reused")
        return _Plan("stream", path, dict(payload), current + 1)

    # ---- recovery ------------------------------------------------------------------------------------------------

    def _recover_locked(self) -> dict:
        counts = {"quarantined": 0, "recovered": 0, "abandoned": 0}
        self._ensure_dir(self.root / "journal")
        for rel in [_JOURNAL] + _STREAM_FILES:
            counts["quarantined"] += self._quarantine_torn_tail(self.root / rel)
        index, pending = {}, {}
        for row in self._read_stream(self.root / _JOURNAL):
            cid = row.get("command_id")
            if row.get("t") == "intent":
                pending[cid] = row
            elif row.get("t") == "completed":
                pending.pop(cid, None)
                index[cid] = (row["payload_digest"], {
                    "schema": "minimoi.cos.store_result/1", "command_id": cid, "outcome": "accepted",
                    "revision": row["revision"], "receipt_id": row["receipt_id"], "replayed": False})
            elif row.get("t") == "abandoned":
                pending.pop(cid, None)
        for cid, intent in pending.items():
            path = self.root / intent["path"]
            if intent["layout"] == "doc":
                landed = self._read_doc(path)
                done = landed is not None and digest(landed) == intent["record_digest"]
            else:
                done = any(digest(r) == intent["record_digest"] for r in self._read_stream(path))
            if done:
                self._journal({"t": "completed", "command_id": cid, "payload_digest": intent["payload_digest"],
                               "receipt_id": intent["receipt_id"], "revision": intent["revision"],
                               "recovered": True, "at": self._clock()})
                index[cid] = (intent["payload_digest"], {
                    "schema": "minimoi.cos.store_result/1", "command_id": cid, "outcome": "accepted",
                    "revision": intent["revision"], "receipt_id": intent["receipt_id"], "replayed": False})
                counts["recovered"] += 1
            else:
                self._journal({"t": "abandoned", "command_id": cid, "at": self._clock()})
                counts["abandoned"] += 1
        self._index = index
        self._recovered = True
        self._needs_recovery = False
        return counts

    def _quarantine_torn_tail(self, path: Path) -> int:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return 0
        if not data or data.endswith(b"\n"):
            return 0
        cut = data.rfind(b"\n") + 1
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        quarantine = self.root / "journal" / f"quarantine-{stamp}-{path.name}.bin"
        self._write_bytes(quarantine, data[cut:])
        fd = os.open(path, os.O_RDWR)
        try:
            os.ftruncate(fd, cut)
            os.fsync(fd)
        finally:
            os.close(fd)
        return 1

    # ---- files ---------------------------------------------------------------------------------------------------

    def _doc_path(self, target_type: str, key: str) -> Path:
        if "/" in key or key.startswith("."):
            raise StoreRefused("payload_invalid", "target_id")
        suffix = ".link.json" if target_type == "cos.meeting" else ".json"
        return self.root / _DOC_DIRS[target_type] / f"{key}{suffix}"

    @contextmanager
    def _file_lock(self):
        self._ensure_dir(self.root / "journal")
        fd = os.open(self.root / _LOCK, os.O_RDWR | os.O_CREAT, FILE_MODE)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _ensure_dir(self, folder: Path) -> None:
        missing, probe = [], folder
        while not probe.exists() and probe != probe.parent:
            missing.append(probe)
            probe = probe.parent
        for item in reversed(missing):
            try:
                os.mkdir(item, DIR_MODE)
            except FileExistsError:
                pass
            os.chmod(item, DIR_MODE)

    def _write_bytes(self, path: Path, data: bytes) -> None:
        self._ensure_dir(path.parent)
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
        tmp = Path(name)
        try:
            try:
                write_all(fd, data)
                os.fchmod(fd, FILE_MODE)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        fsync_dir(path.parent)

    def _write_doc(self, path: Path, record: dict) -> None:
        self._write_bytes(path, (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8"))

    def _append_line(self, path: Path, row: dict) -> None:
        self._ensure_dir(path.parent)
        data = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), FILE_MODE)
        try:
            size = os.fstat(fd).st_size
            try:
                write_all(fd, data)
                os.fsync(fd)
            except OSError:
                os.ftruncate(fd, size)     # never leave our own partial line behind
                os.fsync(fd)
                raise
        finally:
            os.close(fd)
        fsync_dir(path.parent)

    def _journal(self, row: dict) -> None:
        self._append_line(self.root / _JOURNAL, row)

    @staticmethod
    def _read_doc(path: Path) -> dict | None:
        try:
            doc = json.loads(path.read_text("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise OSError("store document unreadable") from exc
        return doc if isinstance(doc, dict) else None

    @staticmethod
    def _read_stream(path: Path) -> list[dict]:
        rows = []
        try:
            with open(path, "rb") as handle:
                for line in handle:
                    if not line.endswith(b"\n"):
                        break                       # a torn tail is never read as a record
                    rows.append(json.loads(line))
        except FileNotFoundError:
            return []
        return rows
