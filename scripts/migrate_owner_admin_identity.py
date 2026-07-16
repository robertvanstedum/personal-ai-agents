#!/usr/bin/env python3
"""
scripts/migrate_owner_admin_identity.py — one-time migration: give
owner/admin accounts (from minimoi_portal/auth/users.json) a matching
row in the Postgres auth.users table, so they get a stable auth_id the
same way guest accounts already do.

Part of Workstream 3, spec_83_multiuser_identity_full.md. Idempotent —
domain_auth.create_user() does ON CONFLICT (email) DO UPDATE, so this is
safe to re-run.

Usage:
    python scripts/migrate_owner_admin_identity.py [--dry-run]
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from minimoi_portal import domain_auth as _dauth

USERS_JSON = REPO_ROOT / "minimoi_portal" / "auth" / "users.json"


def main():
    dry_run = "--dry-run" in sys.argv

    data = json.loads(USERS_JSON.read_text())
    users = data.get("users", [])

    owner_admin = [u for u in users if u.get("tier") in ("owner", "admin")]
    if not owner_admin:
        print("No owner/admin accounts found in users.json — nothing to do.")
        return

    for u in owner_admin:
        email = u.get("email")
        name = u.get("display_name", u.get("username"))
        tier = u["tier"]
        if not email:
            print(f"SKIP {u.get('username')}: no email field, cannot migrate.")
            continue

        existing = _dauth.get_user_by_email(email)
        if existing:
            print(f"ALREADY EXISTS: {email} -> auth_id={existing['id']} (role={existing.get('role')})")
            if dry_run:
                continue

        if dry_run:
            print(f"DRY RUN: would create/update auth.users row for {email} (name={name}, role={tier})")
            continue

        auth_id = _dauth.create_user(email, name, role=tier)
        print(f"OK: {u.get('username')} ({email}) -> auth_id={auth_id}")


if __name__ == "__main__":
    main()
