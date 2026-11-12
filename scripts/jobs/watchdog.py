#!/usr/bin/env python3
"""The scheduled-jobs watchdog: tells Robert when a job on this host failed, did not run, or is stuck.

    venv/bin/python scripts/jobs/watchdog.py

Registered as job ``jobs-watchdog`` (daily 09:00 Chicago, infrastructure/launchd/com.vanstedum.minimoi-jobs-watchdog.plist).
Reads config/scheduled_jobs.json and each job's status file (docs/jobs_status_contract.md) and judges them with
``core.jobs.judge``. A job is a **problem** (red) when: its status file is missing after a run was due (never ran),
its last run failed, it has been ``running`` longer than ``stuck_after_s``, or it has no completed run within
``missed_after_s``.

* **One message per condition.** The last alerted (condition, run) per job is kept in ``watchdog-state.json`` under
  the jobs root. The same condition on the same run is not repeated; a different one is a new message. When the job
  is no longer red, one ``recovered`` message goes out and the entry is cleared. If a message cannot be sent it is not
  recorded, so the next run tries again.
* A run the job's own wrapper already reported (``alert: sent`` on a failed run) is recorded without a second message.
* Only jobs whose ``host`` is this host (MINIMOI_JOBS_HOST, default ``mac``) are judged, and the watchdog never judges
  itself (the Guild Operate tile does that, from its own status file).
* Messages carry the job id, a fixed condition word and an age in hours; never content.
* **Every host (FF-19, #537).** The Mac's id is ``jobs-watchdog``; another host's is ``jobs-watchdog-<host>``. Each run
  also writes ``health-<host>.json`` (``core.jobs.health``: every job's light, code and since when it is off) in the jobs
  root and, when ``MINIMOI_JOBS_HEALTH_DIR`` is set, a copy there for MC (on EC2: inside the portal's mc-observations
  volume). A job whose status lives in another folder names its ``status_root``; the host maps it with
  ``MINIMOI_JOBS_ROOT_<TOKEN>`` (``mc-observations`` -> ``MINIMOI_JOBS_ROOT_MC_OBSERVATIONS``).
* ``MINIMOI_JOBS_HEALTH_PUSH=<dest>`` (the Vault bucket, ``s3://minimoi-backups/vault``) also sends the report to
  ``<dest>/health/<host>/`` so production MC sees this host (``core.jobs.health.push``). A failed push makes the run
  ``warn`` (result ``health-push: failed``); the report itself is still written here.
* ``MINIMOI_JOBS_ALERT=off`` judges and reports without sending anything: on EC2 and Dev the daily check
  (``scripts/jobs/daily_health.py``) is the one that sends, and only for ``needs action``.

Exit codes: 0 done (all alerts delivered or none needed); 1 an alert could not be sent, or the registry is unreadable;
2 the watchdog's own status file could not be written.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core.jobs import alert, health, judge, registry, schedule, status  # noqa: E402

JOB_ID = "jobs-watchdog"
DEFAULT_JOBS_ROOT = "~/minimoi-staging/data/jobs"
STATE_FILE = "watchdog-state.json"
REGISTRY_KEY = "_registry"
HOW = {"never_ran": "never ran", "failed": "last run failed", "stuck": "stuck, still running",
       "missed": "no completed run in time", "no_success": "no completed run on record"}


def load_state(jobs_root) -> dict:
    try:
        doc = json.loads((Path(jobs_root) / STATE_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    return {k: v for k, v in jobs.items() if isinstance(v, dict)} if isinstance(jobs, dict) else {}


def save_state(jobs_root, jobs: dict) -> None:
    status.write_json_atomic(jobs_root, STATE_FILE, {"schema_version": 1, "jobs": jobs})


def _age_h(doc, now) -> str:
    for key in ("last_success_at", "started_at"):
        moment = status.parse_time((doc or {}).get(key))
        if moment:
            return f"{int((now - moment).total_seconds() // 3600)} h"
    return "no run"


def _send(sender, text) -> bool:
    try:
        return sender(text) == alert.SENT
    except Exception:                                   # noqa: BLE001
        return False


def watchdog_id(host: str) -> str:
    return JOB_ID if host == "mac" else f"{JOB_ID}-{host}"


def roots_from_env(jobs_root, environ=None) -> dict:
    """{status_root token: folder}: "jobs" is the jobs root; MINIMOI_JOBS_ROOT_<TOKEN> adds another."""
    environ = os.environ if environ is None else environ
    roots = {"jobs": str(jobs_root)}
    for key, value in environ.items():
        if key.startswith("MINIMOI_JOBS_ROOT_") and value:
            roots[key[len("MINIMOI_JOBS_ROOT_"):].lower().replace("_", "-")] = value
    return roots


def check(jobs_root, jobs, state: dict, sender, now, roots=None, alerts=True) -> tuple[dict[str, str], bool]:
    """Judge ``jobs``; send what must be sent; update ``state`` in place. Returns ({job id: code}, all delivered)."""
    codes, delivered = {}, True
    roots = roots or {"jobs": str(jobs_root)}
    for job in jobs:
        doc, verdict = health.observe(job, roots, now)
        codes[job.id] = verdict.code
        if not alerts:
            continue
        prior = state.get(job.id)
        if verdict.state == "red":
            mark = {"condition": verdict.code, "key": verdict.key}
            if prior and {k: prior.get(k) for k in mark} == mark:
                continue                                                        # already told
            if verdict.code == "failed" and doc and doc.get("alert") == "sent":
                state[job.id] = mark                                            # the wrapper already told Robert
                continue
            text = f"minimoi job {job.id} on {job.host}: {HOW.get(verdict.code, verdict.code)} ({_age_h(doc, now)})"
            if _send(sender, text):
                state[job.id] = mark
            else:
                delivered = False
        elif verdict.state in ("green", "yellow") and prior:
            if _send(sender, f"minimoi job {job.id} on {job.host}: recovered, now {verdict.code}"):
                state.pop(job.id, None)
            else:
                delivered = False
    return codes, delivered


def write_health(reg, host, roots, now, jobs_root, health_dir, own_id, alerts=True, push_dest=None) -> str | None:
    """``health-<host>.json`` in the jobs root (0600) and, for MC, in ``health_dir`` (0644). The watchdog judges itself
    nowhere else, so its own line is left out; the daily check sees a stale report instead."""
    name = health.report_name(host)
    previous = health.read_report(jobs_root, host)
    report = health.host_report(reg, host, roots, now, previous, exclude=(own_id,))
    report["alerts"] = "on" if alerts else "off"
    local = status.write_json_atomic(jobs_root, name, report)
    if health_dir:
        health.publish(health_dir, name, report)
    return health.push(local, push_dest, host) if push_dest else None


def run(*, jobs_root, host="mac", registry_path=None, sender=alert.send,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc), out=sys.stdout, err=sys.stderr,
        roots=None, alerts=True, health_dir=None, push_dest=None) -> int:
    started = now()
    own_id = watchdog_id(host)
    roots = roots or {"jobs": str(jobs_root)}
    try:
        reg = registry.load(registry_path)
    except registry.RegistryError:
        reg = None
    own = reg.get(own_id) if reg else None
    if own is None:
        own = registry.parse({"schema_version": 1, "jobs": [{
            "id": own_id, "name": "Scheduled-jobs watchdog", "host": host,
            "schedule": {"text": "daily 09:00 Chicago", "kind": "daily", "at": "09:00", "tz": "America/Chicago"},
            "expected_every_s": 86400, "missed_after_s": 180000, "stuck_after_s": 3600,
            "status_file": f"{own_id}.json", "owner": "robert", "runbook": "OPERATIONS.md"}]}).jobs[0]
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    try:
        running = status.start_run(jobs_root, own, run_id, started)
    except Exception as exc:                    # noqa: BLE001
        print(f"{own_id}: cannot write the status file ({type(exc).__name__})", file=err)
        if alerts:
            _send(sender, f"minimoi job {own_id} on {host}: status file not writable")
        return 2
    state = load_state(jobs_root)
    results: dict[str, str] = {}
    delivered = True
    try:
        if reg is None:
            results["registry"] = "unreadable"
            if REGISTRY_KEY not in state and alerts:
                if _send(sender, f"minimoi job {own_id} on {host}: the job registry cannot be read"):
                    state[REGISTRY_KEY] = {"condition": "registry", "key": "unreadable"}
                else:
                    delivered = False
            outcome = "failed"
        else:
            if REGISTRY_KEY in state and alerts:
                if _send(sender, f"minimoi job {own_id} on {host}: the job registry is readable again"):
                    state.pop(REGISTRY_KEY)
                else:
                    delivered = False
            judged = [j for j in reg.for_host(host) if j.id != own_id]
            results, ok = check(jobs_root, judged, state, sender, started, roots=roots, alerts=alerts)
            delivered = delivered and ok
            outcome = "ok" if delivered else "warn"
            pushed = write_health(reg, host, roots, started, jobs_root, health_dir, own_id, alerts, push_dest)
            if pushed is not None:
                results["health-push"] = pushed
                if pushed != "ok":
                    outcome = "warn"
        if reg is not None:                     # a job removed from the registry has nothing left to track
            known = {j.id for j in reg.jobs} | {REGISTRY_KEY}
            for stale_id in [k for k in state if k not in known]:
                state.pop(stale_id)
        save_state(jobs_root, state)
    except Exception as exc:                    # noqa: BLE001
        results = {"watchdog": f"exception_{type(exc).__name__.lower()}"[:40]}
        outcome, delivered = "failed", False
    finished = now()
    try:
        next_due = schedule.next_fire(own.schedule, finished)
    except Exception:                           # noqa: BLE001
        next_due = None
    checked = len([k for k in results if k != "health-push"])
    if outcome == "ok":
        summary = f"{checked} jobs checked"
    elif outcome == "warn":
        summary = f"{checked} jobs checked, " + ("alert not sent" if not delivered else "report not sent to production")
    else:
        summary = "watchdog could not finish"
    code = 0 if outcome == "ok" else 1
    try:
        status.finish_run(jobs_root, own, running, state=outcome, now=finished,
                          exit_code=code, summary=summary, results=results, next_due_by=next_due,
                          alert="not_needed")
    except Exception as exc:                    # noqa: BLE001
        print(f"{own_id}: cannot write the final status ({type(exc).__name__})", file=err)
        return 2
    print(f"{own_id} {outcome} {json.dumps(results, sort_keys=True)}", file=out)
    return code


def main() -> int:
    root = Path(os.environ.get("MINIMOI_JOBS_ROOT") or DEFAULT_JOBS_ROOT).expanduser()
    return run(jobs_root=root, host=os.environ.get("MINIMOI_JOBS_HOST") or "mac",
               registry_path=os.environ.get("MINIMOI_JOBS_REGISTRY") or None, roots=roots_from_env(root),
               alerts=(os.environ.get("MINIMOI_JOBS_ALERT") or "on") != "off",
               health_dir=os.environ.get("MINIMOI_JOBS_HEALTH_DIR") or None,
               push_dest=os.environ.get("MINIMOI_JOBS_HEALTH_PUSH") or None)


if __name__ == "__main__":
    sys.exit(main())
