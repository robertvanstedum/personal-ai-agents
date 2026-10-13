#!/usr/bin/env python3
"""Rooms identity provisioning on staging (design: _working/guild-beta/ROOMS_MINIMOI_IDENTITY_DESIGN_2026-10-11.md, draft 3).

  provision [--account robert] [--principal robert]
      once, owner-run: creates the shared bridge key (secrets/rooms-bridge/portal-bridge.key, folder 700, file 600; never printed, never
      overwritten) and the owner's binding (data/guild/rooms/bindings.json) when neither exists. Re-running changes nothing that exists.
  status
      read-only: whether the key is in place (mode only, never the value) and each binding (account, principal, active).

Only this file and minimoi_portal/rooms_identity.py know the formats. It touches nothing but those two places.
"""
from __future__ import annotations

import argparse
import os
import secrets
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from minimoi_portal import rooms_identity as RI  # noqa: E402

ROOT = Path(os.environ.get("STAGING_ROOT", Path.home() / "minimoi-staging"))
KEY_DIR = ROOT / "secrets" / "rooms-bridge"
KEY_FILE = KEY_DIR / "portal-bridge.key"
ACCESS_DIR = ROOT / "data" / "guild" / "rooms"


def mode(path: Path) -> str:
    return oct(stat.S_IMODE(path.stat().st_mode))[2:]


def provision(account: str, principal: str) -> int:
    KEY_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(KEY_DIR, 0o700)
    if KEY_FILE.exists():
        print("key: already in place (not changed)")
    else:
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(f"k1:{secrets.token_urlsafe(48)}\n")
            fh.flush()
            os.fsync(fh.fileno())
        print("key: created (mode 600, value not printed)")
    ACCESS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    seeded = RI.RoomsAccess(str(ACCESS_DIR)).seed_owner(f"account:{account}", principal, granted_by="provision")
    print("binding: created" if seeded else "binding: already present (not changed)")
    return 0


def status() -> int:
    if KEY_FILE.exists():
        ok = RI.load_key(str(KEY_FILE)) is not None
        print(f"key: {'present and well-formed' if ok else 'present but NOT usable'} (mode {mode(KEY_FILE)}, folder {mode(KEY_DIR)})")
    else:
        print("key: missing")
    access = RI.RoomsAccess(str(ACCESS_DIR))
    rows = access._read()
    if rows is None:
        print("bindings: unreadable or damaged (Rooms refuses everyone until repaired)")
        return 1
    if not rows:
        print("bindings: none (nobody has Rooms access)")
    for r in rows:
        print(f"binding: {r['account']} -> {r['principal']} ({'active' if r['active'] else 'withdrawn'})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("provision")
    p.add_argument("--account", default="robert")
    p.add_argument("--principal", default="robert")
    sub.add_parser("status")
    args = parser.parse_args()
    return provision(args.account, args.principal) if args.command == "provision" else status()


if __name__ == "__main__":
    raise SystemExit(main())
