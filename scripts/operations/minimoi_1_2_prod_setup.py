#!/usr/bin/env python3
"""MiniMoi 1.2's production setup: folders and generated secrets the opt-in compose layers require.

``check`` (the default) reads only and changes nothing: it reports each path as present, missing or unsafe.
``apply`` creates only what is missing. It never overwrites, deletes or prints a value, and it refuses to run on anything
but production's own host unless given a throwaway ``--root`` for a test. Provider sign-ins, Robert's owner approval and
anything a person must type are listed as ``by_hand``: this script never handles them.

Records' own state and key come from ``services/records_prepare.py`` and ``scripts/rooms/production_plan.py``; this script
does not make them.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import stat
import sys

SECRETS = "secrets/1-2"
STATE = "state/1-2"

FOLDERS = (
    "cos/store",
    f"{STATE}/cos-turns", f"{STATE}/cos-partnership", f"{STATE}/mc-turns", f"{STATE}/partnership",
    f"{STATE}/cos-guildhall", f"{STATE}/guildhall-cos-journal", f"{STATE}/guildhall-mc-journal",
    f"{STATE}/model-routes",
    f"{SECRETS}/cos-bound", f"{SECRETS}/cos-actions-token", f"{SECRETS}/cos-partnership", f"{SECRETS}/portal-partnership",
    f"{SECRETS}/partnership", f"{SECRETS}/cos-board", f"{SECRETS}/cos-guildhall", f"{SECRETS}/guildhall-cos",
    f"{SECRETS}/guildhall-mc", f"{SECRETS}/mc-rooms-work",
)

# Random values this script may generate. Each is used by exactly the services its folder is mounted to.
GENERATED = (
    f"{SECRETS}/cos-actions-token/token",
    f"{SECRETS}/cos-partnership/cos-owner.key", f"{SECRETS}/portal-partnership/portal-cos-owner.key",
    f"{SECRETS}/portal-partnership/portal-partnership-owner.key", f"{SECRETS}/partnership/owner.key",
    f"{SECRETS}/partnership/drainer.token", f"{SECRETS}/partnership/notices.token",
    f"{SECRETS}/cos-board/cursor.key",
    f"{SECRETS}/cos-guildhall/cos-service.token", f"{SECRETS}/guildhall-cos/discussion-gateway.token",
    f"{SECRETS}/guildhall-mc/mc-service.token", f"{SECRETS}/guildhall-mc/discussion-gateway.token",
)

# Files only a reviewed operator can write, because their content binds identities or comes from another service.
BY_OPERATOR = (
    f"{SECRETS}/cos-bound/bindings.json",
    f"{SECRETS}/partnership/bindings.json", f"{SECRETS}/partnership/grants.json", f"{SECRETS}/partnership/runner-seats.json",
    f"{SECRETS}/partnership/db.url", f"{SECRETS}/partnership/mc.token",
    f"{SECRETS}/portal-partnership/mc.token", f"{SECRETS}/cos-partnership/tasks-cos.token",
    f"{SECRETS}/cos-partnership/tasks-platform.token", f"{SECRETS}/cos-partnership/board-profile.json",
    f"{SECRETS}/cos-board/profiles.json",
    f"{SECRETS}/cos-guildhall/meeting-observe.token", f"{SECRETS}/guildhall-cos/rooms-worker-cos.token",
    f"{SECRETS}/guildhall-cos/cos.token", f"{SECRETS}/guildhall-cos/cos-service.token",
    f"{SECRETS}/guildhall-mc/rooms-worker.token", f"{SECRETS}/guildhall-mc/mc.token",
    f"{SECRETS}/guildhall-mc/mc-relay.token", f"{SECRETS}/mc-rooms-work/work.token",
    f"{STATE}/model-routes/catalogue.json",
)

BY_HAND = (
    "ChatGPT plan sign-in on the production host, for MC and for CoS",
    "Claude plan sign-in for MC on production, if its relay needs one",
    "Robert's approval of MC's production policy",
    "Robert logs in again after the identity changes",
)


def _safe(path: Path, *, directory: bool) -> bool:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return False
    if directory:
        return stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o077
    return stat.S_ISREG(info.st_mode) and not info.st_mode & 0o077


def status(root: Path, rel: str, *, directory: bool) -> str:
    path = root / rel
    if not os.path.lexists(path):
        return "missing"
    return "present" if _safe(path, directory=directory) else "unsafe"


def report(root: Path) -> dict:
    rows = {"folders": {r: status(root, r, directory=True) for r in FOLDERS},
            "generated": {r: status(root, r, directory=False) for r in GENERATED},
            "by_operator": {r: status(root, r, directory=False) for r in BY_OPERATOR}}
    counts = {}
    for group in rows.values():
        for value in group.values():
            counts[value] = counts.get(value, 0) + 1
    return {**rows, "by_hand": list(BY_HAND), "counts": counts}


def apply(root: Path) -> dict:
    before = report(root)
    unsafe = [r for group in ("folders", "generated", "by_operator") for r, v in before[group].items() if v == "unsafe"]
    if unsafe:
        raise SystemExit(f"refused: unsafe paths, nothing changed: {sorted(unsafe)}")
    made = []
    for rel in FOLDERS:
        path = root / rel
        if not path.exists():
            path.mkdir(parents=True, mode=0o700)
            path.chmod(0o700)
            made.append(rel)
    for rel in GENERATED:
        path = root / rel
        if not path.exists():
            # O_EXCL: never overwrites, even in a race. The value is never returned or printed.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(secrets.token_urlsafe(48))
            made.append(rel)
    return {"made": made, "after": report(root)["counts"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", nargs="?", choices=("check", "apply"), default="check")
    parser.add_argument("--root", default="/opt/minimoi")
    args = parser.parse_args(argv)
    root = Path(args.root)
    if args.mode == "apply" and args.root == "/opt/minimoi" and os.environ.get("MINIMOI_1_2_SETUP_APPROVED") != "1":
        raise SystemExit("refused: applying to production needs MINIMOI_1_2_SETUP_APPROVED=1, set by the reviewed operator")
    print(json.dumps(apply(root) if args.mode == "apply" else report(root), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
