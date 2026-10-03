"""Small durable file helpers and the headroom rule (amendment v0.5.1 §4).

Files are 0600, folders 0700. Writes are temp file + fsync + rename, so a reader
sees the old file or the whole new one. ``check_headroom`` runs BEFORE every
writer; below the threshold the writer skips with ``disk_low`` and **never
deletes anything to make room**. If free space cannot be measured, it is
``disk_low``: when in doubt, do not write.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from core.memory_shelf.codes import DISK_LOW

FILE_MODE, DIR_MODE = 0o600, 0o700
GB = 1024 ** 3
DEFAULT_MIN_FREE_BYTES = 5 * GB          # Mac; EC2 uses min_free_fraction=0.10


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


def write_atomic(path: Path, data: bytes) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, FILE_MODE)
    os.replace(tmp, path)
    fsync_dir(path.parent)


def write_json(path: Path, doc: object) -> None:
    write_atomic(path, (json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8"))


def read_json(path: Path, default: object = None) -> object:
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return default


def append_jsonl(path: Path, row: dict) -> None:
    """Append one row with a single write call (O_APPEND), so rows never interleave."""
    path = Path(path)
    ensure_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
    try:
        os.write(fd, (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
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
