"""Small durable file helpers and the headroom rule (amendment v0.5.1 §4).

Files are 0600, folders 0700. Writes are temp file + fsync + rename, so a reader
sees the old file or the whole new one. ``check_headroom`` runs BEFORE every
writer; below the threshold the writer skips with ``disk_low`` and **never
deletes anything to make room**. If free space cannot be measured, it is
``disk_low``: when in doubt, do not write.
"""
from __future__ import annotations

import json
import errno
import fcntl
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from core.memory_shelf.codes import DISK_LOW

FILE_MODE, DIR_MODE = 0o600, 0o700
GB = 1024 ** 3
DEFAULT_MIN_FREE_BYTES = 5 * GB          # Mac; EC2 uses min_free_fraction=0.10


class PublicationUncertain(OSError):
    """Whole bytes were published, but syncing the directory failed.

    The final file may already contain the new value. This is not a rollback;
    callers must verify/reconcile before retrying a non-idempotent action.
    """
    code = "publication_uncertain"


def _sync_publication(path: Path) -> None:
    try:
        fsync_dir(path.parent)
    except OSError as exc:
        raise PublicationUncertain(errno.EIO, "publication_uncertain") from exc


def ensure_dir(path: Path) -> None:
    """Create ``path`` and any missing parents, each created folder mode 0700."""
    missing, probe = [], Path(path)
    while not probe.exists() and probe != probe.parent:
        missing.append(probe)
        probe = probe.parent
    for folder in reversed(missing):
        try:
            os.mkdir(folder, DIR_MODE)
        except FileExistsError:
            pass
        os.chmod(folder, DIR_MODE)


def fsync_dir(path: Path) -> None:
    """Sync publication on the supported POSIX hosts; do not report a failed sync as success."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_all(fd: int, data: bytes) -> None:
    """A positive short write is progress, not completion. Zero progress is an error."""
    remaining = memoryview(data)
    while remaining:
        try:
            written = os.write(fd, remaining)
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError(errno.EIO, "write made no progress")
        remaining = remaining[written:]


@contextmanager
def _staged(path: Path, data: bytes):
    """Unique private staging file; readers never see a partial final file."""
    ensure_dir(path.parent)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    tmp = Path(name)
    try:
        try:
            write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        yield tmp
    finally:
        tmp.unlink(missing_ok=True)         # only this invocation's staging file


def write_atomic(path: Path, data: bytes) -> None:
    path = Path(path)
    with _staged(path, data) as tmp:
        os.replace(tmp, path)
    _sync_publication(path)


def write_exclusive(path: Path, data: bytes) -> None:
    """Publish whole bytes without replacing an existing name (including a symlink).

    Same-filesystem hard-link publication is atomic on the supported Mac/Linux
    hosts. A collision raises FileExistsError and never removes the existing file.
    """
    path = Path(path)
    with _staged(path, data) as tmp:
        os.link(tmp, path)
    _sync_publication(path)


def write_json(path: Path, doc: object) -> None:
    write_atomic(path, (json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8"))


def read_json(path: Path, default: object = None) -> object:
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return default


def append_jsonl(path: Path, row: dict) -> None:
    """Lock, append the complete row and sync; roll back our partial row on failure.

    All writers use this lock, so another writer cannot be truncated by rollback.
    A failed rollback still raises: it is never recorded as a successful append.
    """
    path = Path(path)
    ensure_dir(path.parent)
    data = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), FILE_MODE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        before = os.fstat(fd).st_size
        try:
            if before:
                os.lseek(fd, before - 1, os.SEEK_SET)
                if os.read(fd, 1) != b"\n":
                    # A prior crash or failed rollback left an incomplete row.
                    # Keep those bytes, visibly mark the gap, and start the new
                    # row on its own line rather than glue it to damaged JSON.
                    data = b'\n{"record_type":"log_gap","reason":"truncated_tail"}\n' + data
            write_all(fd, data)
            os.fsync(fd)
            fsync_dir(path.parent)
        except OSError:
            try:
                os.ftruncate(fd, before)
                os.fsync(fd)
            except OSError:
                pass
            raise
    finally:
        os.close(fd)


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    try:
        with open(path, "rb") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def check_headroom(path: str | os.PathLike, min_free_bytes: int | None = DEFAULT_MIN_FREE_BYTES,
                   min_free_fraction: float | None = None) -> str | None:
    """``"disk_low"`` when free space is below EITHER set threshold, else ``None``.

    ``path`` need not exist; the nearest existing ancestor is measured.
    """
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return DISK_LOW
    if min_free_bytes is not None and usage.free < min_free_bytes:
        return DISK_LOW
    if min_free_fraction is not None and usage.total and usage.free / usage.total < min_free_fraction:
        return DISK_LOW
    return None
