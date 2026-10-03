"""Manifests, diffs and state rebuilding (agent-memory v0.4 §3.1, §3.2, T1).

Pure helpers with no publishing logic. A snapshot folder holds only the files
whose stored bytes changed, plus its manifest's ``deleted`` list. The state at
any snapshot is rebuilt by replaying snapshots in order: a path's newest copy
at or before that snapshot, minus the deletions.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .scrub import StoredFile, sha256_hex

SCHEMA_VERSION = 1
MANIFEST = "_manifest.json"


def utc_stamp(moment: datetime) -> str:
    """UTC capture time as a folder name, e.g. ``2026-10-02T101500Z``."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def unique_snapshot_name(moment: datetime, taken: Iterable[str]) -> str:
    """The UTC name for this run; two runs in one second move on to the next free second."""
    taken_set = set(taken)
    probe = moment.astimezone(timezone.utc).replace(microsecond=0)
    while utc_stamp(probe) in taken_set:
        probe += timedelta(seconds=1)
    return utc_stamp(probe)


def build_manifest(*, run_id: str, captured_at: datetime, source_meta: dict, files: Iterable[StoredFile],
                   deleted: Iterable[str], skipped: dict[str, int]) -> dict:
    """The ``_manifest.json`` document of §3.2: relative paths, labels, counts, no names in ``skipped``."""
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "captured_at": iso_utc(captured_at),
        "source": dict(source_meta),
        "files": [f.manifest_entry() for f in sorted(files, key=lambda f: f.path)],
        "deleted": sorted(deleted),
        "skipped": dict(sorted(skipped.items())),
    }


def dump_manifest(doc: dict) -> bytes:
    return (json.dumps(doc, indent=2, sort_keys=False) + "\n").encode("utf-8")


def read_manifest(folder: Path) -> dict | None:
    """The manifest of a ``current/`` or snapshot folder, or ``None`` if absent or unusable."""
    try:
        doc = json.loads((folder / MANIFEST).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION or not isinstance(doc.get("files"), list):
        return None
    return doc


def diff(previous: dict[str, str], new: dict[str, StoredFile]) -> tuple[list[str], list[str]]:
    """(changed-or-new paths, deleted paths) by stored hash. Call only after a COMPLETE scan."""
    changed = sorted(p for p, f in new.items() if previous.get(p) != f.sha256)
    deleted = sorted(p for p in previous if p not in new)
    return changed, deleted


def snapshot_names(source_dir: Path) -> list[str]:
    """Names of complete snapshots (those with a manifest), oldest first."""
    history = source_dir / "history"
    if not history.is_dir():
        return []
    return sorted(d.name for d in history.iterdir() if d.is_dir() and read_manifest(d) is not None)


def rebuild_state(source_dir: Path, upto: str | None = None) -> dict[str, bytes]:
    """Reconstruct the file set at snapshot ``upto`` (default: the newest one)."""
    state: dict[str, bytes] = {}
    for name in snapshot_names(source_dir):
        if upto is not None and name > upto:
            break
        folder = source_dir / "history" / name
        doc = read_manifest(folder)
        for entry in doc["files"]:
            data = (folder / entry["path"]).read_bytes()
            if sha256_hex(data) != entry["sha256"]:
                raise ValueError("snapshot file does not match its manifest")
            state[entry["path"]] = data
        for gone in doc.get("deleted", []):
            state.pop(gone, None)
    return state
