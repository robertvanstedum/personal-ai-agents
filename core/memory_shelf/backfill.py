"""Owner-reviewed backfill listing (v0.5 B4): Guild records and the Curator archive.

Only a **dry-run listing** is built here: path, size, date, sha256 -- metadata, never
content. Nothing is imported. Import needs an approval record for the exact
configuration (``approvals.approve_source``) and the import step itself is **not built
in this slice**: ``import_status`` says ``not_approved`` or ``import_not_built`` and
writes nothing. Files marked private (``selection.private_marked``) or matching
``never_copy`` are listed as skipped, with no hash and no content read beyond one line.

Sources (relative to the repo root):

* ``backfill-guild``: ``docs/DECISIONS.md``, ``docs/decision-records/**``, ``data/cos_memory.md``
* ``backfill-curator``: dated files under ``data/curator_archive/`` (a ``YYYY-MM-DD`` in the name)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

from core.memory_shelf import approvals, codes, fsio, selection
from core.memory_shelf.config import never_copied

SOURCES = {
    "backfill-guild": ("docs/DECISIONS.md", "docs/decision-records", "data/cos_memory.md"),
    "backfill-curator": ("data/curator_archive",),
}
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
UNDATED, NOT_REGULAR = "undated", "not_regular"
NOT_BUILT = "import_not_built"


def fingerprint(name: str, repo_root: Path, never_copy: tuple[str, ...]) -> str:
    doc = {"name": name, "root": str(repo_root), "paths": SOURCES[name], "never_copy": sorted(never_copy)}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()


def _files(name: str, root: Path) -> list[tuple[str, Path]]:
    found = []
    for entry in SOURCES[name]:
        base = root / entry
        if base.is_file() or base.is_symlink():
            found.append((entry, base))
        elif base.is_dir():
            for folder, dirs, files in os.walk(base, followlinks=False):
                dirs.sort()
                for fname in sorted(files):
                    path = Path(folder) / fname
                    found.append((path.relative_to(root).as_posix(), path))
    return found


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_listing(name: str, repo_root: str | Path, never_copy: tuple[str, ...] = (), now: datetime | None = None) -> dict:
    if name not in SOURCES:
        raise KeyError(name)
    root = Path(repo_root)
    rows = []
    for rel, path in _files(name, root):
        row = {"path": rel}
        try:
            st = path.lstat()
        except OSError:
            continue
        if path.is_symlink() or not stat.S_ISREG(st.st_mode):
            rows.append({**row, "decision": "skip", "reason": NOT_REGULAR})
            continue
        date = _DATE.search(path.name)
        row.update(bytes=st.st_size, date=date.group(1) if date else
                   datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d"))
        reason = None
        if never_copied(rel, never_copy):
            reason = codes.NEVER_COPY
        elif name == "backfill-curator" and not date:
            reason = UNDATED
        elif st.st_size == 0:
            reason = codes.EMPTY
        else:
            first = selection.first_line_of(path) if path.suffix in (".md", ".txt", "") else ""
            if selection.private_marked(path.name, first):
                reason = codes.PRIVATE
        if reason:
            rows.append({**row, "decision": "skip", "reason": reason})
        else:
            rows.append({**row, "decision": "would-import", "sha256": _sha(path)})
    skipped: dict[str, int] = {}
    for r in rows:
        if r["decision"] == "skip":
            skipped[r["reason"]] = skipped.get(r["reason"], 0) + 1
    importable = [r for r in rows if r["decision"] == "would-import"]
    digest = hashlib.sha256("\n".join(f"{r['path']}\t{r['sha256']}" for r in importable).encode()).hexdigest()
    return {"source": name, "fingerprint": fingerprint(name, root, never_copy),
            "generated_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "counts": {"would_import": len(importable), "skipped": skipped, "bytes": sum(r["bytes"] for r in importable)},
            "files": rows, "listing_sha256": digest}


def dry_run(shelf, name: str, repo_root: str | Path, never_copy: tuple[str, ...] = (),
            now: datetime | None = None) -> dict:
    shelf.init_layout()
    listing = build_listing(name, repo_root, never_copy, now)
    fsio.write_json(approvals.dry_run_path(shelf, name), listing)
    return listing


def import_status(shelf, name: str, repo_root: str | Path, never_copy: tuple[str, ...] = ()) -> str:
    """Never imports. ``no_dry_run`` | ``not_approved`` | ``stale`` | ``import_not_built`` (approved, still deferred)."""
    status = approvals.source_status(shelf, name, fingerprint(name, Path(repo_root), never_copy))
    return NOT_BUILT if status == approvals.APPROVED else status
