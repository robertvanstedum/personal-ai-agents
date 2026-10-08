"""The journal engine: one authoritative ``events.jsonl`` per workshop stream, appended and read only through this module.

Contract: v0.6 sections 4 to 6 as amended for Unit 1 by v0.7 (the precedence note ``workshop-build-unit1-precedence-20261008``).
In short: a caller prepares an intent (the ID and occurrence time are fixed once, in a durable *prepare receipt*); an append
takes the exclusive lock, checks and if needed recovers the tail, answers a repeated ID from the journal, assigns the next
``seq``, writes the whole line, makes it durable, and only then refreshes the derived ``state.json``. A torn tail is completed
only when it matches a prepare receipt; anything else is preserved in ``quarantine/`` before it is cut away. Interior damage
holds all writes. Reads never create, repair or lock out a writer.

No model, network or HTTP import anywhere in this package (a test scans for them).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterator

from core.workshop_journal import artifacts as artifact_store, fsutil, reducer, schema, strictjson
from core.workshop_journal.errors import (CommitUnknown, Corrupt, IdConflict, InvalidInput, JournalError, LegacyAfterV2,
                                          LockBusy, Missing, RecoveryBlocked, SourceRefused, UnsafeRoot, WriteFailed)
from core.workshop_journal.errors import ArtifactMissing

JOURNAL, LOCK, STATE = "events.jsonl", ".lock", "state.json"
PREPARED, QUARANTINE = "prepared", "quarantine"
MAX_PROBLEMS = 50
DEEP_TAIL = 25                      # an append deep-checks the newest rows; verify deep-checks all of them


# ── scanning ───────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Scan:
    events: list[dict] = field(default_factory=list)       # the valid prefix: legacy rows, then v2 rows in ``seq`` order
    ids: dict[str, dict] = field(default_factory=dict)     # event_id -> {"seq", "intent_hash", "legacy"}
    legacy: int = 0
    v2: int = 0
    last_seq: int = 0
    complete_bytes: int = 0                                 # offset just after the last LF
    valid_bytes: int = 0                                    # where the valid prefix ends
    tail: bytes = b""
    problems: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    after_gap: int = 0
    offsets: list[int] = field(default_factory=list)       # byte offset of each entry in ``events``

    @property
    def next_seq(self) -> int:
        return self.last_seq + 1

    def add_committed(self, row: dict, line_bytes: int) -> None:
        """Account for one event this process just wrote, without rescanning the whole file."""
        self.offsets.append(self.complete_bytes)
        self.events.append(row)
        self.ids[row["event_id"]] = {"seq": row["seq"], "intent_hash": row["intent_hash"], "legacy": False}
        self.v2 += 1
        self.last_seq = row["seq"]
        self.complete_bytes += line_bytes
        self.valid_bytes = self.complete_bytes


def _shallow_ok(row: dict) -> bool:
    return (set(row) == set(schema.PERSISTED_KEYS) and isinstance(row["event_id"], str) and isinstance(row["seq"], int)
            and not isinstance(row["seq"], bool) and isinstance(row["intent_hash"], str) and row["kind"] in schema.KINDS
            and isinstance(row["item"], str))


def scan_bytes(data: bytes, *, deep: bool = True, deep_tail: int = 0, actors: tuple[str, ...] | None = None) -> Scan:
    """Scan the journal bytes. ``deep`` validates every v2 row against its own intent hash (about 14 times the cost of a
    shallow scan); ``deep_tail`` validates only the newest N v2 rows, which is what an append does."""
    scan = Scan()
    cut = data.rfind(b"\n") + 1
    scan.complete_bytes, scan.tail = cut, data[cut:]
    scan.valid_bytes = cut
    broken = False
    offset = 0
    lines = data[:cut].split(b"\n")[:-1] if cut else []
    for line in lines:
        here, offset = offset, offset + len(line) + 1
        if broken:
            scan.after_gap += 1
            continue

        def problem(reason: str):
            nonlocal broken
            broken = True
            scan.valid_bytes = here
            if len(scan.problems) < MAX_PROBLEMS:
                scan.problems.append({"offset": here, "reason": reason})

        if not line.strip():
            problem("blank_line")
            continue
        try:
            row = strictjson.loads(line)
        except strictjson.StrictJSONError as exc:
            problem(exc.reason)
            continue
        if not isinstance(row, dict):
            problem("not_an_object")
            continue
        version = row.get("v")
        if version == 1:
            if scan.v2:
                problem("legacy_after_v2")
                continue
            if not isinstance(row.get("item"), str) or not isinstance(row.get("kind"), str):
                problem("bad_legacy_row")
                continue
            eid = row.get("event_id")
            if isinstance(eid, str) and eid in scan.ids:
                if len(scan.warnings) < MAX_PROBLEMS:
                    scan.warnings.append({"offset": here, "reason": "duplicate_legacy_id"})
            elif isinstance(eid, str):
                scan.ids[eid] = {"seq": None, "intent_hash": None, "legacy": True}
            scan.legacy += 1
            scan.last_seq = scan.legacy
            scan.offsets.append(here)
            scan.events.append(row)
        elif version == 2:
            try:
                if deep:
                    schema.validate_persisted(row, actors=actors)
                elif not _shallow_ok(row):
                    raise schema.SchemaError("event", "bad_keys")
            except schema.SchemaError as exc:
                problem(f"{exc.field}:{exc.reason}")
                continue
            if row["seq"] != scan.next_seq:
                problem("seq_gap_or_forged" if row["seq"] > scan.next_seq else "seq_repeated_or_forged")
                continue
            if row["event_id"] in scan.ids:
                problem("duplicate_id")
                continue
            scan.v2 += 1
            scan.last_seq = row["seq"]
            scan.ids[row["event_id"]] = {"seq": row["seq"], "intent_hash": row["intent_hash"], "legacy": False}
            scan.offsets.append(here)
            scan.events.append(row)
        else:
            problem("unknown_version")
    if deep_tail and not deep and not scan.problems:
        start = max(0, len(scan.events) - deep_tail)
        for index in range(start, len(scan.events)):
            row = scan.events[index]
            if row.get("v") != 2:
                continue
            try:
                schema.validate_persisted(row, actors=actors)
            except schema.SchemaError as exc:
                scan.problems.append({"offset": scan.offsets[index], "reason": f"{exc.field}:{exc.reason}"})
                scan.valid_bytes, scan.after_gap = scan.offsets[index], len(scan.events) - index - 1
                del scan.events[index:], scan.offsets[index:]
                break
    return scan


# ── results ────────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Result:
    ok: bool
    status: str
    event_id: str | None = None
    seq: int | None = None
    committed: bool | None = False
    retryable: bool = False
    reason: str = ""
    evidence: dict = field(default_factory=dict)
    exit_code: int = 0

    def to_json(self) -> dict:
        return {"ok": self.ok, "status": self.status, "event_id": self.event_id, "seq": self.seq, "committed": self.committed,
                "retryable": self.retryable, "reason": self.reason, "evidence": self.evidence}


def _fail(exc: JournalError) -> Result:
    return Result(False, exc.status, exc.event_id, None, exc.committed, exc.retryable, exc.reason, exc.evidence, exc.exit_code)


@dataclass
class ReadResult:
    status: str                                  # ok | missing | unsupported_writer | unsafe_root | tail_in_progress | torn_tail | corrupt
    events: list[dict] = field(default_factory=list)
    scan: Scan | None = None
    reason: str = ""
    data: bytes = b""

    @property
    def complete(self) -> bool:
        return self.status in ("ok",)


@dataclass
class _Session:
    base_fd: int
    jfd: int
    data: bytes
    scan: Scan
    prepared_fd: int | None = None


# ── the engine ─────────────────────────────────────────────────────────────────────────────────────────────────────
class Journal:
    def __init__(self, root: str, workshop_id: str, *, stream: str | None = None,
                 actors: tuple[str, ...] | None = schema.DEFAULT_ACTORS, durability: str = "strict", lock_timeout: float = 5.0,
                 clock: Callable[[], datetime] | None = None, resolver: reducer.Resolver | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        if not schema.WORKSHOP_RE.fullmatch(workshop_id or ""):
            raise InvalidInput("workshop_id_format")
        if durability not in ("strict", "degraded"):
            raise InvalidInput("durability_profile")
        self.root = os.path.expanduser(root)
        self.id = workshop_id
        self.stream = stream or f"{workshop_id}.local"
        if not schema.STREAM_RE.fullmatch(self.stream):
            raise InvalidInput("stream_format")
        self.dir = os.path.join(self.root, workshop_id)
        self.events_path = os.path.join(self.dir, JOURNAL)
        self.state_path = os.path.join(self.dir, STATE)
        self.actors, self.durability, self.lock_timeout = actors, durability, lock_timeout
        self.clock, self.resolver, self._sleep = clock or (lambda: datetime.now(timezone.utc)), resolver, sleep

    # time
    def _now(self) -> str:
        return schema.utc_text(self.clock().astimezone(timezone.utc).isoformat(), "clock")

    # ── locking and layout ────────────────────────────────────────────────────────────────────────────────────────
    def _open_base(self, *, create: bool) -> int:
        root_fd = fsutil.open_root(self.root, create=create)
        try:
            return fsutil.open_subdir(root_fd, self.id, create=create)
        finally:
            os.close(root_fd)

    def _flock(self, base_fd: int, *, exclusive: bool, create: bool, wait: float) -> int | None:
        """The advisory lock, bounded wait. Returns the descriptor, or None for a shared read that a writer is holding."""
        fd = fsutil.open_file(base_fd, LOCK, os.O_RDWR | (os.O_CREAT if create else 0))
        op = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, op)
                break
            except OSError as exc:
                if exc.errno not in (11, 35):                 # EAGAIN, EWOULDBLOCK
                    os.close(fd)
                    raise
                if time.monotonic() >= deadline:
                    os.close(fd)
                    if exclusive:
                        raise LockBusy("lock_busy") from None
                    return None
                self._sleep(0.02)
        named = os.stat(LOCK, dir_fd=base_fd, follow_symlinks=False)
        held = os.fstat(fd)
        if (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino):
            os.close(fd)
            raise UnsafeRoot("descriptor_and_path_diverge")
        return fd

    @contextmanager
    def _locked(self, *, prepared: bool = True) -> Iterator[_Session]:
        base_fd = self._open_base(create=True)
        lock_fd = jfd = prepared_fd = None
        try:
            lock_fd = self._flock(base_fd, exclusive=True, create=True, wait=self.lock_timeout)
            jfd = fsutil.open_file(base_fd, JOURNAL, os.O_RDWR | os.O_CREAT | os.O_APPEND)
            prepared_fd = fsutil.open_subdir(base_fd, PREPARED, create=True) if prepared else None
            data = fsutil.read_all(jfd)
            yield _Session(base_fd, jfd, data, scan_bytes(data, deep=False, deep_tail=DEEP_TAIL), prepared_fd)
        finally:
            for fd in (prepared_fd, jfd, lock_fd, base_fd):
                if fd is not None:
                    try:
                        os.close(fd)                           # closing the lock descriptor releases the flock
                    except OSError:
                        pass

    # ── prepare receipts ──────────────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _receipt_bytes(intent: dict, ih: str, prepared_at: str) -> bytes:
        return strictjson.canonical_bytes({"v": 1, "event_id": intent["event_id"], "intent_hash": ih, "at": intent["at"],
                                           "prepared_at": prepared_at, "workshop": intent["workshop"], "stream": intent["stream"]})

    @staticmethod
    def _load_receipt(prepared_fd: int, event_id: str) -> dict | None:
        try:
            fd = fsutil.open_file(prepared_fd, f"{schema.uuid_text(event_id, 'event_id')}.json", os.O_RDONLY)
        except Missing:
            return None
        try:
            doc = strictjson.loads(fsutil.read_all(fd))
        except strictjson.StrictJSONError:
            raise Corrupt("bad_prepare_receipt", event_id=event_id) from None
        finally:
            os.close(fd)
        if not isinstance(doc, dict) or doc.get("event_id") != event_id or not isinstance(doc.get("intent_hash"), str):
            raise Corrupt("bad_prepare_receipt", event_id=event_id)
        return doc

    def _publish_receipt(self, prepared_fd: int, intent: dict, ih: str) -> dict:
        existing = self._load_receipt(prepared_fd, intent["event_id"])
        if existing is not None:
            if existing["intent_hash"] != ih:
                raise IdConflict("prepared_receipt_differs", event_id=intent["event_id"])
            return existing
        body = self._receipt_bytes(intent, ih, self._now())
        try:
            fsutil.publish_new(prepared_fd, f"{intent['event_id']}.json", body, durable=False)
        except OSError as exc:
            raise WriteFailed("receipt_not_written", event_id=intent["event_id"], evidence={"errno": exc.errno}) from None
        return self._load_receipt(prepared_fd, intent["event_id"]) or {}

    def _resolve(self, envelope: dict, prepared_fd: int, *, software: bool) -> tuple[dict, str]:
        if not isinstance(envelope, dict):
            raise schema.SchemaError("envelope", "not_object")
        supplied = envelope.get("event_id")
        eid = schema.uuid_text(supplied, "event_id") if supplied is not None else str(uuid.uuid4())
        receipt = self._load_receipt(prepared_fd, eid) if supplied is not None else None
        at = schema.utc_text(receipt["at"], "at") if receipt else self._now()
        intent = schema.normalize_intent(envelope, workshop=self.id, stream=self.stream, actors=self.actors, resolved_at=at,
                                         event_id=eid, allow_software=software)
        ih = schema.intent_hash(intent)
        if receipt and receipt["intent_hash"] != ih:
            raise IdConflict("prepared_receipt_differs", event_id=eid)
        return intent, ih

    def prepare(self, envelope: dict) -> Result:
        """Fix the event ID and occurrence time now and keep them, so a retry (or a recovery) can prove it is the same intent.

        Writes only a prepare receipt; the journal is not touched. Returns the envelope to resubmit."""
        try:
            base_fd = self._open_base(create=True)
            try:
                prepared_fd = fsutil.open_subdir(base_fd, PREPARED, create=True)
                try:
                    intent, ih = self._resolve(envelope, prepared_fd, software=False)
                    self._publish_receipt(prepared_fd, intent, ih)
                finally:
                    os.close(prepared_fd)
            finally:
                os.close(base_fd)
        except schema.SchemaError as exc:
            return Result(False, "invalid_input", None, None, False, False, f"{exc.field}:{exc.reason}", {}, 2)
        except JournalError as exc:
            return _fail(exc)
        resubmit = {k: intent[k] for k in schema.INTENT_KEYS if k not in ("workshop", "stream")}
        return Result(True, "prepared", intent["event_id"], None, False, False, "", {"intent_hash": ih, "envelope": resubmit})

    # ── tail recovery ─────────────────────────────────────────────────────────────────────────────────────────────
    def _classify_tail(self, s: _Session) -> tuple[str, str, dict | None]:
        """('complete', reason, row) only when the tail is a whole v2 event that matches a durable prepare receipt."""
        try:
            row = strictjson.loads(s.scan.tail)
        except strictjson.StrictJSONError:
            return "quarantine", "incomplete_record", None
        if not isinstance(row, dict) or row.get("v") != 2:
            return "quarantine", "not_a_v2_event", None
        try:
            schema.validate_persisted(row, actors=None)
        except schema.SchemaError:
            return "quarantine", "invalid_event", None
        if row["seq"] != s.scan.next_seq:
            return "quarantine", "seq_mismatch", None
        if row["event_id"] in s.scan.ids:
            return "quarantine", "duplicate_id", None
        try:
            receipt = None if s.prepared_fd is None else self._load_receipt(s.prepared_fd, row["event_id"])
        except Corrupt:
            receipt = None
        if receipt is None:
            return "quarantine", "no_prepare_receipt", None
        if receipt["intent_hash"] != row["intent_hash"]:
            return "quarantine", "intent_mismatch", None
        return "complete", "matched_prepare_receipt", row

    def _manifest(self, s: _Session, action: str, reason: str, row: dict | None) -> tuple[dict, bytes, str]:
        tail = s.scan.tail
        doc = {"v": 1, "action": action, "reason": reason, "journal_offset": s.scan.complete_bytes, "tail_bytes": len(tail),
               "tail_sha256": hashlib.sha256(tail).hexdigest(),
               "prefix_sha256": hashlib.sha256(s.data[:s.scan.complete_bytes]).hexdigest(),
               "event_id": row["event_id"] if row else None}
        raw = strictjson.canonical_bytes(doc)
        return doc, raw, hashlib.sha256(raw).hexdigest()

    def _recover_tail(self, s: _Session, *, emit: bool, dry_run: bool = False) -> dict | None:
        """Preserve, then complete or cut away, an unterminated final record. Idempotent after a crash: the manifest is
        content addressed and holds no time, so a second pass finds the same files and repeats only what is missing."""
        if not s.scan.tail:
            return None
        action, reason, row = self._classify_tail(s)
        doc, raw, mhash = self._manifest(s, "completed_lf" if action == "complete" else "quarantined_truncated", reason, row)
        plan = {"action": doc["action"], "reason": reason, "manifest_hash": mhash, "journal_offset": doc["journal_offset"],
                "tail_bytes": doc["tail_bytes"], "event_id": doc["event_id"]}
        if dry_run:
            return plan
        try:
            qfd = fsutil.open_subdir(s.base_fd, QUARANTINE, create=True)
            try:
                fsutil.publish_new(qfd, f"tail-{doc['tail_sha256']}.bin", s.scan.tail)
                fsutil.publish_new(qfd, f"manifest-{mhash}.json", raw)
            finally:
                os.close(qfd)
        except (OSError, JournalError) as exc:
            raise RecoveryBlocked("quarantine_not_written", evidence={"errno": getattr(exc, "errno", None)}) from None
        try:
            if action == "complete":
                fsutil.write_all(s.jfd, b"\n")
            else:
                fsutil.truncate(s.jfd, s.scan.complete_bytes)
            fsutil.full_sync(s.jfd, degraded_ok=self.durability == "degraded")
        except OSError as exc:
            raise RecoveryBlocked("recovery_write_failed", evidence={"errno": exc.errno}) from None
        s.data = fsutil.read_all(s.jfd)
        s.scan = scan_bytes(s.data, deep=False)
        if emit:
            self._emit_recovery(s, doc, mhash)
        return plan

    def _emit_recovery(self, s: _Session, doc: dict, mhash: str) -> None:
        envelope = {"actor": "host", "kind": "recovery", "item": "host:journal", "text": "Recovered an incomplete final record.",
                    "payload": {"manifest_hash": mhash, "affected_byte_offset": doc["journal_offset"], "action": doc["action"],
                                "recovered_event_id": doc["event_id"]}}
        intent, ih = self._resolve(envelope, s.prepared_fd, software=True)
        self._commit(s, intent, ih, schema.make_origin("journal-recovery", None, "adapter"))

    def _emit_missing_recoveries(self, s: _Session) -> None:
        try:
            qfd = fsutil.open_subdir(s.base_fd, QUARANTINE, create=False)
        except Missing:
            return
        try:
            names = sorted(n for n in os.listdir(qfd) if n.startswith("manifest-") and n.endswith(".json"))
            known = {(e.get("payload") or {}).get("manifest_hash") for e in s.scan.events if e.get("kind") == "recovery"}
            for name in names:
                mhash = name[len("manifest-"):-len(".json")]
                if mhash in known:
                    continue
                fd = fsutil.open_file(qfd, name, os.O_RDONLY)
                try:
                    doc = strictjson.loads(fsutil.read_all(fd))
                finally:
                    os.close(fd)
                self._emit_recovery(s, doc, mhash)
        finally:
            os.close(qfd)

    # ── commit ────────────────────────────────────────────────────────────────────────────────────────────────────
    def _commit(self, s: _Session, intent: dict, ih: str, origin: dict) -> tuple[dict, str]:
        self._publish_receipt(s.prepared_fd, intent, ih)
        event = schema.build_event(intent, seq=s.scan.next_seq, recorded_at=self._now(), origin=origin)
        line = schema.event_bytes(event)
        if os.fstat(s.jfd).st_size != len(s.data):
            raise Corrupt("journal_changed_under_lock", event_id=intent["event_id"])
        fsutil.check_same_file(s.base_fd, JOURNAL, s.jfd)
        try:
            fsutil.write_all(s.jfd, line)
        except OSError as exc:
            try:
                fsutil.truncate(s.jfd, len(s.data))
            except OSError:
                pass
            raise WriteFailed("write_failed", event_id=intent["event_id"], evidence={"errno": exc.errno}) from None
        try:
            durability = fsutil.full_sync(s.jfd, degraded_ok=self.durability == "degraded")
        except OSError as exc:
            raise CommitUnknown("sync_failed", event_id=intent["event_id"], evidence={"errno": exc.errno}) from None
        s.data += line
        s.scan.add_committed(event, len(line))
        return event, durability

    def _publish_state(self, s: _Session) -> None:
        state = reducer.reduce(self.id, s.scan.events, resolver=self.resolver)
        state["watermark"] = {"journal_bytes": len(s.data), "journal_sha256": hashlib.sha256(s.data).hexdigest(),
                              "last_seq": s.scan.last_seq, "events": len(s.scan.events), "problems": len(s.scan.problems)}
        fsutil.publish_replace(s.base_fd, STATE, strictjson.canonical_bytes(state) + b"\n")

    def _require_healthy(self, s: _Session) -> None:
        if s.scan.problems:
            raise Corrupt("interior_damage_writes_held", evidence={"problems": s.scan.problems[:5], "after_gap": s.scan.after_gap})

    # ── append ────────────────────────────────────────────────────────────────────────────────────────────────────
    def append(self, envelope: dict, *, adapter: str = "local-helper", session_ref: str | None = None, basis: str = "claimed",
               software: bool = False, artifacts: list | None = None) -> Result:
        """Append one event. ``artifacts`` are documents (``artifacts.prepare``) the event refers to: each is published and made
        durable first, under the same lock, and if that fails no event is written."""
        try:
            origin = schema.make_origin(adapter, session_ref, basis)
            with self._locked() as s:
                intent, ih = self._resolve(envelope, s.prepared_fd, software=software)
                cited = {r.get("sha256") for r in intent["refs"]}
                if any(item.retained_sha256 not in cited for item in artifacts or []):
                    raise InvalidInput("artifact_not_referenced")
                self._require_healthy(s)
                self._recover_tail(s, emit=True)
                self._emit_missing_recoveries(s)
                self._require_healthy(s)
                known = s.scan.ids.get(intent["event_id"])
                if known is not None:
                    if known["intent_hash"] != ih:
                        raise IdConflict("same_id_different_intent", event_id=intent["event_id"])
                    return Result(True, "duplicate", intent["event_id"], known["seq"], True, False, "already_committed", {}, 0)
                for item in artifacts or []:
                    try:
                        artifact_store.publish(s.base_fd, item)
                    except OSError as exc:
                        raise WriteFailed("artifact_not_published", event_id=intent["event_id"],
                                          evidence={"errno": exc.errno}) from None
                event, durability = self._commit(s, intent, ih, origin)
                result = Result(True, "committed", event["event_id"], event["seq"], True, False, "",
                                {"durability": durability, "recorded_at": event["recorded_at"]}, 0)
                try:
                    self._publish_state(s)
                except (OSError, JournalError):
                    result.status, result.reason = "committed_state_stale", "state_not_refreshed"
                return result
        except schema.SchemaError as exc:
            return Result(False, "invalid_input", None, None, False, False, f"{exc.field}:{exc.reason}", {}, 2)
        except JournalError as exc:
            return _fail(exc)

    def append_legacy(self, event: dict) -> Result:
        """A v1 line through the same lock, recovery and durability as v2 (compatibility for the existing Workshop callers).

        v1 lines carry no prepare receipt, so a torn tail is always preserved and cut, never completed; once a stream holds v2
        events, v1 appends are refused."""
        try:
            with self._locked(prepared=False) as s:
                self._require_healthy(s)
                if s.scan.v2:
                    raise LegacyAfterV2("stream_has_v2_events")
                self._recover_tail(s, emit=False)
                line = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                if os.fstat(s.jfd).st_size != len(s.data):
                    raise Corrupt("journal_changed_under_lock")
                fsutil.check_same_file(s.base_fd, JOURNAL, s.jfd)
                try:
                    fsutil.write_all(s.jfd, line)
                except OSError as exc:
                    try:
                        fsutil.truncate(s.jfd, len(s.data))
                    except OSError:
                        pass
                    raise WriteFailed("write_failed", evidence={"errno": exc.errno}) from None
                try:
                    durability = fsutil.full_sync(s.jfd, degraded_ok=self.durability == "degraded")
                except OSError as exc:
                    raise CommitUnknown("sync_failed", evidence={"errno": exc.errno}) from None
                s.data += line
                s.scan = scan_bytes(s.data, deep=False)
                result = Result(True, "committed", event.get("event_id"), None, True, False, "", {"durability": durability}, 0)
                try:
                    self._publish_state(s)
                except (OSError, JournalError):
                    result.status, result.reason = "committed_state_stale", "state_not_refreshed"
                return result
        except JournalError as exc:
            return _fail(exc)

    def write_state(self) -> dict:
        """Rebuild ``state.json`` from the journal under the lock (the one writer of that file besides ``append``)."""
        with self._locked() as s:
            self._require_healthy(s)
            self._publish_state(s)
            state = reducer.reduce(self.id, s.scan.events, resolver=self.resolver)
            return state

    # ── reads (never create, repair or block a writer) ────────────────────────────────────────────────────────────
    def read(self, *, deep: bool = True) -> ReadResult:
        try:
            base_fd = self._open_base(create=False)
        except Missing:
            return ReadResult("missing", reason="root_or_workshop_missing")
        except UnsafeRoot as exc:
            return ReadResult("unsafe_root", reason=exc.reason)
        lock_fd = jfd = None
        try:
            try:
                jfd = fsutil.open_file(base_fd, JOURNAL, os.O_RDONLY)
            except Missing:
                return ReadResult("missing", reason="no_journal")
            try:
                lock_fd = self._flock(base_fd, exclusive=False, create=False, wait=0.2)
            except Missing:
                return ReadResult("unsupported_writer", reason="no_lock_file")
            in_progress = lock_fd is None
            data = fsutil.read_all(jfd)
            scan = scan_bytes(data, deep=deep, deep_tail=0 if deep else DEEP_TAIL)
            if scan.problems:
                return ReadResult("corrupt", scan.events, scan, scan.problems[0]["reason"], data)
            if scan.tail:
                return ReadResult("tail_in_progress" if in_progress else "torn_tail", scan.events, scan, "", data)
            return ReadResult("ok", scan.events, scan, "", data)
        except UnsafeRoot as exc:
            return ReadResult("unsafe_root", reason=exc.reason)
        finally:
            for fd in (jfd, lock_fd, base_fd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def state(self) -> tuple[dict | None, str]:
        """(state, source): the derived state, which is always rebuilt from the journal, so the journal always wins.

        ``source`` says how the stored ``state.json`` compared: ``file`` when it is exactly what the journal as read now would
        produce (same watermark, same content apart from its own timestamp), ``rebuilt`` when it is absent, stale or edited.
        Nothing is written by a read. ``unreadable`` means the stored file is not JSON (the state returned is then None, as the
        Workshop screen expects); ``refused`` means the journal itself is damaged and no "as of" is reported; ``missing``,
        ``unsupported_writer`` and ``unsafe_root`` pass through."""
        read = self.read(deep=False)
        if read.status in ("missing", "unsupported_writer", "unsafe_root"):
            return None, read.status
        if read.status == "corrupt":
            return None, "refused"
        doc = None
        try:
            base_fd = self._open_base(create=False)
            try:
                fd = fsutil.open_file(base_fd, STATE, os.O_RDONLY)
                try:
                    doc = strictjson.loads(fsutil.read_all(fd))
                finally:
                    os.close(fd)
            finally:
                os.close(base_fd)
        except Missing:
            doc = None
        except (strictjson.StrictJSONError, UnsafeRoot):
            return None, "unreadable"
        rebuilt = reducer.reduce(self.id, read.events, resolver=self.resolver)
        mark = {"journal_bytes": len(read.data), "journal_sha256": hashlib.sha256(read.data).hexdigest(),
                "last_seq": read.scan.last_seq, "events": len(read.events), "problems": 0}
        if isinstance(doc, dict) and doc.get("watermark") == mark:
            same = {k: v for k, v in doc.items() if k not in ("updated_at", "watermark")} == \
                   {k: v for k, v in rebuilt.items() if k not in ("updated_at", "watermark")}
            if same:
                return rebuilt, "file"
        return rebuilt, "rebuilt"

    # ── retained documents ────────────────────────────────────────────────────────────────────────────────────────
    def open_artifact(self, retained_sha256: str) -> bytes:
        """The exact kept bytes for a hash (checked on every open). Raises ArtifactMissing, ArtifactCorrupt or SourceRefused."""
        if not isinstance(retained_sha256, str) or not artifact_store.HASH.fullmatch(retained_sha256):
            raise SourceRefused("bad_hash")
        try:
            base_fd = self._open_base(create=False)
        except Missing:
            raise ArtifactMissing("no_workshop") from None
        try:
            return artifact_store.open_exact(base_fd, retained_sha256)
        finally:
            os.close(base_fd)

    def artifact_report(self) -> dict:
        """Read-only: orphans (kept but nothing refers to them), missing, damaged and stale temporary files. Deletes nothing."""
        read = self.read(deep=False)
        if read.status in ("missing", "unsafe_root", "unsupported_writer"):
            return {"status": read.status}
        base_fd = self._open_base(create=False)
        try:
            report = artifact_store.inventory(base_fd, artifact_store.referenced_hashes(read.events))
        finally:
            os.close(base_fd)
        report["status"] = "ok" if not (report["missing_referenced"] or report["corrupt"]) else "attention"
        report["journal_status"] = read.status
        return report

    def get(self, event_id: str) -> Result:
        try:
            event_id = schema.uuid_text(event_id, "event_id")
        except schema.SchemaError:
            return Result(False, "invalid_input", None, None, False, False, "event_id:bad_uuid", {}, 2)
        read = self.read()
        if read.status in ("missing", "unsupported_writer", "unsafe_root"):
            return Result(False, read.status, event_id, None, False, False, read.reason, {}, 8 if read.status == "missing" else 2)
        for ev in read.events:
            if ev.get("event_id") == event_id:
                return Result(True, "found", event_id, ev.get("seq"), True, False, "", {"event": ev, "journal_status": read.status}, 0)
        return Result(False, "not_found", event_id, None, False, False, "", {"journal_status": read.status}, 0 if read.complete else 8)

    def verify(self) -> dict:
        """A read-only consistency report. Deep checks every line; nothing is created, repaired or rewritten."""
        read = self.read(deep=True)
        report: dict = {"status": read.status, "reason": read.reason, "events": 0, "legacy": 0, "v2": 0, "last_seq": 0,
                        "tail_bytes": 0, "problems": [], "warnings": [], "quarantine_manifests": 0, "recoveries_missing": 0,
                        "unreferenced_receipts": 0, "after_gap": 0}
        if read.scan:
            sc = read.scan
            report.update(events=len(sc.events), legacy=sc.legacy, v2=sc.v2, last_seq=sc.last_seq, tail_bytes=len(sc.tail),
                          problems=sc.problems, warnings=sc.warnings, after_gap=sc.after_gap)
            try:
                base_fd = self._open_base(create=False)
            except (Missing, UnsafeRoot):
                base_fd = None
            if base_fd is not None:
                try:
                    with_ids = set(sc.ids)
                    try:
                        pfd = fsutil.open_subdir(base_fd, PREPARED, create=False)
                        try:
                            report["unreferenced_receipts"] = sum(1 for n in os.listdir(pfd) if n.endswith(".json")
                                                                  and n[:-5] not in with_ids)
                        finally:
                            os.close(pfd)
                    except Missing:
                        pass
                    try:
                        qfd = fsutil.open_subdir(base_fd, QUARANTINE, create=False)
                        try:
                            manifests = [n for n in os.listdir(qfd) if n.startswith("manifest-") and n.endswith(".json")]
                            known = {(e.get("payload") or {}).get("manifest_hash") for e in sc.events if e.get("kind") == "recovery"}
                            report["quarantine_manifests"] = len(manifests)
                            report["recoveries_missing"] = sum(1 for n in manifests if n[9:-5] not in known)
                        finally:
                            os.close(qfd)
                    except Missing:
                        pass
                finally:
                    os.close(base_fd)
        report["ok"] = read.status == "ok" and not report["recoveries_missing"]
        return report

    def repair(self, *, apply: bool = False) -> dict:
        """Dry run by default: classify the unterminated tail and report what would be preserved and done. With ``apply`` it
        runs the same recovery an append would (preserve, then complete or cut, then record a ``recovery`` event). Interior
        damage is never repaired automatically."""
        if not apply:
            read = self.read(deep=True)
            if read.status == "corrupt":
                return {"status": "blocked", "reason": "interior_damage", "problems": read.scan.problems[:5]}
            if read.status in ("missing", "unsupported_writer", "unsafe_root"):
                return {"status": read.status}
            if not read.scan.tail:
                return {"status": "nothing_to_repair"}
            base_fd = self._open_base(create=False)
            prepared_fd = None
            try:
                try:
                    prepared_fd = fsutil.open_subdir(base_fd, PREPARED, create=False)
                except Missing:
                    prepared_fd = None
                plan = self._recover_tail(_Session(base_fd, -1, read.data, read.scan, prepared_fd), emit=False, dry_run=True)
                return {"status": "would_repair", **(plan or {})}
            finally:
                if prepared_fd is not None:
                    os.close(prepared_fd)
                os.close(base_fd)
        try:
            with self._locked() as s:
                self._require_healthy(s)
                plan = self._recover_tail(s, emit=True)
                self._emit_missing_recoveries(s)
                self._publish_state(s)
                return {"status": "repaired" if plan else "nothing_to_repair", **(plan or {})}
        except JournalError as exc:
            return {"status": exc.status, "reason": exc.reason}


__all__ = ["Journal", "Result", "ReadResult", "Scan", "scan_bytes", "JOURNAL", "LOCK", "STATE", "PREPARED", "QUARANTINE"]
