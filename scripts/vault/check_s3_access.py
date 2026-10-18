#!/usr/bin/env python3
"""Check that a Vault sync principal can do what its side needs and cannot forge or delete the other side's objects.

    <pin python> <pin>/scripts/vault/check_s3_access.py --side mac            (Mac: as the pinned principal; refuses any other)
    python3 -I -B scripts/vault/check_s3_access.py --side ec2 --dest s3://minimoi-backups/vault --expect-principal <role ARN>
        (EC2: from the tools bundle, never a Mac pin; an instance role's arn:aws:sts::A:assumed-role/R/<session> matches
         arn:aws:iam::A:role/R)

It stands in for ``iam simulate-principal-policy`` (which the deploy user cannot call) and tests what S3 actually enforces,
bucket policy included. **The probes are not read-only API calls:** they call S3's write and delete APIs, always against a fresh
synthetic key ``<prefix>_access-probe/<random hex>`` that no Vault job reads or writes. Service semantics relied on (AWS S3 user
guide, "How to prevent object overwrites with conditional writes", checked 18 Oct 2026):

* forgery check (``put_deny``): ``PutObject`` with ``If-Match`` on that missing key, plus the SSE header the policy requires. A
  conditional ``If-Match`` write needs ``s3:PutObject`` and ``s3:GetObject``; with no current object version it fails ``404 Not
  Found`` and stores nothing. So a denied write answers 403 and a wrongly allowed one 404. Used only on prefixes this side may
  read (``FORGERY_NEEDS_READ``), where a 403 can only mean the write is refused.
* delete check (``delete_deny``): an unconditional ``DeleteObject`` of the missing synthetic key. A denied delete answers 403. A
  wrongly allowed one removes nothing that exists, but on a versioned bucket may leave a delete marker on that synthetic key.
* read (``get_allow``): ``HeadObject`` on the missing key must answer 404. A 403 means S3 hides missing keys from this principal (no
  list right), and the jobs would read "no acknowledgement yet" as a failure (``missing_key_forbidden``).
* list (``list_allow``): ``ListObjectsV2`` with ``--max-keys 1`` on the prefix.
* own writes (``put_allow``) are **not probed**: without read rights on those prefixes no conditional write can test them without
  storing an object. The first real job run proves them, and the report lists them as ``not_probed``.

If S3 ever stored a probe (``probe_written``), the report names the key in ``written_probe_keys`` for removal with administrator
credentials. If a delete probe was unexpectedly accepted, the report names that exact key in ``delete_marker_candidate_keys``: on a
versioned bucket it may now carry a delete marker, which an ordinary object listing (``ListObjectsV2``, ``aws s3 ls``) does not
show. Inspect those keys version-aware, with administrator credentials:
``aws s3api list-object-versions --bucket <bucket> --prefix <key>`` (look under ``DeleteMarkers`` and ``Versions``).

A passing check is partial evidence: it shows the forgery and delete refusals and the missing-key reads behave as required. It does
not prove this side's own writes (``not_probed``) or that real acknowledgements and exports can be read; the first real job run and
its receipts do. Output is JSON: principal, one verdict per cell, ok. No
content, key material or credential is printed. Standard library plus core/memory_backup/backup.py.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.memory_backup import backup as B          # noqa: E402

PROBE = "_access-probe"
NO_SUCH_ETAG = '"00000000000000000000000000000000"'

# ACTIVATION_HANDOFF section 3. The Mac's nightly backup goes to the external Files and Photos volume, not S3, so the Mac principal
# has no daily/ or monthly/ grant (its deletes there stay denied). ``put_allow`` is listed for the report, never probed.
MATRIX = {
    "mac": {
        "put_allow": ["production/", "sync/batches/", "sync/mc-export-acks/"],
        "put_deny": ["sync/acks/", "sync/mc-export/"],
        "get_allow": ["sync/acks/", "sync/mc-export/"],
        "list_allow": ["sync/acks/", "sync/mc-export/"],
        "delete_deny": ["production/", "sync/batches/", "sync/acks/", "sync/mc-export/", "sync/mc-export-acks/", "daily/", "monthly/"],
    },
    "ec2": {
        "put_allow": ["sync/acks/", "sync/mc-export/"],
        "put_deny": ["sync/batches/", "production/", "sync/mc-export-acks/"],
        "get_allow": ["sync/batches/", "production/", "sync/mc-export-acks/"],
        "list_allow": ["sync/batches/", "sync/mc-export-acks/"],
        "delete_deny": ["production/", "sync/batches/", "sync/acks/", "sync/mc-export/", "sync/mc-export-acks/", "daily/", "monthly/"],
    },
}
# An If-Match write is authorized only with read rights too: a forgery check is meaningful only where this side may read.
FORGERY_NEEDS_READ = {side: set(m["put_deny"]) <= set(m["get_allow"]) for side, m in MATRIX.items()}
assert all(FORGERY_NEEDS_READ.values()), "every put_deny prefix must be readable by that side"


def _outcome(done) -> str:
    if done.returncode == 0:
        return "success"
    text = (done.stderr or "") + (done.stdout or "")
    if "AccessDenied" in text or "(403)" in text or "Forbidden" in text:
        return "denied"
    if any(t in text for t in ("NoSuchKey", "(404)", "Not Found", "PreconditionFailed", "(412)")):
        return "not_found"
    return "error"


def _verdict(kind: str, outcome: str) -> str:
    if outcome == "error":
        return "inconclusive"
    if kind == "put_deny":
        return {"denied": "ok", "not_found": "allowed_but_must_be_denied", "success": "probe_written"}[outcome]
    if kind == "get_allow":
        return {"not_found": "ok", "denied": "missing_key_forbidden", "success": "unexpected_object"}[outcome]
    if kind == "list_allow":
        return {"success": "ok", "denied": "denied_but_must_be_allowed", "not_found": "inconclusive"}[outcome]
    return {"denied": "ok", "not_found": "allowed_but_must_be_denied", "success": "allowed_but_must_be_denied"}[outcome]


def _role_matches(expected: str, arn: str) -> bool:
    """Exact, or an assumed-role session of the expected role: arn:aws:sts::A:assumed-role/R/S for arn:aws:iam::A:role/R."""
    if arn == expected:
        return True
    head, sep, role = expected.partition(":role/")
    if not sep or not head.startswith("arn:aws:iam::") or "/" in role:
        return False
    account = head[len("arn:aws:iam::"):]
    prefix = f"arn:aws:sts::{account}:assumed-role/{role}/"
    return arn.startswith(prefix) and len(arn) > len(prefix) and "/" not in arn[len(prefix):]


def _ec2_caller(d, expected: str) -> str:
    try:
        done = d.run([d.aws, "sts", "get-caller-identity", "--output", "json"], capture_output=True, text=True, timeout=60)
        arn = json.loads(done.stdout or "{}")["Arn"] if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        arn = None
    if not isinstance(arn, str):
        raise B.BackupError("aws_identity_unavailable")
    if not _role_matches(expected, arn):
        raise B.BackupError("aws_principal_mismatch")
    return arn


def check(dest: str, side: str, *, run=subprocess.run, aws=B._PIN, aws_bin: str = "aws", expect_principal: str | None = None) -> dict:
    if side not in MATRIX:
        raise B.BackupError("side_unknown")
    if not dest.startswith("s3://"):
        raise B.BackupError("destination_unknown")
    d = B.Dest(dest, aws_bin=aws_bin, run=run, aws=aws)
    if d.pin:
        if side != "mac":
            raise B.BackupError("side_mismatch")                  # a Mac pin only ever checks the Mac side
        principal = d.caller()                                    # refuses any principal but the pinned one, before probing
    else:
        if side == "mac" or not expect_principal:
            raise B.BackupError("aws_pin_required" if side == "mac" else "expect_principal_required")
        principal = _ec2_caller(d, expect_principal)
    bucket, _, base = dest[len("s3://"):].partition("/")
    base = f"{base.rstrip('/')}/" if base else ""
    cells, written, delete_markers = [], [], []
    with tempfile.TemporaryDirectory(prefix="vault-access-probe-") as tmp:
        body = Path(tmp) / "probe"
        body.write_bytes(b"access-probe\n")
        for kind, prefixes in MATRIX[side].items():
            for prefix in prefixes:
                if kind == "put_allow":
                    cells.append({"cell": f"{kind}:{prefix}", "verdict": "not_probed", "observed": None})
                    continue
                key = f"{base}{prefix}{PROBE}/{secrets.token_hex(8)}"
                if kind == "put_deny":
                    cmd = [aws_bin, "s3api", "put-object", "--bucket", bucket, "--key", key, "--body", str(body),
                           "--if-match", NO_SUCH_ETAG, "--server-side-encryption", "AES256"]
                elif kind == "get_allow":
                    cmd = [aws_bin, "s3api", "head-object", "--bucket", bucket, "--key", key]
                elif kind == "list_allow":
                    cmd = [aws_bin, "s3api", "list-objects-v2", "--bucket", bucket, "--prefix", base + prefix, "--max-keys", "1",
                           "--output", "json"]
                else:
                    cmd = [aws_bin, "s3api", "delete-object", "--bucket", bucket, "--key", key]
                try:
                    outcome = _outcome(d._s3(cmd, capture_output=True, text=True, timeout=120))
                except (OSError, subprocess.SubprocessError):
                    outcome = "error"
                verdict = _verdict(kind, outcome)
                if verdict == "probe_written":
                    written.append(key)
                elif kind == "delete_deny" and verdict == "allowed_but_must_be_denied":
                    delete_markers.append(key)                    # possibly a delete marker now: inspect version-aware
                cells.append({"cell": f"{kind}:{prefix}", "verdict": verdict, "observed": outcome})
    return {"side": side, "dest": dest, "principal": principal,
            "ok": all(c["verdict"] in ("ok", "not_probed") for c in cells),
            "not_probed": [c["cell"] for c in cells if c["verdict"] == "not_probed"],
            "cells": cells, "written_probe_keys": written, "delete_marker_candidate_keys": delete_markers,
            "inspect": ("aws s3api list-object-versions --bucket " + bucket + " --prefix <key>"
                        if written or delete_markers else None),
            "evidence": "partial: refusals and missing-key reads only; own writes and real receipt reads are proven by real runs"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Non-destructive S3 permission check for a Vault sync principal.")
    parser.add_argument("--side", choices=sorted(MATRIX), required=True)
    parser.add_argument("--dest", default=os.environ.get("MOI_VAULT_BACKUP_DEST", ""))
    parser.add_argument("--expect-principal")
    args = parser.parse_args(argv)
    try:
        report = check(args.dest, args.side, expect_principal=args.expect_principal)
    except B.BackupError as exc:
        print(json.dumps({"side": args.side, "ok": False, "refused": str(exc)}))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
