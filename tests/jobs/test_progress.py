"""WS3 C1.2 (28 Nov 2026): progress in the registry, the status contract and the judge.

A job with ``progress_required`` that completes runs but makes no progress while work is due is not healthy (the
meaning seed's "budget_spent with 0 chunks" read ok for two nights). The rules keep failure precedence, need context
(work due, phase, the job's window) and never turn an unreadable observation green.
"""
from __future__ import annotations

import copy
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.jobs import judge, registry, status

REPO = Path(__file__).resolve().parents[2]
H = timedelta(hours=1)
CHI = "America/Chicago"

ROW = {
    "id": "seed", "name": "Seed", "host": "ec2",
    "schedule": {"text": "every 15 min at night", "kind": "interval", "every_s": 900},
    "expected_every_s": 86400, "missed_after_s": 172800, "stuck_after_s": 7200,
    "status_file": "seed.json", "owner": "robert", "runbook": "x",
    "progress_required": True, "idle_allowed_s": 3600,
    "progress_window": {"start": "00:00", "end": "06:00", "tz": CHI},
}


def job(**changes):
    row = copy.deepcopy(ROW)
    row.update(changes)
    for k in [k for k, v in row.items() if v is None]:
        del row[k]
    return registry.parse({"schema_version": 1, "jobs": [row]}).jobs[0]


def chicago(y, m, d, hh, mm=0):
    from zoneinfo import ZoneInfo
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(CHI)).astimezone(timezone.utc)


def doc(j, *, state="ok", finished, progress, run_id="r1"):
    return status.build(job_id=j.id, host=j.host, run_id=run_id, state=state, started_at=finished - timedelta(minutes=5),
                        finished_at=finished, exit_code=0, summary="x",
                        last_success_at=finished if state in ("ok", "warn") else finished - 3 * H, progress=progress)


def active(due=1, last=None):
    return {"work_due": due, "last_progress_at": status.iso(last) if last else None, "phase": "active"}


# ---- registry ------------------------------------------------------------------------------------------------------

def test_registry_parses_the_progress_fields():
    j = job()
    assert j.progress_required and j.idle_allowed_s == 3600 and j.progress_window == {"start": "00:00", "end": "06:00", "tz": CHI}
    plain = job(progress_required=None, idle_allowed_s=None, progress_window=None)
    assert not plain.progress_required and plain.idle_allowed_s == 0 and plain.progress_window is None


@pytest.mark.parametrize("changes", [
    {"idle_allowed_s": None},                                         # required with progress_required
    {"idle_allowed_s": 30},                                           # at least a minute
    {"progress_required": "yes"},
    {"progress_required": None, "idle_allowed_s": None},              # a window without progress_required
    {"progress_window": {"start": "00:00", "end": "00:00", "tz": CHI}},
    {"progress_window": {"start": "0:00", "end": "06:00", "tz": CHI}},
    {"progress_window": {"start": "00:00", "end": "06:00"}},
    {"status_file": None, "evidence": {"kind": "none", "why": "x"}},  # progress needs a status file
])
def test_registry_refuses_bad_progress_fields(changes):
    with pytest.raises(registry.RegistryError):
        job(**changes)


def test_the_shipped_seed_requires_progress_in_its_night_window():
    seed = registry.load().get("vault-meaning-seed")
    assert seed.progress_required and seed.idle_allowed_s == 3600
    assert seed.progress_window == {"start": "00:00", "end": "06:00", "tz": CHI}


# ---- status contract -----------------------------------------------------------------------------------------------

def test_progress_and_refusal_round_trip(tmp_path):
    j = job()
    now = chicago(2026, 11, 28, 1)
    d = doc(j, finished=now, progress=active(1, now))
    status.write_status(tmp_path, j.status_file, d)
    back = status.read_status(tmp_path, j.status_file, j.id)
    assert back["progress"] == {"work_due": 1, "last_progress_at": status.iso(now), "phase": "active"}
    noted = status.record_refusal(tmp_path, j, "apply_busy", now + H)
    again = status.read_status(tmp_path, j.status_file, j.id)
    assert noted and again["last_refusal_reason"] == "apply_busy" and again["last_refusal_at"] == status.iso(now + H)
    assert again["last_success_at"] == back["last_success_at"] and again["state"] == "ok"


@pytest.mark.parametrize("bad", [
    {"work_due": -1, "last_progress_at": None, "phase": "active"},
    {"work_due": True, "last_progress_at": None, "phase": "active"},
    {"work_due": 1, "last_progress_at": None, "phase": "busy"},
    {"work_due": 1, "last_progress_at": "yesterday", "phase": "active"},
    {"work_due": 1, "last_progress_at": None, "phase": "active", "extra": "x"},
])
def test_bad_progress_is_refused(bad):
    j = job()
    with pytest.raises(status.StatusError):
        doc(j, finished=chicago(2026, 11, 28, 1), progress=bad)


def test_a_document_with_bad_progress_reads_as_unknown(tmp_path):
    j = job()
    d = doc(j, finished=chicago(2026, 11, 28, 1), progress=active())
    d["progress"]["phase"] = "busy"
    status.write_json_atomic(tmp_path, j.status_file, d)
    assert status.read_status(tmp_path, j.status_file, j.id) is None


def test_runs_carry_progress_and_the_last_refusal(tmp_path):
    j = job()
    t0 = chicago(2026, 11, 28, 1)
    running = status.start_run(tmp_path, j, "r1", t0)
    status.finish_run(tmp_path, j, running, state="ok", now=t0 + H, exit_code=0, summary="x", results={},
                      next_due_by=None, progress=active(1, t0 + H))
    status.record_refusal(tmp_path, j, "apply_busy", t0 + 2 * H)
    running = status.start_run(tmp_path, j, "r2", t0 + 3 * H)
    assert running["progress"]["last_progress_at"] == status.iso(t0 + H) and running["last_refusal_reason"] == "apply_busy"
    failed = status.finish_run(tmp_path, j, running, state="failed", now=t0 + 4 * H, exit_code=1, summary="x", results={},
                               next_due_by=None)
    assert failed["progress"]["last_progress_at"] == status.iso(t0 + H)          # a failed run does not erase it


def test_a_refusal_with_no_document_writes_nothing(tmp_path):
    assert status.record_refusal(tmp_path, job(), "apply_busy", chicago(2026, 11, 28, 1)) is None
    assert not (tmp_path / "seed.json").exists()


# ---- judge ---------------------------------------------------------------------------------------------------------

def verdict(j, d, now):
    return judge.judge(j, d, now)


def test_a_backlog_that_stalls_goes_yellow_then_red_inside_the_window():
    j = job()
    last = chicago(2026, 11, 28, 0, 10)
    d = doc(j, finished=chicago(2026, 11, 28, 0, 45), progress=active(1, last))
    assert verdict(j, d, chicago(2026, 11, 28, 1, 0)).code == "ok"
    v = verdict(j, d, chicago(2026, 11, 28, 1, 20))
    assert (v.state, v.code) == ("yellow", "no_progress")
    v = verdict(j, d, chicago(2026, 11, 28, 2, 20))
    assert (v.state, v.code) == ("red", "no_progress")


def test_idle_is_counted_from_the_window_start_not_last_night():
    j = job()
    d = doc(j, finished=chicago(2026, 11, 27, 5, 50), progress=active(1, chicago(2026, 11, 27, 5, 0)))
    assert verdict(j, d, chicago(2026, 11, 28, 0, 30)).code == "ok"          # 30 min into tonight's window
    assert verdict(j, d, chicago(2026, 11, 28, 1, 30)).code == "no_progress"


def test_outside_the_window_no_progress_is_expected():
    j = job()
    d = doc(j, finished=chicago(2026, 11, 28, 5, 50), progress=active(1, chicago(2026, 11, 28, 1, 0)))
    assert verdict(j, d, chicago(2026, 11, 28, 14, 0)).code == "ok"


@pytest.mark.parametrize("phase", ["idle_no_work", "complete", "outside_window"])
def test_phases_that_need_no_progress(phase):
    j = job()
    d = doc(j, finished=chicago(2026, 11, 28, 2), progress={"work_due": 0, "last_progress_at": None, "phase": phase})
    assert verdict(j, d, chicago(2026, 11, 28, 4)).code == "ok"


def test_zero_work_due_is_not_a_stall():
    j = job()
    d = doc(j, finished=chicago(2026, 11, 28, 2), progress=active(0, chicago(2026, 11, 27, 1)))
    assert verdict(j, d, chicago(2026, 11, 28, 4)).code == "ok"


def test_a_failed_run_stays_failed_whatever_the_phase():
    j = job()
    d = doc(j, state="failed", finished=chicago(2026, 11, 28, 2),
            progress={"work_due": 0, "last_progress_at": None, "phase": "complete"})
    assert verdict(j, d, chicago(2026, 11, 28, 3)).code == "failed"


def test_missed_beats_progress():
    j = job()
    d = doc(j, finished=chicago(2026, 11, 25, 2), progress={"work_due": 0, "last_progress_at": None, "phase": "complete"})
    assert verdict(j, d, chicago(2026, 11, 28, 3)).code == "missed"


@pytest.mark.parametrize("progress", [
    None,                                                             # no block from a progress-required job
    {"work_due": 1, "last_progress_at": None, "phase": "active"},     # work due, never progressed
])
def test_unreadable_progress_is_unknown_never_green(progress):
    j = job()
    d = doc(j, finished=chicago(2026, 11, 28, 2), progress=progress)
    v = verdict(j, d, chicago(2026, 11, 28, 3))
    assert (v.state, v.code) == ("unknown", "progress_unknown")


def test_a_future_last_progress_is_unknown():
    j = job()
    now = chicago(2026, 11, 28, 3)
    d = doc(j, finished=now - H, progress=active(1, now + 2 * H))
    d["progress"]["last_progress_at"] = status.iso(now + 2 * H)
    assert verdict(j, d, now).code in ("skew", "progress_unknown")
    assert verdict(j, d, now).state == "unknown"


def test_a_job_without_progress_required_is_unchanged():
    j = job(progress_required=None, idle_allowed_s=None, progress_window=None)
    d = doc(j, finished=chicago(2026, 11, 28, 2), progress=None)
    assert verdict(j, d, chicago(2026, 11, 28, 3)).code == "ok"


@pytest.mark.parametrize("day", [(2026, 11, 1), (2026, 3, 8)])           # daylight saving ends / starts that night
def test_the_window_follows_daylight_saving(day):
    j = job()
    start = judge.window_start(j.progress_window, chicago(*day, 3))
    assert start is not None and start.hour == 0 and start.minute == 0
    assert judge.window_start(j.progress_window, chicago(*day, 7)) is None
    last = chicago(*day, 0, 5)
    d = doc(j, finished=chicago(*day, 0, 10), progress=active(1, last))
    real_idle = (chicago(*day, 4) - last).total_seconds()
    v = verdict(j, d, chicago(*day, 4))
    assert v.code == "no_progress" and v.state == ("red" if real_idle > 7200 else "yellow")


def test_an_overnight_window_starts_the_evening_before():
    j = job(progress_window={"start": "22:00", "end": "02:00", "tz": CHI})
    assert judge.window_start(j.progress_window, chicago(2026, 11, 28, 1)) == chicago(2026, 11, 27, 22)
    assert judge.window_start(j.progress_window, chicago(2026, 11, 28, 12)) is None


# ---- the seed's own progress block ---------------------------------------------------------------------------------

def _vault_apply():
    spec = importlib.util.spec_from_file_location("vault_apply_p", REPO / "scripts/vault/vault_apply.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_seed_progress_from_slice_results():
    va = _vault_apply()
    now = chicago(2026, 11, 28, 1)
    before = {"work_due": 1, "last_progress_at": "2026-11-28T06:00:00Z", "phase": "active"}
    moved = va.seed_progress({"code": "budget_spent", "embedded_chunks": 40}, before, now)
    assert moved == {"work_due": 1, "last_progress_at": status.iso(now), "phase": "active"}
    stuck = va.seed_progress({"code": "budget_spent", "embedded_chunks": 0}, before, now)
    assert stuck["last_progress_at"] == "2026-11-28T06:00:00Z" and stuck["phase"] == "active"
    assert va.seed_progress({"code": "ok", "embedded_chunks": 3}, before, now)["phase"] == "complete"
    assert va.seed_progress({"code": "seed_not_needed"}, before, now) == {
        "work_due": 0, "last_progress_at": "2026-11-28T06:00:00Z", "phase": "complete"}
    assert va.seed_progress({"code": "outside_window"}, None, now)["phase"] == "outside_window"
