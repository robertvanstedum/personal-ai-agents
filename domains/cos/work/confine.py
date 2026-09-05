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
    raise NotImplementedError


def sha256_file(path: Path) -> str:
    """Hex digest of a file's raw bytes."""
    raise NotImplementedError


def normalise_relative(candidate: str) -> PurePosixPath:
    """Validate the textual form of a caller-supplied relative path."""
    raise NotImplementedError


def confine(
    root: Path,
    candidate: str,
    *,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    allowed_extensions: frozenset[str] = ALLOWED_EXTENSIONS,
) -> ConfinedFile:
    """Resolve ``candidate`` under ``root`` and apply every gate."""
    raise NotImplementedError


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
    raise NotImplementedError


def read_text(confined: ConfinedFile) -> str:
    """Read a confined file as UTF-8 text."""
    raise NotImplementedError
