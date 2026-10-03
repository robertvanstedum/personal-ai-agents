"""Where memory files are read from (agent-memory v0.4 §3.3, §3.6, §4).

A source returns a ``SourceScan``. ``complete`` is the only thing that lets the
copier compute deletions: a partial scan publishes nothing (§3.6 rule 5).

* ``DirectorySource``: a folder (tests, and the Mac inbox of §4). For the Mac
  it requires a complete ``_copy_manifest.json`` whose count matches.
* ``TarSource``: a tar archive from a callable, read to its end.
* ``DockerArchiveSource``: the Docker API reader (``GET /containers/<name>/archive``,
  read-only, one deadline), whose tar bytes go through ``TarSource``.

Names rejected before reading are kept in ``SourceScan.rejected`` in memory
only, so the terminal-only dry run can show them; the copier uses just counts.
"""
from __future__ import annotations

import http.client
import io
import json
import os
import socket
import stat
import tarfile
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import quote

from .errors import COPY_INCOMPLETE, DOCKER_TIMEOUT, DOCKER_UNREACHABLE, MODE_UNREADABLE, CopierError
from .selection import HIDDEN, MAX_BYTES, SPECIAL, SYMLINK, TOO_LARGE, UNSAFE_PATH, normalize_path

# relative name -> refusal code, or None to read the file
Accept = Callable[[str], "str | None"]
COPY_MANIFEST = "_copy_manifest.json"


@dataclass
class SourceScan:
    complete: bool
    files: dict[str, bytes] = field(default_factory=dict)
    rejected: dict[str, str] = field(default_factory=dict)
    code: str | None = None            # fixed failure code when incomplete
    data_time: datetime | None = None  # Mac: copied_at; None means "time of the read"

    def reason_counts(self) -> Counter:
        return Counter(self.rejected.values())


def incomplete(code: str = COPY_INCOMPLETE) -> SourceScan:
    return SourceScan(complete=False, code=code)


class Source(Protocol):
    def read(self, accept: Accept | None = None) -> SourceScan: ...


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


class DirectorySource:
    """Read ``*`` files under a folder; symlinks and hidden folders are never followed."""

    def __init__(self, path: str | os.PathLike[str], *, require_copy_manifest: bool = False) -> None:
        self._root = Path(path)
        self._require_manifest = require_copy_manifest

    def read(self, accept: Accept | None = None) -> SourceScan:
        try:
            return self._read(accept)
        except PermissionError:
            return incomplete(MODE_UNREADABLE)
        except OSError:
            return incomplete(COPY_INCOMPLETE)

    def _copy_manifest(self) -> tuple[int, datetime] | None:
        """(file count, copied_at) of a complete Mac copy, else None."""
        path = self._root / COPY_MANIFEST
        if path.is_symlink() or not path.is_file():
            return None
        try:
            doc = json.loads(path.read_text("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(doc, dict) or doc.get("complete") is not True:
            return None
        count, copied = doc.get("file_count"), _parse_utc(doc.get("copied_at"))
        if not isinstance(count, int) or isinstance(count, bool) or count < 0 or copied is None:
            return None
        return count, copied

    def _read(self, accept: Accept | None) -> SourceScan:
        if self._root.is_symlink() or not self._root.is_dir():
            return incomplete()
        manifest = self._copy_manifest() if self._require_manifest else None
        if self._require_manifest and manifest is None:
            return incomplete()
        scan = SourceScan(complete=True)
        regular = 0
        stack = [""]
        while stack:
            prefix = stack.pop()
            with os.scandir(self._root / prefix if prefix else self._root) as entries:
                for entry in sorted(entries, key=lambda e: e.name):
                    rel = f"{prefix}/{entry.name}" if prefix else entry.name
                    if entry.is_symlink():
                        scan.rejected[rel] = SYMLINK
                    elif entry.is_dir(follow_symlinks=False):
                        if entry.name.startswith("."):
                            scan.rejected[rel + "/"] = HIDDEN
                        else:
                            stack.append(rel)
                    elif entry.is_file(follow_symlinks=False):
                        if rel == COPY_MANIFEST:
                            continue
                        regular += 1
                        self._take(scan, entry, rel, accept)
                    else:
                        scan.rejected[rel] = SPECIAL
        if manifest is not None:
            if manifest[0] != regular:
                return incomplete()
            scan.data_time = manifest[1]
        return scan

    @staticmethod
    def _take(scan: SourceScan, entry: os.DirEntry, rel: str, accept: Accept | None) -> None:
        reason = accept(rel) if accept else None
        if reason is None and entry.stat(follow_symlinks=False).st_size > MAX_BYTES:
            reason = TOO_LARGE
        if reason is not None:
            scan.rejected[rel] = reason
            return
        fd = os.open(entry.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                scan.rejected[rel] = SPECIAL
                return
            scan.files[rel] = handle.read(MAX_BYTES + 1)


class TarSource:
    """Read a tar archive to its end. A truncated archive is an incomplete scan (T2).

    ``read_bytes`` returns the archive bytes (or raises ``CopierError`` with a
    fixed code). Docker's archive nests everything under one top folder, which
    ``strip_components`` removes.
    """

    def __init__(self, read_bytes: Callable[[], bytes], *, strip_components: int = 1) -> None:
        self._read_bytes = read_bytes
        self._strip = strip_components

    def read(self, accept: Accept | None = None) -> SourceScan:
        blob = self._read_bytes()
        # A real tar ends with at least two zero blocks; cut-off archives do not.
        if len(blob) < 1024 or any(blob[-1024:]):
            return incomplete()
        try:
            return self._scan(blob, accept)
        except (tarfile.TarError, EOFError, OSError, ValueError):
            return incomplete()

    def _name(self, member_name: str) -> str | None:
        parts = [p for p in member_name.split("/") if p not in ("", ".")]
        if len(parts) < self._strip:
            return None
        return "/".join(parts[self._strip:]) or None

    def _scan(self, blob: bytes, accept: Accept | None) -> SourceScan:
        scan = SourceScan(complete=True)
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as archive:
            for member in archive:
                if member.offset_data + member.size > len(blob):
                    return incomplete()
                if member.name.startswith("/") or ".." in member.name.split("/"):
                    scan.rejected[member.name] = UNSAFE_PATH
                    continue
                rel = self._name(member.name)
                if rel is None or member.isdir():
                    continue
                if member.issym() or member.islnk():
                    scan.rejected[rel] = SYMLINK
                elif not member.isreg():
                    scan.rejected[rel] = SPECIAL
                elif normalize_path(rel) is None:
                    scan.rejected[rel] = UNSAFE_PATH
                elif (reason := accept(rel) if accept else None) is not None:
                    scan.rejected[rel] = reason
                elif member.size > MAX_BYTES:
                    scan.rejected[rel] = TOO_LARGE
                else:
                    handle = archive.extractfile(member)
                    data = handle.read() if handle else b""
                    if len(data) != member.size:
                        return incomplete()
                    scan.files[rel] = data
        return scan


MAX_ARCHIVE_BYTES = 64 * 1024 * 1024        # a bigger archive is refused as incomplete, never half-read


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTP over a unix socket (the Docker API), with its own timeout."""

    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


class DockerArchiveSource:
    """Read a container folder through the Docker API (agent-memory v0.4 §3.3, §6).

    ``GET /containers/<name>/archive?path=<folder>`` over the Docker socket:
    **read-only, the only request this class ever makes; never exec.** One
    overall deadline (``timeout_s``, default 10 s) covers connect, request and
    the whole body. Failures are fixed codes with no text: ``docker_unreachable``
    (no socket, refused, a non-200 answer, a missing container), ``docker_timeout``,
    and ``copy_incomplete`` (a cut-off or oversized body, or an unfinished tar).
    """

    def __init__(self, container: str, workspace_path: str, timeout_s: float = 10.0,
                 socket_path: str = "/var/run/docker.sock") -> None:
        self.container, self.workspace_path, self.timeout_s = container, workspace_path, timeout_s
        self.socket_path = socket_path

    def _fetch(self) -> bytes:
        deadline = time.monotonic() + self.timeout_s
        url = f"/containers/{quote(self.container, safe='')}/archive?path={quote(self.workspace_path, safe='')}"
        conn = _UnixHTTPConnection(self.socket_path, self.timeout_s)
        try:
            conn.request("GET", url)
            resp = conn.getresponse()
            if resp.status != 200:
                raise CopierError(DOCKER_UNREACHABLE)
            declared = resp.getheader("Content-Length")
            expected = int(declared) if declared and declared.isdigit() else None
            if expected is not None and expected > MAX_ARCHIVE_BYTES:
                raise CopierError(COPY_INCOMPLETE)        # refuse an oversized body before reading it
            chunks, total = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise CopierError(DOCKER_TIMEOUT)
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise CopierError(COPY_INCOMPLETE)
                chunks.append(chunk)
            if expected is not None and total != expected:
                raise CopierError(COPY_INCOMPLETE)        # the connection dropped before the whole body arrived
            return b"".join(chunks)
        except CopierError:
            raise
        except (socket.timeout, TimeoutError):
            raise CopierError(DOCKER_TIMEOUT) from None
        except http.client.IncompleteRead:
            raise CopierError(COPY_INCOMPLETE) from None
        except (OSError, http.client.HTTPException):
            raise CopierError(DOCKER_UNREACHABLE) from None
        finally:
            conn.close()

    def read(self, accept: Accept | None = None) -> SourceScan:
        return TarSource(self._fetch, strip_components=1).read(accept)


__all__ = ["Accept", "DirectorySource", "DockerArchiveSource", "Source", "SourceScan", "TarSource", "incomplete"]
