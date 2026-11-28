"""The first operational slice (WS3; Codex build-acceptance item 1 and its checklist), before any dashboard.

One incident end to end, with real functions and injected adapters (clock, sender, notice writer):
a valid backlog with no progress -> the judge goes red -> the hourly watchdog delivers once -> one owner-visible problem
-> an observation outage stays unknown (never recovered) -> progress resumes -> two distinct fresh evaluations plus a
fresh healthy signal close the same problem once -> re-firing within an hour re-opens it.

A passing fixture is not proof of live delivery; the release lead repeats the two cases on Dev and records receipts.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.jobs import judge, problems, registry, status

REPO = Path(__file__).resolve().parents[2]
M = timedelta(minutes=1)


def _watchdog():
    spec = importlib.util.spec_from_file_location("watchdog_slice", REPO / "scripts/jobs/watchdog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wd = _watchdog()

# 01:00 Chicago on 28 Nov 2026 (CST, UTC-6), inside the seed's 00:00-06:00 window
T0 = datetime(2026, 11, 28, 7, 0, tzinfo=timezone.utc)
SEED = registry.parse({"schema_version": 1, "jobs": [{
    "id": "vault-meaning-seed", "name": "Vault meaning seed", "host": "ec2",
    "schedule": {"text": "every 15 min at night", "kind": "interval", "every_s": 900},
    "expected_every_s": 86400, "missed_after_s": 172800, "stuck_after_s": 7200, "status_file": "seed.json",
    "owner": "robert", "runbook": "x", "active_from": "2026-11-26T06:00:00Z",
    "progress_required": True, "idle_allowed_s": 1200,
    "progress_window": {"start": "00:00", "end": "06:00", "tz": "America/Chicago"}}]}).jobs[0]
FP = problems.fingerprint("job_no_progress", SEED.id, SEED.host)


def write_slice(root, at, *, last_progress, run):
    status.write_status(root, SEED.status_file, status.build(
        job_id=SEED.id, host=SEED.host, run_id=run, state="ok", started_at=at - 5 * M, finished_at=at, exit_code=0,
        summary="budget_spent", last_success_at=at,
        progress={"work_due": 1, "last_progress_at": status.iso(last_progress), "phase": "active"}))


def evaluate(root, at, eval_id, *, complete=True, rules="v1"):
    """Stands in for the provider's rule evaluation: the shared rule (job not green with code no_progress) over the
    real judge. The underlying signal is the job's own status; it is healthy when the judge says ok."""
    doc = status.read_status(root, SEED.status_file, SEED.id)
    v = judge.judge(SEED, doc, at)
    firing, healthy = {}, {}
    if v.code == "no_progress":
        firing[FP] = problems.Firing(severity=v.state, owner="vault", reason="no_progress", value=v.state,
                                     runbook="docs/jobs_status_contract.md")
    elif v.code == "ok":
        healthy[FP] = status.parse_time(doc["finished_at"])
    return problems.Evaluation(evaluation_id=eval_id, evaluated_at=at, rules_version=rules, complete=complete,
                               firing=firing, healthy_at=healthy)


class Notices:
    """The audited notice adapter, stubbed: idempotent on the notice key, can be told to fail."""

    def __init__(self):
        self.by_key, self.fail = {}, False

    def __call__(self, notice):
        if self.fail:
            return False
        self.by_key.setdefault(notice["key"], notice)
        return True

    def events(self):
        return [n["event"] for n in self.by_key.values()]


def test_one_incident_from_stall_to_verified_recovery_and_recurrence(tmp_path):
    jobs, state = tmp_path / "jobs", tmp_path / "state" / "ops-problems.json"
    now = {"t": T0}
    clock = lambda: now["t"]                                              # noqa: E731
    notes, sent, wd_state = Notices(), [], {}
    tracker = problems.ProblemTracker(state, notes, clock)

    # 1. a valid backlog, no progress since 00:05 Chicago: yellow at 00:25+, red past 00:45 (idle 1200 s, twice 2400 s)
    write_slice(jobs, T0 - 10 * M, last_progress=T0 - 55 * M, run="r1")
    assert judge.judge(SEED, status.read_status(jobs, SEED.status_file), T0).state == "red"

    # 2. the hourly watchdog delivers it once
    wd.check(jobs, [SEED], wd_state, lambda t: sent.append(t) or "sent", T0)
    wd.check(jobs, [SEED], wd_state, lambda t: sent.append(t) or "sent", T0 + 5 * M)
    assert len(sent) == 1 and "vault-meaning-seed" in sent[0]

    # 3. one owner-visible problem, also across repeated polls and a helper restart
    tracker.observe(evaluate(jobs, T0, "e1"))
    now["t"] = T0 + 5 * M
    tracker.observe(evaluate(jobs, now["t"], "e2"))
    tracker = problems.ProblemTracker(state, notes, clock)                 # restart
    now["t"] = T0 + 10 * M
    tracker.observe(evaluate(jobs, now["t"], "e3"))
    assert list(tracker.problems()) == [FP] and notes.events().count("open") == 1
    problem_id = tracker.problems()[FP]["id"]

    # 4. an observation outage: incomplete, stale, future, the same evaluation again, changed rules -> unknown, open
    now["t"] = T0 + 15 * M
    for bad in (evaluate(jobs, now["t"], "e4", complete=False),
                evaluate(jobs, now["t"] - 20 * M, "e5"),
                evaluate(jobs, now["t"] + 10 * M, "e6"),
                evaluate(jobs, now["t"], "e7", rules="v2")):
        tracker.observe(bad)
        p = tracker.problems()[FP]
        assert p["status"] == "open" and p.get("unknown_since") and "close" not in notes.events()
    now["t"] = T0 + 50 * M                                                 # still blind after 30 min: one notice
    tracker.observe(evaluate(jobs, now["t"], "e8", complete=False))
    tracker.observe(evaluate(jobs, now["t"] + M, "e9", complete=False))
    assert notes.events().count("cannot_see") == 1

    # 5. progress resumes; one clear evaluation, or one without a healthy signal, cannot close
    now["t"] = T0 + 60 * M
    write_slice(jobs, now["t"] - M, last_progress=now["t"] - M, run="r2")
    first = evaluate(jobs, now["t"], "e10", rules="v2")
    tracker.observe(problems.Evaluation(first.evaluation_id, first.evaluated_at, "v2", True, {}, {}))  # no signal
    assert tracker.problems()[FP]["status"] == "open"
    now["t"] = T0 + 65 * M
    tracker.observe(evaluate(jobs, now["t"], "e11", rules="v2"))
    assert tracker.problems()[FP]["status"] == "open"                      # one healthy sample so far
    tracker.observe(evaluate(jobs, now["t"], "e11", rules="v2"))           # the same evaluation again: not a second
    assert tracker.problems()[FP]["status"] == "open" and tracker.problems()[FP]["clear_samples"] == 1

    # 6. a second distinct fresh evaluation with a healthy signal closes it once; the watchdog announces recovery once
    notes.fail = True
    now["t"] = T0 + 70 * M
    write_slice(jobs, now["t"] - M, last_progress=now["t"] - M, run="r2b")    # the seed keeps moving
    tracker.observe(evaluate(jobs, now["t"], "e12", rules="v2"))
    assert tracker.problems()[FP]["status"] == "recovered" and "close" not in notes.events()   # queued, not lost
    notes.fail = False
    now["t"] = T0 + 75 * M
    tracker.observe(evaluate(jobs, now["t"], "e13", rules="v2"))
    tracker.observe(evaluate(jobs, now["t"] + M, "e14", rules="v2"))
    assert notes.events().count("close") == 1
    wd.check(jobs, [SEED], wd_state, lambda t: sent.append(t) or "sent", now["t"])
    wd.check(jobs, [SEED], wd_state, lambda t: sent.append(t) or "sent", now["t"] + 5 * M)
    assert len(sent) == 2 and "recovered" in sent[1]

    # 7. re-firing within the hour re-opens the same problem; history keeps the recovered interval
    now["t"] = T0 + 110 * M
    write_slice(jobs, now["t"] - M, last_progress=T0 + 59 * M, run="r3")
    tracker.observe(evaluate(jobs, now["t"], "e15", rules="v2"))
    p = tracker.problems()[FP]
    assert p["status"] == "open" and p["id"] == problem_id and len(p["history"]) == 1
    assert notes.events().count("reopen") == 1 and notes.events().count("open") == 1

    # 8. side effects and privacy: the only writes are the notices and the state file; notices hold fixed fields
    allowed = {"key", "problem_id", "event", "at", "severity", "owner", "reason", "value", "runbook"}
    for n in notes.by_key.values():
        assert set(n) == allowed and "/" not in (n["value"] or "") and n["reason"] == "no_progress"
    assert sorted(p.name for p in (tmp_path / "state").iterdir()) == ["ops-problems.json"]
    saved = json.loads(state.read_text())
    assert saved["outbox"] == [] and list(saved["problems"]) == [FP]


def test_an_outage_alone_never_reports_recovery(tmp_path):
    """The second slice case: the alert listing cannot be read for hours; the problem stays open, never recovered."""
    jobs, state = tmp_path / "jobs", tmp_path / "ops-problems.json"
    now = {"t": T0}
    notes = Notices()
    tracker = problems.ProblemTracker(state, notes, lambda: now["t"])
    write_slice(jobs, T0 - 10 * M, last_progress=T0 - 55 * M, run="r1")
    tracker.observe(evaluate(jobs, T0, "e1"))
    for i in range(1, 25):
        now["t"] = T0 + i * 10 * M
        tracker.observe(evaluate(jobs, now["t"], f"x{i}", complete=False))
    p = tracker.problems()[FP]
    assert p["status"] == "open" and p["unknown_reason"] == "incomplete"
    assert "close" not in notes.events() and notes.events().count("cannot_see") == 1


def test_an_old_closed_problem_is_not_reopened_after_the_hour(tmp_path):
    jobs, state = tmp_path / "jobs", tmp_path / "ops-problems.json"
    now = {"t": T0}
    notes = Notices()
    tracker = problems.ProblemTracker(state, notes, lambda: now["t"])
    fire = problems.Firing("red", "vault", "no_progress", "red")
    tracker.observe(problems.Evaluation("a", now["t"], "v1", True, {FP: fire}, {}))
    for i, eid in ((1, "b"), (2, "c")):
        now["t"] = T0 + i * 5 * M
        tracker.observe(problems.Evaluation(eid, now["t"], "v1", True, {}, {FP: now["t"] - M}))
    assert tracker.problems()[FP]["status"] == "recovered"
    first_id = tracker.problems()[FP]["id"]
    now["t"] = T0 + 3 * 60 * M
    tracker.observe(problems.Evaluation("d", now["t"], "v1", True, {FP: fire}, {}))
    assert tracker.problems()[FP]["id"] != first_id and notes.events().count("open") == 2


# ---- Codex lifecycle code review (OPS_LIFECYCLE_CODE_REVIEW_v0.1, on e5174bd9) ----------------------------------------

def _open_one(tmp_path, notes=None):
    now = {"t": T0}
    notes = notes or Notices()
    tracker = problems.ProblemTracker(tmp_path / "s.json", notes, lambda: now["t"])
    fire = problems.Firing("red", "vault", "no_progress", "red")
    tracker.observe(problems.Evaluation("a", now["t"], "v1", True, {FP: fire}, {}))
    return tracker, now, notes


def test_a_future_dated_healthy_signal_never_closes(tmp_path):
    tracker, now, notes = _open_one(tmp_path)
    future = T0 + timedelta(days=365)
    for i, eid in ((1, "b"), (2, "c"), (3, "d")):
        now["t"] = T0 + i * 5 * M
        tracker.observe(problems.Evaluation(eid, now["t"], "v1", True, {}, {FP: future}))
    assert tracker.problems()[FP]["status"] == "open" and "close" not in notes.events()


def test_an_outage_between_clear_samples_resets_the_count(tmp_path):
    tracker, now, notes = _open_one(tmp_path)
    now["t"] = T0 + 5 * M
    tracker.observe(problems.Evaluation("b", now["t"], "v1", True, {}, {FP: now["t"] - M}))   # clear sample 1
    for i in range(2, 14):                                                                      # an hour blind
        now["t"] = T0 + i * 5 * M
        tracker.observe(problems.Evaluation(f"x{i}", now["t"], "v1", False, {}, {}))
    now["t"] = T0 + 75 * M
    tracker.observe(problems.Evaluation("c", now["t"], "v1", True, {}, {FP: now["t"] - M}))   # one clear after it
    assert tracker.problems()[FP]["status"] == "open"
    now["t"] = T0 + 80 * M
    tracker.observe(problems.Evaluation("d", now["t"], "v1", True, {}, {FP: now["t"] - M}))
    assert tracker.problems()[FP]["status"] == "recovered"


def test_notices_are_delivered_in_order_after_a_failed_open(tmp_path):
    notes = Notices()
    notes.fail = True
    tracker, now, _ = _open_one(tmp_path, notes)                       # the open notice fails
    notes.fail = False
    calls = []
    real = tracker.notices
    def flaky(n):                                                      # the next attempt: open fails again, close would pass
        calls.append(n["event"])
        return False if n["event"] == "open" and calls.count("open") == 1 else real(n)
    tracker.notices = flaky
    for i, eid in ((1, "b"), (2, "c")):
        now["t"] = T0 + i * 5 * M
        tracker.observe(problems.Evaluation(eid, now["t"], "v1", True, {}, {FP: now["t"] - M}))
    now["t"] = T0 + 15 * M
    tracker.observe(problems.Evaluation("d", now["t"], "v1", True, {}, {FP: now["t"] - M}))
    delivered = [n["event"] for n in notes.by_key.values()]
    assert delivered == ["open", "close"]                              # never close before open


def test_a_corrupt_state_file_is_refused_not_reset(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    try:
        problems.ProblemTracker(path, Notices(), lambda: T0)
    except problems.ProblemStateError:
        pass
    else:
        raise AssertionError("a corrupt state file must be refused")
    assert path.read_text() == "{not json"
    path.write_text('{"schema_version": 1, "problems": []}')
    try:
        problems.ProblemTracker(path, Notices(), lambda: T0)
    except problems.ProblemStateError:
        pass
    else:
        raise AssertionError("an invalid state document must be refused")
