"""The Guild Build Queue's only writer: a verified Save with a receipt.

Every write to ``build_queue.json`` goes through :class:`QueueStore`. Under one
exclusive lock (a sidecar ``.<name>.lock`` file in the queue's own folder, so
the lock survives the file's inode changing on replace) a write does:

    reconcile earlier unfinished intents
    -> read and parse the file (the store never creates it)
    -> a reused idempotency key returns the first receipt only for the SAME
       change (``change_hash``); a different change under that key is refused
    -> per-item unchanged-check (compare-and-swap on the item's digest)
    -> append an ``intent`` journal line
    -> temp file in the same folder, fsync, os.replace, folder fsync
    -> read back and compare the file digest
    -> optional audit callback (reported, never a condition of "saved")
    -> append a ``completed`` journal line with the receipt

An operating-system error (lock, read, journal, temp file, replace) never
escapes as an exception: the Save returns ``failed`` and the intent, if one was
written, is resolved in the journal as ``failed`` with the error's class, as
long as the live file still has its old digest (otherwise ``uncertain``).

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
    # Guild 1.1 slice 2 (spec §4.2): review or testing found something to
    # correct. Additive; nothing is renamed or collapsed.
    "rework",
)
EDITABLE_FIELDS = ("spec_title", "summary", "github_issue")

# "Trouble" is blocked or rework (spec §4.2). While in trouble an item carries
# trouble_reason (required, <= 500 characters), trouble_from (the last
# non-trouble status, never overwritten by blocked <-> rework) and
# trouble_since. They are cleared on the live item when it leaves trouble;
# the journal keeps the full history.
TROUBLE = ("blocked", "rework")
TROUBLE_FIELDS = ("trouble_reason", "trouble_from", "trouble_since")
REASON_MAX = 500
# Owner direction 2026-10-02: positive integer groups; ties and gaps allowed.
MAX_SAFE_RANK = 9007199254740991  # lossless round-trip through browser JSON
# New items (spec §4.5) start early in the lifecycle only.
CREATE_STATUSES = ("idea", "design", "backlog")
TITLE_MAX = 200

# Result codes. The portal maps each to one fixed sentence.
SAVED = "saved"
CONFLICT = "conflict"
BUSY = "busy"
UNCERTAIN = "uncertain"
UNAVAILABLE = "unavailable"
REFUSED = "refused"          # M2: this process may not write the queue
NOT_FOUND = "not_found"
INVALID = "invalid"
FAILED = "failed"            # an OS error; the live file was left untouched
IDEMPOTENCY_MISMATCH = "idempotency_mismatch"  # key reused for another change

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


def change_hash(op: str, item_id: int, change: dict) -> str:
    """SHA-256 of what a Save asks for: the operation, the item and the target
    field values. An idempotency key is only a repeat of the SAME change."""
    canonical = json.dumps({"op": op, "item_id": item_id, "change": change},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                           default=str)
    return sha256_bytes(canonical.encode("utf-8"))


def valid_rank(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_SAFE_RANK


def ranking(items: list) -> list[list[int]]:
    """Every ranked item as sorted ``[id, owner_rank]`` pairs (spec §4.3)."""
    return sorted([it["id"], it["owner_rank"]] for it in items
                  if isinstance(it, dict) and isinstance(it.get("id"), int)
                  and valid_rank(it.get("owner_rank")))


def rank_digest(items: list) -> str:
    """SHA-256 over the sorted (id, owner_rank) pairs of every ranked item: the
    collection's unchanged-check for a rank change (spec §4.3, R3)."""
    canonical = json.dumps(ranking(items), separators=(",", ":"))
    return sha256_bytes(canonical.encode("utf-8"))


def shift_ranks(items: list, item_id: int, rank: int | None) -> dict[int, tuple]:
    """Change only the selected item's group; duplicate ranks and gaps are valid."""
    target = next((it for it in items if isinstance(it, dict) and it.get("id") == item_id), {})
    before = target.get("owner_rank") if valid_rank(target.get("owner_rank")) else None
    return {item_id: (before, rank)} if before != rank else {}


def reason_class(exc: BaseException) -> str:
    """A short, stable class for an OS error, e.g. ``PermissionError/EACCES``."""
    code = errno.errorcode.get(getattr(exc, "errno", None) or 0)
    return f"{type(exc).__name__}/{code}" if code else type(exc).__name__


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
    ranking: list | None = None          # set_rank: the current [id, rank] pairs
    rank_digest: str | None = None       # set_rank: their digest
    changed: list | None = None          # set_rank: [{id, from, to}] for every moved item

    @property
    def ok(self) -> bool:
        return self.result == SAVED

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Plan:
    """A planned write: the whole new list, the item it is about, and what the
    journal intent should record beyond the standard fields."""
    items: list
    item_id: int
    old: Any
    new: Any
    item: dict | None
    intent: dict = field(default_factory=dict)


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
        except OSError:
            log.exception("queue_store: unlocking the queue failed (closing releases it)")
        finally:
            try:
                os.close(fd)
            except OSError:
                log.exception("queue_store: closing the queue lock failed")
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

    def _try_append(self, record: dict) -> bool:
        """Append, but never raise: used on paths already reporting an error."""
        try:
            self._append(record)
            return True
        except OSError:
            log.exception("queue_store: could not append a %s line to the journal",
                          record.get("kind"))
            return False

    def find_receipt(self, receipt: str | None, item_id: int | None = None) -> dict | None:
        """The journal's ``completed`` line for this receipt (and item), or None.

        The portal shows "Saved · verified" only when this finds the receipt,
        so a hand-made ``?receipt=`` link cannot fake a Save."""
        if not self.path or not receipt:
            return None
        try:
            lines = self._journal_lines()
        except OSError:
            return None
        intents = {r.get("op_id"): r for r in lines if r.get("kind") == "intent"}
        for record in lines:
            if record.get("kind") != "completed" or record.get("receipt_id") != receipt:
                continue
            intent = intents.get(record.get("op_id"))
            if intent is None:
                continue
            if item_id is not None and intent.get("item_id") != item_id \
                    and item_id not in {c.get("id") for c in intent.get("items") or [] if isinstance(c, dict)}:
                continue
            return {"op_id": record.get("op_id"), "item_id": intent.get("item_id"),
                    "op": intent.get("op"), "audit": record.get("audit"),
                    "at": record.get("at"), "recovered": bool(record.get("recovered"))}
        return None

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

    def mark_checked(self, op_id: str, principal: str) -> str:
        """Clear a Check. Returns ``checked``, ``refused``, ``invalid``, ``busy``
        or ``failed``; never raises on a busy lock or an OS error."""
        if self.write_problem():
            return REFUSED
        try:
            if op_id not in {c["op_id"] for c in self.unresolved_checks()}:
                return INVALID
            held = self._acquire()
        except QueueBusy:
            return BUSY
        except OSError:
            log.exception("queue_store: could not open the journal or lock to mark a Check")
            return FAILED
        try:
            self._append({"kind": "checked", "op_id": op_id, "principal": principal,
                          "at": _iso(self._clock())})
            return "checked"
        except OSError:
            log.exception("queue_store: could not append the checked line")
            return FAILED
        finally:
            self._release(held)

    # the write
    def _find_repeat(self, lines: Iterable[dict], idempotency_key: str | None, *,
                     op: str, item_id: int, requested_hash: str):
        """An earlier Save under this idempotency key, if it counts as a repeat.

        Only the SAME change (equal ``change_hash``) gets the first receipt
        back. The same key with a different change (a restored form, Back, an
        old hidden field) is refused as ``idempotency_mismatch``: never the
        earlier receipt, and nothing is written. An intent without a
        ``change_hash`` cannot be tied to a change, so it is a mismatch too."""
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
            may_have_applied = record.get("kind") == "completed" or (
                record.get("kind") == "outcome" and record.get("outcome") == UNCERTAIN)
            if may_have_applied and intent.get("change_hash") != requested_hash:
                return SaveResult(IDEMPOTENCY_MISMATCH, item_id=item_id, op=op,
                                  reason="the idempotency key was already used for a different change")
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
        except BaseException:
            try:
                os.unlink(tmp)  # a half-written temp never lingers
            except OSError:
                pass
            raise
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
                mutate: Callable[[dict], tuple], change: dict, principal: str,
                via: str, idempotency_key: str | None,
                audit: Callable[[dict, Any, Any], str] | None = None) -> SaveResult:
        """A one-item change behind that item's digest (status, edit)."""
        problem = self.write_problem()
        if problem:
            return SaveResult(REFUSED, item_id=item_id, op=op, reason=problem)
        if not expect_item_digest:
            return SaveResult(CONFLICT, item_id=item_id, op=op,
                              reason="no unchanged-check digest was sent")

        def plan(items: list):
            index = next((i for i, it in enumerate(items)
                          if isinstance(it, dict) and it.get("id") == item_id), None)
            if index is None or item_id == 0:
                return SaveResult(NOT_FOUND, item_id=item_id, op=op)
            current = items[index]
            current_digest = item_digest(current)
            if current_digest != expect_item_digest:
                return SaveResult(CONFLICT, item_id=item_id, op=op, current_item=current,
                                  current_item_digest=current_digest)
            updated = json.loads(json.dumps(current))
            outcome = mutate(updated)
            old, new = outcome[0], outcome[1]
            detail = outcome[2] if len(outcome) > 2 else {}
            items[index] = updated
            return _Plan(items=items, item_id=item_id, old=old, new=new, item=updated,
                         intent={"item_before_digest": current_digest, **detail})

        return self._commit(op=op, item_id=item_id, plan=plan, change=change, principal=principal,
                            via=via, idempotency_key=idempotency_key, audit=audit)

    def _commit(self, *, op: str, item_id: int, plan: Callable[[list], Any], change: dict,
                principal: str, via: str, idempotency_key: str | None,
                audit: Callable[[dict, Any, Any], str] | None = None) -> SaveResult:
        problem = self.write_problem()
        if problem:
            return SaveResult(REFUSED, item_id=item_id, op=op, reason=problem)
        try:
            held = self._acquire()
        except QueueBusy:
            return SaveResult(BUSY, item_id=item_id, op=op)
        except OSError as exc:  # the lock file could not be opened or locked
            log.exception("queue_store: could not take the queue lock for item %s", item_id)
            return SaveResult(FAILED, item_id=item_id, op=op, reason=reason_class(exc))
        try:
            return self._commit_locked(op=op, item_id=item_id, plan=plan, change=change,
                                       principal=principal, via=via,
                                       idempotency_key=idempotency_key, audit=audit)
        finally:
            self._release(held)

    def _commit_locked(self, *, op, item_id, plan, change, principal, via, idempotency_key,
                       audit) -> SaveResult:
        """Under the lock: reconcile, repeat check, plan the change on a fresh
        read (the plan does every unchanged-check), then intent, atomic write,
        read-back and receipt. One journal intent covers the whole change, even
        when it moves several items (a rank shift)."""
        # ── Before the intent: an OS error writes nothing and needs no outcome.
        try:
            raw = _read_bytes(self.path)
        except FileNotFoundError:
            return SaveResult(UNAVAILABLE, item_id=item_id, op=op,
                              reason="the queue file does not exist")
        except OSError as exc:
            log.exception("queue_store: could not read the queue for item %s", item_id)
            return SaveResult(FAILED, item_id=item_id, op=op, reason=reason_class(exc))
        before_digest = sha256_bytes(raw)
        requested_hash = change_hash(op, item_id, change)
        try:
            reconciled = self._reconcile_locked(before_digest)
            self._clean_stale_temps()
            lines = self._journal_lines()
        except OSError as exc:
            log.exception("queue_store: could not reconcile the journal for item %s", item_id)
            return SaveResult(FAILED, item_id=item_id, op=op, reason=reason_class(exc))
        repeat = self._find_repeat(lines, idempotency_key, op=op, item_id=item_id,
                                   requested_hash=requested_hash)
        if repeat is not None:
            repeat.reconciled = reconciled
            return repeat
        try:
            items = self._parse(raw)
        except QueueUnavailable as exc:
            return SaveResult(UNAVAILABLE, item_id=item_id, op=op, reason=str(exc),
                              reconciled=reconciled)
        try:
            planned = plan(items)
        except ValueError as exc:
            return SaveResult(INVALID, item_id=item_id, op=op, reason=str(exc),
                              reconciled=reconciled)
        if isinstance(planned, SaveResult):
            planned.reconciled = reconciled
            return planned
        item_id, old, new, updated = planned.item_id, planned.old, planned.new, planned.item
        data = serialize(planned.items)
        after_digest = sha256_bytes(data)
        op_id = uuid.uuid4().hex
        moment = self._clock()
        try:
            self._append({
                **planned.intent,
                "kind": "intent", "op_id": op_id, "op": op, "item_id": item_id,
                "from": old, "to": new, "before_digest": before_digest,
                "after_digest": after_digest,
                "change_hash": requested_hash,
                "principal": principal, "via": via, "idempotency_key": idempotency_key,
                "at": _iso(moment),
            })
        except OSError as exc:
            log.exception("queue_store: could not journal the intent for item %s", item_id)
            self._try_append({"kind": "outcome", "op_id": op_id, "outcome": FAILED,
                              "item_id": item_id, "reason_class": reason_class(exc),
                              "at": _iso(self._clock())})
            return SaveResult(FAILED, item_id=item_id, op=op, reason=reason_class(exc),
                              reconciled=reconciled)

        # ── After the intent: temp file, replace, folder fsync.
        try:
            self._write_atomic(data, os.stat(self.path))
        except OSError as exc:
            return self._resolve_write_error(exc, op=op, op_id=op_id, item_id=item_id,
                                             before_digest=before_digest,
                                             after_digest=after_digest, old=old, new=new,
                                             moment=moment, reconciled=reconciled)
        try:
            readback = sha256_bytes(_read_bytes(self.path))
        except OSError:
            readback = None
        if readback != after_digest:
            self._try_append({"kind": "outcome", "op_id": op_id, "outcome": UNCERTAIN,
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
        try:
            self._append({"kind": "completed", "op_id": op_id, "receipt_id": rid,
                          "audit": audit_state, "at": _iso(self._clock())})
        except OSError as exc:
            # The file holds the change and read back correctly, but there is no
            # receipt to show. The next write reconciles it as a recovered
            # completion; until then the owner is told to check the item.
            log.exception("queue_store: could not journal the receipt for item %s", item_id)
            return SaveResult(UNCERTAIN, item_id=item_id, op=op, old=old, new=new,
                              at=_iso(moment), before_digest=before_digest,
                              after_digest=after_digest, reconciled=reconciled,
                              reason=f"the receipt could not be journalled ({reason_class(exc)})")
        result = SaveResult(SAVED, item_id=item_id, op=op, old=old, new=new,
                            at=_iso(moment), receipt_id=rid, verified=True,
                            audit=audit_state, before_digest=before_digest,
                            after_digest=after_digest, current_item=updated,
                            current_item_digest=item_digest(updated) if updated is not None else None,
                            reconciled=reconciled)
        if op == "rank":
            result.ranking = ranking(planned.items)
            result.rank_digest = rank_digest(planned.items)
            result.changed = planned.intent.get("items")
        return result

    def _resolve_write_error(self, exc: OSError, *, op, op_id, item_id, before_digest,
                             after_digest, old, new, moment, reconciled) -> SaveResult:
        """An OS error during temp file, replace or folder fsync.

        If the live file still has its old digest, nothing changed: the intent
        is resolved ``failed`` with the error's class. Anything else (the
        replace happened but the folder fsync failed, or the file cannot be
        read) is ``uncertain`` and becomes a Check."""
        cls = reason_class(exc)
        log.error("queue_store: writing the queue failed for item %s: %s", item_id, cls)
        try:
            current = sha256_bytes(_read_bytes(self.path))
        except OSError:
            current = None
        if current == before_digest:
            self._try_append({"kind": "outcome", "op_id": op_id, "outcome": FAILED,
                              "item_id": item_id, "reason_class": cls,
                              "at": _iso(self._clock())})
            return SaveResult(FAILED, item_id=item_id, op=op, reason=cls,
                              before_digest=before_digest, reconciled=reconciled)
        self._try_append({"kind": "outcome", "op_id": op_id, "outcome": UNCERTAIN,
                          "item_id": item_id, "reason_class": cls,
                          "at": _iso(self._clock())})
        return SaveResult(UNCERTAIN, item_id=item_id, op=op, old=old, new=new,
                          at=_iso(moment), before_digest=before_digest,
                          after_digest=after_digest, reconciled=reconciled, reason=cls)

    def save_status(self, item_id: int, to: str, *, expect_item_digest: str | None,
                    principal: str, note: str | None = None, via: str = "legacy",
                    idempotency_key: str | None = None,
                    audit: Callable[[dict, Any, Any], str] | None = None) -> SaveResult:
        """A status Save, enforcing the trouble rules of spec §4.2:

        * non-trouble -> blocked or rework: a reason is required;
          trouble_from = the old status, trouble_since = now;
        * blocked <-> rework: a reason is required; trouble_from and
          trouble_since are kept (never overwritten with blocked or rework);
        * the same trouble status with a new reason: a reason edit; the journal
          keeps the old reason;
        * trouble -> non-trouble: only by an explicit Save like this one; the
          trouble fields are cleared on the live item.

        ``blocked_reason`` keeps its legacy meaning: the reason while blocked,
        cleared on leaving blocked. A rework reason lives only in trouble_reason."""
        reason = note.strip() if isinstance(note, str) and note.strip() else None

        def mutate(item: dict):
            if to not in STATUSES:
                raise ValueError("unknown status")
            old = item.get("status")
            now = _iso(self._clock())
            reason_before = item.get("trouble_reason") or item.get("blocked_reason") \
                if old in TROUBLE else None
            detail = {"reason_from": reason_before}
            if to in TROUBLE:
                if not reason:
                    raise ValueError("a reason is required for blocked or rework")
                if len(reason) > REASON_MAX:
                    raise ValueError(f"the reason is longer than {REASON_MAX} characters")
                if old not in TROUBLE:
                    item["trouble_from"] = old
                    item["trouble_since"] = now
                # blocked <-> rework, or a reason edit: trouble_from and
                # trouble_since stay as they are.
                item["trouble_reason"] = reason
                detail.update({"reason_to": reason, "trouble_from": item.get("trouble_from"),
                               "reason_edit": old == to})
            else:
                if old in TROUBLE:
                    detail["left_trouble_from"] = item.get("trouble_from")
                for name in TROUBLE_FIELDS:
                    item.pop(name, None)
                detail["reason_to"] = None
            item["status"] = to
            item["last_transition_at"] = now
            item["blocked_reason"] = reason if to == "blocked" else None
            return old, to, detail
        change = {"status": to, "blocked_reason": reason if to == "blocked" else None}
        if to == "rework":
            change["trouble_reason"] = reason
        return self._update(op="status", item_id=item_id, expect_item_digest=expect_item_digest,
                            mutate=mutate, change=change, principal=principal, via=via,
                            idempotency_key=idempotency_key, audit=audit)

    def set_rank(self, item_id: int, rank: int | None, *, expect_rank_digest: str | None,
                 principal: str, via: str = "guild-next",
                 idempotency_key: str | None = None) -> SaveResult:
        """Set a positive whole-number group, or clear it, with a guarded journaled write.
        Other items retain their ranks. Owner direction 2026-10-02 supersedes
        the original three-slot shifting behavior; digest and recovery stay intact.
        """
        if rank is not None and not valid_rank(rank):
            return SaveResult(INVALID, item_id=item_id, op="rank", reason="a rank is a positive safe integer or none")
        if not expect_rank_digest:
            return SaveResult(CONFLICT, item_id=item_id, op="rank",
                              reason="no rank unchanged-check digest was sent")

        def plan(items: list):
            target = next((it for it in items if isinstance(it, dict) and it.get("id") == item_id), None)
            if target is None or item_id == 0:
                return SaveResult(NOT_FOUND, item_id=item_id, op="rank")
            before = rank_digest(items)
            if before != expect_rank_digest:
                return SaveResult(CONFLICT, item_id=item_id, op="rank", ranking=ranking(items),
                                  rank_digest=before, current_item=target,
                                  current_item_digest=item_digest(target))
            moves = shift_ranks(items, item_id, rank)
            updated = []
            for it in items:
                if isinstance(it, dict) and it.get("id") in moves:
                    it = json.loads(json.dumps(it))
                    after = moves[it["id"]][1]
                    if after is None:
                        it.pop("owner_rank", None)
                    else:
                        it["owner_rank"] = after
                updated.append(it)
            changed = [{"id": i, "from": b, "to": a} for i, (b, a) in sorted(moves.items())]
            item = next(it for it in updated if isinstance(it, dict) and it.get("id") == item_id)
            return _Plan(items=updated, item_id=item_id, old=moves.get(item_id, (rank, rank))[0], new=rank,
                         item=item, intent={"items": changed, "rank_before_digest": before,
                                            "rank_after_digest": rank_digest(updated)})

        return self._commit(op="rank", item_id=item_id, plan=plan, change={"rank": rank},
                            principal=principal, via=via, idempotency_key=idempotency_key)

    def create_item(self, spec_title: str, status: str = "idea", *, principal: str,
                    via: str = "guild-next", idempotency_key: str | None = None) -> SaveResult:
        """A new item (spec §4.5): id = max + 1 under the lock, journaled like
        any Save. Only idea, design or backlog. The queue stays a bare list."""
        title = spec_title.strip() if isinstance(spec_title, str) else ""
        if not title or len(title) > TITLE_MAX:
            return SaveResult(INVALID, op="create", reason=f"a title of 1 to {TITLE_MAX} characters is needed")
        if status not in CREATE_STATUSES:
            return SaveResult(INVALID, op="create", reason="a new item starts as idea, design or backlog")

        def plan(items: list):
            ids = [it["id"] for it in items if isinstance(it, dict) and isinstance(it.get("id"), int)
                   and not isinstance(it.get("id"), bool)]
            new_id = max(ids, default=0) + 1
            item = {"id": new_id, "spec_title": title, "spec_file": None, "status": status,
                    "blocked_reason": None, "last_transition_at": _iso(self._clock())}
            return _Plan(items=items + [item], item_id=new_id, old=None, new=status, item=item)

        return self._commit(op="create", item_id=0, plan=plan, change={"spec_title": title, "status": status},
                            principal=principal, via=via, idempotency_key=idempotency_key)

    def item_journal(self, item_id: int) -> list[dict]:
        """The journal's completed changes to one item, oldest first (spec §4.4):
        from, to, principal, via, at and receipt, with the reason change for a
        status Save and the rank change for a rank shift that moved it. A
        recovered completion is marked. Raises OSError when unreadable."""
        lines = self._journal_lines()
        intents = {r.get("op_id"): r for r in lines if r.get("kind") == "intent"}
        out = []
        for record in lines:
            if record.get("kind") != "completed":
                continue
            intent = intents.get(record.get("op_id"))
            if intent is None:
                continue
            entry = None
            if intent.get("item_id") == item_id:
                entry = {"from": intent.get("from"), "to": intent.get("to")}
            elif intent.get("op") == "rank":
                moved = next((c for c in intent.get("items") or []
                              if isinstance(c, dict) and c.get("id") == item_id), None)
                if moved is not None:
                    entry = {"from": moved.get("from"), "to": moved.get("to"), "shifted_by": intent.get("item_id")}
            if entry is None:
                continue
            entry.update({"op": intent.get("op"), "principal": intent.get("principal"),
                          "via": intent.get("via"), "at": intent.get("at"),
                          "receipt_id": record.get("receipt_id"),
                          "recovered": bool(record.get("recovered"))})
            if intent.get("op") == "status":
                entry.update({"reason_from": intent.get("reason_from"), "reason_to": intent.get("reason_to"),
                              "reason_edit": bool(intent.get("reason_edit"))})
            out.append(entry)
        return out

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
                            mutate=mutate, change=dict(fields), principal=principal, via=via,
                            idempotency_key=idempotency_key)
