#!/usr/bin/env python3
"""MiniMoi 1.2's production setup: folders and generated secrets the opt-in compose layers require.

``check`` (the default) reads only and changes nothing: it reports each path as present, missing or unsafe.
``apply`` creates only what is missing. It never overwrites, deletes or prints a value. Production is the resolved
path ``/opt/minimoi``, including a trailing slash or a dot segment, and apply there needs
``MINIMOI_1_2_SETUP_APPROVED=1``. A symlink at the root, or in an ancestor, is refused before that comparison and
before any write. A throwaway ``--root`` is for a test. Provider sign-ins, Robert's owner approval and anything a
person must type are listed as ``by_hand``: this script never handles them.

An assertion pair is one ``kid:secret`` line, identical on both sides, as ``core.portal_assertion.load_key`` reads it.
Discussion gateway tokens are operator-required: this script does not invent a gateway key.

Ownership is the numeric uid and gid of the container that reads the path. It is not a host account name. Apply
refuses, and does not report success, when it cannot set that identity.

Records' own state and key come from ``services/records_prepare.py`` and ``scripts/rooms/production_plan.py``; this script
does not make them.

Run it on the host alone: it is this one file, on the Python standard library, with no copy of ``core`` and no
PYTHONPATH. ``scripts/operations/minimoi_1_2_prod_bind.py`` writes the by-operator files listed below.
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
PRODUCTION = Path("/opt/minimoi")

# Measured with docker exec. Not a passwd lookup.
# 1000: minimoi-cos-agent-a, minimoi-mc-agent, minimoi-mc-relay (Dev and production).
# 0: portal and cos-scheduler (Dev and production). partnership-tasks, partnership-worker,
# guildhall-cos, guildhall-mc, cos-board and records are root on Dev and are not on production yet.
NODE = (1000, 1000)
ROOT = (0, 0)

# The node consumer must be able to read these, including the operator-written bindings.
NODE_PATHS = (
    f"{SECRETS}/cos-bound",
    f"{SECRETS}/cos-bound/token",
    f"{SECRETS}/cos-actions-token",
    f"{SECRETS}/cos-actions-token/token",
)

FOLDERS = (
    "cos/store",
    f"{STATE}/cos-turns", f"{STATE}/cos-partnership", f"{STATE}/mc-turns", f"{STATE}/partnership",
    f"{STATE}/cos-guildhall", f"{STATE}/guildhall-cos-journal", f"{STATE}/guildhall-mc-journal",
    f"{STATE}/model-routes",
    f"{SECRETS}/cos-bound", f"{SECRETS}/cos-actions-token", f"{SECRETS}/cos-partnership", f"{SECRETS}/portal-partnership",
    f"{SECRETS}/partnership", f"{SECRETS}/cos-board", f"{SECRETS}/cos-guildhall", f"{SECRETS}/guildhall-cos",
    f"{SECRETS}/guildhall-mc", f"{SECRETS}/mc-rooms-work",
)

# A folder with two consumers isn't private to one uid. cos/store is written by cos-scheduler (root) and read by CoS's
# runtime through group 1000. This must equal `install -d -m 2750 -o root -g 1000` in cos_prod.sh prepare, which runs on
# every release: then apply, key and prepare can come in any order, any number of times.
SHARED_FOLDERS = {"cos/store": (0o2750, 0, 1000)}

# (portal signer, service reader). Both files are the same kid:secret line.
ASSERTION_PAIRS = (
    (f"{SECRETS}/portal-partnership/portal-cos-owner.key", f"{SECRETS}/cos-partnership/cos-owner.key"),
    (f"{SECRETS}/portal-partnership/portal-partnership-owner.key", f"{SECRETS}/partnership/owner.key"),
)

# Random values this script may generate. Each is used by exactly the services its folder is mounted to.
GENERATED = (
    f"{SECRETS}/cos-actions-token/token",
    f"{SECRETS}/cos-partnership/cos-owner.key", f"{SECRETS}/portal-partnership/portal-cos-owner.key",
    f"{SECRETS}/portal-partnership/portal-partnership-owner.key", f"{SECRETS}/partnership/owner.key",
    f"{SECRETS}/partnership/drainer.token", f"{SECRETS}/partnership/notices.token",
    f"{SECRETS}/cos-board/cursor.key",
    f"{SECRETS}/cos-guildhall/cos-service.token",
    f"{SECRETS}/guildhall-mc/mc-service.token",
)

# Files only a reviewed operator can write, because their content binds identities or comes from another service.
# The discussion tokens are gateway-issued capped keys, not local random strings.
# cos-bound/token: the plugin reads it (docker/cos-agent-a/openclaw.json tokenFile); only its hash leaves the file.
# No reader exists for a mc.token in partnership/: the task service keeps MC's hash in bindings.json, the portal the token.
BY_OPERATOR = (
    f"{SECRETS}/cos-bound/token",
    f"{SECRETS}/partnership/bindings.json", f"{SECRETS}/partnership/grants.json", f"{SECRETS}/partnership/runner-seats.json",
    f"{SECRETS}/partnership/db.url",
    f"{SECRETS}/portal-partnership/mc.token", f"{SECRETS}/portal-partnership/platform.token", f"{SECRETS}/cos-partnership/tasks-cos.token",
    f"{SECRETS}/cos-partnership/tasks-platform.token", f"{SECRETS}/cos-partnership/board-profile.json",
    f"{SECRETS}/cos-partnership/voice-summary-cos.key", f"{SECRETS}/portal-partnership/voice-summary-mc.key",
    f"{SECRETS}/cos-board/profiles.json", f"{SECRETS}/cos-board/db.url",
    f"{SECRETS}/cos-guildhall/meeting-observe.token", f"{SECRETS}/guildhall-cos/rooms-worker-cos.token",
    f"{SECRETS}/guildhall-cos/cos.token", f"{SECRETS}/guildhall-cos/cos-service.token",
    f"{SECRETS}/guildhall-cos/discussion-gateway.token",
    f"{SECRETS}/guildhall-mc/rooms-worker.token", f"{SECRETS}/guildhall-mc/mc.token",
    f"{SECRETS}/guildhall-mc/mc-relay.token", f"{SECRETS}/guildhall-mc/discussion-gateway.token",
    f"{SECRETS}/mc-rooms-work/work.token",
    f"{STATE}/model-routes/catalogue.json",
)

BY_HAND = (
    "ChatGPT plan sign-in on the production host, for MC and for CoS",
    "Claude plan sign-in for MC on production, if its relay needs one",
    "Robert's approval of MC's production policy",
    "Robert logs in again after the identity changes",
    "CoS's own capped key for failover and Grok, $20 a month: scripts/operations/cos_prod.sh key 20",
    "MC's one capped key for failover and Grok, $20 a month: MINIMOI_MC_MODEL_GATEWAY_KEY in MC's env file",
)


def consumer(rel: str):
    """(uid, gid) of the container that reads rel, or None when this script does not track rel."""
    if rel in NODE_PATHS:
        if NODE != (1000, 1000):
            raise SystemExit("refused: the node consumer mapping is missing")
        return NODE
    if rel in FOLDERS or rel in GENERATED or rel in BY_OPERATOR:
        if ROOT != (0, 0):
            raise SystemExit("refused: the root consumer mapping is missing")
        return ROOT
    raise SystemExit(f"refused: no consumer mapping for {rel}")


def readable_by(info, uid: int, gid: int) -> bool:
    """True when uid/gid can read a file, or traverse a directory, from mode and owner."""
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        return False
    need = 0o100 if stat.S_ISDIR(info.st_mode) else 0o400
    if info.st_uid == uid and mode & need:
        return True
    if info.st_gid == gid and mode & (need >> 3):
        return True
    return bool(mode & (need >> 6))


def _partner(rel: str):
    for left, right in ASSERTION_PAIRS:
        if rel == left:
            return right
        if rel == right:
            return left
    return None


def _key_ok(path: Path) -> bool:
    """The rule of core.portal_assertion.load_key, here so a host needs no copy of core (a test holds the two equal)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            line = handle.readline().strip()
    except (OSError, UnicodeDecodeError):
        return False
    kid, sep, secret = line.partition(":")
    return bool(sep and kid and len(secret) >= 32)


def owner_of(rel: str):
    """(uid, gid) a path must carry. Differs from consumer only for a folder with two consumers."""
    if rel in SHARED_FOLDERS:
        return SHARED_FOLDERS[rel][1:]
    return consumer(rel)


def _lexical_symlink(path: Path) -> bool:
    """A symlink in path itself or in an existing ancestor. This does not resolve, so the link is still visible."""
    path = Path(path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    cur = Path(path.parts[0])
    for part in path.parts[1:]:
        cur = cur / part
        if cur.is_symlink():
            return True
        if not cur.exists():
            return False
    return False


def _linked(root: Path, rel: str) -> bool:
    """A symlink at root or on the way to rel. mkdir would follow it and write outside the tree."""
    cur = root
    if cur.is_symlink():
        return True
    for part in Path(rel).parts:
        cur = cur / part
        if cur.is_symlink():
            return True
        if not cur.exists():
            return False
    return False


def _blocked(root: Path, rel: str, uid: int, gid: int) -> bool:
    cur = root
    for part in Path(rel).parts[:-1]:
        cur = cur / part
        if cur.exists() and not readable_by(cur.lstat(), uid, gid):
            return True
    path = root / rel
    return path.exists() and not readable_by(path.lstat(), uid, gid)


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
    if rel in SHARED_FOLDERS:
        mode, uid, gid = SHARED_FOLDERS[rel]
        info = path.lstat()
        right = (stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == mode
                 and (info.st_uid, info.st_gid) == (uid, gid))
        return "present" if right else "unsafe"
    if not _safe(path, directory=directory):
        return "unsafe"
    partner = None if directory else _partner(rel)
    if partner is not None:
        other = root / partner
        if (not other.is_file() or other.is_symlink() or path.read_bytes() != other.read_bytes()
                or not _key_ok(path)):
            return "unsafe"
    uid, gid = consumer(rel)
    if _blocked(root, rel, uid, gid):
        return "unsafe"
    return "present"


def is_production(root: Path) -> bool:
    return Path(root).expanduser().resolve() == PRODUCTION.expanduser().resolve()


def report(root: Path) -> dict:
    root = Path(root)
    rows = {"folders": {r: status(root, r, directory=True) for r in FOLDERS},
            "generated": {r: status(root, r, directory=False) for r in GENERATED},
            "by_operator": {r: status(root, r, directory=False) for r in BY_OPERATOR}}
    counts = {}
    for group in rows.values():
        for value in group.values():
            counts[value] = counts.get(value, 0) + 1
    return {**rows, "by_hand": list(BY_HAND), "counts": counts,
            "owners": {"node": {"uid": NODE[0], "gid": NODE[1]}, "root": {"uid": ROOT[0], "gid": ROOT[1]}}}


def _refuse_unsafe(names) -> None:
    raise SystemExit(f"refused: unsafe paths, nothing changed: {sorted(names)}")


def _write_new(path: Path, text: str) -> None:
    # O_EXCL: never overwrites, even in a race. The value is never returned or printed.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def _assertion_line() -> str:
    kid = secrets.token_hex(8)
    secret = secrets.token_urlsafe(48)
    if ":" in kid or len(secret) < 32:
        raise RuntimeError("assertion material unusable")
    return f"{kid}:{secret}\n"


def _give(path: Path, uid: int, gid: int, rel: str) -> None:
    try:
        os.chown(path, uid, gid)
    except OSError:
        raise SystemExit(f"refused: could not give {rel} to uid {uid} gid {gid}") from None
    info = path.lstat()
    if (info.st_uid, info.st_gid) != (uid, gid):
        raise SystemExit(f"refused: {rel} is not uid {uid} gid {gid}")


def apply(root: Path) -> dict:
    given = Path(root).expanduser()
    if _lexical_symlink(given):
        raise SystemExit("refused: unsafe paths, nothing changed: symlink in the root path")
    if is_production(given) and os.environ.get("MINIMOI_1_2_SETUP_APPROVED") != "1":
        raise SystemExit("refused: applying to production needs MINIMOI_1_2_SETUP_APPROVED=1, set by the reviewed operator")
    root = given
    linked = [rel for rel in (*FOLDERS, *GENERATED, *BY_OPERATOR) if _linked(root, rel)]
    if linked:
        _refuse_unsafe(linked)
    before = report(root)
    unsafe = [r for group in ("folders", "generated", "by_operator") for r, v in before[group].items() if v == "unsafe"]
    if unsafe:
        _refuse_unsafe(unsafe)
    made = []
    for rel in FOLDERS:
        path = root / rel
        if not path.exists():
            path.mkdir(parents=True, mode=0o700)
            path.chmod(SHARED_FOLDERS[rel][0] if rel in SHARED_FOLDERS else 0o700)
            made.append(rel)
    # Parents of every secret must be traversable by both consumers without listing other names.
    for rel in ("secrets", SECRETS):
        parent = root / rel
        if parent.is_dir() and not parent.is_symlink():
            parent.chmod(0o711)
    paired = {rel for pair in ASSERTION_PAIRS for rel in pair}
    for left, right in ASSERTION_PAIRS:
        left_path, right_path = root / left, root / right
        if left_path.exists() or right_path.exists():
            continue
        line = _assertion_line()
        _write_new(left_path, line)
        _write_new(right_path, line)
        made.extend((left, right))
    for rel in GENERATED:
        if rel in paired:
            continue
        path = root / rel
        if not path.exists():
            _write_new(path, secrets.token_urlsafe(48))
            made.append(rel)
    # Node paths first, so a missing host identity fails on the consumer Codex measured.
    # cos: 0755 root:root, as cos_prod.sh prepare makes it, whichever of the two comes first.
    if (root / "cos").is_dir() and not (root / "cos").is_symlink():
        (root / "cos").chmod(0o755)
    owned = [*NODE_PATHS, *FOLDERS, *GENERATED, "secrets", SECRETS, "cos"]
    seen = set()
    for rel in owned:
        if rel in seen:
            continue
        seen.add(rel)
        path = root / rel
        if path.exists() and not path.is_symlink():
            uid, gid = owner_of(rel) if rel not in ("secrets", SECRETS, "cos") else ROOT
            _give(path, uid, gid, rel)
            if rel in SHARED_FOLDERS:
                path.chmod(SHARED_FOLDERS[rel][0])      # after the owner: a chown may drop the setgid bit
    after = report(root)
    still = [r for group in ("folders", "generated", "by_operator") for r, v in after[group].items() if v == "unsafe"]
    if still:
        _refuse_unsafe(still)
    return {"made": made, "after": after["counts"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", nargs="?", choices=("check", "apply"), default="check")
    parser.add_argument("--root", default=str(PRODUCTION))
    args = parser.parse_args(argv)
    root = Path(args.root)
    print(json.dumps(apply(root) if args.mode == "apply" else report(root), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
