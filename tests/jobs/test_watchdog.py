"""The watchdog: alerts once per condition, one 'recovered', this host only, never content.
Synthetic status files in a temp folder and a fake sender; the clock is injected."""
from __future__ import annotations

import importlib.util
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.jobs import registry, status

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("watchdog_under_test", REPO / "scripts" / "jobs" / "watchdog.py")
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

JOB = registry.load().get("memory-watch")
NOW = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)


class Sender:
    def __init__(self, result="sent"):
        self.sent, self.result = [], result

    def __call__(self, text):
        self.sent.append(text)
        return self.result


def put(root, *, state="ok", started=NOW - 5 * H, success="auto", run="run-1", alert=None, job=JOB, results=None):
    root.mkdir(parents=True, exist_ok=True)
    fin = None if state == "running" else started + timedelta(minutes=1)
    suc = (fin if state in ("ok", "warn") else None) if success == "auto" else success
    doc = status.build(job_id=job.id, host=job.host, run_id=run, state=state, started_at=started, finished_at=fin,
                       exit_code=None if state == "running" else (1 if state == "failed" else 0), summary="x",
                       results=results or {}, last_success_at=suc, alert=alert)
    status.write_status(root, job.status_file, doc)


def go(root, sender, now=NOW, **kw):
    out, err = io.StringIO(), io.StringIO()
    code = wd.run(jobs_root=root, host=kw.pop("host", "mac"), sender=sender, now=lambda: now, out=out, err=err, **kw)
    return code, out.getvalue()


def own_status(root):
    return status.read_status(root, "jobs-watchdog.json", "jobs-watchdog")


def test_a_healthy_job_sends_nothing_and_the_watchdog_records_its_own_run(tmp_path):
    put(tmp_path)
    s = Sender()
    code, _ = go(tmp_path, s)
    assert code == 0 and s.sent == []
    own = own_status(tmp_path)
    assert own["state"] == "ok" and own["results"] == {"memory-watch": "ok"}
    assert own["next_due_by"] == "2026-10-07T14:00:00Z"


def test_missing_status_file_alerts_once_a_run_was_due_and_not_before(tmp_path):
    s = Sender()
    go(tmp_path, s, now=JOB.active_from + timedelta(hours=3))
    assert s.sent == []                                                  # registered, not yet due
    go(tmp_path, s, now=JOB.active_from + timedelta(hours=30))
    assert len(s.sent) == 1 and "memory-watch" in s.sent[0] and "never ran" in s.sent[0]
    go(tmp_path, s, now=JOB.active_from + timedelta(hours=54))
    assert len(s.sent) == 1                                              # same condition: not repeated


def test_a_corrupt_file_is_treated_like_a_missing_one(tmp_path):
    (tmp_path).mkdir(exist_ok=True)
    (tmp_path / JOB.status_file).write_text("{broken")
    s = Sender()
    go(tmp_path, s, now=JOB.active_from + timedelta(hours=30))
    assert len(s.sent) == 1 and "never ran" in s.sent[0]


def test_no_success_in_missed_after_s_alerts_once(tmp_path):
    put(tmp_path, started=NOW - 60 * H)
    s = Sender()
    go(tmp_path, s)
    go(tmp_path, s, now=NOW + 12 * H)
    assert len(s.sent) == 1 and "no completed run in time" in s.sent[0] and "59 h" in s.sent[0]


def test_running_longer_than_stuck_after_alerts_once(tmp_path):
    put(tmp_path, state="running", started=NOW - 3 * H, success=NOW - 20 * H)
    s = Sender()
    go(tmp_path, s)
    go(tmp_path, s)
    assert len(s.sent) == 1 and "stuck" in s.sent[0]


def test_running_within_stuck_after_is_not_an_alert(tmp_path):
    put(tmp_path, state="running", started=NOW - H, success=NOW - 20 * H)
    s = Sender()
    go(tmp_path, s)
    assert s.sent == []


def test_a_failed_run_alerts_once_per_run_and_a_new_failed_run_alerts_again(tmp_path):
    put(tmp_path, state="failed", run="run-1", success=NOW - 20 * H)
    s = Sender()
    go(tmp_path, s)
    go(tmp_path, s)
    assert len(s.sent) == 1 and "last run failed" in s.sent[0]
    put(tmp_path, state="failed", run="run-2", success=NOW - 20 * H)
    go(tmp_path, s)
    assert len(s.sent) == 2


def test_a_failure_the_wrapper_already_sent_is_not_sent_twice_but_still_recovers(tmp_path):
    put(tmp_path, state="failed", alert="sent", success=NOW - 20 * H)
    s = Sender()
    go(tmp_path, s)
    assert s.sent == []
    put(tmp_path, state="ok", run="run-2")
    go(tmp_path, s)
    assert len(s.sent) == 1 and "recovered" in s.sent[0]


def test_a_failure_the_wrapper_could_not_send_is_sent_by_the_watchdog(tmp_path):
    put(tmp_path, state="failed", alert="not_sent", success=NOW - 20 * H)
    s = Sender()
    go(tmp_path, s)
    assert len(s.sent) == 1


def test_one_recovered_message_when_the_condition_clears_and_none_after(tmp_path):
    put(tmp_path, state="failed", success=NOW - 20 * H)
    s = Sender()
    go(tmp_path, s)
    put(tmp_path, state="ok", run="run-2")
    go(tmp_path, s)
    go(tmp_path, s)
    assert len(s.sent) == 2 and "recovered" in s.sent[1]
    put(tmp_path, state="failed", run="run-3", success=NOW - H)
    go(tmp_path, s)
    assert len(s.sent) == 3                                              # a fresh failure after recovery alerts again


def test_a_warn_after_a_failure_also_counts_as_recovered_from_the_red(tmp_path):
    put(tmp_path, state="failed", success=NOW - 20 * H)
    s = Sender()
    go(tmp_path, s)
    put(tmp_path, state="warn", run="run-2", results={"codex": "not_approved"})
    go(tmp_path, s)
    assert "recovered" in s.sent[-1]


def test_yellow_alone_never_alerts(tmp_path):
    put(tmp_path, state="warn", results={"codex": "not_approved"})
    put(tmp_path, state="ok", started=NOW - 27 * H)                      # late, not missed
    s = Sender()
    go(tmp_path, s)
    assert s.sent == []


def test_a_message_that_cannot_be_sent_is_retried_next_time_and_the_exit_says_so(tmp_path):
    put(tmp_path, state="failed", success=NOW - 20 * H)
    down = Sender("not_sent")
    code, _ = go(tmp_path, down)
    assert code == 1 and own_status(tmp_path)["state"] == "warn"
    up = Sender()
    code, _ = go(tmp_path, up)
    assert code == 0 and len(up.sent) == 1                                # the failed send was not remembered


def test_a_sender_that_raises_counts_as_not_sent(tmp_path):
    put(tmp_path, state="failed", success=NOW - 20 * H)

    def boom(text):
        raise RuntimeError("telegram down")
    code, _ = go(tmp_path, boom)
    assert code == 1


def test_jobs_on_other_hosts_are_never_judged_or_alerted(tmp_path):
    s = Sender()
    go(tmp_path, s, now=JOB.active_from + timedelta(hours=40), host="ec2")      # nothing registered for ec2
    assert s.sent == []
    go(tmp_path, s, now=JOB.active_from + timedelta(hours=40), host="mac")
    assert len(s.sent) == 1


def test_the_watchdog_never_judges_itself(tmp_path):
    s = Sender()
    go(tmp_path, s, now=JOB.active_from + timedelta(hours=40))
    own = registry.load().get("jobs-watchdog")
    assert own.id not in json.loads((tmp_path / wd.STATE_FILE).read_text())["jobs"]
    assert "jobs-watchdog" not in " ".join(s.sent)


def test_an_unreadable_registry_alerts_once_and_the_run_is_failed(tmp_path):
    s = Sender()
    code, _ = go(tmp_path, s, registry_path=tmp_path / "nope.json")
    assert code == 1 and len(s.sent) == 1 and "registry" in s.sent[0]
    go(tmp_path, s, registry_path=tmp_path / "nope.json")
    assert len(s.sent) == 1
    put(tmp_path)
    go(tmp_path, s)
    assert "readable again" in s.sent[-1]


def test_messages_hold_no_content_and_no_paths(tmp_path):
    put(tmp_path, state="failed", success=NOW - 20 * H, results={"codex": "exit_1"})
    s = Sender()
    go(tmp_path, s)
    for text in s.sent:
        assert "/" not in text and "TOPSECRET" not in text
        assert text == status_text_clean(text)


def status_text_clean(text):
    from core.jobs import alert
    return alert.clean(text)


def test_a_corrupt_state_file_is_treated_as_empty(tmp_path):
    put(tmp_path, state="failed", success=NOW - 20 * H)
    (tmp_path / wd.STATE_FILE).write_text("{nope")
    s = Sender()
    go(tmp_path, s)
    assert len(s.sent) == 1


def test_a_job_removed_from_the_registry_is_dropped_from_the_state(tmp_path):
    (tmp_path).mkdir(exist_ok=True)
    (tmp_path / wd.STATE_FILE).write_text(json.dumps({"schema_version": 1, "jobs": {"gone": {"condition": "failed", "key": "x"}}}))
    put(tmp_path)
    go(tmp_path, Sender())
    assert "gone" not in json.loads((tmp_path / wd.STATE_FILE).read_text())["jobs"]


def test_if_its_own_status_cannot_be_written_it_exits_2(tmp_path):
    blocker = tmp_path / "jobs"
    blocker.write_text("not a folder")
    s = Sender()
    code, _ = go(blocker, s)
    assert code == 2 and len(s.sent) == 1


def test_main_uses_the_environment(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(wd, "run", lambda **kw: seen.update(kw) or 0)
    monkeypatch.setenv("MINIMOI_JOBS_ROOT", str(tmp_path))
    monkeypatch.setenv("MINIMOI_JOBS_HOST", "ec2")
    assert wd.main() == 0 and seen["jobs_root"] == tmp_path and seen["host"] == "ec2"
    monkeypatch.delenv("MINIMOI_JOBS_HOST")
    wd.main()
    assert seen["host"] == "mac"
