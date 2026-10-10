"""How a job's last status reads against its registry entry. The one reference for the contract's rules.

Used by the watchdog today; the Guild Operate tile and any exporter can call it (or copy the rules from
docs/jobs_status_contract.md). Pure: the clock is passed in. ``unknown`` is never healthy, and a status
file that cannot be read is never "fine".

    judge(job, doc, now) -> Verdict(state, code, key)

``state`` is green | yellow | red | unknown. ``code`` is a fixed word (ok, warn, late, first_run, missed,
failed, stuck, never_ran, no_success, not_due, skew). ``key`` identifies the occurrence (a run id, a
last-success time) so an alert can be sent once per condition rather than on every check.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from core.jobs.status import parse_time

SKEW_S = 60
PRECEDENCE = ("unknown", "red", "yellow", "green")


@dataclass(frozen=True)
class Verdict:
    state: str
    code: str
    key: str = ""


def judge(job, doc: dict | None, now: datetime) -> Verdict:
    if doc is None:                                    # missing, unreadable or corrupt
        due = job.active_from + timedelta(seconds=job.overdue_after_s) if job.active_from else None
        if due is not None and now > due:
            return Verdict("red", "never_ran", "none")
        return Verdict("unknown", "not_due", "none")
    started = parse_time(doc.get("started_at"))
    finished = parse_time(doc.get("finished_at"))
    success = parse_time(doc.get("last_success_at"))
    horizon = now + timedelta(seconds=SKEW_S)
    if any(t is not None and t > horizon for t in (started, finished, success)):
        return Verdict("unknown", "skew", str(doc.get("run_id", "")))
    run_id, state = str(doc.get("run_id", "")), doc.get("state")
    if state == "failed":
        return Verdict("red", "failed", run_id)
    running = state == "running"
    if running and started is not None and (now - started).total_seconds() > job.stuck_after_s:
        return Verdict("red", "stuck", run_id)
    if success is None:
        return Verdict("yellow", "first_run", run_id) if running else Verdict("red", "no_success", run_id)
    age = (now - success).total_seconds()
    if age > job.missed_after_s:
        return Verdict("red", "missed", doc.get("last_success_at") or "")
    if age > job.overdue_after_s:
        return Verdict("yellow", "late", doc.get("last_success_at") or "")
    if state == "warn":
        return Verdict("yellow", "warn", run_id)
    return Verdict("green", "ok", run_id)


def worst(states) -> str:
    """Summary of several lights: unknown > red > yellow > green. No lights at all is unknown."""
    seen = set(states)
    return next((s for s in PRECEDENCE if s in seen), "unknown")
