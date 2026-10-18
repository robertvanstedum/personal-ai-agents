#!/usr/bin/env python3
"""AWS ``credential_process`` helper: the Mac Vault jobs' own scoped keys, read from the login Keychain.

    <python> -I -B <pin>/scripts/vault/aws_keychain_credentials.py --service minimoi-vault-mac

The pinned runtime's ``config/aws_config`` names this command for the Vault jobs' AWS profile (scripts/vault/runtime_install.py), so
the keys never sit in a file, an environment variable or a plist. Two generic-password items under one Keychain service hold them:
accounts ``access-key-id`` and ``secret-access-key``. The owner creates them at a terminal and types each value at the prompt:

    security add-generic-password -U -s minimoi-vault-mac -a access-key-id -w
    security add-generic-password -U -s minimoi-vault-mac -a secret-access-key -w

Output is the documented credential_process JSON (Version 1) on stdout, nothing else. On any failure it prints one code on stderr
(``service_invalid``, ``keychain_item_missing``, ``keychain_unavailable``, ``credential_malformed``) and exits 1, never a value. A
failure here fails the AWS command: there is no fallback to other credentials (the jobs' environment has no shared credentials file,
no ambient keys and no instance metadata; core/memory_backup/backup.py ``aws_env``). Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

SECURITY = "/usr/bin/security"
ACCOUNTS = ("access-key-id", "secret-access-key")
SERVICE = re.compile(r"[A-Za-z0-9._-]{1,128}")
KEY_ID = re.compile(r"AKIA[A-Z0-9]{16}")             # long-term user keys only: this helper emits no session token
SECRET = re.compile(r"[A-Za-z0-9/+=]{40}")
NOT_FOUND = 44                                   # errSecItemNotFound


class Refused(Exception):
    pass


def _read(service: str, account: str, run) -> str:
    try:
        done = run([SECURITY, "find-generic-password", "-s", service, "-a", account, "-w"],
                   capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise Refused("keychain_unavailable") from None
    if done.returncode != 0:
        raise Refused("keychain_item_missing" if done.returncode == NOT_FOUND else "keychain_unavailable")
    return (done.stdout or "").strip()


def credentials(service: str, run=subprocess.run) -> dict:
    if not isinstance(service, str) or not SERVICE.fullmatch(service):
        raise Refused("service_invalid")
    key_id = _read(service, ACCOUNTS[0], run)
    secret = _read(service, ACCOUNTS[1], run)
    if not KEY_ID.fullmatch(key_id) or not SECRET.fullmatch(secret):
        raise Refused("credential_malformed")
    return {"Version": 1, "AccessKeyId": key_id, "SecretAccessKey": secret}


def main(argv=None, run=subprocess.run) -> int:
    parser = argparse.ArgumentParser(description="AWS credential_process helper for the Mac Vault jobs (Keychain).")
    parser.add_argument("--service", required=True)
    args = parser.parse_args(argv)
    try:
        doc = credentials(args.service, run=run)
    except Refused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(doc))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
