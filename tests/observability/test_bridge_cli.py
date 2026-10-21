"""Status-file bridge (spec 161 §8) and the shell CLI (§7.5)."""
import json
import os
from argparse import Namespace
from datetime import datetime, timedelta, timezone

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from core.jobs import status as job_status
from core.observability import bridge, cli
from core.observability.emitter import Telemetry
from tests.observability.test_library import points

NOW = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)


def tel_for(tmp_path, strict=True):
    reader = InMemoryMetricReader()
    tel = Telemetry(service_name="minimoi-observability-bridge", environment="dev", local_dir=tmp_path / "local",
                    enabled=True, strict=strict, metric_reader=reader, log_exporter=InMemoryLogRecordExporter(),
                    heartbeat_s=0)
    return tel, reader


def registry(tmp_path, jobs):
    doc = {"schema_version": 1, "jobs": [
        {"id": j, "name": j, "host": "ec2", "schedule": {"text": "daily", "kind": "daily", "at": "03:00",
                                                        "tz": "America/Chicago"},
         "expected_every_s": 86400, "missed_after_s": 172800, "stuck_after_s": 7200,
         "status_file": f"{j}.json", "owner": "robert", "runbook": "x"} for j in jobs]}
    path = tmp_path / "scheduled_jobs.json"
    path.write_text(json.dumps(doc))
    return path


def status(root, job, state="ok", exit_code=0, success=NOW):
    doc = job_status.build(job_id=job, host="ec2", run_id="r1", state=state, started_at=NOW - timedelta(minutes=5),
                           finished_at=None if state == "running" else NOW, exit_code=None if state == "running" else exit_code,
                           summary="done", last_success_at=success)
    job_status.write_status(root, f"{job}.json", doc)


def snapshots(root):
    (root / "health").mkdir(parents=True, exist_ok=True)
    (root / "health" / "services.json").write_text(json.dumps({
        "schema": "mc-health-snapshot/1", "sampled_at": "2026-10-21T09:59:00Z", "environment": "prod",
        "services": [{"name": "portal", "status": "ok"}, {"name": "curator", "status": "fail"},
                     {"name": "someone-unregistered", "status": "ok"}]}))
    (root / "health" / "host.json").write_text(json.dumps({
        "schema": "mc-host-snapshot/1", "sampled_at": "2026-10-21T09:59:00Z",
        "filesystems": [{"mount": "/", "total_bytes": 100, "avail_bytes": 17}],
        "mem_total_bytes": 1000, "mem_available_bytes": 600}))


def release(tmp_path, status_value="selected_images_observed", verification=True, ok=True):
    doc = {"format": "minimoi.release-observation/1", "observed_at": "2026-10-21T09:00:00Z", "status": status_value}
    if verification:
        doc["verification"] = {"services": [{"name": "portal", "ok": ok}]}
    path = tmp_path / "release.json"
    path.write_text(json.dumps(doc))
    return path


def run(tmp_path, root, reg, rel=None, strict=True):
    tel, reader = tel_for(tmp_path, strict)
    bridge.collect_once(tel, Namespace(jobs_root=str(root), registry=str(reg), health="health/services.json",
                                       host="health/host.json", release=str(rel) if rel else None))
    return points(reader)[0], tel


def g(got, name, **labels):
    return got.get((name, tuple(sorted(labels.items()))))


def test_jobs_become_facts_including_their_windows(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir(mode=0o700)
    reg = registry(tmp_path, ["backup-s3", "vault-sync", "never-ran"])
    status(root, "backup-s3")
    status(root, "vault-sync", state="failed", exit_code=3, success=NOW - timedelta(days=2))
    snapshots(root)
    got, _ = run(tmp_path, root, reg)
    assert g(got, "minimoi_job_last_success_timestamp_seconds", job_id="backup-s3") == NOW.timestamp()
    assert g(got, "minimoi_job_last_exit_code", job_id="vault-sync") == 3
    assert g(got, "minimoi_job_missed_after_seconds", job_id="backup-s3") == 172800
    assert g(got, "minimoi_job_status_readable", job_id="never-ran") == 0          # unknown, never healthy
    assert g(got, "minimoi_job_last_success_timestamp_seconds", job_id="never-ran") is None
    assert g(got, "minimoi_job_running", job_id="backup-s3") == 0


def test_running_job_reports_running(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir(mode=0o700)
    reg = registry(tmp_path, ["backup-s3"])
    status(root, "backup-s3", state="running")
    got, _ = run(tmp_path, root, reg)
    assert g(got, "minimoi_job_running", job_id="backup-s3") == 1


def test_health_and_host_snapshots_become_facts_and_unregistered_names_are_refused_alone(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir(mode=0o700)
    snapshots(root)
    got, tel = run(tmp_path, root, registry(tmp_path, []))
    assert g(got, "minimoi_service_up", service="portal") == 1
    assert g(got, "minimoi_service_up", service="curator") == 0
    assert not [k for k in got if k[0] == "minimoi_service_up" and ("service", "someone-unregistered") in k[1]]
    assert tel.dropped()["metrics"] == 1
    assert g(got, "minimoi_host_filesystem_used_ratio", mountpoint="/") == 0.83
    assert g(got, "minimoi_host_memory_used_ratio") == 0.4
    assert g(got, "minimoi_bridge_source_readable", source="host_snapshot") == 1


@pytest.mark.parametrize("body", ['{"schema": "other"}', "{not json", "[]",
                                  json.dumps({"schema": "mc-host-snapshot/1", "filesystems": [
                                      {"mount": "/", "total_bytes": 10, "avail_bytes": 20}],
                                      "mem_total_bytes": 1, "mem_available_bytes": 0})])
def test_bad_snapshots_are_unreadable_not_zero(tmp_path, body):
    root = tmp_path / "jobs"
    (root / "health").mkdir(parents=True)
    (root / "health" / "host.json").write_text(body)
    got, _ = run(tmp_path, root, registry(tmp_path, []))
    assert g(got, "minimoi_bridge_source_readable", source="host_snapshot") == 0
    assert g(got, "minimoi_host_memory_used_ratio") is None


def test_symlinked_snapshot_is_refused(tmp_path):
    root = tmp_path / "jobs"
    (root / "health").mkdir(parents=True)
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    (root / "health" / "services.json").symlink_to(target)
    got, _ = run(tmp_path, root, registry(tmp_path, []))
    assert g(got, "minimoi_bridge_source_readable", source="health_snapshot") == 0


@pytest.mark.parametrize("kw,outcome", [({}, 0), ({"ok": False}, 1), ({"verification": False}, 2),
                                        ({"status_value": "deployment_in_progress", "verification": False}, 2),
                                        ({"status_value": "selected_image_check_failed", "verification": False}, 1)])
def test_release_status_outcomes(tmp_path, kw, outcome):
    root = tmp_path / "jobs"
    root.mkdir()
    got, _ = run(tmp_path, root, registry(tmp_path, []), rel=release(tmp_path, **kw))
    assert g(got, "minimoi_deploy_last_outcome") == outcome


def test_unreadable_registry_is_reported_not_crashed(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    bad = tmp_path / "reg.json"
    bad.write_text("{broken")
    got, _ = run(tmp_path, root, bad)
    assert g(got, "minimoi_bridge_source_readable", source="job_registry") == 0


def test_shipped_registry_jobs_all_pass_the_vocabulary(tmp_path):
    from core.jobs import registry as job_registry
    from core.observability import vocabulary
    v = vocabulary.load()
    for job in job_registry.load().jobs:
        v.check_label("job_id", job.id)


def test_cli_always_exits_zero_and_writes_the_local_copy(tmp_path, monkeypatch, capsys):
    from core import observability as obs
    obs._reset_for_tests()
    monkeypatch.setenv("MINIMOI_ENV", "dev")
    monkeypatch.setenv("MINIMOI_TELEMETRY_LOCAL_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("MINIMOI_TELEMETRY_ENABLED", "0")
    assert cli.main(["gauge", "minimoi_job_last_success_timestamp_seconds", "1761036000",
                     "--label", "job_id=backup-local"]) == 0
    assert "minimoi_job_last_success_timestamp_seconds|job_id=backup-local" in \
        (tmp_path / "t" / "metrics-latest.minimoi-mac-jobs.json").read_text()
    obs._reset_for_tests()
    assert cli.main(["gauge", "minimoi_job_running", "1", "--label", "job_id=Robert's private job"]) == 0
    assert "refused" in capsys.readouterr().err
    obs._reset_for_tests()
    assert cli.main(["no-such-command"]) == 0
    obs._reset_for_tests()


def test_a_fifo_snapshot_never_hangs_the_bridge(tmp_path):
    """Claude re-review of spec 161 v0.3 (O1 conformance): the bridge reader could block on a FIFO."""
    import threading
    root = tmp_path / "jobs"
    (root / "health").mkdir(parents=True)
    os.mkfifo(root / "health" / "host.json")
    out = {}

    def call():
        out["got"], _ = run(tmp_path, root, registry(tmp_path, []))
    t = threading.Thread(target=call, daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive(), "bridge blocked on a FIFO"
    assert g(out["got"], "minimoi_bridge_source_readable", source="host_snapshot") == 0
