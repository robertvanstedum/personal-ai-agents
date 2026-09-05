"""The canonical tree on disk: locks, publication, transactions, recovery.

This module knows where bytes go and how they get there safely. It does not
know what an effect *means*; it has no effect vocabulary at all.

Four properties are load-bearing, and each is a mechanism rather than a
convention:

*Immutable publication.* ``os.rename`` silently replaces an existing target,
so it can never express "publish this name only if nothing is there".
:func:`publish` writes a temp, fsyncs it, and hard-links it to the final
name: the link fails with ``EEXIST`` rather than overwriting, and the final
name never appears holding partial bytes. Every immutable canonical object
goes through it — captured sources, artifact revisions, the create
reservation, the pending object and the terminal marker alike. The only two
files ever *replaced* are ``work.json`` and a conversation binding, and those
use a digest-pinned candidate plus ``os.replace``, which is the correct
primitive for replacement.

*One pending object, one terminal marker.* An operation's state is derived
from which files exist, never from a field that has to be rewritten. The
pending object is the intent *and* the in-flight marker, published once, so
there is no window in which an intent exists but cannot be found. The
terminal marker has one name, so "exactly one terminal exists" is an
``O_EXCL`` property rather than an argument.

*The candidate is staged before anything canonical is published.* Recovery
never rebuilds a record from an intent — an intent deliberately omits the
private text a record carries — so it commits only from a digest-pinned
candidate it verified from the bytes it is about to install.

*One confined snapshot per internal file.* Digest and size always come from
the same bytes, read once through the no-follow descriptor walk. Two
observations of a pathname are not one snapshot, so no path-based digest
helper is ever the authority for a transaction here.
"""

from __future__ import annotations

import errno
import fcntl
import itertools
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from importlib import import_module

from . import records

#: The confinement *module*, deliberately not the package attribute of the
#: same name: the package re-exports ``confine``'s own ``confine`` function,
#: which would shadow it. Both gates are called through this module object,
#: which is also what a test replaces when it checks that the same limits
#: reach both of them.
confine = import_module(f"{__package__}.confine")
from .envelope import (
    ARTIFACT_CONTEXT_CLASSES,
    EFFECTS,
    SOURCE_CONTEXT_CLASSES,
    TERMINAL_OUTCOMES,
    Receipt,
    WorkError,
    freeze,
    is_identifier,
    is_uuid4,
)
from .retrieval import (
    CONVERSATIONS_DIRNAME,
    SUBJECTS_DIRNAME,
    WORK_DIRNAME,
    WORK_RECORD_FILENAME,
)

#: Bookkeeping files this module reads. They sit outside the caller-facing
#: extension set on purpose, so the two gates must be told about them.
INTERNAL_EXTENSIONS: frozenset[str] = frozenset({".json", ".candidate", ".tmp"})

#: The ceiling on any single internal file this module reads.
MAX_RECORD_BYTES = confine.DEFAULT_MAX_FILE_BYTES

#: Everything one work item may hold, across every captured source and every
#: artifact revision. The per-file cap is confine's; this is the total.
MAX_WORK_TOTAL_BYTES = 8 * 1024 * 1024

#: How many pending objects one recovery pass may act on, and how many
#: orphan publication temps one staging sweep may retire. Both are bounds on
#: work done, not on correctness: a pass that sweeps nothing still recovers
#: exactly what it would otherwise have recovered.
MAX_RECOVERED_OPERATIONS = 64
MAX_ORPHAN_TEMPS_SWEPT = 64

#: A pending object younger than this is reported, not acted on. Under the
#: lock nothing can be in flight, so this is belt-and-braces against a
#: pre-lock probe in a later caller.
RECOVERY_MIN_AGE_SECONDS = 60

LOCK_ATTEMPTS = 5
LOCK_RETRY_SECONDS = 0.1
LOCK_BUDGET_SECONDS = 0.5

CREATES_DIRNAME = "creates"
OPERATIONS_DIRNAME = "operations"
PENDING_DIRNAME = "pending"
STAGING_DIRNAME = "staging"
QUARANTINE_DIRNAME = "quarantine"
LOCK_FILENAME = ".lock"
CREATE_LOCK_FILENAME = ".create.lock"

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_PENDING_NAME = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.json$"
)
_ORPHAN_TEMP_NAME = re.compile(
    r"^([0-9a-f-]{36})\.json\.([0-9a-f-]{36})\.tmp$"
)

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
MAX_SLUG_CHARS = 40


class AlreadyPublished(Exception):
    """The canonical name this operation wanted already exists."""


def _checkpoint(step: str) -> None:
    """A named point in a transaction.

    Every publication boundary calls this before it acts. In ordinary use it
    does nothing; a test replaces it to inject a failure at one exact step,
    which is how the crash truth tables are exercised against the real code
    rather than against a simulation of it.
    """
    return None


def now_stamp(moment: datetime | None = None) -> str:
    """The UTC timestamp form every stored record uses."""
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def parse_stamp(value: str) -> datetime | None:
    """Read a stored timestamp back, or ``None`` when it is unreadable."""
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def slugify(hint: Any, fallback: str) -> str:
    """Reduce a caller's filename hint to the slug it may actually contribute."""
    if not isinstance(hint, str):
        return fallback
    slug = _SLUG_PATTERN.sub("-", hint.casefold()).strip("-")[:MAX_SLUG_CHARS].strip("-")
    return slug or fallback


def encode_json(document: Mapping[str, Any]) -> bytes:
    """One canonical byte form, so a digest over a document is stable."""
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


# -- the confined snapshot -------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    """One file's bytes, with the digest and size of *those* bytes."""

    relative_path: str
    sha256: str
    bytes: int
    raw: bytes


def snapshot(
    root: Path,
    relative_path: str,
    *,
    extensions: frozenset[str] = INTERNAL_EXTENSIONS,
    max_bytes: int = MAX_RECORD_BYTES,
) -> Snapshot:
    """Read one file once, through the no-follow walk, and describe it.

    Both gates get the same explicit limits. ``confine.confine`` applies its
    own extension and size checks before any read happens, so passing them to
    only one of the two calls would refuse every bookkeeping file before a
    byte was read — and would let the two gates drift apart if either default
    ever changed.
    """
    confined = confine.confine(
        root,
        relative_path,
        max_bytes=max_bytes,
        allowed_extensions=extensions,
    )
    raw = confine.read_bytes(
        confined,
        max_bytes=max_bytes,
        allowed_extensions=extensions,
    )
    return Snapshot(
        relative_path=confined.relative_path,
        sha256=confine.sha256_bytes(raw),
        bytes=len(raw),
        raw=raw,
    )


def try_snapshot(
    root: Path,
    relative_path: str,
    *,
    extensions: frozenset[str] = INTERNAL_EXTENSIONS,
    max_bytes: int = MAX_RECORD_BYTES,
) -> Snapshot | None:
    """The same read, with absence reported as ``None`` rather than raised."""
    try:
        return snapshot(root, relative_path, extensions=extensions, max_bytes=max_bytes)
    except confine.NotFound:
        return None
    except WorkError:
        raise


# -- directory and file primitives ----------------------------------------


def open_dir(path: Path) -> int:
    """Open a directory descriptor with no-follow semantics."""
    return os.open(str(path), _DIR_FLAGS)


def fsync_dir(path: Path) -> None:
    """Make a directory's own entries durable."""
    handle = open_dir(path)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def temp_name(final_basename: str, operation_id: str) -> str:
    """The one pre-link temp name an operation may use for a given final name."""
    return f"{final_basename}.{operation_id}.tmp"


def candidate_name(final_basename: str, operation_id: str) -> str:
    """The one digest-pinned candidate name an operation may stage."""
    return f"{final_basename}.{operation_id}.candidate"


def unlink_quietly(directory: Path, name: str) -> bool:
    """Remove one derived name, reporting whether it was there."""
    handle = open_dir(directory)
    try:
        os.unlink(name, dir_fd=handle)
        return True
    except FileNotFoundError:
        return False
    except IsADirectoryError:
        return False
    finally:
        os.close(handle)


def _raw_write(handle: int, view: memoryview) -> int:
    """One raw write syscall.

    The same seam as :func:`_checkpoint`: a test forces the short writes the
    kernel is allowed to make, against the real writer rather than a
    simulation of one.
    """
    return os.write(handle, view)


def write_all(handle: int, payload: bytes) -> None:
    """Write every byte of ``payload`` to ``handle``, or raise.

    ``os.write`` is permitted to write fewer bytes than it was given. Ignoring
    the count is how a truncated file gets fsynced, linked at its final
    canonical name and then confirmed by a terminal marker: the bytes on disk
    are not the bytes whose digest was pinned, and nothing downstream would
    notice. This is the only writer in this module, so there is one place
    where "the whole planned payload landed" is established.

    A zero-byte write with bytes still outstanding is treated as a failure
    rather than a reason to spin.
    """
    view = memoryview(payload)
    written = 0
    while written < len(view):
        count = _raw_write(handle, view[written:])
        if count <= 0:
            raise OSError(errno.EIO, "the file could not be written in full")
        written += count


def publish(
    final_dir: Path,
    final_name: str,
    payload: bytes,
    *,
    operation_id: str,
    staging_dir: Path | None = None,
) -> None:
    """Publish an immutable canonical name, or refuse because it exists.

    The bytes are written and fsynced before the name exists, and the name is
    created by ``os.link``, which fails with ``EEXIST`` instead of replacing.
    A crash anywhere in here leaves the final name absent and at most a
    derived temp, which is non-canonical, decides nothing, and is removed by
    the retry that finds its final name missing.
    """
    staging = Path(staging_dir) if staging_dir is not None else Path(final_dir)
    tmp = temp_name(final_name, operation_id)
    source_fd = open_dir(staging)
    try:
        destination_fd = source_fd if staging == Path(final_dir) else open_dir(Path(final_dir))
        try:
            try:
                os.unlink(tmp, dir_fd=source_fd)
            except FileNotFoundError:
                pass
            handle = os.open(
                tmp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=source_fd,
            )
            try:
                write_all(handle, payload)
                os.fsync(handle)
            finally:
                os.close(handle)
            os.fsync(source_fd)
            _checkpoint(f"link:{final_name}")
            try:
                os.link(tmp, final_name, src_dir_fd=source_fd, dst_dir_fd=destination_fd)
            except FileExistsError as exc:
                raise AlreadyPublished(final_name) from exc
            os.fsync(destination_fd)
            try:
                os.unlink(tmp, dir_fd=source_fd)
            except FileNotFoundError:
                pass
            os.fsync(source_fd)
        finally:
            if destination_fd != source_fd:
                os.close(destination_fd)
    finally:
        os.close(source_fd)


def stage_candidate(directory: Path, name: str, payload: bytes) -> None:
    """Write a digest-pinned candidate beside the file it will replace."""
    handle_dir = open_dir(directory)
    try:
        try:
            os.unlink(name, dir_fd=handle_dir)
        except FileNotFoundError:
            pass
        handle = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=handle_dir,
        )
        try:
            write_all(handle, payload)
            os.fsync(handle)
        finally:
            os.close(handle)
        os.fsync(handle_dir)
    finally:
        os.close(handle_dir)


def install_candidate(directory: Path, name: str, final_name: str) -> None:
    """Replace one of the two mutable files from its staged candidate."""
    os.replace(directory / name, directory / final_name)
    fsync_dir(directory)


def touch_lock(path: Path) -> None:
    """Create the work item's lock file as part of the work item itself.

    The lock target is never unlinked — unlinking it would let two processes
    lock two different inodes under one name — so it exists from the moment
    the directory does. Creating it here rather than on first use also means
    that merely *taking* the lock is not itself a change to the tree.
    """
    handle = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.close(handle)
    fsync_dir(path.parent)


def make_dir(path: Path) -> bool:
    """Create one owner-private directory, reporting whether it was new."""
    try:
        os.mkdir(str(path), 0o700)
    except FileExistsError:
        return False
    fsync_dir(path.parent)
    return True


# -- locks ------------------------------------------------------------------


_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


def _local_lock(key: str) -> threading.Lock:
    with _LOCAL_LOCKS_GUARD:
        lock = _LOCAL_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCAL_LOCKS[key] = lock
        return lock


class Lock:
    """One writer per work item, bounded by a single acquisition budget.

    ``flock`` is held per open file description, so the same process opening
    the lock file twice would acquire it twice and quietly defeat the
    guarantee. A process-local mutex closes that, and it uses the *same*
    bounded budget rather than blocking: a second in-process writer is told
    ``locked`` inside the budget instead of proceeding minutes later when the
    first one happens to finish.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._local = _local_lock(str(self._path))
        self._handle: int | None = None
        self._held_local = False

    def __enter__(self) -> "Lock":
        started = time.monotonic()
        if not self._local.acquire(blocking=True, timeout=LOCK_BUDGET_SECONDS):
            raise WorkError("locked", "another change to this work item is in progress")
        self._held_local = True
        try:
            handle = os.open(
                str(self._path),
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
            )
        except OSError as exc:
            self._release_local()
            raise WorkError("locked", "this work item could not be locked") from exc
        for attempt in range(LOCK_ATTEMPTS):
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._handle = handle
                return self
            except OSError as exc:
                if exc.errno not in (errno.EWOULDBLOCK, errno.EACCES, errno.EAGAIN):
                    os.close(handle)
                    self._release_local()
                    raise WorkError("locked", "this work item could not be locked") from exc
            spent = time.monotonic() - started
            if spent >= LOCK_BUDGET_SECONDS or attempt == LOCK_ATTEMPTS - 1:
                break
            time.sleep(min(LOCK_RETRY_SECONDS, LOCK_BUDGET_SECONDS - spent))
        os.close(handle)
        self._release_local()
        raise WorkError("locked", "another change to this work item is in progress")

    def _release_local(self) -> None:
        if self._held_local:
            self._local.release()
            self._held_local = False

    def __exit__(self, *exc_info: Any) -> None:
        if self._handle is not None:
            try:
                fcntl.flock(self._handle, fcntl.LOCK_UN)
            finally:
                os.close(self._handle)
                self._handle = None
        self._release_local()


# -- layout -----------------------------------------------------------------


@dataclass(frozen=True)
class SubjectPaths:
    """Where one subject's work, reservations and bindings live."""

    root: Path
    subject: str

    @property
    def base(self) -> Path:
        return self.root / SUBJECTS_DIRNAME / self.subject

    @property
    def work_base(self) -> Path:
        return self.base / WORK_DIRNAME

    @property
    def creates(self) -> Path:
        return self.base / CREATES_DIRNAME

    @property
    def conversations(self) -> Path:
        return self.base / CONVERSATIONS_DIRNAME

    @property
    def create_lock(self) -> Path:
        return self.base / CREATE_LOCK_FILENAME

    def binding_relative_path(self, conversation_id: str) -> str:
        return f"{CONVERSATIONS_DIRNAME}/{conversation_id}.json"


@dataclass(frozen=True)
class WorkPaths:
    """Every derived path inside one work item. No path is ever guessed."""

    directory: Path

    @property
    def record(self) -> Path:
        return self.directory / WORK_RECORD_FILENAME

    @property
    def sources(self) -> Path:
        return self.directory / records.SOURCES_DIRNAME

    @property
    def artifacts(self) -> Path:
        return self.directory / records.ARTIFACTS_DIRNAME

    @property
    def operations(self) -> Path:
        return self.directory / OPERATIONS_DIRNAME

    @property
    def pending(self) -> Path:
        return self.operations / PENDING_DIRNAME

    @property
    def staging(self) -> Path:
        return self.operations / STAGING_DIRNAME

    @property
    def quarantine(self) -> Path:
        return self.operations / QUARANTINE_DIRNAME

    @property
    def lock(self) -> Path:
        return self.directory / LOCK_FILENAME

    def terminal_name(self, operation_id: str) -> str:
        return f"{operation_id}.terminal.json"

    def terminal_relative(self, operation_id: str) -> str:
        return f"{OPERATIONS_DIRNAME}/{operation_id}.terminal.json"

    def pending_relative(self, operation_id: str) -> str:
        return f"{OPERATIONS_DIRNAME}/{PENDING_DIRNAME}/{operation_id}.json"

    def record_candidate(self, operation_id: str) -> str:
        return candidate_name(WORK_RECORD_FILENAME, operation_id)


#: The five directories a work item is made of. ``operations/staging/`` holds
#: pre-link publication temps and nothing else, so the one namespace the
#: recovery scan enumerates can never contain a name that scan must skip.
CREATE_SUBDIRECTORIES: tuple[str, ...] = (
    records.SOURCES_DIRNAME,
    records.ARTIFACTS_DIRNAME,
    OPERATIONS_DIRNAME,
    f"{OPERATIONS_DIRNAME}/{PENDING_DIRNAME}",
    f"{OPERATIONS_DIRNAME}/{STAGING_DIRNAME}",
)


# -- the pending object and the terminal marker ----------------------------


@dataclass(frozen=True)
class Intent:
    """The pending object: one operation's intent *and* its in-flight marker."""

    operation_id: str
    effect: str
    work_id: str
    subject: str
    request_sha256: str
    record_sha256_before: str | None
    record_candidate_sha256: str
    created_at: str
    target_relative_path: str | None = None
    output_sha256: str | None = None
    output_bytes: int | None = None
    ref: str | None = None
    revision: int | None = None
    context_class: str | None = None
    supersedes_ref: str | None = None
    expected_inputs: tuple[Mapping[str, str], ...] = ()
    binding_relative_path: str | None = None
    binding_sha256_before: str | None = None
    binding_candidate_sha256: str | None = None
    #: The receipt this operation validated at P4, before anything was
    #: published. It is content-free by construction, and carrying it here is
    #: what lets recovery commit an operation forward without reconstructing
    #: a record: a metadata-only write's receipt names a pending id, a
    #: proposed state and an expiry that the reduced intent could not
    #: otherwise state.
    receipt: Receipt | None = None

    @property
    def writes_content(self) -> bool:
        return self.target_relative_path is not None

    @property
    def writes_binding(self) -> bool:
        return self.binding_relative_path is not None

    def as_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema_version": 1,
            "operation_id": self.operation_id,
            "effect": self.effect,
            "work_id": self.work_id,
            "subject": self.subject,
            "request_sha256": self.request_sha256,
            "record_sha256_before": self.record_sha256_before,
            "record_candidate_sha256": self.record_candidate_sha256,
            "created_at": self.created_at,
        }
        if self.target_relative_path is not None:
            document["target_relative_path"] = self.target_relative_path
            document["output_sha256"] = self.output_sha256
            document["output_bytes"] = self.output_bytes
            document["ref"] = self.ref
            document["revision"] = self.revision
            document["context_class"] = self.context_class
            document["supersedes_ref"] = self.supersedes_ref
            document["expected_inputs"] = [dict(entry) for entry in self.expected_inputs]
        if self.binding_relative_path is not None:
            document["binding_relative_path"] = self.binding_relative_path
            document["binding_sha256_before"] = self.binding_sha256_before
            document["binding_candidate_sha256"] = self.binding_candidate_sha256
        if self.receipt is not None:
            document["receipt"] = self.receipt.as_dict()
        return document


#: The exact key set a pending object always carries.
_INTENT_REQUIRED: tuple[str, ...] = (
    "schema_version",
    "operation_id",
    "effect",
    "work_id",
    "subject",
    "request_sha256",
    "record_sha256_before",
    "record_candidate_sha256",
    "created_at",
)

#: The all-or-nothing group a content-writing operation adds.
_INTENT_CONTENT_GROUP: tuple[str, ...] = (
    "target_relative_path",
    "output_sha256",
    "output_bytes",
    "ref",
    "revision",
    "context_class",
    "supersedes_ref",
    "expected_inputs",
)

#: The all-or-nothing group a binding-writing operation adds.
_INTENT_BINDING_GROUP: tuple[str, ...] = (
    "binding_relative_path",
    "binding_sha256_before",
    "binding_candidate_sha256",
)

_TERMINAL_REQUIRED: tuple[str, ...] = (
    "schema_version",
    "operation_id",
    "outcome",
    "request_sha256",
)

_RESERVATION_REQUIRED: tuple[str, ...] = (
    "schema_version",
    "operation_id",
    "work_id",
    "subject",
    "request_sha256",
    "work_dirname",
    "record_sha256_before",
    "record_candidate_sha256",
    "binding_relative_path",
    "binding_sha256_before",
    "binding_candidate_sha256",
    "reserved_at",
    "receipt",
)

#: The effects that publish bytes of their own beside the record, and the
#: subtree each one is allowed to have written into. An operation record that
#: claims a source was written into ``artifacts/`` is not a record this
#: service ever wrote, whatever else it says.
_CONTENT_SUBTREE: dict[str, str] = {
    "attach_source": records.SOURCES_DIRNAME,
    "write_artifact": records.ARTIFACTS_DIRNAME,
    "use_robert_edit": records.ARTIFACTS_DIRNAME,
}

#: The closed set of reasons recovery may quarantine for.
QUARANTINE_REASON_CODES: frozenset[str] = frozenset(
    {
        "record_changed_underneath",
        "recorded_bytes_changed",
        "binding_candidate_lost",
        "unreferenced_output",
        "record_candidate_lost",
    }
)

_STAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DERIVED_REF_PATTERN = re.compile(r"^(?:src|art)-[0-9]{4,}$")

#: How many pinned inputs one artifact record may name.
MAX_EXPECTED_INPUTS = 64


class ControlRecordInvalid(WorkError):
    """A stored transaction-control record failed exact validation.

    A pending object, a terminal marker and a create reservation are not
    inputs a caller supplies; they are files this service wrote. They still
    drive recovery mutations and duplicate answers, so they are validated as
    exactly the bound schemas the design defines rather than trusted because
    of where they were found. Failing here is a closed Work error: nothing is
    installed, no path derived from the unvalidated content is cleaned, and
    no success is reported.
    """

    def __init__(self, message: str) -> None:
        super().__init__("invalid_request", message)


def _control_object(document: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(document, Mapping):
        raise ControlRecordInvalid(f"{what} must be an object")
    for key in document:
        if not isinstance(key, str):
            raise ControlRecordInvalid(f"{what} has a field name that is not text")
    return document


def _control_keys(
    document: Mapping[str, Any],
    required: Sequence[str],
    optional: Sequence[str],
    what: str,
) -> None:
    keys = set(document)
    unknown = sorted(keys - set(required) - set(optional))
    if unknown:
        raise ControlRecordInvalid(
            f"{what} has fields that are not part of the contract: {', '.join(unknown)}"
        )
    missing = sorted(set(required) - keys)
    if missing:
        raise ControlRecordInvalid(f"{what} is missing: {', '.join(missing)}")


def _control_schema_version(document: Mapping[str, Any], what: str) -> int:
    value = document.get("schema_version")
    if value != 1:
        raise ControlRecordInvalid(f"{what} is not a version-1 record")
    return 1


def _control_uuid(value: Any, key: str, what: str) -> str:
    if not is_uuid4(value):
        raise ControlRecordInvalid(f"{what} needs a UUID4 {key}")
    return str(value)


def _control_digest(value: Any, key: str, what: str) -> str:
    if not isinstance(value, str) or records.SHA256_PATTERN.fullmatch(value) is None:
        raise ControlRecordInvalid(f"{what} needs a lowercase hex sha256 digest for {key}")
    return value


def _control_optional_digest(value: Any, key: str, what: str) -> str | None:
    if value is None:
        return None
    return _control_digest(value, key, what)


def _control_stamp(value: Any, key: str, what: str) -> str:
    if not isinstance(value, str) or _STAMP_PATTERN.fullmatch(value) is None:
        raise ControlRecordInvalid(f"{what} needs a UTC timestamp for {key}")
    return value


def _control_identifier(value: Any, key: str, what: str) -> str:
    if not is_identifier(value):
        raise ControlRecordInvalid(f"{what} needs a valid name for {key}")
    return str(value)


def _control_relative(value: Any, key: str, what: str, *, subtree: str | None = None) -> str:
    """A stored path that must stay inside the tree it names."""
    if not isinstance(value, str) or not value:
        raise ControlRecordInvalid(f"{what} needs a relative path for {key}")
    parts = value.split("/")
    if (
        value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or ".." in parts
        or "." in parts
        or "" in parts
        or any(part.strip() != part for part in parts)
    ):
        raise ControlRecordInvalid(f"{what} needs a relative path inside the work item for {key}")
    if subtree is not None and (len(parts) < 2 or parts[0] != subtree):
        raise ControlRecordInvalid(f"{what} needs a path inside {subtree}/ for {key}")
    return value


def _control_ref(value: Any, key: str, what: str, *, prefix: str | None = None) -> str:
    if not isinstance(value, str) or _DERIVED_REF_PATTERN.fullmatch(value) is None:
        raise ControlRecordInvalid(f"{what} needs a record reference for {key}")
    if prefix is not None and not value.startswith(prefix):
        raise ControlRecordInvalid(f"{what} names a reference of the wrong kind for {key}")
    return value


def _control_int(value: Any, key: str, what: str, *, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ControlRecordInvalid(f"{what} needs a whole number for {key}")
    return value


def _control_binding_path(value: Any, what: str) -> str:
    relative = _control_relative(
        value, "binding_relative_path", what, subtree=CONVERSATIONS_DIRNAME
    )
    parts = relative.split("/")
    if len(parts) != 2 or not parts[1].endswith(".json"):
        raise ControlRecordInvalid(f"{what} needs a conversation binding path")
    _control_identifier(parts[1][: -len(".json")], "conversation_id", what)
    return relative


def _control_group(document: Mapping[str, Any], group: Sequence[str], what: str) -> bool:
    """True when a conditional group is wholly present, refusing half of one."""
    present = [key for key in group if key in document]
    if not present:
        return False
    if len(present) != len(group):
        missing = sorted(set(group) - set(present))
        raise ControlRecordInvalid(f"{what} is missing: {', '.join(missing)}")
    return True


def _control_expected_inputs(value: Any, what: str) -> tuple[Mapping[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ControlRecordInvalid(f"{what} needs a list for expected_inputs")
    if len(value) > MAX_EXPECTED_INPUTS:
        raise ControlRecordInvalid(f"{what} names too many pinned inputs")
    entries: list[Mapping[str, str]] = []
    for item in value:
        entry = _control_object(item, f"{what} pinned input")
        _control_keys(entry, ("ref", "sha256"), (), f"{what} pinned input")
        entries.append(
            freeze(
                {
                    "ref": _control_ref(entry.get("ref"), "ref", f"{what} pinned input"),
                    "sha256": _control_digest(
                        entry.get("sha256"), "sha256", f"{what} pinned input"
                    ),
                }
            )
        )
    return tuple(entries)


def parse_intent(document: Any) -> Intent:
    """Read a pending object back as the exact bound schema it must be."""
    what = "an operation record"
    data = _control_object(document, what)
    writes_content = _control_group(data, _INTENT_CONTENT_GROUP, what)
    writes_binding = _control_group(data, _INTENT_BINDING_GROUP, what)
    optional = ["receipt"]
    if writes_content:
        optional.extend(_INTENT_CONTENT_GROUP)
    if writes_binding:
        optional.extend(_INTENT_BINDING_GROUP)
    _control_keys(data, _INTENT_REQUIRED, optional, what)
    _control_schema_version(data, what)

    operation_id = _control_uuid(data.get("operation_id"), "operation_id", what)
    effect = data.get("effect")
    if effect not in EFFECTS:
        raise ControlRecordInvalid(f"{what} names an operation that does not exist")
    work_id = _control_uuid(data.get("work_id"), "work_id", what)
    subject = _control_identifier(data.get("subject"), "subject", what)

    target_relative_path = None
    output_sha256 = None
    output_bytes = None
    ref = None
    revision = None
    context_class = None
    supersedes_ref = None
    expected_inputs: tuple[Mapping[str, str], ...] = ()
    if writes_content:
        subtree = _CONTENT_SUBTREE.get(str(effect))
        if subtree is None:
            raise ControlRecordInvalid(f"{what} claims content this operation never writes")
        target_relative_path = _control_relative(
            data.get("target_relative_path"), "target_relative_path", what, subtree=subtree
        )
        output_sha256 = _control_digest(data.get("output_sha256"), "output_sha256", what)
        output_bytes = _control_int(data.get("output_bytes"), "output_bytes", what, minimum=0)
        is_source = subtree == records.SOURCES_DIRNAME
        ref = _control_ref(data.get("ref"), "ref", what, prefix="src-" if is_source else "art-")
        if data.get("revision") is not None:
            revision = _control_int(data.get("revision"), "revision", what, minimum=1)
        if is_source and revision is not None:
            raise ControlRecordInvalid(f"{what} gives a captured source a revision")
        if not is_source and revision is None:
            raise ControlRecordInvalid(f"{what} is missing: revision")
        context_class = data.get("context_class")
        permitted = SOURCE_CONTEXT_CLASSES if is_source else ARTIFACT_CONTEXT_CLASSES
        if context_class not in permitted:
            raise ControlRecordInvalid(f"{what} has an unknown provenance class")
        if data.get("supersedes_ref") is not None:
            supersedes_ref = _control_ref(
                data.get("supersedes_ref"), "supersedes_ref", what, prefix="art-"
            )
            if is_source:
                raise ControlRecordInvalid(f"{what} says a captured source supersedes something")
        expected_inputs = _control_expected_inputs(data.get("expected_inputs"), what)

    binding_relative_path = None
    binding_sha256_before = None
    binding_candidate_sha256 = None
    if writes_binding:
        binding_relative_path = _control_binding_path(data.get("binding_relative_path"), what)
        binding_sha256_before = _control_optional_digest(
            data.get("binding_sha256_before"), "binding_sha256_before", what
        )
        binding_candidate_sha256 = _control_digest(
            data.get("binding_candidate_sha256"), "binding_candidate_sha256", what
        )

    intent = Intent(
        operation_id=operation_id,
        effect=str(effect),
        work_id=work_id,
        subject=subject,
        request_sha256=_control_digest(data.get("request_sha256"), "request_sha256", what),
        record_sha256_before=_control_optional_digest(
            data.get("record_sha256_before"), "record_sha256_before", what
        ),
        record_candidate_sha256=_control_digest(
            data.get("record_candidate_sha256"), "record_candidate_sha256", what
        ),
        created_at=_control_stamp(data.get("created_at"), "created_at", what),
        target_relative_path=target_relative_path,
        output_sha256=output_sha256,
        output_bytes=output_bytes,
        ref=ref,
        revision=revision,
        context_class=context_class,
        supersedes_ref=supersedes_ref,
        expected_inputs=expected_inputs,
        binding_relative_path=binding_relative_path,
        binding_sha256_before=binding_sha256_before,
        binding_candidate_sha256=binding_candidate_sha256,
        receipt=_receipt_from_document(data.get("receipt"), what),
    )
    if intent.receipt is not None:
        _require_receipt_matches(
            intent.receipt,
            what,
            operation_id=operation_id,
            effect=str(effect),
            subject=subject,
            work_id=work_id,
            relative_path=target_relative_path,
            sha256=output_sha256,
            ref=ref,
        )
    return intent


def _receipt_from_document(payload: Any, what: str) -> Receipt | None:
    """Read a stored receipt back through the validated constructor."""
    if payload is None:
        return None
    data = _control_object(payload, f"{what} receipt")
    for key in ("operation_id", "effect", "outcome"):
        if key not in data:
            raise ControlRecordInvalid(f"{what} receipt is missing: {key}")
    fields = {
        key: value
        for key, value in data.items()
        if key not in ("operation_id", "effect", "outcome")
    }
    try:
        return Receipt(
            operation_id=data["operation_id"],
            effect=data["effect"],
            outcome=data["outcome"],
            fields=fields,
        )
    except WorkError as exc:
        raise ControlRecordInvalid(f"{what} carries a receipt that is not valid") from exc


def _require_receipt_matches(
    receipt: Receipt,
    what: str,
    *,
    operation_id: str,
    effect: str | None = None,
    subject: str | None = None,
    work_id: str | None = None,
    relative_path: str | None = None,
    sha256: str | None = None,
    ref: str | None = None,
) -> None:
    """A stored receipt must describe the very record that carries it."""
    if receipt.operation_id != operation_id:
        raise ControlRecordInvalid(f"{what} carries a receipt for another operation")
    if effect is not None and receipt.effect != effect:
        raise ControlRecordInvalid(f"{what} carries a receipt for another operation")
    for key, expected in (
        ("subject", subject),
        ("work_id", work_id),
        ("relative_path", relative_path),
        ("sha256", sha256),
        ("ref", ref),
    ):
        if expected is None:
            continue
        actual = receipt.fields.get(key)
        if actual is not None and actual != expected:
            raise ControlRecordInvalid(f"{what} carries a receipt about different work")


@dataclass(frozen=True)
class Terminal:
    """The single terminal marker: what happened, and the receipt if any."""

    operation_id: str
    outcome: str
    request_sha256: str
    receipt: Receipt | None
    reason_code: str | None = None
    relative_path: str | None = None

    def as_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema_version": 1,
            "operation_id": self.operation_id,
            "outcome": self.outcome,
            "request_sha256": self.request_sha256,
        }
        if self.receipt is not None:
            document["receipt"] = self.receipt.as_dict()
        if self.reason_code is not None:
            document["reason_code"] = self.reason_code
        if self.relative_path is not None:
            document["relative_path"] = self.relative_path
        return document


def parse_terminal(document: Any) -> Terminal:
    """Read a terminal marker back as the exact bound schema it must be."""
    what = "a terminal marker"
    data = _control_object(document, what)
    _control_keys(
        data, _TERMINAL_REQUIRED, ("receipt", "reason_code", "relative_path"), what
    )
    _control_schema_version(data, what)
    operation_id = _control_uuid(data.get("operation_id"), "operation_id", what)
    outcome = data.get("outcome")
    if outcome not in TERMINAL_OUTCOMES:
        raise ControlRecordInvalid(f"{what} records an unknown outcome")
    request_sha256 = _control_digest(data.get("request_sha256"), "request_sha256", what)
    receipt = _receipt_from_document(data.get("receipt"), what)
    reason_code = data.get("reason_code")
    relative_path = data.get("relative_path")

    if outcome == "committed":
        if receipt is None:
            raise ControlRecordInvalid(f"{what} says committed and carries no receipt")
        if receipt.outcome != "committed":
            raise ControlRecordInvalid(f"{what} says committed over a receipt that does not")
        if reason_code is not None or relative_path is not None:
            raise ControlRecordInvalid(f"{what} says committed and also gives a failure reason")
        _require_receipt_matches(receipt, what, operation_id=operation_id)
    else:
        if receipt is not None:
            raise ControlRecordInvalid(f"{what} did not commit and may carry no receipt")
    if outcome == "quarantined":
        if reason_code not in QUARANTINE_REASON_CODES:
            raise ControlRecordInvalid(f"{what} gives an unknown quarantine reason")
        relative_path = _control_relative(relative_path, "relative_path", what)
    elif outcome == "abandoned":
        if reason_code is not None or relative_path is not None:
            raise ControlRecordInvalid(f"{what} was abandoned and names nothing")

    return Terminal(
        operation_id=operation_id,
        outcome=str(outcome),
        request_sha256=request_sha256,
        receipt=receipt,
        reason_code=reason_code if outcome == "quarantined" else None,
        relative_path=relative_path if outcome == "quarantined" else None,
    )


@dataclass(frozen=True)
class Reservation:
    """The content-free create reservation that owns one operation id."""

    operation_id: str
    work_id: str
    subject: str
    request_sha256: str
    work_dirname: str
    record_candidate_sha256: str
    reserved_at: str
    receipt: Receipt
    record_sha256_before: str | None = None
    binding_relative_path: str | None = None
    binding_sha256_before: str | None = None
    binding_candidate_sha256: str | None = None

    def as_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "operation_id": self.operation_id,
            "work_id": self.work_id,
            "subject": self.subject,
            "request_sha256": self.request_sha256,
            "work_dirname": self.work_dirname,
            "record_sha256_before": self.record_sha256_before,
            "record_candidate_sha256": self.record_candidate_sha256,
            "binding_relative_path": self.binding_relative_path,
            "binding_sha256_before": self.binding_sha256_before,
            "binding_candidate_sha256": self.binding_candidate_sha256,
            "reserved_at": self.reserved_at,
            "receipt": self.receipt.as_dict(),
        }


def parse_reservation(document: Any) -> Reservation:
    """Read a create reservation back as the exact bound schema it must be."""
    what = "a create reservation"
    data = _control_object(document, what)
    _control_keys(data, _RESERVATION_REQUIRED, (), what)
    _control_schema_version(data, what)
    operation_id = _control_uuid(data.get("operation_id"), "operation_id", what)
    work_id = _control_uuid(data.get("work_id"), "work_id", what)
    subject = _control_identifier(data.get("subject"), "subject", what)

    work_dirname = data.get("work_dirname")
    suffix = f"--{work_id}"
    if (
        not isinstance(work_dirname, str)
        or not work_dirname.endswith(suffix)
        or not is_identifier(work_dirname[: -len(suffix)])
    ):
        raise ControlRecordInvalid(f"{what} does not name a directory it could have reserved")

    if data.get("record_sha256_before") is not None:
        raise ControlRecordInvalid(f"{what} claims a record that existed before the work item")

    binding_relative_path = data.get("binding_relative_path")
    binding_sha256_before = data.get("binding_sha256_before")
    binding_candidate_sha256 = data.get("binding_candidate_sha256")
    if binding_relative_path is None:
        if binding_sha256_before is not None or binding_candidate_sha256 is not None:
            raise ControlRecordInvalid(f"{what} pins a binding it does not name")
    else:
        binding_relative_path = _control_binding_path(binding_relative_path, what)
        binding_sha256_before = _control_optional_digest(
            binding_sha256_before, "binding_sha256_before", what
        )
        binding_candidate_sha256 = _control_digest(
            binding_candidate_sha256, "binding_candidate_sha256", what
        )

    receipt = _receipt_from_document(data.get("receipt"), what)
    if receipt is None:
        raise ControlRecordInvalid(f"{what} is missing: receipt")
    if receipt.effect != "open_work" or receipt.outcome != "committed":
        raise ControlRecordInvalid(f"{what} carries a receipt for another operation")
    reserved_at = _control_stamp(data.get("reserved_at"), "reserved_at", what)
    _require_receipt_matches(
        receipt,
        what,
        operation_id=operation_id,
        effect="open_work",
        subject=subject,
        work_id=work_id,
    )
    if receipt.fields.get("created_at") != reserved_at:
        raise ControlRecordInvalid(f"{what} carries a receipt from a different reservation")

    return Reservation(
        operation_id=operation_id,
        work_id=work_id,
        subject=subject,
        request_sha256=_control_digest(data.get("request_sha256"), "request_sha256", what),
        work_dirname=work_dirname,
        record_candidate_sha256=_control_digest(
            data.get("record_candidate_sha256"), "record_candidate_sha256", what
        ),
        reserved_at=reserved_at,
        receipt=receipt,
        record_sha256_before=None,
        binding_relative_path=binding_relative_path,
        binding_sha256_before=binding_sha256_before,
        binding_candidate_sha256=binding_candidate_sha256,
    )


def decode_control(raw: bytes, what: str) -> Any:
    """Decode a stored control record's bytes, failing closed on garbage."""
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ControlRecordInvalid(f"{what} is not readable as JSON") from exc


# -- the write plan ---------------------------------------------------------


@dataclass
class WritePlan:
    """Everything one write has already decided, before it touches disk."""

    intent: Intent
    record_candidate: bytes
    receipt: Receipt
    output: bytes | None = None
    binding_candidate: bytes | None = None
    conversations_dir: Path | None = None
    binding_filename: str | None = None


@dataclass(frozen=True)
class RecoveryOutcome:
    """What recovery decided about one operation, and what it published."""

    operation_id: str
    effect: str
    outcome: str
    receipt: Receipt | None = None
    relative_path: str | None = None
    reason_code: str | None = None


# -- reading the tree -------------------------------------------------------


def read_terminal(paths: WorkPaths, operation_id: str) -> Terminal | None:
    """One direct open on a path derived from the operation id. No scan."""
    snap = try_snapshot(paths.directory, paths.terminal_relative(operation_id))
    if snap is None:
        return None
    return parse_terminal(decode_control(snap.raw, "a terminal marker"))


def read_pending(paths: WorkPaths, operation_id: str) -> Intent | None:
    """The pending object for one operation, by derived name."""
    snap = try_snapshot(paths.directory, paths.pending_relative(operation_id))
    if snap is None:
        return None
    return parse_intent(decode_control(snap.raw, "an operation record"))


def read_record(paths: WorkPaths) -> tuple[records.WorkRecord, dict[str, Any], Snapshot] | None:
    """Snapshot ``work.json`` once and parse *those* bytes."""
    snap = try_snapshot(paths.directory, WORK_RECORD_FILENAME)
    if snap is None:
        return None
    document = decode_control(snap.raw, "the work record")
    return records.parse_work_record(document), document, snap


def read_binding(
    subject_paths: SubjectPaths, conversation_id: str
) -> tuple[records.ConversationBinding | None, Snapshot | None]:
    """The one direct open the binding authority rule permits."""
    snap = try_snapshot(
        subject_paths.base, subject_paths.binding_relative_path(conversation_id)
    )
    if snap is None:
        return None, None
    return (
        records.parse_conversation_binding(
            decode_control(snap.raw, "a conversation binding")
        ),
        snap,
    )


# -- the transaction --------------------------------------------------------


class WorkStore:
    """The canonical tree, and the only thing that writes into it."""

    def __init__(
        self,
        work_root: Path,
        *,
        recovery_min_age_seconds: int = RECOVERY_MIN_AGE_SECONDS,
    ) -> None:
        self.root = Path(work_root)
        self.recovery_min_age_seconds = recovery_min_age_seconds

    # -- layout ------------------------------------------------------

    def subject_paths(self, subject: str) -> SubjectPaths:
        return SubjectPaths(root=self.root, subject=subject)

    def ensure_subject(self, subject: str) -> SubjectPaths:
        """Create the subject's own directories, idempotently."""
        paths = self.subject_paths(subject)
        for directory in (
            self.root / SUBJECTS_DIRNAME,
            paths.base,
            paths.work_base,
            paths.creates,
            paths.conversations,
        ):
            directory.mkdir(mode=0o700, exist_ok=True)
        return paths

    def find_work_directory(self, subject: str, work_id: str) -> Path | None:
        """Locate one work item by the directory name its id is embedded in.

        The lookup is derived, not searched: the id is the tail of the
        directory name, so a single ``scandir`` filtered on that suffix
        answers it without reading anything.
        """
        base = self.subject_paths(subject).work_base
        if not base.is_dir():
            return None
        suffix = f"--{work_id}"
        try:
            with os.scandir(base) as entries:
                for entry in entries:
                    if entry.name.endswith(suffix) and entry.is_dir(follow_symlinks=False):
                        return base / entry.name
        except FileNotFoundError:
            return None
        return None

    def work_paths(self, subject: str, work_id: str) -> WorkPaths:
        directory = self.find_work_directory(subject, work_id)
        if directory is None:
            raise WorkError("not_found", "there is no such work item")
        return WorkPaths(directory=directory)

    # -- byte accounting ---------------------------------------------

    def measure(self, paths: WorkPaths, record: records.WorkRecord) -> int:
        """What this work item already holds, measured from the bytes.

        Every entry is re-read as one confined snapshot and both its digest
        and its byte count come from those same bytes. A stale or misstated
        count therefore cannot buy capacity, and a file swapped between the
        two observations cannot either, because there is only one.
        """
        used = 0
        for entry in list(record.sources) + list(record.artifacts):
            snap = try_snapshot(
                paths.directory, entry.path, extensions=confine.ALLOWED_EXTENSIONS
            )
            if snap is None:
                raise records.StaleContext(entry.path)
            if snap.sha256 != entry.sha256:
                raise records.StaleContext(entry.path)
            if entry.bytes is not None and entry.bytes != snap.bytes:
                raise records.RecordInvalid(
                    "the recorded size does not match the stored file",
                    relative_path=entry.path,
                )
            used += snap.bytes
        return used

    def require_capacity(self, used: int, additional: int, relative_path: str) -> None:
        """Refuse before an intent exists, so nothing at all is consumed."""
        if used + additional > MAX_WORK_TOTAL_BYTES:
            raise WorkError(
                "too_large",
                "this work item has no room left for another file",
                relative_path=relative_path,
            )

    # -- the staging sweep -------------------------------------------

    def sweep_staging(self, paths: WorkPaths) -> int:
        """Retire a bounded number of orphan publication temps.

        Cleanup, never correctness. The sweep looks at no more entries than
        its bound, unlinks only names matching the derived grammar, leaves
        anything else exactly as found, and fsyncs once. It therefore removes
        ``min(bound, n)`` of ``n`` orphans on every pass — strictly positive
        while any remain — so the orphan set reaches zero, while selection
        reaches a real pending object on the first pass whatever ``n`` is.
        """
        staging = paths.staging
        if not staging.is_dir():
            return 0
        removed = 0
        handle = open_dir(staging)
        try:
            with os.scandir(staging) as entries:
                for entry in itertools.islice(entries, MAX_ORPHAN_TEMPS_SWEPT):
                    match = _ORPHAN_TEMP_NAME.fullmatch(entry.name)
                    if match is None or match.group(1) != match.group(2):
                        continue
                    if not is_uuid4(match.group(1)):
                        continue
                    try:
                        os.unlink(entry.name, dir_fd=handle)
                        removed += 1
                    except FileNotFoundError:
                        continue
            if removed:
                os.fsync(handle)
        finally:
            os.close(handle)
        return removed

    # -- selection ---------------------------------------------------

    def select_pending(self, paths: WorkPaths) -> tuple[tuple[Intent, ...], bool]:
        """The bounded pending scan. One small namespace, final names only."""
        pending_dir = paths.pending
        if not pending_dir.is_dir():
            return (), False
        names: list[str] = []
        truncated = False
        with os.scandir(pending_dir) as entries:
            for index, entry in enumerate(
                itertools.islice(entries, MAX_RECOVERED_OPERATIONS + 1)
            ):
                if index == MAX_RECOVERED_OPERATIONS:
                    truncated = True
                    break
                match = _PENDING_NAME.fullmatch(entry.name)
                if match is None:
                    continue
                names.append(match.group(1))
        found: list[Intent] = []
        for operation_id in sorted(names):
            try:
                intent = read_pending(paths, operation_id)
            except (WorkError, ValueError):
                # This is a survey of *other* operations, so an entry that is
                # not the exact control record is left exactly as found and
                # acted on by nothing: it is never recovered from, never
                # cleaned up, and never counted as evidence. The operation
                # this call is actually answering reads its own pending
                # object by derived name, where the same failure is a closed
                # error rather than an omission.
                continue
            if intent is not None:
                found.append(intent)
        return tuple(found), truncated

    def is_old_enough(self, intent: Intent, now: datetime | None = None) -> bool:
        """True when a pending object is old enough for the lazy pass to act."""
        if self.recovery_min_age_seconds <= 0:
            return True
        created = parse_stamp(intent.created_at)
        if created is None:
            return True
        moment = now or datetime.now(timezone.utc)
        return (moment - created).total_seconds() >= self.recovery_min_age_seconds

    # -- cleanup -----------------------------------------------------

    def cleanup(self, paths: WorkPaths, intent: Intent, *, subject_paths: SubjectPaths) -> None:
        """Remove every derived name this operation may have left behind.

        Each path is computed from the operation id, so nothing here globs,
        scans or walks. A pre-link temp is non-canonical and describes
        nothing that was ever published, so it is unlinked rather than kept.
        """
        operation_id = intent.operation_id
        unlink_quietly(paths.pending, f"{operation_id}.json")
        fsync_dir(paths.pending)
        unlink_quietly(paths.staging, temp_name(f"{operation_id}.json", operation_id))
        fsync_dir(paths.staging)
        unlink_quietly(paths.directory, paths.record_candidate(operation_id))
        unlink_quietly(
            paths.operations, temp_name(paths.terminal_name(operation_id), operation_id)
        )
        fsync_dir(paths.operations)
        if intent.writes_content and intent.target_relative_path:
            target = Path(intent.target_relative_path)
            directory = paths.directory / target.parent
            if directory.is_dir():
                unlink_quietly(directory, temp_name(target.name, operation_id))
                fsync_dir(directory)
        if intent.binding_relative_path:
            binding = Path(intent.binding_relative_path)
            directory = subject_paths.base / binding.parent
            if directory.is_dir():
                unlink_quietly(directory, candidate_name(binding.name, operation_id))
                fsync_dir(directory)
        fsync_dir(paths.directory)

    def preserve_candidate(self, paths: WorkPaths, operation_id: str) -> None:
        """Keep a record candidate as evidence, outside the work directory."""
        name = paths.record_candidate(operation_id)
        source = paths.directory / name
        if not source.is_file():
            return
        paths.quarantine.mkdir(mode=0o700, exist_ok=True)
        destination = paths.quarantine / f"{operation_id}.record-candidate.json"
        try:
            os.link(str(source), str(destination))
        except FileExistsError:
            pass
        fsync_dir(paths.quarantine)
        unlink_quietly(paths.directory, name)
        fsync_dir(paths.directory)

    def preserve_output(self, paths: WorkPaths, operation_id: str, relative_path: str) -> None:
        """Move an unreferenced output aside rather than destroying it."""
        source = paths.directory / relative_path
        if not source.is_file():
            return
        paths.quarantine.mkdir(mode=0o700, exist_ok=True)
        destination = paths.quarantine / f"{operation_id}-{Path(relative_path).name}"
        try:
            os.link(str(source), str(destination))
        except FileExistsError:
            pass
        fsync_dir(paths.quarantine)
        unlink_quietly(source.parent, source.name)
        fsync_dir(source.parent)

    # -- publication -------------------------------------------------

    def publish_pending(self, paths: WorkPaths, intent: Intent) -> None:
        _checkpoint("P5")
        publish(
            paths.pending,
            f"{intent.operation_id}.json",
            encode_json(intent.as_document()),
            operation_id=intent.operation_id,
            staging_dir=paths.staging,
        )

    def publish_terminal(self, paths: WorkPaths, terminal: Terminal) -> bool:
        """Publish the one terminal name, or report that one already exists."""
        _checkpoint("P11")
        try:
            publish(
                paths.operations,
                paths.terminal_name(terminal.operation_id),
                encode_json(terminal.as_document()),
                operation_id=terminal.operation_id,
            )
        except AlreadyPublished:
            return False
        return True

    # -- the write path ----------------------------------------------

    def commit(
        self,
        paths: WorkPaths,
        plan: WritePlan,
        *,
        subject_paths: SubjectPaths,
    ) -> Receipt:
        """Steps P5 to P13, in the one order every truth table is stated against."""
        intent = plan.intent
        operation_id = intent.operation_id

        self.publish_pending(paths, intent)

        _checkpoint("P6")
        stage_candidate(
            paths.directory, paths.record_candidate(operation_id), plan.record_candidate
        )

        if plan.binding_candidate is not None:
            _checkpoint("P6b")
            assert plan.conversations_dir is not None and plan.binding_filename is not None
            stage_candidate(
                plan.conversations_dir,
                candidate_name(plan.binding_filename, operation_id),
                plan.binding_candidate,
            )

        if plan.output is not None and intent.target_relative_path:
            target = Path(intent.target_relative_path)
            directory = paths.directory / target.parent
            _checkpoint("P7")
            handle_dir = open_dir(directory)
            try:
                try:
                    os.unlink(temp_name(target.name, operation_id), dir_fd=handle_dir)
                except FileNotFoundError:
                    pass
                handle = os.open(
                    temp_name(target.name, operation_id),
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=handle_dir,
                )
                try:
                    write_all(handle, plan.output)
                    os.fsync(handle)
                finally:
                    os.close(handle)
                os.fsync(handle_dir)
                _checkpoint("P8")
                try:
                    os.link(
                        temp_name(target.name, operation_id),
                        target.name,
                        src_dir_fd=handle_dir,
                        dst_dir_fd=handle_dir,
                    )
                except FileExistsError as exc:
                    raise AlreadyPublished(target.name) from exc
                os.fsync(handle_dir)
                _checkpoint("P9")
                try:
                    os.unlink(temp_name(target.name, operation_id), dir_fd=handle_dir)
                except FileNotFoundError:
                    pass
                os.fsync(handle_dir)
            finally:
                os.close(handle_dir)

        _checkpoint("P10")
        install_candidate(
            paths.directory, paths.record_candidate(operation_id), WORK_RECORD_FILENAME
        )

        if plan.binding_candidate is not None:
            _checkpoint("P10b")
            assert plan.conversations_dir is not None and plan.binding_filename is not None
            install_candidate(
                plan.conversations_dir,
                candidate_name(plan.binding_filename, operation_id),
                plan.binding_filename,
            )

        terminal = Terminal(
            operation_id=operation_id,
            outcome="committed",
            request_sha256=intent.request_sha256,
            receipt=plan.receipt,
        )
        self.publish_terminal(paths, terminal)

        _checkpoint("P12")
        self.cleanup(paths, intent, subject_paths=subject_paths)

        stored = read_terminal(paths, operation_id)
        if stored is None or stored.outcome != "committed" or stored.receipt is None:
            raise WorkError("internal_error", "this change could not be confirmed")
        return stored.receipt

    # -- recovery ----------------------------------------------------

    def recover(
        self,
        paths: WorkPaths,
        intent: Intent,
        *,
        subject_paths: SubjectPaths,
    ) -> RecoveryOutcome:
        """Decide one crashed operation from digests alone, and finish it.

        Nothing here needs to know what any of the bytes say. An existing
        terminal marker is authoritative before anything else is considered;
        after that the record's own digest against the two pinned values, and
        the published content's digest against the pinned output, decide
        between committing forward, abandoning and quarantining. A record is
        never rebuilt from an intent: recovery commits only from a candidate
        whose digest it verified from the bytes it is about to install.
        """
        operation_id = intent.operation_id

        # Row 0 of every table: an existing terminal marker is authoritative.
        existing = read_terminal(paths, operation_id)
        if existing is not None:
            self.cleanup(paths, intent, subject_paths=subject_paths)
            return RecoveryOutcome(
                operation_id=operation_id,
                effect=intent.effect,
                outcome=existing.outcome,
                receipt=existing.receipt,
                relative_path=existing.relative_path,
                reason_code=existing.reason_code,
            )

        record_snap = try_snapshot(paths.directory, WORK_RECORD_FILENAME)
        live = record_snap.sha256 if record_snap is not None else None
        binding_state = self._binding_state(intent, subject_paths)

        if binding_state == "changed":
            return self._quarantine(
                paths,
                intent,
                subject_paths=subject_paths,
                reason_code="record_changed_underneath",
                relative_path=intent.binding_relative_path,
                preserve=False,
            )

        if live is not None and live == intent.record_candidate_sha256:
            # The record is installed. Nothing may be un-installed from here.
            if intent.writes_content and intent.target_relative_path:
                published = try_snapshot(
                    paths.directory,
                    intent.target_relative_path,
                    extensions=confine.ALLOWED_EXTENSIONS,
                )
                if published is None or published.sha256 != intent.output_sha256:
                    # The bytes the record already refers to are not the bytes
                    # this operation wrote. They may be a change made outside
                    # the system, so they are recorded and left exactly where
                    # they are: destroying them would destroy real work.
                    return self._quarantine(
                        paths,
                        intent,
                        subject_paths=subject_paths,
                        reason_code="recorded_bytes_changed",
                        relative_path=intent.target_relative_path,
                        preserve=False,
                    )
            if intent.writes_binding and binding_state == "before":
                if not self._install_binding(intent, subject_paths):
                    if intent.record_sha256_before == intent.record_candidate_sha256:
                        # The record leg was a no-op, so nothing canonical has
                        # landed and "nothing was written" is the truthful
                        # answer. Only once a record has actually changed does
                        # abandoning become a false statement about the tree.
                        return self._abandon(paths, intent, subject_paths=subject_paths)
                    return self._quarantine(
                        paths,
                        intent,
                        subject_paths=subject_paths,
                        reason_code="binding_candidate_lost",
                        relative_path=intent.binding_relative_path,
                        preserve=False,
                    )
            return self._commit_forward(paths, intent, subject_paths=subject_paths)

        if live != intent.record_sha256_before:
            return self._quarantine(
                paths,
                intent,
                subject_paths=subject_paths,
                reason_code="record_changed_underneath",
                relative_path=WORK_RECORD_FILENAME,
                preserve=True,
            )

        # The replacement never happened, so nothing canonical is referenced
        # yet and abandoning is a truthful answer wherever the evidence is
        # incomplete.
        candidate = try_snapshot(paths.directory, paths.record_candidate(operation_id))

        if intent.writes_content and intent.target_relative_path:
            published = try_snapshot(
                paths.directory,
                intent.target_relative_path,
                extensions=confine.ALLOWED_EXTENSIONS,
            )
            if published is None:
                return self._abandon(paths, intent, subject_paths=subject_paths)
            if published.sha256 != intent.output_sha256:
                self.preserve_output(paths, operation_id, intent.target_relative_path)
                return self._quarantine(
                    paths,
                    intent,
                    subject_paths=subject_paths,
                    reason_code="unreferenced_output",
                    relative_path=intent.target_relative_path,
                    preserve=False,
                )
            if candidate is None or candidate.sha256 != intent.record_candidate_sha256:
                self.preserve_output(paths, operation_id, intent.target_relative_path)
                return self._quarantine(
                    paths,
                    intent,
                    subject_paths=subject_paths,
                    reason_code="record_candidate_lost",
                    relative_path=intent.target_relative_path,
                    preserve=False,
                )
        elif candidate is None or candidate.sha256 != intent.record_candidate_sha256:
            return self._abandon(paths, intent, subject_paths=subject_paths)

        if intent.writes_binding and binding_state == "before":
            # The record and the binding are decided together, so a lost
            # binding candidate before the record is installed abandons both.
            if not self._install_binding(intent, subject_paths):
                return self._abandon(paths, intent, subject_paths=subject_paths)

        if intent.writes_content and intent.target_relative_path:
            target = Path(intent.target_relative_path)
            directory = paths.directory / target.parent
            if unlink_quietly(directory, temp_name(target.name, operation_id)):
                fsync_dir(directory)

        install_candidate(
            paths.directory, paths.record_candidate(operation_id), WORK_RECORD_FILENAME
        )
        return self._commit_forward(paths, intent, subject_paths=subject_paths)

    def _binding_state(self, intent: Intent, subject_paths: SubjectPaths) -> str | None:
        """``before``, ``installed`` or ``changed`` — or ``None`` when unused."""
        if not intent.writes_binding or not intent.binding_relative_path:
            return None
        snap = try_snapshot(subject_paths.base, intent.binding_relative_path)
        live = snap.sha256 if snap is not None else None
        if live == intent.binding_candidate_sha256:
            return "installed"
        if live == intent.binding_sha256_before:
            return "before"
        return "changed"

    def _install_binding(self, intent: Intent, subject_paths: SubjectPaths) -> bool:
        """Install the pinned binding from its candidate, or report failure."""
        assert intent.binding_relative_path is not None
        binding = Path(intent.binding_relative_path)
        directory = subject_paths.base / binding.parent
        name = candidate_name(binding.name, intent.operation_id)
        staged = try_snapshot(directory, name)
        if staged is None or staged.sha256 != intent.binding_candidate_sha256:
            return False
        install_candidate(directory, name, binding.name)
        return True

    def _commit_forward(
        self, paths: WorkPaths, intent: Intent, *, subject_paths: SubjectPaths
    ) -> RecoveryOutcome:
        """Publish the terminal marker this operation never reached, then tidy."""
        if intent.receipt is None:
            return self._quarantine(
                paths,
                intent,
                subject_paths=subject_paths,
                reason_code="record_changed_underneath",
                relative_path=WORK_RECORD_FILENAME,
                preserve=True,
            )
        self.publish_terminal(
            paths,
            Terminal(
                operation_id=intent.operation_id,
                outcome="committed",
                request_sha256=intent.request_sha256,
                receipt=intent.receipt,
            ),
        )
        stored = read_terminal(paths, intent.operation_id)
        self.cleanup(paths, intent, subject_paths=subject_paths)
        if stored is None or stored.outcome != "committed" or stored.receipt is None:
            raise WorkError("internal_error", "this change could not be confirmed")
        return RecoveryOutcome(
            operation_id=intent.operation_id,
            effect=intent.effect,
            outcome="committed",
            receipt=stored.receipt,
        )


    def _abandon(
        self, paths: WorkPaths, intent: Intent, *, subject_paths: SubjectPaths
    ) -> RecoveryOutcome:
        self.publish_terminal(
            paths,
            Terminal(
                operation_id=intent.operation_id,
                outcome="abandoned",
                request_sha256=intent.request_sha256,
                receipt=None,
            ),
        )
        self.cleanup(paths, intent, subject_paths=subject_paths)
        return RecoveryOutcome(
            operation_id=intent.operation_id, effect=intent.effect, outcome="abandoned"
        )

    def _quarantine(
        self,
        paths: WorkPaths,
        intent: Intent,
        *,
        subject_paths: SubjectPaths,
        reason_code: str,
        relative_path: str | None,
        preserve: bool,
    ) -> RecoveryOutcome:
        if preserve:
            self.preserve_candidate(paths, intent.operation_id)
        self.publish_terminal(
            paths,
            Terminal(
                operation_id=intent.operation_id,
                outcome="quarantined",
                request_sha256=intent.request_sha256,
                receipt=None,
                reason_code=reason_code,
                relative_path=relative_path,
            ),
        )
        self.cleanup(paths, intent, subject_paths=subject_paths)
        return RecoveryOutcome(
            operation_id=intent.operation_id,
            effect=intent.effect,
            outcome="quarantined",
            relative_path=relative_path,
            reason_code=reason_code,
        )


def new_work_id() -> str:
    """A fresh work identifier."""
    return str(uuid.uuid4())


__all__ = [
    "CREATE_SUBDIRECTORIES",
    "INTERNAL_EXTENSIONS",
    "LOCK_BUDGET_SECONDS",
    "MAX_ORPHAN_TEMPS_SWEPT",
    "MAX_RECOVERED_OPERATIONS",
    "MAX_WORK_TOTAL_BYTES",
    "AlreadyPublished",
    "Intent",
    "Lock",
    "RecoveryOutcome",
    "Snapshot",
    "SubjectPaths",
    "Terminal",
    "WorkPaths",
    "WorkStore",
    "WritePlan",
    "candidate_name",
    "encode_json",
    "fsync_dir",
    "make_dir",
    "new_work_id",
    "now_stamp",
    "ControlRecordInvalid",
    "Reservation",
    "decode_control",
    "parse_intent",
    "parse_reservation",
    "write_all",
    "parse_terminal",
    "publish",
    "read_binding",
    "read_pending",
    "read_record",
    "read_terminal",
    "slugify",
    "snapshot",
    "stage_candidate",
    "temp_name",
    "touch_lock",
    "try_snapshot",
    "unlink_quietly",
]
