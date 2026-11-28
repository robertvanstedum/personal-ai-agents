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
* a red item of this host is not told on the day it appears only when this host's hourly watchdog *recorded sending it*
  (``watchdog-state.json``: same job, same condition, same occurrence key); it is repeated on later days. ``alerts: on`` in a report proves
  no delivery, so another host's red items (the Mac's) are told here too: a duplicate beats silence;
* a told condition is announced as recovered only on affirmative evidence: its host's report is fresh (not stale, not
  dated in the future) **and** lists the same job green (``ok``). A job observed yellow ``late``/``warn`` is announced as
  improved. Anything else (a missing or stale report, the job absent from the report, ``not_tracked``, unknown,
  ``skew``, ``not_due``) is lost observation: the condition is carried, unresolved. A job retired or removed from the
  registry is dropped without a message. A recovery whose message fails is retried on the next run. (This host's watchdog may also
  announce a recovery it sent itself; a second "recovered" line beats a silent one.)

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

from core.jobs import alert, health, judge, registry, schedule, status  # noqa: E402

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


def _watchdog_told(jobs_root) -> dict:
    """What this host's hourly watchdog recorded as sent ({job id: {condition, key}}), the only proof that it delivered.
    A report's ``alerts: on`` says the switch is on, not that a message went out."""
    try:
        doc = json.loads((Path(jobs_root) / "watchdog-state.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    return {k: v for k, v in jobs.items() if isinstance(v, dict)} if isinstance(jobs, dict) else {}


def _fresh(host: str, report: dict, now: datetime) -> bool:
    """A report is fresh when it is neither older than its host's limit nor dated in the future beyond the clock-skew
    tolerance. A future-dated report is skewed evidence: it authorizes neither a recovery nor a suppression."""
    age = (now - status.parse_time(report["checked_at"])).total_seconds()
    return -judge.SKEW_S <= age <= health.STALE_AFTER_S.get(host, health.REPORT_STALE_S)


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
    fresh = sorted(h for h, r in reporting.items() if _fresh(h, r, now))
    # What each fresh host's report says about every job, unfiltered (daily_items drops ok and not_tracked): recovery
    # and improvement are announced only from a present, positive observation of the same job.
    observed = {h: {i["id"]: {"light": i.get("light"), "code": i.get("code")}
                    for i in reporting[h]["items"] if isinstance(i, dict) and isinstance(i.get("id"), str)}
                for h in fresh}
    for item in items:
        item["alerts_on_host"] = item["host"] in fresh and (reporting.get(item["host"]) or {}).get("alerts") == "on"
    return {"schema_version": 1, "checked_at": status.iso(now), "hosts_reporting": sorted(reporting),
            "hosts_fresh": fresh, "observed": observed, "registered": sorted(j.id for j in reg.jobs if not j.retired),
            "alerts_on_hosts": sorted(h for h in fresh if reporting[h].get("alerts") == "on"),
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


def notify(daily: dict, told: dict, sender, now: datetime | None = None, watchdog_told: dict | None = None,
           own_host: str = "ec2") -> tuple[dict, bool]:
    """Tell each needs-action condition once per Chicago day while it lasts, and its recovery once. Returns (the new told
    map, all delivered). See the module docstring for the rules."""
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(DAY_TZ).date().isoformat()
    fresh = set(daily.get("hosts_fresh", ()))
    observed = daily.get("observed") or {}
    registered = set(daily.get("registered") or ())
    watchdog_told = watchdog_told or {}
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
        record = watchdog_told.get(item["id"]) or {}
        sent_by_watchdog = (item["host"] == own_host and item.get("light") == "red" and item.get("key")
                            and record.get("condition") == item["code"] and record.get("key") == item["key"])
        if not repeat and sent_by_watchdog:
            current[item["id"]] = key                   # this host's watchdog recorded sending it; repeat from tomorrow
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
        if job_id not in registered:
            continue                                    # retired or removed from the registry: not a recovery, dropped quietly
        if host not in fresh:
            current[job_id] = prior                     # cannot see that host now: carried, never called recovered
            continue
        seen = observed.get(host, {}).get(job_id) or {}
        if seen.get("light") == "green" and seen.get("code") == "ok":
            text = f"minimoi health, recovered: {job_id} ({host}) is green again"
        elif seen.get("light") == "yellow" and seen.get("code") in ("late", "warn") and job_id in by_id:
            text = f"minimoi health, improved: {health.line(by_id[job_id])}"   # an observed operational downgrade
        else:
            current[job_id] = prior                     # absent, not tracked, unknown, skewed, not due: observation lost,
            continue                                    # the incident stays open
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
        told, delivered = notify(daily, _state(jobs_root), sender, started, _watchdog_told(jobs_root), host)
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
