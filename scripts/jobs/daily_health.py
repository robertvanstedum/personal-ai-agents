#!/usr/bin/env python3
"""The daily health check for MC: every non-green job on every host that reports, with a severity and how long it has
been off (FF-19, #537). Registered as job ``jobs-daily-health`` (EC2, daily 09:10 Chicago, after the hourly watchdog).

    python3 scripts/jobs/daily_health.py

Reads ``health-<host>.json`` for every host in the registry from ``MINIMOI_JOBS_HEALTH_DIR`` (on EC2: the portal's
mc-observations volume, ``health/``), and writes there:

* ``health-daily.json``: ``items`` (each non-green job: host, severity ``fine`` / ``watch`` / ``needs action``,
  ``off_for`` such as "2 days"), ``not_reporting`` (hosts with no report here), ``not_tracked`` and ``text``, the same
  in a few plain lines, problems first. MC reads it through ``minimoi_portal/guild_ui/jobs_health.py``.

Telegram (``MINIMOI_JOBS_NOTIFY=off`` turns it off; Dev runs that way): only ``needs action`` (red, or yellow for more
than 24 h; ``core.jobs.health.severity``). Kept in ``health-daily-state.json`` in the jobs root (WS3 C0.1, 28 Nov 2026):

* a condition (the job, its code and since when it is off) is told once per Chicago day while it lasts, so an open
  problem is repeated daily ("still needs action") instead of told once and forgotten;
* a red item of a host whose own watchdog alerts (its report says ``alerts: on`` and is fresh) is not told on the day it
  appears, because that watchdog already sent it within the hour; it is repeated on later days. A stale report's
  ``alerts: on`` counts for nothing: a host that stopped reporting is not alerting either;
* a told condition that is gone is announced once as recovered, but only when its host's report is fresh (a missing or
  stale report proves nothing, so the condition is carried until the host is seen again), and not for a host whose
  watchdog alerts (it announces its own recoveries). A condition that drops to ``watch`` is announced as improved.

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
from zoneinfo import ZoneInfo
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
DAY_TZ = ZoneInfo("America/Chicago")    # "once a day" is Robert's day


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
    fresh = sorted(h for h, r in reporting.items()
                   if (now - status.parse_time(r["checked_at"])).total_seconds()
                   <= health.STALE_AFTER_S.get(h, health.REPORT_STALE_S))
    for item in items:
        item["alerts_on_host"] = item["host"] in fresh and (reporting.get(item["host"]) or {}).get("alerts") == "on"
    return {"schema_version": 1, "checked_at": status.iso(now), "hosts_reporting": sorted(reporting),
            "hosts_fresh": fresh, "alerts_on_hosts": sorted(h for h in fresh if reporting[h].get("alerts") == "on"),
            "not_reporting": not_reporting, "items": items,
            "not_tracked": [{"id": i["id"], "name": i["name"], "host": i["host"]} for i in not_tracked],
            "text": text}


def _key(item: dict, day: str) -> str:
    return f"{item['code']}|{item.get('off_since') or ''}|{day}|{item['host']}"


def _split(key: str) -> tuple[str, str, str]:
    """(condition, day, host) of a told key. A key from before C0.1 ("code|since") has no day or host."""
    parts = key.split("|")
    if len(parts) >= 4:
        return "|".join(parts[:2]), parts[2], parts[3]
    return "|".join(parts[:2]), "", ""


def notify(daily: dict, told: dict, sender, now: datetime | None = None) -> tuple[dict, bool]:
    """Tell each needs-action condition once per Chicago day while it lasts, and its recovery once. Returns (the new told
    map, all delivered). See the module docstring for the rules."""
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(DAY_TZ).date().isoformat()
    fresh, self_alerting = set(daily.get("hosts_fresh", ())), set(daily.get("alerts_on_hosts", ()))
    delivered, current = True, {}

    def send(text: str) -> bool:
        try:
            return sender(text) == alert.SENT
        except Exception:                               # noqa: BLE001
            return False

    by_id = {i["id"]: i for i in daily["items"]}
    for item in daily["items"]:
        if item["severity"] != health.NEEDS_ACTION:
            continue
        key, prior = _key(item, day), told.get(item["id"])
        condition = _key(item, day).rsplit("|", 2)[0]
        prior_condition, prior_day, _ = _split(prior) if prior else ("", "", "")
        if prior_condition == condition and prior_day == day:
            current[item["id"]] = prior                 # already told today
            continue
        repeat = prior_condition == condition
        if not repeat and item.get("alerts_on_host") and item.get("light") == "red":
            current[item["id"]] = key                   # its own watchdog sent it within the hour; repeat from tomorrow
            continue
        head = "still needs action" if repeat else "needs action"
        if send(f"minimoi health, {head}: {health.line(item)}"):
            current[item["id"]] = key
        else:
            delivered = False                           # not recorded for today: the next run tries again
            if prior:
                current[item["id"]] = prior
    for job_id, prior in told.items():
        if job_id in current:
            continue
        _, _, host = _split(prior)
        if not host:
            continue                                    # a key from before C0.1: forgotten quietly, once
        if host not in fresh:
            current[job_id] = prior                     # cannot see that host now: carried, never called recovered
            continue
        if host in self_alerting:
            continue                                    # its watchdog announces its own recoveries
        now_item = by_id.get(job_id)
        text = (f"minimoi health, improved: {health.line(now_item)}" if now_item
                else f"minimoi health, recovered: {job_id} ({host}) is no longer off")
        if not send(text):
            delivered = False
            current[job_id] = prior                     # announced on the next run instead
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
        told, delivered = notify(daily, _state(jobs_root), sender, started)
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
