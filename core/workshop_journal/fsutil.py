"""Small, careful file operations for the journal (v0.6 section 6).

Every managed path is opened relative to a directory descriptor with no link followed, and each file is checked on the open
descriptor (regular, one link, owned by this user, no group or other access) and against its name (same device and inode), so a
folder or file swapped for a link, or a file hard-linked elsewhere, is refused instead of followed. Writes loop until every byte
is written; a failed write is cut back; durability is ``F_FULLFSYNC`` on macOS and ``fsync`` elsewhere, with a degraded
profile that is reported, never silent.
"""
from __future__ import annotations

import errno
import fcntl
import os
import secrets
import stat
import sys
import time

from core.workshop_journal.errors import IdConflict, Missing, UnsafeRoot

FILE_MODE, DIR_MODE = 0o600, 0o700
CREATE_RETRIES = 8
_BASE = os.O_CLOEXEC if hasattr(os, "O_CLOEXEC") else 0
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _BASE


def _refuse(reason: str):
    raise UnsafeRoot(reason)


def open_root(path: str, *, create: bool = False) -> int:
    """The workshop root: not a link, owned by this user, not writable by anyone else."""
    try:
        fd = os.open(path, _DIR_FLAGS)
    except FileNotFoundError:
        if not create:
            raise Missing("root_missing") from None
        os.makedirs(path, mode=DIR_MODE, exist_ok=True)
        fd = os.open(path, _DIR_FLAGS)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            _refuse("root_is_a_link_or_not_a_folder")
        raise
    st = os.fstat(fd)
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        os.close(fd)
        _refuse("root_owner_or_permissions")
    return fd


def open_subdir(parent_fd: int, name: str, *, create: bool = False) -> int:
    """A managed folder below a descriptor: created 0700, never followed if it is a link, never group or other readable."""
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise Missing("folder_missing") from None
        try:
            os.mkdir(name, DIR_MODE, dir_fd=parent_fd)
        except FileExistsError:
            pass
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            _refuse("folder_is_a_link_or_not_a_folder")
        raise
    st = os.fstat(fd)
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        os.close(fd)
        _refuse("folder_owner_or_permissions")
    return fd


def open_file(dir_fd: int, name: str, flags: int, *, mode: int = FILE_MODE) -> int:
    """A managed file: regular, one link, owned here, private, and still the file its name points to."""
    for attempt in range(CREATE_RETRIES if flags & os.O_CREAT else 1):
        try:
            fd = os.open(name, flags | os.O_NOFOLLOW | _BASE, mode, dir_fd=dir_fd)
            break
        except FileNotFoundError:
            # APFS can answer ENOENT to an O_CREAT open that races another process creating the same name; the name is
            # created by one of them, so asking again is correct. Without O_CREAT, a missing file is simply missing.
            if not flags & os.O_CREAT or attempt == CREATE_RETRIES - 1:
                raise Missing("file_missing") from None
            time.sleep(0.005 * (attempt + 1))
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENXIO, errno.EISDIR):
                _refuse("file_is_a_link_or_not_regular")
            raise
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.getuid() or st.st_mode & 0o077:
        os.close(fd)
        _refuse("file_not_regular_single_private")
    try:
        named = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        os.close(fd)
        _refuse("descriptor_and_path_diverge")
    if (named.st_dev, named.st_ino) != (st.st_dev, st.st_ino):
        os.close(fd)
        _refuse("descriptor_and_path_diverge")
    return fd


def check_same_file(dir_fd: int, name: str, fd: int) -> None:
    """Refuse if the name no longer points at the descriptor we hold (swapped after open): v0.7 Unit 1, Grok G8."""
    try:
        named = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        _refuse("descriptor_and_path_diverge")
    held = os.fstat(fd)
    if (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino):
        _refuse("descriptor_and_path_diverge")


def read_all(fd: int) -> bytes:
    size = os.fstat(fd).st_size
    chunks, offset = [], 0
    while offset < size:
        chunk = os.pread(fd, min(1 << 20, size - offset), offset)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _write(fd: int, view) -> int:        # the one place bytes reach the file: tests replace it to inject short, failing or zero writes
    return os.write(fd, view)


def truncate(fd: int, size: int) -> None:
    os.ftruncate(fd, size)


def write_all(fd: int, data: bytes) -> None:
    """Write every byte or raise. Retries an interrupted call, completes a short write, treats a zero-byte write as failure."""
    view, written = memoryview(data), 0
    while written < len(data):
        try:
            n = _write(fd, view[written:])
        except InterruptedError:
            continue
        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            raise
        if n <= 0:
            raise OSError(errno.EIO, "write made no progress")
        written += n


def full_sync(fd: int, *, degraded_ok: bool = False) -> str:
    """Make the bytes durable. Returns ``"full"``, or ``"degraded"`` when only ``fsync`` was possible and the caller allowed it.

    macOS ``fsync`` does not flush the drive's cache; ``F_FULLFSYNC`` does (measured here: about 3 ms). If it is unavailable the
    strict profile raises, so the caller reports ``commit_unknown`` instead of a durable success."""
    if sys.platform == "darwin":
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return "full"
        except OSError:
            if not degraded_ok:
                raise
            os.fsync(fd)
            return "degraded"
    os.fsync(fd)
    return "full"


def fsync_dir(dir_fd: int, *, durable: bool = True) -> None:
    """Make a folder's entries durable. A failure raises when the caller needs the entry to survive a crash (evidence, artifacts);
    for derived or recoverable files (``durable=False``) it is best effort."""
    try:
        full_sync(dir_fd) if durable else os.fsync(dir_fd)
    except OSError:
        if durable:
            raise


def _temp_name() -> str:
    return f".tmp-{os.getpid()}-{secrets.token_hex(4)}"


def _write_temp(dir_fd: int, data: bytes, durable: bool = True) -> str:
    name = _temp_name()
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _BASE, FILE_MODE, dir_fd=dir_fd)
    try:
        write_all(fd, data)
        full_sync(fd) if durable else os.fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            os.unlink(name, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    os.close(fd)
    return name


def publish_new(dir_fd: int, name: str, data: bytes, *, durable: bool = True) -> bool:
    """Publish a file that must not replace a different one: temp, full sync, hard-link into place, sync the folder.

    ``durable=False`` is for files a crash can safely lose (a prepare receipt: losing it only turns a completion into a
    quarantine); everything else keeps full sync on the file and the folder, and a failure raises.

    Returns True if created, False if the identical bytes were already there; different bytes under the same name raise
    ``IdConflict`` (content-addressed names make that a real conflict, never a retry)."""
    temp = _write_temp(dir_fd, data, durable)
    try:
        try:
            os.link(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
            created = True
        except FileExistsError:
            fd = open_file(dir_fd, name, os.O_RDONLY)
            try:
                same = read_all(fd) == data
            finally:
                os.close(fd)
            if not same:
                raise IdConflict("different_bytes_under_the_same_name") from None
            created = False
    finally:
        try:
            os.unlink(temp, dir_fd=dir_fd)
        except OSError:
            pass
    fsync_dir(dir_fd, durable=durable)
    return created


def publish_replace(dir_fd: int, name: str, data: bytes, *, durable: bool = False) -> None:
    """Replace a derived file whole: temp, full write, fsync, rename, fsync the folder."""
    temp = _write_temp(dir_fd, data, durable)
    try:
        os.rename(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(temp, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    fsync_dir(dir_fd, durable=durable)
