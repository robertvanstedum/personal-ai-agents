#!/usr/bin/env python3
"""vault_apply.py: install the Mac's incremental Vault sync batches on the EC2 host. Runs ON the host as root, not in a container. Plan-only by default.

  vault_apply.py plan [--check-remote]          serving release and its kind (snapshot or sync), whether its index is keyword-only (else the
                                                index seed is due), applied batches, the last inventory; with --check-remote also lists the
                                                COMPLETE batches not yet applied (ids only)
  vault_apply.py run --execute                  COMPLETE batches not yet applied, oldest first: chain -> ciphertext -> decrypt -> plaintext ->
                                                strict extract -> per-version checks; build a new release (hard links + applied records + keyword
                                                index), verify, fsync, switch current; then the acks, applied.json and the inventory
  vault_apply.py index-step (--shelf DIR --index FILE | --release DIR) (--only-file F | --rebuild)
                                                internal: the keyword-only index step, run inside the image by --index-runner docker:<image>
                                                (shelf mounted read-only, only the index folder writable); prints one JSON line
                                                {"protocol": 1, "code": ..., counts}

Settings (flag, else environment, else default):
  --dest         MOI_VAULT_PUBLISH_DEST     s3://minimoi-backups/vault
  --vault-root   MOI_VAULT_ROOT             /opt/minimoi/vault
  --identity     VAULT_BACKUP_IDENTITY      file:/opt/minimoi-vault-keys/identity.agekey   (file:/path | ssm:/name | cmd:program args)
  --age-bin      MOI_AGE_BIN                /opt/minimoi-vault-tools/age-v1.3.2/age
  --expect-age   MOI_AGE_VERSION            v1.3.2
  --reader-uid / --reader-gid               0 / 0
  --keep                                    3       previous releases kept for rollback
  --max-run-mib                             2048    plaintext batch bytes one run applies (the rest waits for the next run)
  --jobs-root    MINIMOI_JOBS_ROOT          /opt/minimoi/jobs
  --index-runner                            docker:<image> | host   REQUIRED for run: where the keyword index is written and probed; use the
                                                                   portal image (the host SQLite may be < 3.43 and cannot write the index)
  --force-inventory                         write sync/acks/inventory.json this run even when nothing changed (gap repair)
  --rebuild-index                           rebuild the keyword index from the shelf in a new release this run, batch or not (an index
                                            the image's indexer reports rebuild_needed is rebuilt automatically when a batch applies)

Exit codes: 0 ok/noop, 1 failed, 2 refused (fixed reason on stderr). Job id: vault-apply. Runbook and cron entry: core/memory_sync/apply.py.
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
from core.memory_backup import backup                                     # noqa: E402
from core.memory_sync import apply, batch                                 # noqa: E402

JOB_ID = "vault-apply"
EXIT_OK, EXIT_FAIL, EXIT_REFUSED = 0, 1, 2
# Nothing to do right now (a snapshot refresh or another apply holds the lock): warn, never a FAILED alert.
QUIET_REFUSALS = {"apply_busy"}
REFUSALS = {"root_required", "apply_busy", "nothing_installed", "vault_root_unsafe", "current_not_a_link", "current_link_unexpected",
            "sync_state_unsafe", "bad_arguments", "index_runner_required", "host_sqlite_too_old"}


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
        "id": JOB_ID, "name": "Vault incremental apply", "host": "ec2",
        "schedule": {"text": "every 15 minutes", "kind": "interval", "every_s": 900},
        "expected_every_s": 900, "missed_after_s": 7200, "stuck_after_s": 3600,
        "status_file": f"{JOB_ID}.json", "owner": "robert", "runbook": "core/memory_sync/apply.py"}]}).jobs[0]


def cmd_plan(a) -> int:
    s = settings(a)
    plan = {"job": JOB_ID, "destination": s["dest"], "vault_root": s["vault_root"], "identity_spec_kind": s["identity"].partition(":")[0],
            "reader": f"{s['reader_uid']}:{s['reader_gid']}", "keep": a.keep, "max_run_mib": a.max_run_mib,
            "index_runner": a.index_runner or "(required for run)", **apply.plan_apply(vault_root=s["vault_root"])}
    if a.check_remote:
        try:
            ids = batch.complete_batches(backup.Dest(s["dest"]))
            done = set()
            sync = Path(s["vault_root"]).parent / f"{Path(s['vault_root']).name}-sync" / "applied.json"
            if sync.is_file():
                done = set(json.loads(sync.read_text()))
            waiting = [b for b in ids if b not in done]
            plan["remote"] = {"complete_batches": len(ids), "not_applied": len(waiting), "oldest_waiting": waiting[0] if waiting else None}
        except (backup.BackupError, OSError, ValueError) as exc:
            plan["remote"] = {"error": getattr(exc, "code", "list_failed")}
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
            alert_result = alert.send(f"minimoi job {job.id} FAILED on {job.host}, run {run_id}: {results.get('apply')}")
        except Exception:                                                   # noqa: BLE001
            alert_result = alert.NOT_SENT
        if alert_result not in status.ALERT_VALUES:
            alert_result = alert.NOT_SENT
    status.finish_run(s["jobs_root"], job, running, state=state, now=finished, exit_code=code, summary=summary[: status.SUMMARY_MAX],
                      results=results, next_due_by=next_due, alert=alert_result)


def cmd_run(a) -> int:
    if not a.execute:
        return refuse("plan_only_use_execute")
    s = settings(a)
    if os.geteuid() != 0 and not a._allow_non_root:
        return refuse("root_required")
    if not a.index_runner:
        # The EC2 host's own SQLite may predate the FTS5 option the index uses (contentless_delete, 3.43+): say where the index is
        # written, every time.
        return refuse("index_runner_required_use_docker_image")
    try:
        runner = apply.make_index_runner(a.index_runner, reader_uid=s["reader_uid"], reader_gid=s["reader_gid"])
    except backup.BackupError as exc:
        return refuse(exc.code)
    if a.max_run_mib < 1 or a.keep < 1:
        return refuse("bad_arguments")
    # One admission for the whole run (Codex, 17 Oct): the vault's lock is taken BEFORE the status start and held to the finish, so a
    # competitor refused at the lock writes nothing at all, not even a status, and the active run's receipt stays its own.
    try:
        admission = apply.P.Admission(s["vault_root"], owner_uid=os.geteuid() if a._allow_non_root else 0, reader_uid=s["reader_uid"],
                                     reader_gid=s["reader_gid"]).__enter__()
    except backup.BackupError as exc:
        return refuse("apply_busy" if exc.code == "refresh_busy" else exc.code)
    try:
        job = job_definition(a.registry)
        started = datetime.now(timezone.utc)
        run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
        try:
            running = status.start_run(s["jobs_root"], job, run_id, started)
        except Exception:                                                       # noqa: BLE001
            return refuse("status_file_not_writable")
        results, state, code, summary = {}, "failed", EXIT_FAIL, "apply failed"
        try:
            out = apply.run_apply(dest=s["dest"], vault_root=s["vault_root"], identity_spec=s["identity"], index_runner=runner,
                                  age_bin=s["age_bin"], expect_age=s["expect_age"], owner_uid=os.geteuid() if a._allow_non_root else 0,
                                  reader_uid=s["reader_uid"], reader_gid=s["reader_gid"], keep=a.keep, max_run_bytes=a.max_run_mib << 20,
                                  require_root=not a._allow_non_root, force_inventory=a.force_inventory, rebuild_index=a.rebuild_index, admission=admission)
            results = {"apply": out["code"], "batches": str(out["batches"]), "stored": str(out["stored"]), "superseded": str(out["superseded"]),
                       "rejected": str(out["rejected"]), "applied": str(out["applied"]), "release": out["release"].lower(),   # status codes are lower case
                       "index_seed": "yes" if out["index_seed"] else "no", "inventory": out["inventory"],
                       "last_batch": (out.get("last_batch") or "none").lower()}                    # the newest batch id this run processed
            if out["code"] in ("ok", "noop"):
                state, code = ("warn" if out["rejected"] else "ok"), EXIT_OK
                summary = (f"{out['code']}: {out['batches']} batches, {out['applied']} applied, {out['stored']} stored, {out['rejected']} rejected"
                           f"{', index seeded' if out['index_seed'] else ''}, release {out['release']}")
            else:                                                               # a transient stop: processed batches completed, the rest retries
                summary = f"failed: {out['code']} after {out['batches']} batches"
        except backup.BackupError as exc:
            results["apply"] = exc.code[:40]
            summary = f"{'refused' if exc.code in REFUSALS else 'failed'}: {exc.code}"
            code = EXIT_REFUSED if exc.code in REFUSALS else EXIT_FAIL
            if exc.code in QUIET_REFUSALS:
                state = "warn"
        except Exception as exc:                                                # noqa: BLE001
            results["apply"] = f"exception_{type(exc).__name__.lower()}"[:40]
            summary = "failed: unexpected error"
        _finish(s, job, running, state, code, summary, results, run_id)
        print(summary)
        return code
    finally:
        admission.__exit__(None, None, None)


def cmd_index_step(a) -> int:
    """Inside the image (or on the host): one JSON line, nothing else on stdout."""
    try:
        only = None
        if not a.rebuild:
            doc = json.loads(Path(a.only_file).read_text())
            if not isinstance(doc, list) or not all(isinstance(x, str) for x in doc):
                raise ValueError("only_file_malformed")
            only = doc
        if a.release and not (a.shelf or a.index):
            shelf, index = Path(a.release) / "shelf", Path(a.release) / "index" / apply.P.INDEX_NAME
        elif a.shelf and a.index and not a.release:
            shelf, index = Path(a.shelf), Path(a.index)
        else:
            raise ValueError("index_step_paths")
        out = apply.index_step(shelf, index, only)
    except Exception as exc:                                                # noqa: BLE001
        out = {"protocol": apply.INDEX_PROTOCOL, "code": f"exception_{type(exc).__name__.lower()}"[:40]}
    print(json.dumps(out, sort_keys=True))
    return EXIT_OK if out.get("code") == "ok" else EXIT_FAIL


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("--dest", "--vault-root", "--identity", "--age-bin", "--expect-age", "--jobs-root", "--registry", "--index-runner"):
        ap.add_argument(flag)
    ap.add_argument("--reader-uid", type=int, default=0)
    ap.add_argument("--reader-gid", type=int, default=0)
    ap.add_argument("--keep", type=int, default=3)
    ap.add_argument("--max-run-mib", type=int, default=2048)
    ap.add_argument("--force-inventory", action="store_true")
    ap.add_argument("--rebuild-index", action="store_true")
    ap.add_argument("--_allow-non-root", dest="_allow_non_root", action="store_true", help=argparse.SUPPRESS)   # synthetic tests only
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan")
    p.add_argument("--check-remote", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--execute", action="store_true")
    i = sub.add_parser("index-step")
    i.add_argument("--release")
    i.add_argument("--shelf")
    i.add_argument("--index")
    g = i.add_mutually_exclusive_group(required=True)
    g.add_argument("--only-file")
    g.add_argument("--rebuild", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd is None:
        a.check_remote = False
    return {"plan": cmd_plan, "run": cmd_run, "index-step": cmd_index_step, None: cmd_plan}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
