#!/usr/bin/env python3
"""Back up and restore-test the standing worker's durable spool (standing v0.3.1 §11).

    backup_spool.py backup --state-root ROOT --out DIR     # consistent archive + .sha256, 0600
    backup_spool.py verify --archive FILE                   # restore into a temp dir and read every record

The backup takes the worker's cycle lock (waiting up to --wait seconds), so it never captures a
half-finished cycle; it never runs while a cycle is in flight. Temporary files are excluded.
``verify`` checks the digest, extracts with path checks into a private temp directory and reads
every record through the spool's own reader: a corrupt or oversize record fails the check.
Output is counts and names only, never record content.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.mc_standing.spool import DIRS, Spool, SpoolError  # noqa: E402


def _lock(root: Path, wait_s: float) -> int:
    fd = os.open(root / ".cycle.lock", os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise SystemExit(json.dumps({"state": "busy", "reason": "cycle_in_flight"}))
            time.sleep(1)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup(root: Path, out: Path, wait_s: float = 300) -> dict:
    root = Path(root)
    Spool(root)                                   # validates the layout (and refuses symlinks)
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = out / f"mc-standing-spool-{stamp}.tar.gz"
    fd = _lock(root, wait_s)
    try:
        counts = {}
        tmp = archive.with_name(f".{archive.name}.tmp")
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as raw, \
                tarfile.open(fileobj=raw, mode="w:gz") as tar:
            for kind in DIRS:
                files = sorted(p for p in (root / kind).iterdir()
                               if p.name.endswith(".json") and not p.name.startswith(".") and p.is_file()
                               and not p.is_symlink())
                counts[kind] = len(files)
                for p in files:
                    tar.add(p, arcname=f"{kind}/{p.name}", recursive=False)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(tmp, archive)
    finally:
        os.close(fd)
    digest = _sha256(archive)
    side = archive.with_name(archive.name + ".sha256")
    side.write_text(f"{digest}  {archive.name}\n")
    os.chmod(side, 0o600)
    return {"state": "ok", "archive": archive.name, "sha256": digest, "counts": counts}


def verify(archive: Path) -> dict:
    archive = Path(archive)
    side = archive.with_name(archive.name + ".sha256")
    if not side.exists() or side.read_text().split()[0] != _sha256(archive):
        return {"state": "failed", "reason": "digest_mismatch"}
    with tempfile.TemporaryDirectory(prefix="mc-spool-restore-") as tmp:
        dest = Path(tmp) / "state"
        with tarfile.open(archive, "r:gz") as tar:
            for m in tar.getmembers():
                kind, _, name = m.name.partition("/")
                if not m.isfile() or kind not in DIRS or "/" in name or name.startswith(".") \
                        or not name.endswith(".json"):
                    return {"state": "failed", "reason": "unexpected_member"}
            tar.extractall(dest, filter="data")
        spool = Spool(dest)
        counts = {}
        try:
            for kind in DIRS:
                names = spool.names(kind)
                for n in names:
                    spool.read(kind, n)
                counts[kind] = len(names)
        except SpoolError as exc:
            return {"state": "failed", "reason": str(exc)}
    return {"state": "ok", "counts": counts}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--state-root", required=True, type=Path)
    b.add_argument("--out", required=True, type=Path)
    b.add_argument("--wait", type=float, default=300)
    v = sub.add_parser("verify")
    v.add_argument("--archive", required=True, type=Path)
    args = p.parse_args(argv)
    result = backup(args.state_root, args.out, args.wait) if args.cmd == "backup" else verify(args.archive)
    print(json.dumps(result))
    return 0 if result["state"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
