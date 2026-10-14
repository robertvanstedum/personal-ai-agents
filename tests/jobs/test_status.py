"""The status contract: atomic 0600 writes, no content ever, tolerant readers, carried-forward success."""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from core.jobs import registry, schedule, status

NOW = datetime(2026, 10, 4, 9, 0, 5, tzinfo=timezone.utc)
JOB = registry.load().get("memory-watch")


def _read(root):
    return status.read_status(root, JOB.status_file, JOB.id)


def test_start_then_finish_round_trip(tmp_path):
    root = tmp_path / "jobs"
    running = status.start_run(root, JOB, "run-1", NOW)
    doc = _read(root)
    assert doc == running and doc["state"] == "running" and doc["finished_at"] is None
    assert doc["last_success_at"] is None and doc["schema_version"] == 1 and doc["host"] == "mac"
    later = NOW + timedelta(seconds=30)
    final = status.finish_run(root, JOB, running, state="ok", now=later, exit_code=0, summary="2 sources ok",
                              results={"claude-code": "ok", "codex": "ok"}, next_due_by=later + timedelta(days=1),
                              alert="not_needed")
    doc = _read(root)
    assert doc == final and doc["state"] == "ok" and doc["exit_code"] == 0
    assert doc["last_success_at"] == status.iso(later) and doc["run_id"] == "run-1"
    assert doc["results"] == {"claude-code": "ok", "codex": "ok"}


def test_files_are_0600_and_the_folder_0700_with_no_temp_left(tmp_path):
    root = tmp_path / "a" / "jobs"
    status.start_run(root, JOB, "r", NOW)
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(root / JOB.status_file).st_mode) == 0o600
    assert [p.name for p in root.iterdir()] == [JOB.status_file]


def test_a_failed_write_leaves_the_old_file_whole(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    running = status.start_run(root, JOB, "r", NOW)
    before = (root / JOB.status_file).read_bytes()

    def boom(*_a, **_k):
        raise OSError("rename failed")
    monkeypatch.setattr(status.os, "replace", boom)
    with pytest.raises(OSError):
        status.finish_run(root, JOB, running, state="ok", now=NOW, exit_code=0, summary="x", results={},
                          next_due_by=None)
    assert (root / JOB.status_file).read_bytes() == before


def test_last_success_is_carried_forward_and_only_completed_runs_move_it(tmp_path):
    root = tmp_path / "jobs"
    r1 = status.start_run(root, JOB, "r1", NOW)
    status.finish_run(root, JOB, r1, state="warn", now=NOW, exit_code=0, summary="warn", results={"codex": "not_approved"},
                      next_due_by=None)
    day2 = NOW + timedelta(days=1)
    r2 = status.start_run(root, JOB, "r2", day2)
    assert _read(root)["last_success_at"] == status.iso(NOW)          # running does not erase it
    status.finish_run(root, JOB, r2, state="failed", now=day2, exit_code=1, summary="failed", results={"codex": "exit_1"},
                      next_due_by=None)
    assert _read(root)["last_success_at"] == status.iso(NOW)          # failed does not move it
    r3 = status.start_run(root, JOB, "r3", day2 + timedelta(days=1))
    status.finish_run(root, JOB, r3, state="ok", now=day2 + timedelta(days=1), exit_code=0, summary="ok", results={},
                      next_due_by=None)
    assert _read(root)["last_success_at"] == status.iso(day2 + timedelta(days=1))


@pytest.mark.parametrize("summary", [
    "/Users/robert/.claude/projects/secret.jsonl", "read ~/.codex/x", "line one\nline two", "a" * 121,
    "<script>", "title: \"my private chat\"", "quote ' and \"",
])
def test_a_summary_that_could_hold_content_is_refused_and_nothing_is_written(tmp_path, summary):
    root = tmp_path / "jobs"
    running = status.start_run(root, JOB, "r", NOW)
    before = (root / JOB.status_file).read_bytes()
    with pytest.raises(status.StatusError):
        status.finish_run(root, JOB, running, state="ok", now=NOW, exit_code=0, summary=summary, results={},
                          next_due_by=None)
    assert (root / JOB.status_file).read_bytes() == before


@pytest.mark.parametrize("results", [
    {"claude-code": "/Users/x/file"}, {"claude-code": "has space"}, {"claude code": "ok"}, {"../x": "ok"},
    {"codex": "x" * 41}, {"codex": 5}, {f"p{i}": "ok" for i in range(21)}, [("codex", "ok")],
])
def test_results_hold_only_part_names_and_fixed_codes(results):
    with pytest.raises(status.StatusError):
        status.build(job_id="memory-watch", host="mac", run_id="r", state="ok", started_at=NOW, finished_at=NOW,
                     exit_code=0, results=results)


@pytest.mark.parametrize("field,value", [("state", "great"), ("job_id", "Bad Id"), ("run_id", "has space"),
                                         ("alert", "maybe"), ("exit_code", "0")])
def test_other_fields_are_checked(field, value):
    args = dict(job_id="memory-watch", host="mac", run_id="r", state="ok", started_at=NOW, finished_at=NOW, exit_code=0)
    args[field] = value
    with pytest.raises(status.StatusError):
        status.build(**args)


def test_a_running_file_has_no_finish_and_a_finished_one_has(tmp_path):
    with pytest.raises(status.StatusError):
        status.build(job_id="j", host="mac", run_id="r", state="running", started_at=NOW, finished_at=NOW)
    with pytest.raises(status.StatusError):
        status.build(job_id="j", host="mac", run_id="r", state="ok", started_at=NOW)


def test_the_written_file_holds_no_text_beyond_the_contract_fields(tmp_path):
    root = tmp_path / "jobs"
    running = status.start_run(root, JOB, "r", NOW)
    status.finish_run(root, JOB, running, state="warn", now=NOW, exit_code=0, summary="1 of 2 sources ok",
                      results={"claude-code": "ok", "codex": "not_approved"}, next_due_by=NOW, alert="not_needed")
    doc = json.loads((root / JOB.status_file).read_text())
    assert set(doc) == {"schema_version", "job_id", "host", "run_id", "state", "started_at", "finished_at",
                        "exit_code", "summary", "results", "next_due_by", "last_success_at", "alert"}
    assert "/" not in json.dumps({k: v for k, v in doc.items() if k not in ("started_at", "finished_at")})


@pytest.mark.parametrize("content", [None, "", "{not json", "[]", "null", '{"schema_version": 2}',
                                     '{"schema_version": 1, "job_id": "memory-watch"}',
                                     json.dumps({"schema_version": 1, "job_id": "memory-watch", "host": "mac",
                                                 "run_id": "r", "state": "green", "started_at": "2026-10-04T09:00:00Z"}),
                                     json.dumps({"schema_version": 1, "job_id": "other", "host": "mac", "run_id": "r",
                                                 "state": "ok", "started_at": "2026-10-04T09:00:00Z",
                                                 "finished_at": "2026-10-04T09:00:01Z", "results": {}})])
def test_readers_treat_missing_corrupt_or_foreign_files_as_unknown(tmp_path, content):
    if content is not None:
        (tmp_path / JOB.status_file).write_text(content)
    assert status.read_status(tmp_path, JOB.status_file, JOB.id) is None


def test_an_unreadable_file_is_unknown(tmp_path):
    path = tmp_path / JOB.status_file
    path.write_text("{}")
    path.chmod(0)
    try:
        assert status.read_status(tmp_path, JOB.status_file, JOB.id) is None
    finally:
        path.chmod(0o600)


def test_a_directory_in_place_of_the_file_is_unknown(tmp_path):
    (tmp_path / JOB.status_file).mkdir()
    assert status.read_status(tmp_path, JOB.status_file, JOB.id) is None


# ── next due ────────────────────────────────────────────────────────────────

DAILY = {"kind": "daily", "at": "04:00", "tz": "America/Chicago", "text": "x"}


@pytest.mark.parametrize("after,expected", [
    ("2026-10-04T09:00:05Z", "2026-10-05T09:00:00Z"),      # 04:00 CDT is 09:00Z; today's has passed
    ("2026-10-04T08:59:59Z", "2026-10-04T09:00:00Z"),
    ("2026-10-04T09:00:00Z", "2026-10-05T09:00:00Z"),      # strictly after
    ("2026-10-31T20:00:00Z", "2026-11-01T10:00:00Z"),      # DST ends at 02:00 on Nov 1, so 04:00 is already CST
    ("2026-11-01T12:00:00Z", "2026-11-02T10:00:00Z"),      # CST afterwards: 04:00 is 10:00Z
])
def test_daily_next_fire_follows_the_zone(after, expected):
    got = schedule.next_fire(DAILY, status.parse_time(after))
    assert status.iso(got) == expected


def test_interval_and_unknown_kinds():
    assert schedule.next_fire({"kind": "interval", "every_s": 3600}, NOW) == NOW + timedelta(hours=1)
    with pytest.raises(ValueError):
        schedule.next_fire({"kind": "weekly"}, NOW)
