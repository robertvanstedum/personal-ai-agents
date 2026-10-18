#!/usr/bin/env python3
"""publish_production.py: publish one consistent, encrypted (shelf, index) snapshot for production. Plan-only unless told to run. Mac side.

  publish_production.py plan                       what would happen: destination, recipient fingerprint, window, age version, sizes. Uploads nothing.
  publish_production.py run --execute              index (online-backup copy) then shelf, both age-encrypted to the PUBLIC recipient, uploaded under
                                                   <dest>/production/<date>/<attempt>/, COMPLETE last, then <dest>/production/latest.json

Settings (flag, else environment, else default):
  --shelf        MOI_SHELF_ROOT                 ~/minimoi-staging/data/memory
  --index        MOI_INDEX_PATH                 <shelf parent>/memory-index/<shelf name>.sqlite (staging: ~/minimoi-staging/data/memory-index/memory.sqlite)
  --dest         MOI_VAULT_PUBLISH_DEST         s3://minimoi-backups/vault   (file:///dir for dev and tests)
  --recipient    VAULT_BACKUP_AGE_RECIPIENT     the existing age PUBLIC recipient, or --recipient-file / VAULT_BACKUP_AGE_RECIPIENT_FILE
  --age-bin      MOI_AGE_BIN                    ~/minimoi-staging/tools/age-v1.3.2/age
  --expect-age   MOI_AGE_VERSION                v1.3.2
  --jobs-root    MINIMOI_JOBS_ROOT              ~/minimoi-staging/data/jobs
  --workdir      MOI_VAULT_PUBLISH_WORK         ~/minimoi-staging/.vault-publish-work   (plaintext scratch, 0700, wiped; outside
                                                data/, which staging mounts into containers)

It runs only inside the standing batch window (00:00-06:00 America/Chicago) unless --allow-outside-window, and takes the shared
<jobs-root>/vault-batch.lock unless --no-batch-lock (pass that when a runner such as runtime_install.py run-job already holds it).
Exit codes: 0 ok, 1 warn or failed, 2 refused (fixed reason on stderr). Job id: vault-publish-production. Runbook: core/memory_backup/production.py.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.jobs import alert, registry, schedule, status                # noqa: E402
from core.memory_backup import backup, production                          # noqa: E402

JOB_ID = "vault-publish-production"
EXIT_OK, EXIT_FAIL, EXIT_REFUSED = 0, 1, 2


def env(flag, name, default=None):
    return flag if flag else os.environ.get(name, default)


def settings(a) -> dict:
    recipient = env(a.recipient, "VAULT_BACKUP_AGE_RECIPIENT")
    rfile = env(a.recipient_file, "VAULT_BACKUP_AGE_RECIPIENT_FILE")
    if not recipient and rfile:
        recipient = Path(rfile).expanduser().read_text().strip()
    shelf = os.path.expanduser(env(a.shelf, "MOI_SHELF_ROOT", "~/minimoi-staging/data/memory"))
    jobs = os.path.expanduser(env(a.jobs_root, "MINIMOI_JOBS_ROOT", "~/minimoi-staging/data/jobs"))
    shelf_path = Path(shelf)
    return {
        "shelf_root": shelf,
        "index_path": os.path.expanduser(env(a.index, "MOI_INDEX_PATH", str(shelf_path.parent / "memory-index" / f"{shelf_path.name}.sqlite"))),
        "dest": env(a.dest, "MOI_VAULT_PUBLISH_DEST", "s3://minimoi-backups/vault"), "recipient": (recipient or "").strip(),
        "age_bin": os.path.expanduser(env(a.age_bin, "MOI_AGE_BIN", "~/minimoi-staging/tools/age-v1.3.2/age")),
        "expect_age": env(a.expect_age, "MOI_AGE_VERSION", "v1.3.2"), "jobs_root": jobs,
        "workdir": os.path.expanduser(env(a.workdir, "MOI_VAULT_PUBLISH_WORK", "~/minimoi-staging/.vault-publish-work")),
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
        "id": JOB_ID, "name": "Vault production publish", "host": "mac",
        "schedule": {"text": "daily 05:00 Chicago", "kind": "daily", "at": "05:00", "tz": "America/Chicago"},
        "expected_every_s": 86400, "missed_after_s": 180000, "stuck_after_s": 7200,
        "status_file": f"{JOB_ID}.json", "owner": "robert", "runbook": "core/memory_backup/production.py"}]}).jobs[0]
    return fallback, {}


def cmd_plan(a) -> int:
    s = settings(a)
    now = datetime.now(timezone.utc)
    index = Path(s["index_path"])
    plan = {"job": JOB_ID, "destination": s["dest"], "pointer": f"{production.PREFIX}/{production.LATEST}",
            "recipient_fingerprint": backup.fingerprint(s["recipient"]) if s["recipient"] else None,
            "recipient_valid": bool(backup.RECIPIENT.match(s["recipient"])), "inside_window_now": backup.in_window(now),
            "window": "00:00-06:00 America/Chicago", "age_bin": s["age_bin"], "expect_age": s["expect_age"],
            "shelf_root_exists": Path(s["shelf_root"]).is_dir(), "index_exists": index.is_file(),
            "index_bytes": index.stat().st_size if index.is_file() else None, "workdir": s["workdir"], "reserve_bytes": backup.reserve_bytes()}
    if plan["shelf_root_exists"] and plan["index_exists"]:
        fp = backup.footprint(s["shelf_root"])
        plan["shelf_files"], plan["shelf_bytes"] = fp["files"], fp["bytes"]
        plan["scratch_need_bytes"] = production.publish_need(fp, plan["index_bytes"])
    try:
        plan["age_version"] = backup.check_age(s["age_bin"], s["expect_age"])
    except backup.BackupError as exc:
        plan["age_version"] = f"unavailable ({exc.code})"
    print(json.dumps(plan, indent=2, sort_keys=True))
    return EXIT_OK


LAST_PUBLISHED = "last-published.json"                               # in the work folder: what the pointer now carries, and where and to whom


def _binding(s, fingerprint: str) -> dict:
    """A remembered publish is the content fingerprint bound to the destination, the recipient and the expected age version: a config change
    (a new destination or recipient) is never skipped even when the content is the same."""
    return {"fingerprint": fingerprint, "dest": s["dest"], "recipient_fingerprint": backup.fingerprint(s["recipient"]),
            "expect_age": s.get("expect_age")}


def _last_published(s) -> dict | None:
    try:
        doc = json.loads((Path(s["workdir"]) / LAST_PUBLISHED).read_text())
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def _is_unchanged(s, fingerprint: str | None) -> bool:
    last = _last_published(s)
    if not fingerprint or not last:
        return False
    want = _binding(s, fingerprint)
    return all(last.get(k) == v for k, v in want.items())


def _remember(s, fingerprint: str, manifest: dict) -> None:
    path = Path(s["workdir"]) / LAST_PUBLISHED
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({**_binding(s, fingerprint), "attempt": manifest.get("attempt"),
                               "records": manifest["shelf"]["records"]}, sort_keys=True) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _next_due(job, finished):
    try:
        return schedule.next_fire(job.schedule, finished)
    except Exception:                                                       # noqa: BLE001
        return None


def _publish(a, s) -> int:
    # Catch-up runs several times inside the window (and after a missed run on wake): a run whose content, destination and recipient match
    # the last confirmed publish uploads nothing and reports "unchanged" (a success, with its own status run so the watchdog sees it).
    # --force publishes anyway.
    job, extra = job_definition(a.registry)
    started = datetime.now(timezone.utc)
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    try:
        running = status.start_run(s["jobs_root"], job, run_id, started)
    except Exception:                                                       # noqa: BLE001
        return refuse("status_file_not_writable")
    try:
        fingerprint = production.content_fingerprint(s["shelf_root"], s["index_path"])
    except Exception:                                                       # noqa: BLE001 - publish decides; no skip
        fingerprint = None
    if not a.force and _is_unchanged(s, fingerprint):
        summary = "unchanged: the last published snapshot is current; nothing uploaded"
        finished = datetime.now(timezone.utc)
        status.finish_run(s["jobs_root"], job, running, state="ok", now=finished, exit_code=EXIT_OK, summary=summary[: status.SUMMARY_MAX],
                          results={**extra, "publish": "unchanged"}, next_due_by=_next_due(job, finished), alert="not_needed")
        print(summary)
        return EXIT_OK
    results, state, summary = dict(extra), "failed", "publish failed"
    try:
        manifest = production.run_publish(shelf_root=s["shelf_root"], index_path=s["index_path"], dest=s["dest"], recipient=s["recipient"],
                                          workdir=s["workdir"], age_bin=s["age_bin"], expect_age=s["expect_age"],
                                          enforce_window=not a.allow_outside_window)
        state = manifest["state"]
        if state == "ok" and fingerprint:
            _remember(s, fingerprint, manifest)
        results.update({"publish": "ok" if state == "ok" else "shelf_verify_warn", "records": str(manifest["shelf"]["records"]),
                        "index_chunks": str(manifest["index"]["chunks"]), "not_indexed": str(manifest["pair"]["shelf_records_not_in_index"])})
        mib = (manifest["objects"]["shelf"]["bytes"] + manifest["objects"]["index"]["bytes"]) // (1 << 20)
        summary = f"{state}: {manifest['shelf']['records']} records, {manifest['index']['chunks']} chunks, {mib} MiB encrypted"
    except backup.BackupError as exc:
        results["publish"] = exc.code
        summary = f"failed: {exc.code}"
    except Exception as exc:                                                # noqa: BLE001 - a fixed code, never the message
        results["publish"] = f"exception_{type(exc).__name__.lower()}"[:40]
        summary = "failed: unexpected error"
    finished = datetime.now(timezone.utc)
    next_due = _next_due(job, finished)
    code = {"ok": EXIT_OK, "warn": EXIT_FAIL, "failed": EXIT_FAIL}[state]
    alert_result = "not_needed"
    if state == "failed":
        try:
            alert_result = alert.send(f"minimoi job {job.id} FAILED on {job.host}, run {run_id}: {results.get('publish')}")
        except Exception:                                                   # noqa: BLE001
            alert_result = alert.NOT_SENT
        if alert_result not in status.ALERT_VALUES:
            alert_result = alert.NOT_SENT
    status.finish_run(s["jobs_root"], job, running, state=state, now=finished, exit_code=code, summary=summary[: status.SUMMARY_MAX],
                      results=results, next_due_by=next_due, alert=alert_result)
    print(summary)
    return code


def cmd_run(a) -> int:
    if not a.execute:
        return refuse("plan_only_use_execute")
    s = settings(a)
    if not s["dest"]:
        return refuse("destination_required")
    if not s["recipient"]:
        return refuse("recipient_required")
    if a.no_batch_lock:
        return _publish(a, s)
    status.ensure_root(s["jobs_root"])
    fd = os.open(str(Path(s["jobs_root"]) / "vault-batch.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return refuse("batch_busy")
        return _publish(a, s)
    finally:
        os.close(fd)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("--shelf", "--index", "--dest", "--recipient", "--recipient-file", "--age-bin", "--expect-age", "--jobs-root", "--workdir", "--registry"):
        ap.add_argument(flag)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("plan")
    r = sub.add_parser("run")
    r.add_argument("--execute", action="store_true")
    r.add_argument("--allow-outside-window", action="store_true", help="run now even outside 00:00-06:00 Chicago (the owner starting it by hand)")
    r.add_argument("--no-batch-lock", action="store_true", help="the caller already holds the shared vault-batch lock")
    r.add_argument("--force", action="store_true", help="publish even when the content matches the last publish")
    a = ap.parse_args(argv)
    return {"plan": cmd_plan, "run": cmd_run, None: cmd_plan}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
