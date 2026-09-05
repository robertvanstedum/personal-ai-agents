"""Path confinement, file gates and hashing.

Every candidate path is resolved component by component under its root.
Rejected without reading anything: caller-supplied absolute paths, ``..``
segments, NUL bytes, a symbolic link at any component, non-regular files,
unsupported extensions, files above the size cap, and anything that escapes
the root after resolution.

The read itself is race-resistant. A validated absolute path is never handed
to a later ordinary ``open``: that leaves a window in which a component can be
replaced by a symbolic link between the check and the read. Instead the read
walks from the root's own directory descriptor, opening each component with
``O_NOFOLLOW`` relative to the descriptor above it, opens the final file with
``O_NOFOLLOW | O_NONBLOCK``, and re-checks *that descriptor* with ``fstat``
before a single byte is read. A component swapped after validation therefore
fails the read, not merely the earlier check.

:class:`ConfinedFile` deliberately carries no absolute path. Callers hold a
relative path and the root they were confined to, and read through the helpers
here; there is nothing for a later layer to open directly.

Only relative paths are ever returned, and error messages carry no filesystem
detail beyond that relative path.
"""

from __future__ import annotations

import errno
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

_READ_CHUNK_BYTES = 65536

#: Open flags. ``O_NOFOLLOW`` refuses a symbolic link at the component being
#: opened; ``O_NONBLOCK`` keeps a FIFO that slipped past the type check from
#: blocking the process before ``fstat`` can refuse it.
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


class PathDenied(WorkError):
    """The path is outside the boundary or otherwise refused."""

    def __init__(self, message: str, *, relative_path: str | None = None) -> None:
        super().__init__("path_denied", message, relative_path=relative_path)


class NotFound(WorkError):
    """No such file under the root."""

    def __init__(self, relative_path: str) -> None:
        super().__init__("not_found", "no such file in this root", relative_path=relative_path)


class UnsupportedMedia(WorkError):
    """The file type is not readable by this reference."""

    def __init__(self, relative_path: str) -> None:
        super().__init__(
            "unsupported_media",
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
    """A file proven to sit inside its root and pass every gate.

    It names the root it was confined to and its path relative to that root.
    It deliberately exposes no absolute path, so no later layer can open the
    file by name and reintroduce the swap race this module exists to close.
    """

    relative_path: str
    root: Path
    size: int


def sha256_bytes(data: bytes) -> str:
    """Hex digest over raw bytes, with no normalisation."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Hex digest of a file's raw bytes, by absolute path.

    For files already confined, use :func:`sha256_confined`, which reads
    through the race-resistant descriptor walk instead of a path.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalise_relative(candidate: str) -> PurePosixPath:
    """Validate the textual form of a caller-supplied relative path."""
    if not isinstance(candidate, str) or not candidate.strip():
        raise PathDenied("a file name is required")
    if "\0" in candidate:
        raise PathDenied("that file name is not allowed")
    if (
        candidate.startswith("/")
        or os.path.isabs(candidate)
        or PurePosixPath(candidate).is_absolute()
    ):
        raise PathDenied("a full filesystem path is not accepted here")
    parts = [part for part in PurePosixPath(candidate).parts if part != "."]
    if any(part == ".." for part in parts):
        raise PathDenied("that file name is not allowed")
    if not parts:
        raise PathDenied("a file name is required")
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in parts):
        raise PathDenied("that location is not readable", relative_path=str(PurePosixPath(*parts)))
    return PurePosixPath(*parts)


def _translate(exc: OSError, shown: str) -> WorkError:
    """Map an ``os.open`` failure onto the closed error vocabulary."""
    if exc.errno in (errno.ELOOP, errno.EMLINK):
        return PathDenied("a part of that path is a symbolic link", relative_path=shown)
    if exc.errno == errno.ENOENT:
        return NotFound(shown)
    if exc.errno == errno.ENOTDIR:
        return PathDenied("that path is not a directory", relative_path=shown)
    if exc.errno in (errno.EACCES, errno.EPERM):
        return PathDenied("that location is not readable", relative_path=shown)
    return PathDenied("that file could not be opened", relative_path=shown)


def _open_confined(
    root: Path, relative: PurePosixPath, shown: str
) -> tuple[int, os.stat_result]:
    """Open ``relative`` under ``root`` with no-follow semantics throughout.

    Returns the file descriptor and its ``fstat``. The caller owns the
    descriptor and must close it. Every component — the root included — is
    opened with ``O_NOFOLLOW`` relative to the descriptor above it, so a
    symbolic link introduced at any moment fails the open rather than being
    quietly traversed.
    """
    try:
        current = os.open(str(root), _DIR_FLAGS)
    except OSError as exc:
        raise _translate(exc, shown) from exc

    parts = relative.parts
    try:
        for part in parts[:-1]:
            try:
                nxt = os.open(part, _DIR_FLAGS, dir_fd=current)
            except OSError as exc:
                raise _translate(exc, shown) from exc
            os.close(current)
            current = nxt
        try:
            handle = os.open(parts[-1], _FILE_FLAGS, dir_fd=current)
        except OSError as exc:
            raise _translate(exc, shown) from exc
    finally:
        os.close(current)

    try:
        info = os.fstat(handle)
    except OSError as exc:
        os.close(handle)
        raise _translate(exc, shown) from exc
    return handle, info


def _check_descriptor(
    info: os.stat_result,
    shown: str,
    suffix: str,
    max_bytes: int,
    allowed_extensions: frozenset[str],
) -> None:
    """Apply every file gate to the descriptor actually opened."""
    if not stat.S_ISREG(info.st_mode):
        raise PathDenied("only regular files can be read", relative_path=shown)
    if info.st_uid != os.geteuid():
        raise PathDenied("that file is not readable here", relative_path=shown)
    if suffix.lower() not in allowed_extensions:
        raise UnsupportedMedia(shown)
    if info.st_size > max_bytes:
        raise TooLarge(shown)


def confine(
    root: Path,
    candidate: str,
    *,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    allowed_extensions: frozenset[str] = ALLOWED_EXTENSIONS,
) -> ConfinedFile:
    """Resolve ``candidate`` under ``root`` and apply every gate.

    Validation opens the same descriptor chain the read will open. The result
    is a *name*, not a handle: the read repeats the walk and re-checks the
    descriptor it gets, so nothing here is trusted later on faith.
    """
    relative = normalise_relative(candidate)
    shown = str(relative)
    handle, info = _open_confined(Path(root), relative, shown)
    try:
        _check_descriptor(
            info, shown, PurePosixPath(shown).suffix, max_bytes, allowed_extensions
        )
    finally:
        os.close(handle)
    return ConfinedFile(relative_path=shown, root=Path(root), size=info.st_size)


def read_bytes(
    confined: ConfinedFile,
    *,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    allowed_extensions: frozenset[str] = ALLOWED_EXTENSIONS,
) -> bytes:
    """Read a confined file's raw bytes through a fresh no-follow walk.

    The gates are applied to the descriptor this call opens, never to an
    earlier one. A path validated a moment ago whose directory has since been
    replaced by a symbolic link fails here with ``path_denied``.
    """
    relative = normalise_relative(confined.relative_path)
    shown = str(relative)
    handle, info = _open_confined(confined.root, relative, shown)
    try:
        _check_descriptor(
            info, shown, PurePosixPath(shown).suffix, max_bytes, allowed_extensions
        )
        chunks: list[bytes] = []
        read = 0
        while True:
            chunk = os.read(handle, _READ_CHUNK_BYTES)
            if not chunk:
                break
            read += len(chunk)
            if read > max_bytes:
                raise TooLarge(shown)
            chunks.append(chunk)
    finally:
        os.close(handle)
    return b"".join(chunks)


def read_text(
    confined: ConfinedFile,
    *,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    allowed_extensions: frozenset[str] = ALLOWED_EXTENSIONS,
) -> str:
    """Read a confined file as UTF-8 text."""
    raw = read_bytes(confined, max_bytes=max_bytes, allowed_extensions=allowed_extensions)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedMedia(confined.relative_path) from exc


def sha256_confined(
    confined: ConfinedFile,
    *,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    allowed_extensions: frozenset[str] = ALLOWED_EXTENSIONS,
) -> str:
    """Hex digest of a confined file, read through the no-follow walk."""
    return sha256_bytes(
        read_bytes(confined, max_bytes=max_bytes, allowed_extensions=allowed_extensions)
    )


def iter_files(
    root: Path,
    *,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    allowed_extensions: frozenset[str] = ALLOWED_EXTENSIONS,
) -> Iterator[ConfinedFile]:
    """Yield every readable file under ``root``, in deterministic order.

    Skips excluded directories, symbolic links at any level, non-regular
    files, unsupported extensions and oversized files silently — they are not
    errors, they are simply not part of the readable surface. Enumeration
    names candidates; the bytes are still read through the confined walk.
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
            yield ConfinedFile(relative_path=str(relative), root=root, size=info.st_size)
