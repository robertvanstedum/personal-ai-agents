"""Path confinement, file gates and hashing.

Every candidate path is resolved component by component under its root.
Rejected without reading anything: caller-supplied absolute paths, ``..``
segments, NUL bytes, a symbolic link at any component, non-regular files,
unsupported extensions, files above the size cap, and anything that escapes
the root after resolution.

Only relative paths are ever returned, and error messages carry no filesystem
detail beyond that relative path.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

from .envelope import WorkError

#: The only file types the first reference reads.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".md", ".txt"})

#: Per-file ceiling, configurable per call.
DEFAULT_MAX_FILE_BYTES = 512 * 1024

#: Directory names never descended into or exposed.
EXCLUDED_DIRECTORY_NAMES: frozenset[str] = frozenset({".git"})

_HASH_CHUNK_BYTES = 65536


class PathRejected(WorkError):
    """The path is outside the boundary or otherwise refused."""

    def __init__(self, message: str, *, relative_path: str | None = None) -> None:
        super().__init__("path_rejected", message, relative_path=relative_path)


class NotFound(WorkError):
    """No such file under the root."""

    def __init__(self, relative_path: str) -> None:
        super().__init__("not_found", "no such file in this root", relative_path=relative_path)


class UnsupportedFile(WorkError):
    """The file type is not readable by this reference."""

    def __init__(self, relative_path: str) -> None:
        super().__init__(
            "unsupported_file",
            "only plain text and Markdown files can be read",
            relative_path=relative_path,
        )


class TooLarge(WorkError):
    """The file is above the configured size ceiling."""

    def __init__(self, relative_path: str) -> None:
        super().__init__(
            "too_large", "this file is larger than the reading limit", relative_path=relative_path
        )


@dataclass(frozen=True)
class ConfinedFile:
    """A file proven to sit inside its root and pass every gate."""

    relative_path: str
    absolute_path: Path
    size: int


def sha256_bytes(data: bytes) -> str:
    """Hex digest over raw bytes, with no normalisation."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Hex digest of a file's raw bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalise_relative(candidate: str) -> PurePosixPath:
    """Validate the textual form of a caller-supplied relative path."""
    if not isinstance(candidate, str) or not candidate.strip():
        raise PathRejected("a file name is required")
    if "\0" in candidate:
        raise PathRejected("that file name is not allowed")
    if candidate.startswith("/") or os.path.isabs(candidate) or PurePosixPath(candidate).is_absolute():
        raise PathRejected("a full filesystem path is not accepted here")
    parts = [part for part in PurePosixPath(candidate).parts if part != "."]
    if any(part == ".." for part in parts):
        raise PathRejected("that file name is not allowed")
    if not parts:
        raise PathRejected("a file name is required")
    return PurePosixPath(*parts)


def confine(
    root: Path,
    candidate: str,
    *,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    allowed_extensions: frozenset[str] = ALLOWED_EXTENSIONS,
) -> ConfinedFile:
    """Resolve ``candidate`` under ``root`` and apply every gate."""
    relative = normalise_relative(candidate)
    shown = str(relative)

    current = Path(root)
    parts = relative.parts
    for index, part in enumerate(parts):
        if part in EXCLUDED_DIRECTORY_NAMES:
            raise PathRejected("that location is not readable", relative_path=shown)
        current = current / part
        if current.is_symlink():
            raise PathRejected(
                "a part of that path is a symbolic link", relative_path=shown
            )
        if not current.exists():
            raise NotFound(shown)
        is_last = index == len(parts) - 1
        if not is_last and not current.is_dir():
            raise PathRejected("that path is not a directory", relative_path=shown)

    info = current.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise PathRejected("only regular files can be read", relative_path=shown)
    if current.suffix.lower() not in allowed_extensions:
        raise UnsupportedFile(shown)
    if info.st_size > max_bytes:
        raise TooLarge(shown)

    root_real = os.path.realpath(root)
    file_real = os.path.realpath(current)
    if os.path.commonpath([root_real, file_real]) != root_real:
        raise PathRejected("that file is outside the authorized area", relative_path=shown)

    return ConfinedFile(relative_path=shown, absolute_path=current, size=info.st_size)


def iter_files(
    root: Path,
    *,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    allowed_extensions: frozenset[str] = ALLOWED_EXTENSIONS,
) -> Iterator[ConfinedFile]:
    """Yield every readable file under ``root``, in deterministic order.

    Skips excluded directories, symbolic links at any level, non-regular
    files, unsupported extensions and oversized files silently — they are not
    errors, they are simply not part of the readable surface.
    """
    root = Path(root)
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(directory)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in EXCLUDED_DIRECTORY_NAMES and not (here / name).is_symlink()
        )
        for name in sorted(filenames):
            path = here / name
            if path.is_symlink():
                continue
            try:
                info = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            if path.suffix.lower() not in allowed_extensions:
                continue
            if info.st_size > max_bytes:
                continue
            relative = PurePosixPath(*path.relative_to(root).parts)
            yield ConfinedFile(
                relative_path=str(relative), absolute_path=path, size=info.st_size
            )


def read_text(confined: ConfinedFile) -> str:
    """Read a confined file as UTF-8 text."""
    try:
        return confined.absolute_path.read_text("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedFile(confined.relative_path) from exc
