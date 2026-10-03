"""The canary (amendment R4): a fixed synthetic conversation that proves the whole path.

The **producer** (``emit``) is write-scoped to the inbox: it takes only the inbox
folder and writes one file, ``canary-YYYY-MM-DD.canary.json``, never touching the
shelf. The inbox normalizer turns it into a record with ``kind: canary``,
``scope: robert``, tier raw, and the dated id as its source id. Canaries are
excluded from ordinary listings, ledger totals and queries; they appear only when
asked for by name (``--canary``). The normalizer accepts a canary file only if its
turns are **exactly** the fixed ones, so the file can carry no other content.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "minimoi-canary/1"
TURNS = (
    ("human", "Canary check: please confirm the memory path is carrying this conversation."),
    ("assistant", "Canary confirmed. This is a fixed synthetic conversation, not real memory."),
    ("human", "Thank you. Nothing here is real data."),
)


def canary_id(day: str) -> str:
    return f"canary-{day}"


def document(day: str) -> dict:
    return {"format": FORMAT, "id": canary_id(day),
            "turns": [{"speaker": s, "text": t} for s, t in TURNS]}


def valid(doc: object) -> bool:
    if not (isinstance(doc, dict) and doc.get("format") == FORMAT and isinstance(doc.get("id"), str)):
        return False
    day = doc["id"].removeprefix("canary-")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return False
    return doc == document(day)


def emit(inbox_root: str | Path, now: datetime | None = None) -> Path:
    """Write today's canary into the inbox (never over an existing file)."""
    from core.memory_shelf import fsio
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    path = Path(inbox_root) / f"{canary_id(day)}.canary.json"
    fsio.ensure_dir(path.parent)
    data = (json.dumps(document(day), sort_keys=True) + "\n").encode("utf-8")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    return path
