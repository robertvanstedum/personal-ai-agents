"""The Guild Build Queue's only writer: a verified Save with a receipt.

Every write to ``build_queue.json`` goes through :class:`QueueStore`. Under one
exclusive lock (a sidecar ``.<name>.lock`` file in the queue's own folder, so
the lock survives the file's inode changing on replace) a write does:

    reconcile earlier unfinished intents
    -> read and parse the file (the store never creates it)
    -> per-item unchanged-check (compare-and-swap on the item's digest)
    -> append an ``intent`` journal line
    -> temp file in the same folder, fsync, os.replace, folder fsync
    -> read back and compare the file digest
    -> optional audit callback (reported, never a condition of "saved")
    -> append a ``completed`` journal line with the receipt

The lock is ``fcntl.flock`` on the sidecar file. The host-side seed and publish
script (``scripts/operations/seed_build_queue.sh``) takes the same flock on the
same file, so on native Linux (EC2) the container and the host exclude each
other. ``flock`` does not cross the Colima VM on a Mac, so on dev exactly one
process (the Docker portal) writes the state folder.

Where writes are allowed (review item M2): ``GUILD_QUEUE_PATH`` must be set,
and the queue's folder must be a mount point (``os.path.ismount``) inside a
container, or, outside a container, a mount point or a folder outside the
application code tree. A container started without its volume therefore
refuses to Save instead of "verifying" a copy baked into its own layer.

This module is self-contained on purpose (standard library only), so it can
be reverted with its deliverable without touching anything else.
"""

from __future__ import annotations

import errno
import fcntl
import glob
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

CODE_ROOT = Path(__file__).resolve().parent.parent.parent
JOURNAL_NAME = "queue_journal.jsonl"

STATUSES = (
    "idea", "design", "backlog", "spec_ready", "in_build", "blocked",
    "deferred", "cancelled", "superseded", "done",
)
EDITABLE_FIELDS = ("spec_title", "summary", "github_issue")

# Result codes. The portal maps each to one fixed sentence.
SAVED = "saved"
CONFLICT = "conflict"
BUSY = "busy"
UNCERTAIN = "uncertain"
UNAVAILABLE = "unavailable"
REFUSED = "refused"          # M2: this process may not write the queue
NOT_FOUND = "not_found"
INVALID = "invalid"

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()

# Indirection points so tests can inject faults (a kill before the replace, a
# corrupted read-back) without patching the os module globally.
_replace = os.replace


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as stream:
        return stream.read()


# ── Digests ───────────────────────────────────────────────────────────────────

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def item_digest(item: dict) -> str:
    """SHA-256 of the item's canonical JSON (sorted keys, no spaces)."""
    canonical = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_bytes(canonical.encode("utf-8"))


def serialize(items: list) -> bytes:
    """The queue file's on-disk format (unchanged from the portal's old writer)."""
    return json.dumps(items, indent=2, ensure_ascii=False).encode("utf-8")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def receipt_id(moment: datetime, after_digest: str) -> str:
    return f"q-{moment.strftime('%Y%m%dT%H%M%SZ')}-{after_digest[:6]}"


# ── Where writes are allowed (M2) ─────────────────────────────────────────────

def _running_in_container() -> bool:
    return (
        os.path.exists("/.dockerenv")
        or os.path.exists("/run/.containerenv")
        or os.environ.get("FLASK_ENV") == "production"
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def write_location_problem(path: str | os.PathLike | None, *,
                           in_container: bool | None = None,
                           code_root: Path | None = None) -> str | None:
    """Return why this process may not write the queue at ``path``, or None.

    The mount check is ``os.path.ismount`` on the queue's folder: a bind mount
    or volume is a different device from the image's filesystem, so the check
    is true exactly when compose attached the host folder. It needs no flag a
    compose file could set while forgetting the volume itself.
    """
    if not path:
        return "GUILD_QUEUE_PATH is not set"
    folder = Path(path).parent
    if not folder.is_dir():
        return "the queue folder does not exist"
    in_container = _running_in_container() if in_container is None else in_container
    code_root = CODE_ROOT if code_root is None else code_root
    if os.path.ismount(folder):
        return None
    if in_container:
        return "the queue folder is not a mounted host folder"
    if _is_within(folder, code_root):
        return "the queue folder is inside the application code tree, not a state folder"
    return None


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class SaveResult:
    result: str
    item_id: int | None = None
    op: str | None = None
    old: Any = None
    new: Any = None
    at: str | None = None
    receipt_id: str | None = None
    verified: bool = False
    audit: str | None = None
    recovered: bool = False
    repeated: bool = False
    before_digest: str | None = None
    after_digest: str | None = None
    current_item: dict | None = None
    current_item_digest: str | None = None
    reason: str | None = None
    reconciled: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result == SAVED

    def as_dict(self) -> dict:
        return asdict(self)


class QueueBusy(Exception):
    pass


class QueueUnavailable(Exception):
    pass


# ── The store ─────────────────────────────────────────────────────────────────

class QueueStore:
    def __init__(self, path: str | os.PathLike | None, *,
                 lock_timeout_s: float = 5.0,
                 in_container: bool | None = None,
                 code_root: Path | None = None,
                 clock: Callable[[], datetime] = _now):
        self.path = str(path) if path else None
        self.lock_timeout_s = lock_timeout_s
        self._in_container = in_container
        self._code_root = code_root
        self._clock = clock

    # paths
    @property
    def folder(self) -> str:
        return os.path.dirname(self.path)

    @property
    def lock_path(self) -> str:
        return os.path.join(self.folder, f".{os.path.basename(self.path)}.lock")

    @property
    def journal_path(self) -> str:
        return os.path.join(self.folder, JOURNAL_NAME)

    def write_problem(self) -> str | None:
        return write_location_problem(self.path, in_container=self._in_container,
                                      code_root=self._code_root)

    # reads (no lock: writers replace atomically)
    def read_items(self) -> list:
        """Validating read: raises QueueUnavailable when missing or corrupt."""
        if not self.path:
            raise QueueUnavailable("GUILD_QUEUE_PATH is not set")
        try:
            raw = _read_bytes(self.path)
        except FileNotFoundError as exc:
            raise QueueUnavailable("the queue file does not exist") from exc
        return self._parse(raw)

    @staticmethod
    def _parse(raw: bytes) -> list:
        try:
            items = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QueueUnavailable("the queue file is not valid JSON") from exc
        if not isinstance(items, list):
            raise QueueUnavailable("the queue file is not a list")
        return items

    # the lock
    def _thread_lock(self) -> threading.Lock:
        with _THREAD_LOCKS_GUARD:
            return _THREAD_LOCKS.setdefault(self.lock_path, threading.Lock())

    def _acquire(self):
        tlock = self._thread_lock()
        deadline = time.monotonic() + self.lock_timeout_s
        if not tlock.acquire(timeout=max(self.lock_timeout_s, 0.0)):
            raise QueueBusy()
        try:
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            tlock.release()
            raise
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return tlock, fd
            except OSError as exc:
                if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    os.close(fd)
                    tlock.release()
                    raise
                if time.monotonic() >= deadline:
                    os.close(fd)
                    tlock.release()
                    raise QueueBusy()
                time.sleep(0.02)

    @staticmethod
    def _release(held):
        tlock, fd = held
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
            tlock.release()

    # journal
    def _journal_lines(self) -> list[dict]:
        try:
            raw = _read_bytes(self.journal_path)
        except FileNotFoundError:
            return []
        lines = []
        for chunk in raw.split(b"\n"):
            if not chunk.strip():
                continue
            try:
                record = json.loads(chunk.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue  # a torn line from a crashed writer; skipped
            if isinstance(record, dict):
                lines.append(record)
        return lines

    def _append(self, record: dict) -> None:
        data = json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
        fd = os.open(self.journal_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            size = os.fstat(fd).st_size
            if size:
                with open(self.journal_path, "rb") as stream:
                    stream.seek(size - 1)
                    if stream.read(1) != b"\n":
                        data = b"\n" + data  # never join onto a torn last line
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)

    # reconciliation of every unfinished intent (not only the last line)
    def _reconcile_locked(self, current_digest: str | None) -> list[dict]:
        lines = self._journal_lines()
        resolved = {r.get("op_id") for r in lines if r.get("kind") in ("completed", "outcome")}
        outcomes = []
        for index, record in enumerate(lines):
            if record.get("kind") != "intent" or record.get("op_id") in resolved:
                continue
            # The file as it stood right after this intent's window is the
            # before_digest of the next journalled write (Save, seed or
            # publish), since every writer records it under the same lock;
            # with no later write it is the file as it is now.
            observed = current_digest
            for later in lines[index + 1:]:
                if later.get("kind") in ("intent", "replaced") and "before_digest" in later:
                    observed = later.get("before_digest")
                    break
            at = _iso(self._clock())
            if observed is not None and observed == record.get("after_digest"):
                try:
                    moment = datetime.fromisoformat(record["at"])
                except (KeyError, TypeError, ValueError):
                    moment = self._clock()
                rec = {"kind": "completed", "op_id": record["op_id"], "recovered": True,
                       "audit": "unknown", "at": at,
                       "receipt_id": receipt_id(moment, record["after_digest"])}
            elif observed is not None and observed == record.get("before_digest"):
                rec = {"kind": "outcome", "op_id": record["op_id"], "outcome": "not_applied",
                       "at": at}
            else:
                rec = {"kind": "outcome", "op_id": record["op_id"], "outcome": UNCERTAIN,
                       "item_id": record.get("item_id"), "at": at}
            self._append(rec)
            resolved.add(record["op_id"])
            outcomes.append(rec)
        return outcomes

    def reconcile(self) -> list[dict]:
        """Take the lock and reconcile every unfinished intent in the journal."""
        if self.write_problem():
            return []
        held = self._acquire()
        try:
            try:
                digest = sha256_bytes(_read_bytes(self.path))
            except FileNotFoundError:
                digest = None
            return self._reconcile_locked(digest)
        finally:
            self._release(held)

    def unresolved_checks(self) -> list[dict]:
        """Saves whose outcome is uncertain and that nobody has marked checked."""
        if not self.path:
            return []
        lines = self._journal_lines()
        checked = {r.get("op_id") for r in lines if r.get("kind") == "checked"}
        intents = {r.get("op_id"): r for r in lines if r.get("kind") == "intent"}
        checks = []
        for record in lines:
            if record.get("kind") == "outcome" and record.get("outcome") == UNCERTAIN \
                    and record.get("op_id") not in checked:
                intent = intents.get(record.get("op_id"), {})
                checks.append({"op_id": record.get("op_id"),
                               "item_id": intent.get("item_id", record.get("item_id")),
                               "at": record.get("at")})
        return checks

    def mark_checked(self, op_id: str, principal: str) -> bool:
        if self.write_problem():
            return False
        if op_id not in {c["op_id"] for c in self.unresolved_checks()}:
            return False
        held = self._acquire()
        try:
            self._append({"kind": "checked", "op_id": op_id, "principal": principal,
                          "at": _iso(self._clock())})
            return True
        finally:
            self._release(held)

    # the write
    def _find_repeat(self, lines: Iterable[dict], idempotency_key: str | None):
        if not idempotency_key:
            return None
        lines = list(lines)
        intent = None
        for record in lines:
            if record.get("kind") == "intent" and record.get("idempotency_key") == idempotency_key:
                intent = record
        if intent is None:
            return None
        for record in lines:
            if record.get("op_id") != intent["op_id"]:
                continue
            if record.get("kind") == "completed":
                return SaveResult(SAVED, item_id=intent.get("item_id"), op=intent.get("op"),
                                  old=intent.get("from"), new=intent.get("to"),
                                  at=record.get("at"), receipt_id=record.get("receipt_id"),
                                  verified=True, audit=record.get("audit"), repeated=True,
                                  before_digest=intent.get("before_digest"),
                                  after_digest=intent.get("after_digest"))
            if record.get("kind") == "outcome" and record.get("outcome") == UNCERTAIN:
                return SaveResult(UNCERTAIN, item_id=intent.get("item_id"), op=intent.get("op"),
                                  repeated=True)
        return None  # not applied: a fresh attempt may proceed

    def _clean_stale_temps(self) -> None:
        pattern = os.path.join(self.folder, f".{os.path.basename(self.path)}.tmp-*")
        for stale in glob.glob(pattern):
            try:
                os.unlink(stale)
            except OSError:
                pass

    def _write_atomic(self, data: bytes, template_stat: os.stat_result) -> None:
        fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(self.path)}.tmp-",
                                   dir=self.folder)
        try:
            # mkstemp creates 0600; keep the live file's mode and owner instead.
            os.fchmod(fd, template_stat.st_mode & 0o7777)
            try:
                os.fchown(fd, template_stat.st_uid, template_stat.st_gid)
            except PermissionError:
                if os.fstat(fd).st_uid != template_stat.st_uid:
                    log.warning("queue_store: could not keep the queue file's owner uid=%s",
                                template_stat.st_uid)
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            _replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        dir_fd = os.open(self.folder, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def _update(self, *, op: str, item_id: int, expect_item_digest: str | None,
                mutate: Callable[[dict], tuple[Any, Any]], principal: str, via: str,
                idempotency_key: str | None,
                audit: Callable[[dict, Any, Any], str] | None = None) -> SaveResult:
        problem = self.write_problem()
        if problem:
            return SaveResult(REFUSED, item_id=item_id, op=op, reason=problem)
        if not expect_item_digest:
            return SaveResult(CONFLICT, item_id=item_id, op=op,
                              reason="no unchanged-check digest was sent")
        try:
            held = self._acquire()
        except QueueBusy:
            return SaveResult(BUSY, item_id=item_id, op=op)
        try:
            try:
                raw = _read_bytes(self.path)
            except FileNotFoundError:
                return SaveResult(UNAVAILABLE, item_id=item_id, op=op,
                                  reason="the queue file does not exist")
            before_digest = sha256_bytes(raw)
            reconciled = self._reconcile_locked(before_digest)
            self._clean_stale_temps()
            repeat = self._find_repeat(self._journal_lines(), idempotency_key)
            if repeat is not None:
                repeat.reconciled = reconciled
                return repeat
            try:
                items = self._parse(raw)
            except QueueUnavailable as exc:
                return SaveResult(UNAVAILABLE, item_id=item_id, op=op, reason=str(exc),
                                  reconciled=reconciled)
            index = next((i for i, it in enumerate(items)
                          if isinstance(it, dict) and it.get("id") == item_id), None)
            if index is None or item_id == 0:
                return SaveResult(NOT_FOUND, item_id=item_id, op=op, reconciled=reconciled)
            current = items[index]
            current_digest = item_digest(current)
            if current_digest != expect_item_digest:
                return SaveResult(CONFLICT, item_id=item_id, op=op, current_item=current,
                                  current_item_digest=current_digest, reconciled=reconciled)
            updated = json.loads(json.dumps(current))
            try:
                old, new = mutate(updated)
            except ValueError as exc:
                return SaveResult(INVALID, item_id=item_id, op=op, reason=str(exc),
                                  reconciled=reconciled)
            items[index] = updated
            data = serialize(items)
            after_digest = sha256_bytes(data)
            op_id = uuid.uuid4().hex
            moment = self._clock()
            self._append({
                "kind": "intent", "op_id": op_id, "op": op, "item_id": item_id,
                "from": old, "to": new, "before_digest": before_digest,
                "after_digest": after_digest, "item_before_digest": current_digest,
                "principal": principal, "via": via, "idempotency_key": idempotency_key,
                "at": _iso(moment),
            })
            self._write_atomic(data, os.stat(self.path))
            try:
                readback = sha256_bytes(_read_bytes(self.path))
            except OSError:
                readback = None
            if readback != after_digest:
                self._append({"kind": "outcome", "op_id": op_id, "outcome": UNCERTAIN,
                              "item_id": item_id, "at": _iso(self._clock()),
                              "reason": "read-back did not match"})
                return SaveResult(UNCERTAIN, item_id=item_id, op=op, old=old, new=new,
                                  at=_iso(moment), before_digest=before_digest,
                                  after_digest=after_digest, reconciled=reconciled,
                                  reason="read-back did not match")
            audit_state = "none"
            if audit is not None:
                try:
                    audit_state = audit(updated, old, new) or "ok"
                except Exception:  # reported, never a condition of "saved"
                    log.exception("queue_store: audit insert failed for item %s", item_id)
                    audit_state = "failed"
            rid = receipt_id(moment, after_digest)
            self._append({"kind": "completed", "op_id": op_id, "receipt_id": rid,
                          "audit": audit_state, "at": _iso(self._clock())})
            return SaveResult(SAVED, item_id=item_id, op=op, old=old, new=new,
                              at=_iso(moment), receipt_id=rid, verified=True,
                              audit=audit_state, before_digest=before_digest,
                              after_digest=after_digest, current_item=updated,
                              current_item_digest=item_digest(updated),
                              reconciled=reconciled)
        finally:
            self._release(held)

    def save_status(self, item_id: int, to: str, *, expect_item_digest: str | None,
                    principal: str, note: str | None = None, via: str = "legacy",
                    idempotency_key: str | None = None,
                    audit: Callable[[dict, Any, Any], str] | None = None) -> SaveResult:
        def mutate(item: dict):
            if to not in STATUSES:
                raise ValueError("unknown status")
            old = item.get("status")
            item["status"] = to
            item["last_transition_at"] = _iso(self._clock())
            item["blocked_reason"] = note if to == "blocked" else None
            return old, to
        return self._update(op="status", item_id=item_id, expect_item_digest=expect_item_digest,
                            mutate=mutate, principal=principal, via=via,
                            idempotency_key=idempotency_key, audit=audit)

    def edit_metadata(self, item_id: int, fields: dict, *, expect_item_digest: str | None,
                      principal: str, via: str = "legacy",
                      idempotency_key: str | None = None) -> SaveResult:
        def mutate(item: dict):
            unknown = set(fields) - set(EDITABLE_FIELDS)
            if unknown:
                raise ValueError("not an editable field")
            old = {name: item.get(name) for name in fields}
            item.update(fields)
            return old, dict(fields)
        return self._update(op="edit", item_id=item_id, expect_item_digest=expect_item_digest,
                            mutate=mutate, principal=principal, via=via,
                            idempotency_key=idempotency_key)
