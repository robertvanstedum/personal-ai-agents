"""Immutable source editions (amendment R3).

A record keeps the original bytes of every edition it has ever been built from.
They are never overwritten, and a new export never silently replaces an
earlier paste. Each edition is a file ``editions/<n>--<sha256-12>.<ext>`` next
to the record. Adding bytes that are already an edition is a no-op (the same
conversation ingested twice yields one edition). An existing file is never
rewritten, even with the same name.

Matching two sources as "the same conversation" is **not done here**: that
needs an exact source conversation id (see ``same_source``), and anything
weaker is only a *possible* match for the owner to confirm.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

_NAME = re.compile(r"^(\d+)--([0-9a-f]{12})\.([a-z0-9]+)$")


@dataclass(frozen=True)
class Edition:
    number: int
    sha256: str
    path: Path
    added: bool                      # False when these exact bytes were already an edition


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def list_editions(record_dir: Path) -> list[Edition]:
    folder = Path(record_dir) / "editions"
    found = []
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            m = _NAME.match(path.name)
            if m and path.is_file():
                found.append(Edition(int(m.group(1)), "", path, False))
    return sorted(found, key=lambda e: e.number)


def find(record_dir: Path, raw: bytes) -> Edition | None:
    """The edition holding exactly these bytes, or None."""
    full = digest(raw)
    for edition in list_editions(record_dir):
        if _NAME.match(edition.path.name).group(2) == full[:12] and digest(edition.path.read_bytes()) == full:
            return Edition(edition.number, full, edition.path, False)
    return None


def add_edition(record_dir: Path, raw: bytes, ext: str = "txt") -> Edition:
    """Store ``raw`` as the next edition unless these exact bytes are already one."""
    ext = re.sub(r"[^a-z0-9]", "", ext.lower()) or "txt"
    full = digest(raw)
    folder = Path(record_dir) / "editions"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = list_editions(record_dir)
    for edition in existing:
        if _NAME.match(edition.path.name).group(2) == full[:12] and digest(edition.path.read_bytes()) == full:
            return Edition(edition.number, full, edition.path, False)
    number = (existing[-1].number if existing else 0) + 1
    target = folder / f"{number}--{full[:12]}.{ext}"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)      # never overwrites
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    return Edition(number, full, target, True)


def same_source(a: dict, b: dict) -> bool:
    """Auto-match only on an exact source conversation id from the same provider."""
    ka, kb = a.get("source_id"), b.get("source_id")
    return bool(ka) and ka == kb and a.get("provider") == b.get("provider")


__all__ = ["Edition", "find", "add_edition", "list_editions", "digest", "same_source"]
