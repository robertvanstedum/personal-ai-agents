from datetime import datetime, timezone, timedelta
import json
from core.jobs.status import build, write_json_atomic
from minimoi_portal.guild_ui.scheduled_jobs import overview

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)

def setup(tmp_path):
    root = tmp_path / "jobs"; root.mkdir()
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "jobs": [{
        "id": "test", "name": "Test job", "host": "mac", "owner": "owner", "runbook": "OPERATIONS.md",
        "schedule": {"text": "daily", "kind": "interval", "every_s": 100},
        "expected_every_s": 100, "grace_s": 10, "missed_after_s": 200, "stuck_after_s": 50,
        "active_from": (NOW-timedelta(seconds=400)).isoformat(), "status_file": "test.json"}]}))
    return root, registry

def test_missing_due_is_red_but_corrupt_is_unknown(tmp_path):
    root, registry = setup(tmp_path)
    assert overview(str(root), registry_path=registry, now=NOW)["light"]["state"] == "red"
    (root / "test.json").write_text("bad json")
    assert overview(str(root), registry_path=registry, now=NOW)["light"]["state"] == "unknown"

def test_missing_mount_never_means_never_ran(tmp_path):
    root, registry = setup(tmp_path)
    assert overview(str(root / "absent"), registry_path=registry, now=NOW)["light"]["state"] == "unknown"

def test_fresh_warn_failed_late_missed_and_wrong_host(tmp_path):
    root, registry = setup(tmp_path)
    for state, age, host, expected in [("ok", 1, "mac", "green"), ("warn", 1, "mac", "yellow"),
        ("failed", 1, "mac", "red"), ("ok", 120, "mac", "yellow"), ("ok", 220, "mac", "red"),
        ("ok", 1, "ec2", "unknown")]:
        finish=NOW-timedelta(seconds=age)
        doc=build(job_id="test",host=host,run_id="test-run",state=state,started_at=finish-timedelta(seconds=1),
                  finished_at=finish,last_success_at=finish,exit_code=0,results={"source":"ok"})
        write_json_atomic(root,"test.json",doc)
        assert overview(str(root),registry_path=registry,now=NOW)["light"]["state"] == expected


def test_null_results_safe_to_render(tmp_path):
    from jinja2 import Environment
    root, registry = setup(tmp_path)
    doc=build(job_id="test",host="mac",run_id="test-run",state="ok",started_at=NOW,
              finished_at=NOW,last_success_at=NOW,exit_code=0)
    doc["results"] = None
    (root/"test.json").write_text(json.dumps(doc))
    rows=overview(str(root),registry_path=registry,now=NOW)["jobs"]
    assert Environment().from_string("{% for part, code in job.results.items() %}{{ code }}{% endfor %}").render(job=rows[0]) == ""
