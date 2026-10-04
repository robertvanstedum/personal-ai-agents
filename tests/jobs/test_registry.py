"""The scheduled-jobs registry: the shipped file is valid and every malformed one is refused."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.jobs import registry

REPO = Path(__file__).resolve().parents[2]

GOOD = {
    "schema_version": 1,
    "jobs": [{
        "id": "memory-watch", "name": "Memory capture", "host": "mac",
        "schedule": {"text": "daily 04:00 Chicago", "kind": "daily", "at": "04:00", "tz": "America/Chicago"},
        "expected_every_s": 86400, "missed_after_s": 180000, "stuck_after_s": 7200,
        "status_file": "memory-watch.json", "owner": "robert", "runbook": "OPERATIONS.md#x",
    }],
}


def doc(**changes):
    out = copy.deepcopy(GOOD)
    out["jobs"][0].update(changes)
    return out


def test_the_shipped_registry_loads_and_names_memory_watch_as_briefed():
    reg = registry.load()
    job = reg.get("memory-watch")
    assert job is not None and job.host == "mac"
    assert job.schedule == {"text": "daily 04:00 Chicago", "kind": "daily", "at": "04:00", "tz": "America/Chicago"}
    assert (job.expected_every_s, job.missed_after_s, job.stuck_after_s) == (86400, 50 * 3600, 2 * 3600)
    assert job.status_file == "memory-watch.json" and job.active_from is not None
    ZoneInfo(job.schedule["tz"])          # the shipped zone exists in the zone database


def test_defaults_and_helpers():
    job = registry.parse(GOOD).jobs[0]
    assert job.grace_s == registry.DEFAULT_GRACE_S and job.overdue_after_s == 86400 + 7200
    assert job.active_from is None
    reg = registry.parse(GOOD)
    assert reg.for_host("mac") == reg.jobs and reg.for_host("ec2") == () and reg.get("nope") is None


def test_interval_schedule_is_accepted():
    job = registry.parse(doc(schedule={"text": "hourly", "kind": "interval", "every_s": 3600})).jobs[0]
    assert job.schedule["every_s"] == 3600


@pytest.mark.parametrize("changes", [
    {"id": "Memory Watch"}, {"id": ""}, {"host": "Mac!"}, {"host": ""},
    {"status_file": "../memory-watch.json"}, {"status_file": "/etc/x.json"}, {"status_file": "a/b.json"},
    {"status_file": "memory-watch.txt"},
    {"expected_every_s": 0}, {"expected_every_s": "86400"}, {"expected_every_s": True},
    {"missed_after_s": 100}, {"stuck_after_s": -1}, {"grace_s": -5},
    {"name": ""}, {"owner": " "}, {"runbook": None},
    {"schedule": "daily"}, {"schedule": {"text": "x", "kind": "weekly"}},
    {"schedule": {"text": "x", "kind": "daily", "at": "25:00", "tz": "America/Chicago"}},
    {"schedule": {"text": "x", "kind": "daily", "at": "04:00", "tz": "chicago"}},
    {"schedule": {"text": "x", "kind": "interval", "every_s": 0}},
    {"schedule": {"kind": "daily", "at": "04:00", "tz": "UTC"}},
    {"active_from": "yesterday"},
])
def test_malformed_jobs_are_refused(changes):
    with pytest.raises(registry.RegistryError):
        registry.parse(doc(**changes))


@pytest.mark.parametrize("bad", [None, [], {"schema_version": 2, "jobs": []}, {"schema_version": 1},
                                 {"schema_version": 1, "jobs": {}}, {"schema_version": 1, "jobs": ["x"]}])
def test_malformed_documents_are_refused(bad):
    with pytest.raises(registry.RegistryError):
        registry.parse(bad)


def test_duplicate_ids_and_status_files_are_refused():
    twin = copy.deepcopy(GOOD)
    twin["jobs"].append(copy.deepcopy(twin["jobs"][0]))
    with pytest.raises(registry.RegistryError, match="duplicate id"):
        registry.parse(twin)
    twin["jobs"][1]["id"] = "other"
    with pytest.raises(registry.RegistryError, match="status_file"):
        registry.parse(twin)


def test_load_reports_unreadable_and_invalid_files(tmp_path):
    with pytest.raises(registry.RegistryError):
        registry.load(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(registry.RegistryError):
        registry.load(bad)
    good = tmp_path / "good.json"
    good.write_text(json.dumps(GOOD))
    assert registry.load(good).get("memory-watch")
