"""The owner's review queue (amendment R2, R3): metadata only, never content.

Items wait here for Robert: ``designation-candidate`` (a marker found in a paste,
whose speakers are only guessed), ``possible-same-conversation`` (title, date or
first-turn overlap, never a merge). One JSON file per item under
``_status/review/``, keyed by ``<type>--<name>``, so re-detecting the same thing
never makes a second item. Resolving keeps the file (state ``resolved``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.memory_shelf import fsio

TYPES = frozenset({"designation-candidate", "possible-same-conversation"})


def _dir(shelf) -> Path:
    return Path(shelf.status_dir) / "review"


def add(shelf, type_: str, name: str, detail: dict) -> bool:
    """Add an open item unless one with this type and name exists. True when added."""
    if type_ not in TYPES:
        raise ValueError("unknown review item type")
    path = _dir(shelf) / f"{type_}--{name}.json"
    if path.exists():
        return False
    fsio.write_json(path, {"type": type_, "name": name, "state": "open", "detail": detail,
                           "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return True


def items(shelf, *, state: str | None = "open") -> list[dict]:
    out = []
    folder = _dir(shelf)
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            doc = fsio.read_json(path)
            if isinstance(doc, dict) and (state is None or doc.get("state") == state):
                out.append(doc)
    return out


def resolve(shelf, type_: str, name: str, how: str) -> bool:
    path = _dir(shelf) / f"{type_}--{name}.json"
    doc = fsio.read_json(path)
    if not isinstance(doc, dict) or doc.get("state") != "open":
        return False
    doc.update(state="resolved", resolved=how,
               resolved_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    fsio.write_json(path, doc)
    return True
