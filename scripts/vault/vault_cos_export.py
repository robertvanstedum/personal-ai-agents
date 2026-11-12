#!/usr/bin/env python3
"""vault_cos_export.py: send production's changed CoS conversations to the Mac, encrypted, and reconcile them against the serving Vault
(11 Nov 2026, #509). Runs ON the EC2 host as root (cron, every 2 h), not in a container. Plan-only by default.

  vault_cos_export.py plan                      settings, what the state says and whether the history folder is there (counts only)
  vault_cos_export.py run --execute             read the CoS history (read-only, private never) -> strict read -> acks -> gap repair
                                                -> one encrypted batch -> upload (COMPLETE last) -> wipe; status vault-cos-export

Settings (flag, else environment, else default):
  --dest              MOI_VAULT_PUBLISH_DEST            s3://minimoi-backups/vault
  --vault-root        MOI_VAULT_ROOT                    /opt/minimoi/vault      (state and lock in <vault-root>-sync, scratch in <vault-root>-work)
  --recipient         VAULT_BACKUP_AGE_RECIPIENT        the existing age PUBLIC recipient (or --recipient-file / VAULT_BACKUP_AGE_RECIPIENT_FILE);
                                                        required for run: this host can encrypt a batch, never read it back
  --history           MOI_COS_HISTORY                   /opt/minimoi/cos/conversations/history   (read-only; written only by cos-scheduler)
  --environment       MOI_COS_ENVIRONMENT               prod    the history's own environment (dev | staging | prod); a conversation of
                                                                another environment is never sent
  --age-bin           MOI_AGE_BIN                       /opt/minimoi-vault-tools/age-v1.3.2/age
  --expect-age        MOI_AGE_VERSION                   v1.3.2
  --jobs-root         MINIMOI_JOBS_ROOT                 /opt/minimoi/jobs
  --gap-grace-h                                         12      hours after an ack before a conversation not current in the Vault is re-sent
  --max-batch-mib                                       256     file bytes in one batch (the rest waits for the next run)

Exit codes: 0 ok/warn, 1 failed, 2 refused (fixed reason on stderr). A run refused at the lock (``refused: cos_export_busy``) writes
NOTHING, not even a status. Job id: vault-cos-export.
Warn (exit 0): a partial export (a conversation that could not be read this run: unreadable, unstable, too large, a stray name),
rejections, an unparseable ack, a conversation behind past the grace or unacknowledged past it, one still behind after its single gap
re-send, or a serving Vault whose index cannot be read. Private conversations are counted (``private``), never sent, and never a warning.
Runbook: cron line, recipient file and rollback in the PR that added this job (#509); how it works: core/memory_sync/cos_export.py.
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
from core.memory_sync import cos_export                                   # noqa: E402

JOB_ID = "vault-cos-export"
EXIT_OK, EXIT_FAIL, EXIT_REFUSED = 0, 1, 2
REFUSALS = {"root_required", "cos_export_busy", "sync_state_unsafe", "work_unsafe", "bad_arguments", "recipient_malformed"}
RESULT_KEYS = ("conversations", "changed", "sent", "acked", "rejected", "ack_malformed", "pending_files", "gap_requeued", "behind_after_repair",
               "vault", "in_vault_current", "in_vault_stale", "missing_in_vault", "lag_oldest_s", "oldest_unacked_s", "private",
               "gone_from_export")
# The status holds at most 20 results: the exporter's other counts are folded in two (the run's own result keeps them apart).
FOLDED = {"excluded": ("other_environment", "other_scope"), "not_read": ("unreadable", "unstable", "too_large")}
WARN_KEYS = ("rejected", "ack_malformed", "behind_past_grace", "unacked_past_grace", "behind_after_repair")


def _value(v) -> str:
    return "unknown" if v is None else str(v)


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
            "recipient": (recipient or "").strip(),
            "history": env(a.history, "MOI_COS_HISTORY", cos_export.DEFAULT_HISTORY),
            "environment": env(a.environment, "MOI_COS_ENVIRONMENT", "prod"),
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
        "id": JOB_ID, "name": "Vault CoS export from production (EC2)", "host": "ec2",
        "schedule": {"text": "every 2 hours", "kind": "interval", "every_s": 7200},
        "expected_every_s": 7200, "missed_after_s": 21600, "stuck_after_s": 3600,
        "status_file": f"{JOB_ID}.json", "owner": "robert", "runbook": "core/memory_sync/cos_export.py"}]}).jobs[0]


def cmd_plan(a) -> int:
    s = settings(a)
    if s["environment"] not in ("dev", "staging", "prod"):
        return refuse("bad_arguments")
    plan = {"job": JOB_ID, "destination": s["dest"], "vault_root": s["vault_root"], "history_root": s["history"],
            "environment": s["environment"], "recipient_fingerprint": backup.fingerprint(s["recipient"]) if s["recipient"] else None,
            "recipient_valid": bool(backup.RECIPIENT.match(s["recipient"])), "gap_grace_h": a.gap_grace_h, "max_batch_mib": a.max_batch_mib,
            **cos_export.plan_export(vault_root=s["vault_root"], history_root=s["history"], environment=s["environment"])}
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
    if not s["recipient"]:
        return refuse("recipient_required")
    if a.max_batch_mib < 1 or a.gap_grace_h < 0 or s["environment"] not in ("dev", "staging", "prod") \
            or not Path(s["history"]).is_absolute():
        return refuse("bad_arguments")
    owner_uid = os.geteuid() if a._allow_non_root else 0
    # One admission for the whole run (the vault-mc-export pattern): the lock before the status start, held to the finish.
    try:
        admission = cos_export.admission(s["vault_root"], environment=s["environment"], owner_uid=owner_uid).__enter__()
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
            out = cos_export.run_export(dest=s["dest"], vault_root=s["vault_root"], recipient=s["recipient"], history_root=s["history"],
                                        environment=s["environment"], runner=runner, age_bin=s["age_bin"], expect_age=s["expect_age"],
                                        owner_uid=owner_uid, require_root=not a._allow_non_root,
                                        gap_grace=timedelta(hours=a.gap_grace_h), max_batch_bytes=a.max_batch_mib << 20, now=now, put=put,
                                        admission=admission)
            results = {"export": out["export"], **{k: _value(out[k]) for k in RESULT_KEYS},
                       **{k: str(sum(out[p] for p in parts)) for k, parts in FOLDED.items()}}
            warn = out["export"] == "partial" or out["vault"] != "ok" or any(out[k] for k in WARN_KEYS)
            state, code = ("warn" if warn else "ok"), EXIT_OK
            behind = "unknown" if out["vault"] != "ok" else out["missing_in_vault"] + out["in_vault_stale"]
            summary = (f"{'warn' if warn and out['export'] == 'ok' else out['export']}: {out['conversations']} conversations, {out['sent']} sent,"
                       f" {out['acked']} acked, {out['rejected']} rejected, {out['private']} private not sent, {behind} not current,"
                       f" lag {out['lag_oldest_s']}s, oldest unacked {out['oldest_unacked_s']}s")
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
    """``runner`` / ``now`` / ``put``: injected by the synthetic tests only."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("--dest", "--vault-root", "--recipient", "--recipient-file", "--history", "--environment", "--age-bin", "--expect-age",
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
