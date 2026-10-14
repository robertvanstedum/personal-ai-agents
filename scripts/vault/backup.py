#!/usr/bin/env python3
"""backup.py: the Vault's independent backup and its restore drill (V5). Plan-only unless told to run.

  backup.py plan                                what would happen: destination, recipient fingerprint, window, age version. Reads nothing of the shelf, uploads nothing.
  backup.py run --execute                       verify the shelf, archive, encrypt to the PUBLIC recipient, upload, manifest last; writes the job status file
  backup.py drill --day YYYY-MM-DD --identity SPEC [--monthly] [--attempt ID]
                                                fetch the day's latest COMPLETE attempt (or exactly --attempt), check every hash, decrypt with the private identity,
                                                extract to a clean folder, verify, open one edition

Settings (flag, else environment, else default):
  --shelf        MOI_SHELF_ROOT               the shelf folder                        ~/minimoi-staging/data/memory
  --dest         MOI_VAULT_BACKUP_DEST        file:///dir (dev) or s3://bucket/vault   (required to run or drill)
  --recipient    VAULT_BACKUP_AGE_RECIPIENT   the age PUBLIC recipient (age1...)       or --recipient-file PATH
  --age-bin      MOI_AGE_BIN                  the pinned age binary                    age
  --expect-age   MOI_AGE_VERSION              required `age --version` text (pin)     none
  --identity     VAULT_BACKUP_IDENTITY        drill only: file:/path | keychain:service/account | ssm:/name | cmd:program args
  --jobs-root    MINIMOI_JOBS_ROOT            where the job status file goes          ~/minimoi-staging/data/jobs

The job holds only the public recipient. It runs only inside the standing batch window (00:00 to 06:00 America/Chicago) unless --allow-outside-window.
Free space: every step keeps the standing reserve (MOI_RESERVE_GIB, default 5) free or refuses with insufficient_space before writing.
Exit codes: 0 ok, 1 finished with a warning or failed, 2 refused (a fixed reason on stderr, never a key, a path or a record name). Job id: vault-backup.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.jobs import alert, registry, schedule, status                # noqa: E402
from core.memory_backup import backup                                        # noqa: E402

JOB_ID = "vault-backup"
EXIT_OK, EXIT_FAIL, EXIT_REFUSED = 0, 1, 2


def env(flag, name, default=None):
    return flag if flag else os.environ.get(name, default)


def settings(a) -> dict:
    recipient = env(a.recipient, "VAULT_BACKUP_AGE_RECIPIENT")
    rfile = env(a.recipient_file, "VAULT_BACKUP_AGE_RECIPIENT_FILE")
    if not recipient and rfile:
        recipient = Path(rfile).expanduser().read_text().strip()
    return {
        "shelf_root": os.path.expanduser(env(a.shelf, "MOI_SHELF_ROOT", "~/minimoi-staging/data/memory")),
        "dest": env(a.dest, "MOI_VAULT_BACKUP_DEST"), "recipient": (recipient or "").strip(),
        "age_bin": env(a.age_bin, "MOI_AGE_BIN", "age"), "expect_age": env(a.expect_age, "MOI_AGE_VERSION"),
        "jobs_root": os.path.expanduser(env(a.jobs_root, "MINIMOI_JOBS_ROOT", "~/minimoi-staging/data/jobs")),
    }


def refuse(code: str) -> int:
    print(f"refused: {code}", file=sys.stderr)
    return EXIT_REFUSED


def job_definition(path=None):
    try:
        job = registry.load(path).get(JOB_ID)
        if job:
            return job, {}
    except registry.RegistryError:
        pass
    fallback = registry.parse({"schema_version": 1, "jobs": [{
        "id": JOB_ID, "name": "Vault independent backup", "host": "mac",
        "schedule": {"text": "daily 04:40 Chicago", "kind": "daily", "at": "04:40", "tz": "America/Chicago"},
        "expected_every_s": 86400, "missed_after_s": 180000, "stuck_after_s": 7200,
        "status_file": f"{JOB_ID}.json", "owner": "robert", "runbook": "docs/vault/RECOVERY.md"}]}).jobs[0]
    return fallback, {"registry": "unreadable"}


def cmd_plan(a) -> int:
    s = settings(a)
    now = datetime.now(timezone.utc)
    plan = {"job": JOB_ID, "destination": s["dest"], "recipient_fingerprint": backup.fingerprint(s["recipient"]) if s["recipient"] else None,
            "recipient_valid": bool(backup.RECIPIENT.match(s["recipient"])), "inside_window_now": backup.in_window(now),
            "window": "00:00-06:00 America/Chicago", "age_bin": s["age_bin"], "expect_age": s["expect_age"], "shelf_root_exists": Path(s["shelf_root"]).is_dir()}
    try:
        plan["age_version"] = backup.check_age(s["age_bin"], s["expect_age"])
    except backup.BackupError as exc:
        plan["age_version"] = f"unavailable ({exc.code})"
    print(json.dumps(plan, indent=2, sort_keys=True))
    return EXIT_OK


def cmd_run(a) -> int:
    if not a.execute:
        return refuse("plan_only_use_execute")
    s = settings(a)
    if not s["dest"]:
        return refuse("destination_required")
    if not s["recipient"]:
        return refuse("recipient_required")
    job, extra = job_definition(a.registry)
    started = datetime.now(timezone.utc)
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    try:
        running = status.start_run(s["jobs_root"], job, run_id, started)
    except Exception:                                                       # noqa: BLE001
        return refuse("status_file_not_writable")
    results, state, summary = dict(extra), "failed", "backup failed"
    try:
        manifest = backup.run_backup(shelf_root=s["shelf_root"], dest=s["dest"], recipient=s["recipient"],
                                     workdir=str(Path(s["jobs_root"]) / ".vault-backup-work"), age_bin=s["age_bin"], expect_age=s["expect_age"],
                                     enforce_window=not a.allow_outside_window)
        state = manifest["state"]
        results.update({"backup": "ok" if state == "ok" else "shelf_verify_warn", "records": str(manifest["shelf"]["records"])})
        summary = f"{state}: {manifest['shelf']['records']} records, {manifest['ciphertext']['bytes'] // (1 << 20)} MiB encrypted"
    except backup.BackupError as exc:
        results["backup"] = exc.code
        summary = f"failed: {exc.code}"
    except Exception as exc:                                                # noqa: BLE001 - a fixed code, never the message
        results["backup"] = f"exception_{type(exc).__name__.lower()}"[:40]
        summary = "failed: unexpected error"
    finished = datetime.now(timezone.utc)
    try:
        next_due = schedule.next_fire(job.schedule, finished)
    except Exception:                                                       # noqa: BLE001
        next_due = None
    alert_result = "not_needed"
    code = {"ok": EXIT_OK, "warn": EXIT_FAIL, "failed": EXIT_FAIL}[state]
    if state == "failed":
        try:
            alert_result = alert.send(f"minimoi job {job.id} FAILED on {job.host}, run {run_id}: {results.get('backup')}")
        except Exception:                                                   # noqa: BLE001
            alert_result = alert.NOT_SENT
        if alert_result not in status.ALERT_VALUES:
            alert_result = alert.NOT_SENT
    status.finish_run(s["jobs_root"], job, running, state=state, now=finished, exit_code=code, summary=summary[: status.SUMMARY_MAX],
                      results=results, next_due_by=next_due, alert=alert_result)
    print(summary)
    return code


def cmd_drill(a) -> int:
    s = settings(a)
    identity = env(a.identity, "VAULT_BACKUP_IDENTITY")
    if not s["dest"] or not identity or not a.day:
        return refuse("destination_identity_and_day_required")
    work = Path(os.path.expanduser(a.workdir or "~/minimoi-staging/data/jobs/.vault-drill-work"))
    try:
        evidence = backup.restore_drill(dest=s["dest"], day=a.day, identity_spec=identity, workdir=str(work), age_bin=s["age_bin"], monthly=a.monthly,
                                        attempt=a.attempt)
    except backup.BackupError as exc:
        print(json.dumps({"ok": False, "reason": exc.code}))
        return EXIT_FAIL
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return EXIT_OK if evidence["ok"] else EXIT_FAIL


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("--shelf", "--dest", "--recipient", "--recipient-file", "--age-bin", "--expect-age", "--jobs-root", "--registry"):
        ap.add_argument(flag)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("plan")
    r = sub.add_parser("run")
    r.add_argument("--execute", action="store_true")
    r.add_argument("--allow-outside-window", action="store_true", help="run now even outside 00:00-06:00 Chicago (the owner starting it by hand)")
    d = sub.add_parser("drill")
    d.add_argument("--day")
    d.add_argument("--identity")
    d.add_argument("--monthly", action="store_true")
    d.add_argument("--attempt", help="restore exactly this attempt id instead of the day's latest pointer")
    d.add_argument("--workdir")
    a = ap.parse_args(argv)
    return {"plan": cmd_plan, "run": cmd_run, "drill": cmd_drill, None: cmd_plan}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
