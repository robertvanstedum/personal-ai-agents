#!/usr/bin/env python3
"""vault_mc_export.py: send production's changed MC conversations to the Mac, encrypted, and reconcile them against the serving Vault. Runs ON
the EC2 host as root (cron, every 2 h), not in a container. Plan-only by default.

  vault_mc_export.py plan                       settings and what the state says (counts only); never runs the exporter
  vault_mc_export.py run --execute              export (docker exec, --stdout-tar) -> strict read -> acks -> gap repair -> one encrypted batch
                                                -> upload (COMPLETE last) -> wipe; status vault-mc-export

Settings (flag, else environment, else default):
  --dest              MOI_VAULT_PUBLISH_DEST            s3://minimoi-backups/vault
  --vault-root        MOI_VAULT_ROOT                    /opt/minimoi/vault      (state and lock in <vault-root>-sync, scratch in <vault-root>-work)
  --recipient         VAULT_BACKUP_AGE_RECIPIENT        the existing age PUBLIC recipient (or --recipient-file / VAULT_BACKUP_AGE_RECIPIENT_FILE);
                                                        required for run: this host can encrypt a batch, never read it back
  --principal         MOI_MC_PRINCIPAL                  required for run: the owner account whose conversations are exported
  --portal-container  MOI_PORTAL_CONTAINER              minimoi-portal
  --age-bin           MOI_AGE_BIN                       /opt/minimoi-vault-tools/age-v1.3.2/age
  --expect-age        MOI_AGE_VERSION                   v1.3.2
  --jobs-root         MINIMOI_JOBS_ROOT                 /opt/minimoi/jobs
  --gap-grace-h                                         12      hours after an ack before a conversation not current in the Vault is re-sent
  --max-batch-mib                                       256     file bytes in one batch (the rest waits for the next run)

Exit codes: 0 ok/warn, 1 failed, 2 refused (fixed reason on stderr). A run refused at the lock (``refused: mc_export_busy``) writes
NOTHING, not even a status: the lock is taken before the status start and held to the finish, so the status always describes the run
holding it (and a busy run never advances ``last_success_at``). Job id: vault-mc-export.
Warn (exit 0): a partial export, rejections, an unparseable ack, a conversation behind past the grace or unacknowledged past it, one
still behind after its single gap re-send, or a serving Vault whose index cannot be read (reconciliation unknown).
Runbook (described, NOT installed; nothing here has run against real S3 or EC2):
  python3 -I -B /opt/minimoi/vault-tools/<sha>/scripts/vault/vault_mc_export.py plan
  cron (root): 25 */2 * * * AWS_DEFAULT_REGION=us-east-1 MOI_MC_PRINCIPAL=<owner> VAULT_BACKUP_AGE_RECIPIENT=<age1...> \\
      /usr/bin/python3 -I -B /opt/minimoi/vault-tools/current/scripts/vault/vault_mc_export.py run --execute >> /opt/minimoi/logs/vault_mc_export.log 2>&1
How it works: core/memory_sync/mc_export.py.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.jobs import alert, registry, schedule, status                # noqa: E402
from core.memory_backup import backup                                     # noqa: E402
from core.memory_sync import mc_export                                    # noqa: E402

JOB_ID = "vault-mc-export"
EXIT_OK, EXIT_FAIL, EXIT_REFUSED = 0, 1, 2
REFUSALS = {"root_required", "mc_export_busy", "sync_state_unsafe", "work_unsafe", "bad_arguments", "recipient_malformed"}
RESULT_KEYS = ("conversations", "changed", "sent", "acked", "rejected", "ack_malformed", "pending_files", "gap_requeued", "behind_after_repair",
               "vault", "in_vault_current", "in_vault_stale", "missing_in_vault", "lag_oldest_s", "oldest_unacked_s", "other_principal",
               "off_record", "gone_from_export")
WARN_KEYS = ("rejected", "ack_malformed", "behind_past_grace", "unacked_past_grace", "behind_after_repair")


def _value(v) -> str:
    return "unknown" if v is None else str(v)              # the in-Vault counts when the serving index could not be read


def env(flag, name, default=None):
    return flag if flag not in (None, "") else os.environ.get(name, default)


def settings(a) -> dict:
    recipient = env(a.recipient, "VAULT_BACKUP_AGE_RECIPIENT")
    rfile = env(a.recipient_file, "VAULT_BACKUP_AGE_RECIPIENT_FILE")
    if not recipient and rfile:
        try:
            recipient = Path(rfile).read_text().strip()
        except OSError:
            recipient = ""
    return {"dest": env(a.dest, "MOI_VAULT_PUBLISH_DEST", "s3://minimoi-backups/vault"),
            "vault_root": env(a.vault_root, "MOI_VAULT_ROOT", "/opt/minimoi/vault"),
            "recipient": (recipient or "").strip(), "principal": env(a.principal, "MOI_MC_PRINCIPAL", "") or "",
            "container": env(a.portal_container, "MOI_PORTAL_CONTAINER", "minimoi-portal"),
            "age_bin": env(a.age_bin, "MOI_AGE_BIN", "/opt/minimoi-vault-tools/age-v1.3.2/age"),
            "expect_age": env(a.expect_age, "MOI_AGE_VERSION", "v1.3.2"),
            "jobs_root": env(a.jobs_root, "MINIMOI_JOBS_ROOT", "/opt/minimoi/jobs")}


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
        "id": JOB_ID, "name": "Vault MC export (production to Mac)", "host": "ec2",
        "schedule": {"text": "every 2 hours", "kind": "interval", "every_s": 7200},
        "expected_every_s": 7200, "missed_after_s": 21600, "stuck_after_s": 3600,
        "status_file": f"{JOB_ID}.json", "owner": "robert", "runbook": "core/memory_sync/mc_export.py"}]}).jobs[0]


def cmd_plan(a) -> int:
    s = settings(a)
    plan = {"job": JOB_ID, "destination": s["dest"], "vault_root": s["vault_root"], "principal_set": bool(s["principal"]),
            "recipient_fingerprint": backup.fingerprint(s["recipient"]) if s["recipient"] else None,
            "recipient_valid": bool(backup.RECIPIENT.match(s["recipient"])), "portal_container": s["container"],
            "gap_grace_h": a.gap_grace_h, "max_batch_mib": a.max_batch_mib, **mc_export.plan_export(vault_root=s["vault_root"])}
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
            alert_result = alert.send(f"minimoi job {job.id} FAILED on {job.host}, run {run_id}: {results.get('export')}")
        except Exception:                                                   # noqa: BLE001
            alert_result = alert.NOT_SENT
        if alert_result not in status.ALERT_VALUES:
            alert_result = alert.NOT_SENT
    status.finish_run(s["jobs_root"], job, running, state=state, now=finished, exit_code=code, summary=summary[: status.SUMMARY_MAX],
                      results=results, next_due_by=next_due, alert=alert_result)


def cmd_run(a, runner=None, now=None, put=None) -> int:
    if not a.execute:
        return refuse("plan_only_use_execute")
    s = settings(a)
    if os.geteuid() != 0 and not a._allow_non_root:
        return refuse("root_required")
    if not s["principal"]:
        return refuse("principal_required")
    if not s["recipient"]:
        return refuse("recipient_required")
    if a.max_batch_mib < 1 or a.gap_grace_h < 0:
        return refuse("bad_arguments")
    # One admission for the whole run (Codex, 17 Oct): the lock is taken BEFORE the status start and held to the finish, so a competitor
    # refused at the lock writes nothing at all, not even a status, and the active run's receipt stays its own.
    try:
        admission = mc_export.Admission(s["vault_root"], owner_uid=os.geteuid() if a._allow_non_root else 0).__enter__()
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
        results, state, code, summary = {}, "failed", EXIT_FAIL, "export failed"
        try:
            out = mc_export.run_export(dest=s["dest"], vault_root=s["vault_root"], recipient=s["recipient"], principal=s["principal"],
                                       runner=runner, container=s["container"], age_bin=s["age_bin"], expect_age=s["expect_age"],
                                       owner_uid=os.geteuid() if a._allow_non_root else 0, require_root=not a._allow_non_root,
                                       gap_grace=timedelta(hours=a.gap_grace_h), max_batch_bytes=a.max_batch_mib << 20, now=now, put=put,
                                       admission=admission)
            results = {"export": out["export"], **{k: _value(out[k]) for k in RESULT_KEYS}}
            warn = out["export"] == "partial" or out["vault"] != "ok" or any(out[k] for k in WARN_KEYS)
            state, code = ("warn" if warn else "ok"), EXIT_OK
            behind = "unknown" if out["vault"] != "ok" else out["missing_in_vault"] + out["in_vault_stale"]
            summary = (f"{'warn' if warn and out['export'] == 'ok' else out['export']}: {out['conversations']} conversations, {out['sent']} sent,"
                       f" {out['acked']} acked, {out['rejected']} rejected, {behind} not current, lag {out['lag_oldest_s']}s,"
                       f" oldest unacked {out['oldest_unacked_s']}s")
        except backup.BackupError as exc:
            results["export"] = exc.code[:40]
            summary = f"{'refused' if exc.code in REFUSALS else 'failed'}: {exc.code}"
            code = EXIT_REFUSED if exc.code in REFUSALS else EXIT_FAIL
        except Exception as exc:                                                # noqa: BLE001
            results["export"] = f"exception_{type(exc).__name__.lower()}"[:40]
            summary = "failed: unexpected error"
        _finish(s, job, running, state, code, summary, results, run_id)
        print(summary)
        return code
    finally:
        admission.__exit__(None, None, None)


def main(argv=None, *, runner=None, now=None, put=None) -> int:
    """``runner`` / ``now`` / ``put``: injected by the synthetic tests only (the exporter in-process, a clock, an interrupted upload)."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("--dest", "--vault-root", "--recipient", "--recipient-file", "--principal", "--portal-container", "--age-bin", "--expect-age",
                 "--jobs-root", "--registry"):
        ap.add_argument(flag)
    ap.add_argument("--gap-grace-h", type=float, default=12.0)
    ap.add_argument("--max-batch-mib", type=int, default=256)
    ap.add_argument("--_allow-non-root", dest="_allow_non_root", action="store_true", help=argparse.SUPPRESS)   # synthetic tests only
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("plan")
    r = sub.add_parser("run")
    r.add_argument("--execute", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return cmd_run(a, runner=runner, now=now, put=put)
    return cmd_plan(a)


if __name__ == "__main__":
    sys.exit(main())
