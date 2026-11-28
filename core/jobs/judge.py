"""How a job's last status reads against its registry entry. The one reference for the contract's rules.

Used by the watchdog today; the Guild Operate tile and any exporter can call it (or copy the rules from
docs/jobs_status_contract.md). Pure: the clock is passed in. ``unknown`` is never healthy, and a status
file that cannot be read is never "fine".

    judge(job, doc, now) -> Verdict(state, code, key)

``state`` is green | yellow | red | unknown. ``code`` is a fixed word (ok, warn, late, first_run, missed,
failed, stuck, never_ran, no_success, not_due, skew, no_progress, progress_unknown).

Progress (WS3 C1.2): for a job with ``progress_required``, a *completed* run (ok or warn) that is not missed is also
checked for progress. Failure precedence is kept: failed, stuck, no_success and missed are decided first and never
turned green by a phase. Then: no ``progress`` block, an unknown phase, a bad ``work_due``, or a missing or future
``last_progress_at`` while work is due is ``unknown/progress_unknown``. ``complete``, ``idle_no_work`` and
``outside_window`` (or the clock outside the job's ``progress_window``) need no progress. ``active`` with work due:
idle time is counted from the later of ``last_progress_at`` and the current window's start; beyond ``idle_allowed_s``
it is ``yellow/no_progress``, beyond twice that ``red/no_progress``. ``key`` identifies the occurrence (a run id, a
last-success time) so an alert can be sent once per condition rather than on every check.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta

try:                                       # the portal image may lack the zone database; registry loading does not need it
    from zoneinfo import ZoneInfo
except ImportError:                        # pragma: no cover
    ZoneInfo = None

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
    if getattr(job, "progress_required", False) and state in ("ok", "warn"):
        stalled = _progress(job, doc, now, horizon)
        if stalled is not None:
            return stalled
    if age > job.overdue_after_s:
        return Verdict("yellow", "late", doc.get("last_success_at") or "")
    if state == "warn":
        return Verdict("yellow", "warn", run_id)
    return Verdict("green", "ok", run_id)


def window_start(window: dict, now: datetime) -> datetime | None:
    """The start of the progress window ``now`` is in, or None when ``now`` is outside it. Overnight windows
    (start after end) are supported; local wall times follow daylight saving through the zone database."""
    zone = ZoneInfo(window["tz"])
    local = now.astimezone(zone)
    start = dtime(*map(int, window["start"].split(":")))
    end = dtime(*map(int, window["end"].split(":")))
    t = local.time()
    if start < end:
        inside, day = start <= t < end, local.date()
    else:
        inside = t >= start or t < end
        day = local.date() if t >= start else local.date() - timedelta(days=1)
    if not inside:
        return None
    return datetime.combine(day, start, tzinfo=zone)


def _progress(job, doc: dict, now: datetime, horizon: datetime) -> Verdict | None:
    run_id = str(doc.get("run_id", ""))
    block = doc.get("progress")
    if not isinstance(block, dict) or block.get("phase") not in ("active", "idle_no_work", "complete", "outside_window"):
        return Verdict("unknown", "progress_unknown", run_id)
    if block["phase"] != "active":
        return None
    since = None
    if job.progress_window:
        since = window_start(job.progress_window, now)
        if since is None:
            return None                                # outside the window: no progress expected
    due = block.get("work_due")
    if isinstance(due, bool) or not isinstance(due, int) or due < 0:
        return Verdict("unknown", "progress_unknown", run_id)
    if due == 0:
        return None
    last = parse_time(block.get("last_progress_at"))
    if last is None or last > horizon:
        return Verdict("unknown", "progress_unknown", run_id)
    idle = (now - max(last, since) if since else now - last).total_seconds()
    key = block.get("last_progress_at") or ""
    if idle > 2 * job.idle_allowed_s:
        return Verdict("red", "no_progress", key)
    if idle > job.idle_allowed_s:
        return Verdict("yellow", "no_progress", key)
    return None


def worst(states) -> str:
    """Summary of several lights: unknown > red > yellow > green. No lights at all is unknown."""
    seen = set(states)
    return next((s for s in PRECEDENCE if s in seen), "unknown")
