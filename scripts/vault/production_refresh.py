#!/usr/bin/env python3
"""production_refresh.py: install the latest published Vault snapshot on the EC2 host. Runs ON the host as root, not in a container. Plan-only by default.

  production_refresh.py plan [--check-remote]     installed attempt, retained releases, free space, python/sqlite/PyYAML/age, key file owner+mode
                                                  (never its content); with --check-remote also reads production/latest.json and says if it is newer
  production_refresh.py refresh --execute         pointer -> COMPLETE -> manifest -> ciphertext hashes; decrypt with the root-only identity; extract into
                                                  <vault-root>/releases/<attempt>; verify; fsync; switch current atomically; keep --keep previous releases.
                                                  Unchanged re-verifies the serving release first and rebuilds it from the chain if it fails.
                                                  Once vault-apply serves its own releases this refuses (incremental_active, quiet);
                                                  --reseed installs a newer snapshot over them on purpose (disaster recovery)
  production_refresh.py rollback --execute [--to ATTEMPT]
                                                  point current at the previous retained release (or ATTEMPT); refresh will not reinstall the one left

Settings (flag, else environment, else default):
  --dest         MOI_VAULT_PUBLISH_DEST     s3://minimoi-backups/vault
  --vault-root   MOI_VAULT_ROOT             /opt/minimoi/vault
  --identity     VAULT_BACKUP_IDENTITY      file:/opt/minimoi-vault-keys/identity.agekey   (file:/path | ssm:/name | cmd:program args)
  --age-bin      MOI_AGE_BIN                /opt/minimoi-vault-tools/age-v1.3.2/age
  --expect-age   MOI_AGE_VERSION            v1.3.2
  --reader-uid / --reader-gid               0 / 0   (docker/Dockerfile.portal has no USER line, so the portal runs as root)
  --keep                                    2       previous releases kept for rollback
  --jobs-root    MINIMOI_JOBS_ROOT          /opt/minimoi/jobs
  --fts-probe                               docker:<image> | host   REQUIRED for refresh: where the read-only keyword query runs during
                                                                   verification; use the portal image (the host SQLite may be < 3.43)

Exit codes: 0 installed/unchanged/rolled back, 1 failed, 2 refused (fixed reason on stderr). Job id: vault-production-refresh.
Runbook, bundle file list and cron entry: core/memory_backup/production.py.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.jobs import alert, registry, schedule, status                # noqa: E402
from core.memory_backup import backup, production                          # noqa: E402

JOB_ID = "vault-production-refresh"
EXIT_OK, EXIT_FAIL, EXIT_REFUSED = 0, 1, 2
# Refusals: the snapshot or host is not acceptable; nothing changed and the current release still serves.
# Nothing to install (an older or rolled-back pointer, or another refresh running): reported as warn, never a FAILED
# alert, so an hourly refresh after a rollback stays quiet until the Mac publishes again.
QUIET_REFUSALS = {"pointer_not_newer", "attempt_rolled_back", "refresh_busy", "incremental_active"}
REFUSALS = {"root_required", "pointer_not_newer", "attempt_rolled_back", "refresh_busy", "insufficient_space", "vault_root_unsafe",
            "current_not_a_link", "current_link_unexpected", "release_conflict", "bad_arguments", "incremental_active"}


def env(flag, name, default=None):
    return flag if flag not in (None, "") else os.environ.get(name, default)


def settings(a) -> dict:
    return {"dest": env(a.dest, "MOI_VAULT_PUBLISH_DEST", "s3://minimoi-backups/vault"),
            "vault_root": env(a.vault_root, "MOI_VAULT_ROOT", "/opt/minimoi/vault"),
            "identity": env(a.identity, "VAULT_BACKUP_IDENTITY", "file:/opt/minimoi-vault-keys/identity.agekey"),
            "age_bin": env(a.age_bin, "MOI_AGE_BIN", "/opt/minimoi-vault-tools/age-v1.3.2/age"),
            "expect_age": env(a.expect_age, "MOI_AGE_VERSION", "v1.3.2"),
            "jobs_root": env(a.jobs_root, "MINIMOI_JOBS_ROOT", "/opt/minimoi/jobs"),
            "reader_uid": int(a.reader_uid), "reader_gid": int(a.reader_gid)}


def refuse(code: str) -> int:
    print(f"refused: {code}", file=sys.stderr)
    return EXIT_REFUSED


def job_definition(path=None):
    try:
        job = registry.load(path).get(JOB_ID)
        if job:
            return job
    except registry.RegistryError:
        pass
    return registry.parse({"schema_version": 1, "jobs": [{
        "id": JOB_ID, "name": "Vault production refresh", "host": "ec2",
        "schedule": {"text": "daily 06:15 Chicago", "kind": "daily", "at": "06:15", "tz": "America/Chicago"},
        "expected_every_s": 86400, "missed_after_s": 180000, "stuck_after_s": 7200,
        "status_file": f"{JOB_ID}.json", "owner": "robert", "runbook": "core/memory_backup/production.py"}]}).jobs[0]


def probe_for(spec: str, s: dict):
    if spec in (None, "", "host"):
        return None
    if spec.startswith("docker:"):
        image = spec[len("docker:"):]
        return lambda release: production.docker_fts_probe(image, release, s["reader_uid"], s["reader_gid"])
    raise backup.BackupError("bad_arguments")


def cmd_plan(a) -> int:
    s = settings(a)
    plan = {"job": JOB_ID, "destination": s["dest"], "vault_root": s["vault_root"], "identity_spec_kind": s["identity"].partition(":")[0],
            "reader": f"{s['reader_uid']}:{s['reader_gid']}", "keep": a.keep, "fts_probe": a.fts_probe or "host",
            **production.plan_refresh(vault_root=s["vault_root"], age_bin=s["age_bin"], identity_spec=s["identity"])}
    if a.check_remote:
        work = Path(tempfile.mkdtemp(prefix="vault-refresh-plan-"))
        try:
            dest = backup.Dest(s["dest"])
            pointer_path = work / production.LATEST
            dest.get(f"{production.PREFIX}/{production.LATEST}", pointer_path)
            pointer = json.loads(pointer_path.read_text())
            plan["remote"] = {"attempt": pointer.get("attempt"), "day": pointer.get("day"), "created_utc": pointer.get("created_utc"),
                              "newer_than_current": bool(pointer.get("attempt")) and pointer.get("attempt") > (plan.get("current") or "")}
        except (backup.BackupError, OSError, ValueError) as exc:
            plan["remote"] = {"error": getattr(exc, "code", "pointer_unreadable")}
        finally:
            shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(plan, indent=2, sort_keys=True, default=str))
    return EXIT_OK


def _finish(s, job, running, state, code, summary, results, run_id) -> None:
    finished = datetime.now(timezone.utc)
    try:
        next_due = schedule.next_fire(job.schedule, finished)
    except Exception:                                                       # noqa: BLE001
        next_due = None
    alert_result = "not_needed"
    if state == "failed":
        try:
            alert_result = alert.send(f"minimoi job {job.id} FAILED on {job.host}, run {run_id}: {results.get('refresh')}")
        except Exception:                                                   # noqa: BLE001
            alert_result = alert.NOT_SENT
        if alert_result not in status.ALERT_VALUES:
            alert_result = alert.NOT_SENT
    status.finish_run(s["jobs_root"], job, running, state=state, now=finished, exit_code=code, summary=summary[: status.SUMMARY_MAX],
                      results=results, next_due_by=next_due, alert=alert_result)


def cmd_refresh(a) -> int:
    if not a.execute:
        return refuse("plan_only_use_execute")
    s = settings(a)
    if os.geteuid() != 0 and not a._allow_non_root:
        return refuse("root_required")
    if not a.fts_probe:
        # The EC2 host's own SQLite may predate the FTS5 option the index uses (contentless_delete, 3.43+), and only
        # the consumer image proves the index works where it will be read: say where to probe, every time.
        return refuse("fts_probe_required_use_docker_image")
    # One admission for the whole run (Codex, 17 Oct): the vault's lock is taken BEFORE the status start and held to the finish, so a
    # competitor refused at the lock writes nothing at all, not even a status, and the active run's receipt stays its own.
    try:
        admission = production.Admission(s["vault_root"], owner_uid=os.geteuid() if a._allow_non_root else 0, reader_uid=s["reader_uid"],
                                     reader_gid=s["reader_gid"]).__enter__()
    except backup.BackupError as exc:
        return refuse(exc.code)
    try:
        job = job_definition(a.registry)
        started = datetime.now(timezone.utc)
        run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
        try:
            running = status.start_run(s["jobs_root"], job, run_id, started)
        except Exception:                                                       # noqa: BLE001
            return refuse("status_file_not_writable")
        results, state, code, summary = {}, "failed", EXIT_FAIL, "refresh failed"
        try:
            out = production.run_refresh(dest=s["dest"], vault_root=s["vault_root"], identity_spec=s["identity"], age_bin=s["age_bin"],
                                         expect_age=s["expect_age"], owner_uid=os.geteuid() if a._allow_non_root else 0,
                                         reader_uid=s["reader_uid"], reader_gid=s["reader_gid"], keep=a.keep,
                                         require_root=not a._allow_non_root, probe=probe_for(a.fts_probe, s), reseed=a.reseed, admission=admission)
            state, code = "ok", EXIT_OK
            results = {"refresh": out["state"]}
            if out["state"] == "installed":
                results.update({"records": str(out["records"]), "index_chunks": str(out["index_chunks"]), "pruned": str(out["pruned"]),
                                "fts_probe": out["fts"]})
                if out.get("repaired"):
                    results["repaired"] = str(out.get("repair_cause"))[:40]         # the serving release failed re-verification and was rebuilt
                summary = f"{'repaired' if out.get('repaired') else 'installed'} {out['attempt']}: {out['records']} records, {out['index_chunks']} chunks"
            else:
                results["verified"] = "ok" if out.get("verified") else "no"
                summary = f"unchanged {out['attempt']} (re-verified)"
        except backup.BackupError as exc:
            results["refresh"] = exc.code
            if exc.code == "current_release_corrupt":
                results["cause"] = str(exc.detail)[:40]                            # two fixed codes: why it failed, why the rebuild failed
            summary = f"{'refused' if exc.code in REFUSALS else 'failed'}: {exc.code}"
            code = EXIT_REFUSED if exc.code in REFUSALS else EXIT_FAIL
            if exc.code in QUIET_REFUSALS:
                state = "warn"
        except Exception as exc:                                                # noqa: BLE001
            results["refresh"] = f"exception_{type(exc).__name__.lower()}"[:40]
            summary = "failed: unexpected error"
        _finish(s, job, running, state, code, summary, results, run_id)
        print(summary)
        return code
    finally:
        admission.__exit__(None, None, None)


def cmd_rollback(a) -> int:
    if not a.execute:
        return refuse("plan_only_use_execute")
    s = settings(a)
    try:
        out = production.run_rollback(vault_root=s["vault_root"], to=a.to, owner_uid=os.geteuid() if a._allow_non_root else 0,
                                      reader_uid=s["reader_uid"], reader_gid=s["reader_gid"], require_root=not a._allow_non_root)
    except backup.BackupError as exc:
        return refuse(exc.code)
    print(json.dumps(out, sort_keys=True))
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("--dest", "--vault-root", "--identity", "--age-bin", "--expect-age", "--jobs-root", "--registry", "--fts-probe"):
        ap.add_argument(flag)
    ap.add_argument("--reader-uid", type=int, default=0)
    ap.add_argument("--reader-gid", type=int, default=0)
    ap.add_argument("--keep", type=int, default=2)
    ap.add_argument("--_allow-non-root", dest="_allow_non_root", action="store_true", help=argparse.SUPPRESS)   # synthetic tests only
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan")
    p.add_argument("--check-remote", action="store_true")
    r = sub.add_parser("refresh")
    r.add_argument("--execute", action="store_true")
    r.add_argument("--reseed", action="store_true")          # replace vault-apply's releases with a newer snapshot (disaster recovery)
    b = sub.add_parser("rollback")
    b.add_argument("--execute", action="store_true")
    b.add_argument("--to")
    a = ap.parse_args(argv)
    if a.cmd is None:
        a.check_remote = False
    return {"plan": cmd_plan, "refresh": cmd_refresh, "rollback": cmd_rollback, None: cmd_plan}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
