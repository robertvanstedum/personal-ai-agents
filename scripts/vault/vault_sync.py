#!/usr/bin/env python3
"""vault_sync.py: the Vault's incremental sync to production, Mac side (job id vault-sync). Plan-only unless told to run.

  vault_sync.py plan [--check-remote]          what a run would do: window, mode, pending by state, local records; with --check-remote also
                                               production's inventory and the batches a run would send. Writes nothing, uploads nothing.
  vault_sync.py run --execute                  one run: acknowledgements, diff, then new or changed record versions in age-encrypted batches
                                               under <dest>/sync/batches/<batch_id>/ (COMPLETE last). Light outside 00:00-06:00 Chicago.

The launchd agent comes from the pinned Vault runtime, like every other Vault job: ``runtime_install.py prepare`` writes the candidate
``com.vanstedum.minimoi-vault-sync.plist`` (every 2 h, ``StartInterval`` 7200, and ``RunAtLoad``, so a missed interval runs once on wake),
running ``<pin>/scripts/vault/vault_sync.py run --execute``. Never run it from a working checkout.

Settings (flag, else environment, else default):
  --shelf          MOI_SHELF_ROOT                 ~/minimoi-staging/data/memory
  --dest           MOI_VAULT_PUBLISH_DEST         s3://minimoi-backups/vault   (file:///dir for dev and tests)
  --recipient      VAULT_BACKUP_AGE_RECIPIENT     the existing age PUBLIC recipient, or --recipient-file / VAULT_BACKUP_AGE_RECIPIENT_FILE
  --age-bin        MOI_AGE_BIN                    ~/minimoi-staging/tools/age-v1.3.2/age
  --expect-age     MOI_AGE_VERSION                v1.3.2
  --jobs-root      MINIMOI_JOBS_ROOT              ~/minimoi-staging/data/jobs
  --state-dir      MOI_VAULT_SYNC_STATE           ~/minimoi-staging/.vault-sync   (pending.json, the hash cache, work/ plaintext scratch 0700;
                                                  outside data/, which staging mounts into containers)
  --light-mib 64   --light-records 200   --heavy-mib 512

It takes the shared <jobs-root>/vault-batch.lock FIRST, without waiting, and holds it across its status start, work and finish (busy:
writes nothing, not even its status, and exits 0) unless --no-batch-lock (pass that
when a runner already holds it). Exit codes: 0 ok, busy or an expected warn (no production inventory yet, short of space, a rejection),
1 failed, 2 refused (fixed reason on stderr). Runbook: core/memory_sync/sync.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.memory_backup import backup                                     # noqa: E402
from core.memory_sync import sync                                          # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_REFUSED = sync.EXIT_OK, sync.EXIT_FAIL, sync.EXIT_REFUSED


def env(flag, name, default=None):
    return flag if flag not in (None, "") else os.environ.get(name, default)


def _positive(value, name, default):
    raw = env(value, name, default)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise ValueError("bad_arguments") from None
    if n < 1:
        raise ValueError("bad_arguments")
    return n


def settings(a) -> dict:
    recipient = env(a.recipient, "VAULT_BACKUP_AGE_RECIPIENT")
    rfile = env(a.recipient_file, "VAULT_BACKUP_AGE_RECIPIENT_FILE")
    if not recipient and rfile:
        recipient = Path(rfile).expanduser().read_text().strip()
    return {
        "shelf_root": os.path.expanduser(env(a.shelf, "MOI_SHELF_ROOT", "~/minimoi-staging/data/memory")),
        "dest": env(a.dest, "MOI_VAULT_PUBLISH_DEST", "s3://minimoi-backups/vault"), "recipient": (recipient or "").strip(),
        "age_bin": os.path.expanduser(env(a.age_bin, "MOI_AGE_BIN", "~/minimoi-staging/tools/age-v1.3.2/age")),
        "expect_age": env(a.expect_age, "MOI_AGE_VERSION", "v1.3.2"),
        "jobs_root": os.path.expanduser(env(a.jobs_root, "MINIMOI_JOBS_ROOT", "~/minimoi-staging/data/jobs")),
        "state_dir": os.path.expanduser(env(a.state_dir, "MOI_VAULT_SYNC_STATE", "~/minimoi-staging/.vault-sync")),
        "light_bytes": _positive(a.light_mib, "MOI_VAULT_SYNC_LIGHT_MIB", "64") * sync.MIB,
        "light_records": _positive(a.light_records, "MOI_VAULT_SYNC_LIGHT_RECORDS", "200"),
        "heavy_bytes": _positive(a.heavy_mib, "MOI_VAULT_SYNC_HEAVY_MIB", "512") * sync.MIB,
    }


def refuse(code: str) -> int:
    print(f"refused: {code}", file=sys.stderr)
    return EXIT_REFUSED


def _settings_or_refuse(a):
    try:
        return settings(a), None
    except ValueError as exc:
        return None, refuse(str(exc))
    except OSError:
        return None, refuse("recipient_file_unreadable")


def cmd_plan(a) -> int:
    s, refused = _settings_or_refuse(a)
    if refused is not None:
        return refused
    plan = sync.plan_sync(shelf_root=s["shelf_root"], state_dir=s["state_dir"], dest=s["dest"], check_remote=a.check_remote,
                          light_bytes=s["light_bytes"], light_records=s["light_records"], heavy_bytes=s["heavy_bytes"])
    plan.update({"destination": s["dest"], "window": "00:00-06:00 America/Chicago", "state_dir": s["state_dir"],
                 "recipient_fingerprint": backup.fingerprint(s["recipient"]) if s["recipient"] else None,
                 "recipient_valid": bool(backup.RECIPIENT.match(s["recipient"])), "age_bin": s["age_bin"], "expect_age": s["expect_age"],
                 "light_mib": s["light_bytes"] // sync.MIB, "light_records": s["light_records"], "heavy_mib": s["heavy_bytes"] // sync.MIB,
                 "reserve_bytes": backup.reserve_bytes()})
    try:
        plan["age_version"] = backup.check_age(s["age_bin"], s["expect_age"])
    except backup.BackupError as exc:
        plan["age_version"] = f"unavailable ({exc.code})"
    print(json.dumps(plan, indent=2, sort_keys=True))
    return EXIT_OK


def cmd_run(a) -> int:
    if not a.execute:
        return refuse("plan_only_use_execute")
    s, refused = _settings_or_refuse(a)
    if refused is not None:
        return refused
    if not s["dest"]:
        return refuse("destination_required")
    if not s["recipient"]:
        return refuse("recipient_required")
    out = sync.run_sync(shelf_root=s["shelf_root"], dest=s["dest"], recipient=s["recipient"], age_bin=s["age_bin"], expect_age=s["expect_age"],
                        jobs_root=s["jobs_root"], state_dir=s["state_dir"], registry_path=a.registry, light_bytes=s["light_bytes"],
                        light_records=s["light_records"], heavy_bytes=s["heavy_bytes"], take_lock=not a.no_batch_lock)
    if out["state"] is None:
        print(out["summary"], file=sys.stderr)
        return out["exit"]
    print(out["summary"])
    return out["exit"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("--shelf", "--dest", "--recipient", "--recipient-file", "--age-bin", "--expect-age", "--jobs-root", "--state-dir", "--registry",
                 "--light-mib", "--light-records", "--heavy-mib"):
        ap.add_argument(flag)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan")
    p.add_argument("--check-remote", action="store_true", help="also read production's inventory and acknowledgements (read-only)")
    r = sub.add_parser("run")
    r.add_argument("--execute", action="store_true")
    r.add_argument("--no-batch-lock", action="store_true", help="the caller already holds the shared vault-batch lock")
    a = ap.parse_args(argv)
    if a.cmd is None:
        a.check_remote = False
    return {"plan": cmd_plan, "run": cmd_run, None: cmd_plan}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
