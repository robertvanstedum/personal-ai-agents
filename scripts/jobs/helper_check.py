#!/usr/bin/env python3
"""MC's helper writes the daily job-health note (FF-19, #537). Robert, 12 Nov 2026: MC's helper may run this on its own
schedule; he does not need to be in the loop.

    docker exec minimoi-portal python /app/scripts/jobs/helper_check.py      (root cron, 09:15 Chicago, /etc/cron.d/minimoi-jobs-health)

Runs inside the portal, which holds MC's task-service token and the platform token (never the agent). Once a day:

1. **Start** a chartered Apprentice task of type ``health-check`` through the task service, exactly like MC's
   ``minimoi_helper_ask`` does, except that the source is the platform's schedule record for that type (store ``schedule``,
   id ``health-check:<date>``), which the task service admits only for a type the approved charter marks as scheduled.
   Ids come from the date, so a rerun is the same task.
2. **Wait** (up to ``WAIT_S``) for the Apprentice to submit. It reads every host's job-health reports (``health.read``),
   judges them with the same deterministic code, and words a short note in one capped model call.
3. **Review as MC**: the note's items must match what the reports say now (same jobs, same severities). Then MC records its
   review and accepts. A mismatch is not accepted, and the note falls back to the deterministic words.
4. **File it for MC**: ``helper-note.json`` beside the reports; MC's health block shows it on the next owner turn.

Telegram stays with the deterministic daily check (scripts/jobs/daily_health.py): only ``needs action``. A helper has no
effects outside its task by charter.

Status: ``jobs-helper-check.json`` in the mc-observations jobs folder (registry ``jobs-helper-check``).
Exit codes: 0 filed (accepted, or deterministic fallback filed); 1 could not start or file; 2 helpers are off here.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from datetime import timedelta

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core.canonical_json import digest  # noqa: E402
from core.jobs import health, registry, schedule, status  # noqa: E402

JOB_ID = "jobs-helper-check"
SEAT, TYPE_ID, LEAD = "apprentice", "health-check", "mc"
NOTE_NAME = "helper-note.json"
HEALTH_DIR = "/app/runtime/mc-observations/health"
JOBS_DIR = "/app/runtime/mc-observations/jobs"
WAIT_S, POLL_S = 240, 3
RUNTIME = {"kind": "deterministic", "model": None, "provider": None, "build": "jobs/helper-check/1", "config_version": None}


class NotStarted(RuntimeError):
    pass


def chicago_day(now: datetime) -> str:
    """The Chicago date; the portal image may lack the zone database, so then a fixed UTC-6 (the date differs by at most
    one hour a year, around the clock change, and the run is at 09:15)."""
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/Chicago")).date().isoformat()
    except Exception:                                   # noqa: BLE001
        return (now.astimezone(timezone.utc) - timedelta(hours=6)).date().isoformat()


def ids(day: str) -> dict:
    key = digest([TYPE_ID, day])[7:31]
    return {"sid": f"SE-sched-{key}", "iid": f"IN-sched-{key}", "rid": f"RQ-sched-{key}", "tid": f"TK-hc-{key}",
            "key": key}


def _command(kind, ttype, tid, payload, cid, *, environment, scope, expected=None, parent=None):
    from services.partnership_tasks.client import command
    return command(kind, ttype, tid, payload, command_id=cid[:80], environment=environment, scope=scope,
                   expected_revision=expected, causal_parent=parent, session_id=None, runtime=RUNTIME)


def _send(client, cmd):
    out = client.submit(cmd)
    if out.get("outcome") != "accepted":
        raise NotStarted(f"{cmd['kind']}:{out.get('detail') or out.get('code')}")


def start(mc, platform, charter, day: str, *, environment: str, scope: str = "personal", seat: str = SEAT) -> str:
    """The day's task id, started if it was not already. Raises NotStarted with the task service's reason."""
    kind = charter.types.get(TYPE_ID)
    if kind is None or not kind.get("schedule"):
        raise NotStarted("not_chartered")
    i = ids(day)
    if mc.get("task", i["tid"]) is not None:
        return i["tid"]
    inputs = json.dumps({"date": day}, sort_keys=True)
    terms = {"lead": LEAD, "holder": seat, "outcome": kind["purpose"][:500], "done_when": inputs, "return_window": None}
    proposed = {"outcome": terms["outcome"], "scope": scope, "recipients": ["principal"], "continuing": True, "task": terms}
    grant = {"kind": "existing_grant", "ref": {"store": "grants", "id": charter.grant_id, "edition": None, "digest": charter.digest}}
    charter_ref = {"seat": seat, "version": charter.version, "digest": charter.digest, "type_id": TYPE_ID}
    parent = {"store": "task_service", "id": i["sid"], "edition": None, "digest": None}
    c = dict(environment=environment, scope=scope)
    if platform.get("source_event", i["sid"]) is None:
        _send(platform, _command("source_event.record", "source_event", i["sid"], {
            "source": {"store": "schedule", "id": f"{TYPE_ID}:{day}", "position": 1},
            "content": f"Daily job-health check for {day}: the Apprentice's scheduled charter type {TYPE_ID} "
                       f"(Robert, 12 Nov 2026: MC's helper runs it on its own schedule).",
            "correction_of": None}, f"cmd-sched-src-{i['key']}", **c))
    if mc.get("interpretation", i["iid"]) is None:
        _send(mc, _command("interpretation.record", "interpretation", i["iid"], {
            "source_event_ids": [i["sid"]], "correction_event_ids": [], "proposed": proposed, "context_refs": [],
            "supersedes": None}, f"cmd-sched-int-{i['key']}", parent=parent, **c))
    cur = mc.get("interpretation", i["iid"])
    if cur["disposition"] != "adopted":
        _send(mc, _command("interpretation.dispose", "interpretation", i["iid"], {"disposition": "adopted", "authority": grant},
                           f"cmd-sched-adp-{i['key']}", expected=cur["revision"], parent=parent, **c))
    if mc.get("request", i["rid"]) is None:
        _send(mc, _command("request.record", "request", i["rid"], {"source_event_id": i["sid"], "intent_text": terms["outcome"],
                                                                     "interpretation_ref": i["iid"], "responder": LEAD},
                           f"cmd-sched-req-{i['key']}", parent=parent, **c))
    req = mc.get("request", i["rid"])
    if req["task_ref"] is None:
        _send(mc, _command("request.promote", "request", i["rid"], {"task_id": i["tid"], "parent_ref": None, **terms,
                                                                      "charter_ref": charter_ref},
                           f"cmd-sched-pro-{i['key']}", expected=req["revision"], parent=parent, **c))
    return i["tid"]


def wait_for_result(mc, tid, *, wait_s=WAIT_S, poll_s=POLL_S, sleep=time.sleep):
    waited = 0
    while True:
        task = mc.get("task", tid)
        if task and task["artifact_sets"]:
            return task
        if waited >= wait_s:
            return task
        sleep(poll_s)
        waited += poll_s


def review(mc, platform, task, outcome: dict, expected: list, *, environment: str, scope: str = "personal") -> bool:
    """MC's review of the exact set: accepted when the helper's items are the reports' items. Returns accepted."""
    from services.helper_seats.review_evidence import evidence
    got = sorted((i.get("id"), i.get("severity")) for i in outcome.get("items") or [])
    want = sorted((i["id"], i["severity"]) for i in expected)
    if got != want:
        return False
    current = task["artifact_sets"][-1]["digest"]
    if any(a["status"] == "current" and a["artifact_set_digest"] == current for a in task["acceptances"]):
        return True
    review_id = f"RV-sched-{digest([task['task_id'], current])[7:31]}"
    reasons = f"Checked against the watchdog reports: the same {len(want)} items with the same severities."
    ref, keep = evidence(task["task_id"], review_id, reasons, environment=environment, scope=scope, runtime=RUNTIME)
    if platform.get("source_event", ref["id"]) is None:
        _send(platform, keep)
    c = dict(environment=environment, scope=scope)
    t = mc.get("task", task["task_id"])
    if not any(r["review_id"] == review_id for r in t["reviews"]):
        _send(mc, _command("task.record_review", "task", t["task_id"], {"review_id": review_id, "reviewed_set_digest": current,
                                                                         "input_digest": current, "verdict": "approve",
                                                                         "review_ref": ref, "findings_ref": None},
                           f"cmd-sched-rev-{review_id}", expected=t["revision"], **c))
    t = mc.get("task", task["task_id"])
    _send(mc, _command("task.accept", "task", t["task_id"], {"artifact_set_digest": current, "acknowledged_disagreements": [],
                                                              "fence_generation": t["fence_generation"]},
                       f"cmd-sched-acc-{review_id}", expected=t["revision"], **c))
    return True


def result_of(mc, task) -> dict:
    record = mc.get("source_event", task["artifact_sets"][-1]["artifacts"][0]["id"])
    try:
        return json.loads(record["content"])["outcome"]
    except (TypeError, ValueError, KeyError):
        return {}


def run_once(mc, platform, charter, *, environment, health_dir, now, scope="personal", wait_s=WAIT_S, sleep=time.sleep,
             seat=SEAT) -> dict:
    """Start, wait, review, and the note to file. Never raises for a task-service refusal: it says why in the note."""
    from services.helper_seats.types import health_items
    day = chicago_day(now)
    items, reporting, silent = health_items(health_dir, now)
    plain = health.words(items, hosts=reporting) + (f"\nNo report from: {', '.join(silent)}." if silent else "")
    note = {"schema_version": 1, "date": day, "written_at": status.iso(now), "drafted_by": None, "accepted_by": None,
            "note": plain, "note_by": "deterministic", "needs_action": sum(1 for i in items if i["severity"] == health.NEEDS_ACTION),
            "task_id": None, "why": None}
    try:
        tid = start(mc, platform, charter, day, environment=environment, scope=scope, seat=seat)
    except NotStarted as exc:
        note["why"] = f"helper not started ({exc})"
        return note
    note["task_id"] = tid
    task = wait_for_result(mc, tid, wait_s=wait_s, sleep=sleep)
    if not task or not task["artifact_sets"]:
        note["why"] = "the helper had not finished; the deterministic note is filed instead"
        return note
    outcome = result_of(mc, task)
    note["drafted_by"] = seat
    if outcome.get("stopped"):
        note["why"] = f"the helper stopped ({outcome.get('stopped')})"
        return note
    try:
        accepted = review(mc, platform, task, outcome, items, environment=environment, scope=scope)
    except NotStarted as exc:
        note["why"] = f"review not recorded ({exc})"
        accepted = False
    if accepted and isinstance(outcome.get("note"), str) and outcome["note"].strip():
        note.update(note=outcome["note"].strip()[:1500], note_by=outcome.get("note_by", "model"), accepted_by=LEAD)
    elif not accepted and not note["why"]:
        note["why"] = "the helper's items did not match the reports, so MC did not accept them"
    return note


def main() -> int:
    sys.path.insert(0, "/app")
    started = datetime.now(timezone.utc)
    reg = registry.load()
    own = reg.get(JOB_ID)
    jobs_dir = os.environ.get("MINIMOI_JOBS_HELPER_STATUS_DIR") or JOBS_DIR
    health_dir = os.environ.get("MINIMOI_JOBS_HEALTH_DIR") or HEALTH_DIR
    running = status.start_run(jobs_dir, own, f"{started.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}", started)

    def finish(state, code, summary, results):
        try:
            next_due = schedule.next_fire(own.schedule, started)
        except Exception:                               # noqa: BLE001 - no zone database in this image
            next_due = None
        status.finish_run(jobs_dir, own, running, state=state, now=datetime.now(timezone.utc), exit_code=code,
                          summary=summary, results=results, next_due_by=next_due, alert="not_needed")
        return code

    from minimoi_portal.guild_ui.mc_tools_wiring import tasks_adapter
    adapter = tasks_adapter(os.environ)
    charter = adapter.charters.get(SEAT) if adapter is not None and adapter.charters is not None else None
    if adapter is None or adapter.platform is None or charter is None:
        return finish("failed", 2, "helpers are off here", {"helper": "off"})
    note = run_once(adapter.client, adapter.platform, charter, environment=adapter.env, health_dir=health_dir, now=started,
                    scope=adapter.scope)
    health.publish(health_dir, NOTE_NAME, note)
    if note["accepted_by"]:
        return finish("ok", 0, "helper note accepted by MC", {"helper": "accepted", "note_by": note["note_by"]})
    return finish("warn", 0 if note["task_id"] else 1, "deterministic note filed, helper not accepted",
                  {"helper": "not_accepted" if note["task_id"] else "not_started"})


if __name__ == "__main__":
    sys.exit(main())
