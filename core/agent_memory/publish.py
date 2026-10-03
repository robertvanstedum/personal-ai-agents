"""Atomic publishing and crash recovery (agent-memory v0.4 §3.6).

Steps of one publish (a failure at any step publishes nothing):

1. write the new full set to ``.tmp-<run_id>/current/`` (manifest last);
2. validate it against its manifest;
3. stage the snapshot in ``.tmp-<run_id>/snapshot/`` (manifest last), validate;
4. publish the snapshot by one folder rename into ``history/<UTC time>/``;
5. COMMIT: rename ``current/`` to ``current.prev/``, then ``.tmp-…/current``
   to ``current/``; delete ``current.prev/`` and ``.tmp-…``.

``recover`` runs before every run. A leftover from an uncommitted run (its
``run_id`` is not the one in ``current/_manifest.json``) is rolled BACK: the
old ``current/`` is restored, that run's snapshot is removed, the temp folder
goes. A leftover from a committed run is only cleaned up.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from .config import Headroom
from .errors import DISK_LOW, CopierError
from .fsio import ensure_dir, fsync_dir, remove_tree, write_file
from .headroom import check_headroom
from .scrub import StoredFile, sha256_hex
from .snapshot import MANIFEST, build_manifest, dump_manifest, read_manifest, unique_snapshot_name

TMP_PREFIX = ".tmp-"


def _checkpoint(step: str) -> None:
    """Named steps between the publish stages. Tests replace this to inject a crash."""


def _write_set(folder: Path, files: list[StoredFile], manifest: dict) -> None:
    ensure_dir(folder)
    for stored in files:
        write_file(folder / stored.path, stored.data)
    write_file(folder / MANIFEST, dump_manifest(manifest))   # manifest last
    fsync_dir(folder)


def _validate(folder: Path, expected: list[StoredFile]) -> None:
    """The folder holds exactly the expected files with the expected sizes and hashes."""
    doc = read_manifest(folder)
    if doc is None or {e["path"] for e in doc["files"]} != {f.path for f in expected}:
        raise CopierError("internal")
    for stored in expected:
        data = (folder / stored.path).read_bytes()
        if len(data) != stored.size or sha256_hex(data) != stored.sha256:
            raise CopierError("internal")


def publish(source_dir: Path, *, run_id: str, now: datetime, source_meta: dict,
            stored: dict[str, StoredFile], changed: list[str], deleted: list[str],
            skipped: dict[str, int], headroom: Headroom) -> str:
    """Publish a validated, complete scan. Returns the new snapshot's name."""
    if check_headroom(source_dir, headroom.min_free_bytes, headroom.min_free_fraction):
        raise CopierError(DISK_LOW)
    tmp = source_dir / f"{TMP_PREFIX}{run_id}"
    new_dir, snap_dir = tmp / "current", tmp / "snapshot"
    everything = [stored[p] for p in sorted(stored)]
    changes = [stored[p] for p in changed]
    base = {"run_id": run_id, "captured_at": now, "source_meta": source_meta,
            "deleted": deleted, "skipped": skipped}
    _write_set(new_dir, everything, build_manifest(files=everything, **base))
    _checkpoint("tmp_written")
    _validate(new_dir, everything)
    _checkpoint("validated")
    _write_set(snap_dir, changes, build_manifest(files=changes, **base))
    _validate(snap_dir, changes)
    _checkpoint("snapshot_staged")
    history = source_dir / "history"
    ensure_dir(history)
    name = unique_snapshot_name(now, (d.name for d in history.iterdir()))
    os.replace(snap_dir, history / name)
    fsync_dir(history)
    _checkpoint("snapshot_published")
    current, prev = source_dir / "current", source_dir / "current.prev"
    if current.exists():
        os.replace(current, prev)
        _checkpoint("current_moved_aside")
    os.replace(new_dir, current)
    fsync_dir(source_dir)
    _checkpoint("current_swapped")
    remove_tree(prev)
    remove_tree(tmp)
    return name


def current_files(source_dir: Path) -> dict[str, str] | None:
    """{path: stored sha256} of the published ``current/``, ``{}`` if none yet, ``None`` if unusable."""
    folder = source_dir / "current"
    if not folder.exists():
        return {}
    doc = read_manifest(folder)
    if doc is None:
        return None
    return {e["path"]: e["sha256"] for e in doc["files"]}


def _run_id_of(folder: Path) -> str | None:
    doc = read_manifest(folder)
    return doc.get("run_id") if doc else None


def recover(source_dir: Path) -> None:
    """Clean or roll back whatever an interrupted run left behind (§3.6 rule 4)."""
    if not source_dir.is_dir():
        return
    current, prev = source_dir / "current", source_dir / "current.prev"
    committed = _run_id_of(current) if current.is_dir() else None
    for tmp in sorted(source_dir.glob(f"{TMP_PREFIX}*")):
        run_id = tmp.name[len(TMP_PREFIX):]
        if run_id != committed:
            if prev.is_dir() and not current.exists():
                os.replace(prev, current)           # roll back the swap
            _remove_snapshots_of(source_dir, run_id)
        remove_tree(tmp)
    if prev.exists():
        if current.exists():
            remove_tree(prev)
        else:
            os.replace(prev, current)
    history = source_dir / "history"
    if history.is_dir():
        for folder in history.iterdir():
            if folder.is_dir() and read_manifest(folder) is None:
                remove_tree(folder)                  # a snapshot without a manifest is incomplete
    fsync_dir(source_dir)


def _remove_snapshots_of(source_dir: Path, run_id: str) -> None:
    history = source_dir / "history"
    if not history.is_dir():
        return
    for folder in history.iterdir():
        if folder.is_dir() and _run_id_of(folder) == run_id:
            remove_tree(folder)


__all__ = ["current_files", "publish", "recover"]
