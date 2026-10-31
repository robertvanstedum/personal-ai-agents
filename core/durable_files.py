"""Durable file primitives for records other containers read: the CoS store and history (spec v0.3 sections 4.5,
4.19) and the domain activity records (section 4.3).

Atomic documents (temp file + fsync + rename + directory fsync), fsynced appended lines that never leave a partial
line of their own behind, torn-tail quarantine, and readers that never treat a torn last line as a record. Files are
0640 and folders 0750 (contracts/cos/v1/access-matrix.json): the agent's group reads, only the CoS backend writes.

The Vault's fsio does the same; CoS keeps its own copy so the CoS images take no dependency on the Vault runtime.
"""
from __future__ import annotations

import errno
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FILE_MODE, DIR_MODE = 0o640, 0o750


def write_all(fd: int, data: bytes) -> None:
    """A short write is progress, not completion; zero progress is an error."""
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


def ensure_dir(folder: Path) -> None:
    """Create missing folders 0750. A folder whose parent has the setgid bit keeps it, so files deep inside still
    inherit the reader group the deploy set on the root (scripts/operations/cos_prod.sh prepare); a plain chmod to
    0750 would clear it and leave new files in root's group, unreadable to the agent."""
    missing, probe = [], folder
    while not probe.exists() and probe != probe.parent:
        missing.append(probe)
        probe = probe.parent
    for item in reversed(missing):
        try:
            os.mkdir(item, DIR_MODE)
        except FileExistsError:
            pass
        os.chmod(item, DIR_MODE | (os.stat(item.parent).st_mode & stat.S_ISGID))


def write_bytes(path: Path, data: bytes) -> None:
    ensure_dir(path.parent)
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


def write_doc(path: Path, record: dict) -> None:
    write_bytes(path, (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8"))


def append_line(path: Path, row: dict) -> None:
    ensure_dir(path.parent)
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


def read_doc(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise OSError("document unreadable") from exc
    return doc if isinstance(doc, dict) else None


def read_stream(path: Path) -> list[dict]:
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


def quarantine_torn_tail(path: Path, quarantine_dir: Path) -> int:
    """Move an incomplete last line to quarantine_dir/quarantine-<stamp>-<name>.bin; return 1 if one was moved."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return 0
    if not data or data.endswith(b"\n"):
        return 0
    cut = data.rfind(b"\n") + 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    write_bytes(quarantine_dir / f"quarantine-{stamp}-{path.name}.bin", data[cut:])
    fd = os.open(path, os.O_RDWR)
    try:
        os.ftruncate(fd, cut)
        os.fsync(fd)
    finally:
        os.close(fd)
    return 1
