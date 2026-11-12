#!/usr/bin/env python3
"""The daily health check for MC: every non-green job on every host that reports, with a severity and how long it has
been off (FF-19, #537). Registered as job ``jobs-daily-health`` (EC2, daily 09:10 Chicago, after the hourly watchdog).

    python3 scripts/jobs/daily_health.py

Reads ``health-<host>.json`` for every host in the registry from ``MINIMOI_JOBS_HEALTH_DIR`` (on EC2: the portal's
mc-observations volume, ``health/``), and writes there:

* ``health-daily.json``: ``items`` (each non-green job: host, severity ``fine`` / ``watch`` / ``needs action``,
  ``off_for`` such as "2 days"), ``not_reporting`` (hosts with no report here), ``not_tracked`` and ``text``, the same
  in a few plain lines, problems first. MC reads it through ``minimoi_portal/guild_ui/jobs_health.py``.

Telegram (``MINIMOI_JOBS_NOTIFY=off`` turns it off; Dev runs that way): only ``needs action``, once per condition (the job, its code and since when it is off; kept in
``health-daily-state.json`` in the jobs root), and never for a host whose own watchdog already alerts (its report says
``alerts: on``, the Mac's). Nothing else is sent.

**Why not a helper agent (yet).** Robert asked for a helper (Apprentice or EA) to file this for MC. In 1.2 helpers take
tasks MC hands them; nothing schedules a helper task on a clock, and adding that scheduler is not a fast-follow change.
So the judging and the wording are both deterministic here, and MC reads the result directly. When helper tasks can be
scheduled, the helper's part is only to word ``items`` and file them; the inputs and outputs stay as they are.

Exit codes: 0 done; 1 a needs-action message could not be sent (retried next run); 2 the report could not be written.
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

from core.jobs import alert, health, registry, schedule, status  # noqa: E402

JOB_ID = "jobs-daily-health"
DAILY_NAME = "health-daily.json"
STATE_FILE = "health-daily-state.json"
NOT_A_HOST = ("github",)                # listed for completeness; no watchdog runs there


def _state(jobs_root) -> dict:
    try:
        doc = json.loads((Path(jobs_root) / STATE_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    told = doc.get("told") if isinstance(doc, dict) else None
    return {k: v for k, v in told.items() if isinstance(v, str)} if isinstance(told, dict) else {}


def build(reg, health_dir, now: datetime) -> dict:
    hosts = [h for h in reg.hosts() if h not in NOT_A_HOST]
    reports = {h: health.read_report(health_dir, h) for h in hosts}
    reporting = {h: r for h, r in reports.items() if r is not None}
    not_reporting = sorted(h for h in hosts if h not in reporting)
    items = health.daily_items(reporting, now)
    not_tracked = health.untracked(reporting)
    text = health.words(items, hosts=sorted(reporting), not_tracked=len(not_tracked))
    if not_reporting:
        text += f"\nNot reporting here: {', '.join(not_reporting)} (their health is not visible to this host)."
    for item in items:
        item["alerts_on_host"] = (reporting.get(item["host"]) or {}).get("alerts") == "on"
    return {"schema_version": 1, "checked_at": status.iso(now), "hosts_reporting": sorted(reporting),
            "not_reporting": not_reporting, "items": items,
            "not_tracked": [{"id": i["id"], "name": i["name"], "host": i["host"]} for i in not_tracked],
            "text": text}


def notify(daily: dict, told: dict, sender) -> tuple[dict, bool]:
    """Send each needs-action item once. Returns (the new told map, all delivered). A condition that cleared is
    forgotten, so if it comes back it is told again."""
    delivered, current = True, {}
    for item in daily["items"]:
        if item["severity"] != health.NEEDS_ACTION:
            continue
        key = f"{item['code']}|{item.get('off_since') or ''}"
        current[item["id"]] = key
        if told.get(item["id"]) == key or item.get("alerts_on_host"):
            continue
        text = f"minimoi health, needs action: {health.line(item)}"
        try:
            ok = sender(text) == alert.SENT
        except Exception:                               # noqa: BLE001
            ok = False
        if not ok:
            delivered = False
            current.pop(item["id"])                     # not recorded: the next run tries again
    return current, delivered


def daily_id(host: str) -> str:
    return JOB_ID if host == "ec2" else f"{JOB_ID}-{host}"


def run(*, jobs_root, health_dir, registry_path=None, sender=alert.send, notify_on=True, host="ec2",
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc), out=sys.stdout, err=sys.stderr) -> int:
    started = now()
    reg = registry.load(registry_path)
    own_id = daily_id(host)
    own = reg.get(own_id) or registry.parse({"schema_version": 1, "jobs": [{
        "id": own_id, "name": "Daily health check", "host": host,
        "schedule": {"text": "daily 09:10 Chicago", "kind": "daily", "at": "09:10", "tz": "America/Chicago"},
        "expected_every_s": 86400, "missed_after_s": 180000, "stuck_after_s": 1800,
        "status_file": f"{own_id}.json", "owner": "robert", "runbook": "scripts/jobs/daily_health.py"}]}).jobs[0]
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    try:
        running = status.start_run(jobs_root, own, run_id, started)
        daily = build(reg, health_dir, started)
        health.publish(health_dir, DAILY_NAME, daily)
    except Exception as exc:                            # noqa: BLE001
        print(f"{own_id}: cannot write the report ({type(exc).__name__})", file=err)
        return 2
    if notify_on:
        told, delivered = notify(daily, _state(jobs_root), sender)
    else:
        told, delivered = {}, True
    status.write_json_atomic(jobs_root, STATE_FILE, {"schema_version": 1, "told": told})
    counts = {s: sum(1 for i in daily["items"] if i["severity"] == s)
              for s in (health.NEEDS_ACTION, health.WATCH, health.FINE)}
    results = {"needs_action": str(counts[health.NEEDS_ACTION]), "watch": str(counts[health.WATCH]),
               "fine": str(counts[health.FINE]), "hosts": str(len(daily["hosts_reporting"]))}
    outcome = "ok" if delivered else "warn"
    finished = now()
    summary = (f"{counts[health.NEEDS_ACTION]} needs action, {counts[health.WATCH]} watch, "
               f"{counts[health.FINE]} fine")
    if not delivered:
        summary += ", alert not sent"
    status.finish_run(jobs_root, own, running, state=outcome, now=finished, exit_code=0 if delivered else 1,
                      summary=summary, results=results, next_due_by=schedule.next_fire(own.schedule, finished),
                      alert="sent" if counts[health.NEEDS_ACTION] and delivered else "not_needed")
    print(daily["text"], file=out)
    return 0 if delivered else 1


def main() -> int:
    root = Path(os.environ.get("MINIMOI_JOBS_ROOT") or "/opt/minimoi/jobs")
    folder = os.environ.get("MINIMOI_JOBS_HEALTH_DIR") or str(root)
    return run(jobs_root=root, health_dir=folder, registry_path=os.environ.get("MINIMOI_JOBS_REGISTRY") or None,
               notify_on=(os.environ.get("MINIMOI_JOBS_NOTIFY") or "on") != "off",
               host=os.environ.get("MINIMOI_JOBS_HOST") or "ec2")


if __name__ == "__main__":
    sys.exit(main())
