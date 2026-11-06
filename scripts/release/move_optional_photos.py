#!/usr/bin/env python3
"""Move optional photos out of git into stored data. Without --apply it only prints what it would do.

Run from a clean checkout of main (the release worktree), once Robert has picked where they live:

    python scripts/release/move_optional_photos.py                       # dry run: the exact list, nothing changes
    python scripts/release/move_optional_photos.py --apply --dest DIR    # copies, verifies, then git-removes

DIR is laid out like a host's data folder, so it copies onto Dev and production as it is:

    DIR/german/photos/<name>     host: $MINIMOI_ROOT/data/german/photos/<name>
    DIR/guild/photos/<name>      host: $MINIMOI_ROOT/data/guild/photos/<name>   (Guild art, the CoS photo)

Files are copied byte for byte and checked by SHA-256 before anything leaves git. A photo a page names is never moved,
even if the inventory says so. --apply stages the removal and the inventory update and commits nothing; moving the
folder to a host is a separate, deliberate step.

--include-unused also carries the images no page, README or test uses, under DIR/guild/photos/archive/, keeping their
repository paths. Images under docs/ or named only by documents stay put: docs/ is protected, and those documents link them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INVENTORY = REPO / "scripts" / "release" / "photo_inventory.json"
# Where pages name their images; a moving photo found here means the inventory is out of date.
PAGE_ROOTS = ("domains/german/templates", "domains/german/static", "domains/portuguese/templates",
              "domains/portuguese/static", "domains/cos/templates", "domains/cos/static", "domains/curator/templates",
              "minimoi_portal/templates", "minimoi_portal/guild_ui", "minimoi_portal/workspaces.py", "core/founders_ui")
PAGE_SUFFIXES = {".html", ".css", ".js", ".py"}
# A comment in german.css names this one as the alternate; no rule loads it. Inventory row: optional_photo.
COMMENT_ONLY = {"bg-alternate.jpg"}


def load_inventory(path: Path = INVENTORY) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["images"]


def target_for(path: str, row: dict) -> str:
    """Where this image lands, relative to DIR."""
    name = Path(path).name
    if row["class"] == "unused":
        return f"guild/photos/archive/{path}"
    return f"german/photos/{name}" if path.startswith("domains/german/") else f"guild/photos/{name}"


def plan(images: dict, include_unused: bool = False) -> list[tuple[str, str, int]]:
    """(repository path, target under DIR, bytes), in a stable order."""
    rows = []
    for path, row in sorted(images.items()):
        moves = row["class"] == "moves"
        carried = include_unused and row["class"] == "unused" and row["reason"] != "docs_only" and not path.startswith("docs/")
        if moves or carried:
            rows.append((path, target_for(path, row), row["bytes"]))
    targets = [target for _, target, _ in rows]
    if len(set(targets)) != len(targets):
        raise SystemExit("Two photos would land on the same target; nothing was changed.")
    return rows


def names_on_pages(repo: Path = REPO) -> str:
    """All text that pages and their stylesheets carry."""
    parts = []
    for root in PAGE_ROOTS:
        base = repo / root
        files = [base] if base.is_file() else base.rglob("*")
        parts.extend(f.read_text(encoding="utf-8", errors="ignore") for f in files
                     if f.is_file() and f.suffix in PAGE_SUFFIXES)
    return "\n".join(parts)


def still_on_a_page(rows: list[tuple[str, str, int]], page_text: str) -> list[str]:
    # The match is by file name. No app serves the root static/ folder, so a page naming bg-primary.jpg means its own
    # domain's copy, never that one.
    return [path for path, _, _ in rows
            if not path.startswith("static/") and Path(path).name not in COMMENT_ONLY and Path(path).name in page_text]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_verified(source: Path, target: Path) -> None:
    """Copy byte for byte and prove it. An existing different file at the target stops the run."""
    if target.exists() and sha256(target) != sha256(source):
        raise SystemExit(f"{target} already exists with different bytes; nothing further was changed.")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if sha256(target) != sha256(source):
        raise SystemExit(f"Copy of {source} did not verify; nothing was removed from git.")


def check_dest(dest: Path, repo: Path = REPO) -> None:
    resolved = dest.resolve()
    if resolved == repo or repo in resolved.parents:
        raise SystemExit("--dest must be outside the repository: these files are leaving git.")


def apply(rows: list[tuple[str, str, int]], dest: Path, images: dict) -> None:
    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, check=True, capture_output=True, text=True)
    if status.stdout.strip():
        raise SystemExit("The checkout is not clean; run from a clean release worktree.")
    for path, target, _ in rows:
        copy_verified(REPO / path, dest / target)
    for path, _, _ in rows:               # second pass: remove nothing until every copy has verified
        subprocess.run(["git", "rm", "-q", "--", path], cwd=REPO, check=True)
        del images[path]
    INVENTORY.write_text(json.dumps({"images": images}, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", str(INVENTORY.relative_to(REPO))], cwd=REPO, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="really move; the default is a dry run")
    parser.add_argument("--dest", type=Path, help="the stored-data folder to fill; required with --apply")
    parser.add_argument("--include-unused", action="store_true", help="also carry unused images, into the archive")
    args = parser.parse_args(argv)

    if args.apply and args.dest is None:
        parser.error("--apply needs --dest")
    if args.dest is not None:
        check_dest(args.dest)

    images = load_inventory()
    rows = plan(images, args.include_unused)
    blocked = still_on_a_page(rows, names_on_pages())
    if blocked:
        print("These are named by a page and must not move; fix the inventory first:", *blocked, sep="\n  ")
        return 2

    shown_dest = args.dest if args.dest is not None else Path("<DIR>")
    for path, target, size in rows:
        print(f"{size / 1e6:8.2f} MB  {path}  ->  {shown_dest / target}")
    print(f"{len(rows)} files, {sum(size for _, _, size in rows) / 1e6:.1f} MB")

    if not args.apply:
        print("Dry run: nothing was copied or removed.")
        return 0
    apply(rows, args.dest, images)
    print(f"Copied and verified into {args.dest}; removals and inventory staged, not committed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
