"""The standing worker's private durable spool (standing spec §5.1). Not a task list.

Layout under ``MC_STANDING_STATE_ROOT``: observations/, findings/, cycles/, outbox/, delivery/.
Files 0600, directories 0700. Writes are temp-file + fsync + rename + directory fsync, so a crash
leaves either the old or the new file, never half of one. Records are immutable: writing an
existing record with different bytes is refused (``SpoolConflict``); the same bytes is a no-op.
Symlinks anywhere under the root are refused. A disk-full or corrupt read fails closed.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

DIRS = ("observations", "findings", "cycles", "outbox", "delivery")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,150}$")
MAX_RECORD_BYTES = 256 * 1024


class SpoolError(RuntimeError):
    pass


class SpoolConflict(SpoolError):
    """A different record already exists under this immutable name."""


def _no_symlink(path: Path):
    if path.is_symlink():
        raise SpoolError("spool_symlink_refused")


class Spool:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _no_symlink(self.root)
        os.chmod(self.root, 0o700)
        for d in DIRS:
            p = self.root / d
            p.mkdir(mode=0o700, exist_ok=True)
            _no_symlink(p)
            os.chmod(p, 0o700)

    def _path(self, kind: str, name: str) -> Path:
        if kind not in DIRS or not _NAME.match(name):
            raise SpoolError("spool_name_invalid")
        path = self.root / kind / f"{name}.json"
        _no_symlink(path)
        return path

    def read(self, kind: str, name: str) -> dict | None:
        path = self._path(kind, name)
        if not path.exists():
            return None
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
            raise SpoolError("spool_record_invalid")
        try:
            return json.loads(path.read_bytes())
        except ValueError:
            raise SpoolError("spool_record_corrupt") from None

    def _write(self, path: Path, data: bytes):
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _encode(doc: dict) -> bytes:
        data = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        if len(data) > MAX_RECORD_BYTES:
            raise SpoolError("spool_record_too_large")
        return data

    def put_immutable(self, kind: str, name: str, doc: dict) -> bool:
        """Write once. True if written now, False if the identical record already existed."""
        path = self._path(kind, name)
        data = self._encode(doc)
        if path.exists():
            if path.read_bytes() == data:
                return False
            raise SpoolConflict(f"{kind}/{name} differs from the stored record")
        self._write(path, data)
        return True

    def put_state(self, kind: str, name: str, doc: dict):
        """Replace a small state record (cycle or outbox state) atomically."""
        self._write(self._path(kind, name), self._encode(doc))

    def names(self, kind: str) -> list[str]:
        if kind not in DIRS:
            raise SpoolError("spool_name_invalid")
        return sorted(p.name[:-5] for p in (self.root / kind).iterdir()
                      if p.name.endswith(".json") and not p.name.startswith(".") and not p.is_symlink())
