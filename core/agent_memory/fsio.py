"""Small durable file helpers: files 0600, folders 0700 (agent-memory v0.4 §2, §3.6)."""
from __future__ import annotations

import fcntl
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

FILE_MODE = 0o600
DIR_MODE = 0o700


def ensure_dir(path: Path) -> None:
    """Create ``path`` and any missing parents, each mode 0700."""
    missing: list[Path] = []
    probe = path
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
    """Flush a folder's entries to disk (best effort; some filesystems refuse)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_file(path: Path, data: bytes) -> None:
    """Write ``data`` to a new file, mode 0600, and fsync it."""
    ensure_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, FILE_MODE)


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` so a reader sees the old file or the whole new one, never half."""
    tmp = path.with_name(path.name + ".part")
    write_file(tmp, data)
    os.replace(tmp, path)
    fsync_dir(path.parent)


def remove_tree(path: Path) -> None:
    """Delete a leftover file or folder of ours; missing is fine."""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


class LockBusy(Exception):
    """Another process (or run) holds this lock."""


@contextmanager
def exclusive(path: Path):
    """An exclusive, non-blocking flock on ``path`` (created 0600), held for the ``with`` block. Raises ``LockBusy``
    if someone else holds it. The kernel drops it if the holder dies, so a crashed run never leaves a stuck lock."""
    ensure_dir(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, FILE_MODE)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise LockBusy from None
        yield
    finally:
        os.close(fd)             # closing releases the lock
