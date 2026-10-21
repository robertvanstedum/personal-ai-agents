"""core/observability library (spec 161 §7): vocabulary, privacy, local copy, OTel export,
non-blocking behaviour and drop accounting. In-memory OTel readers/exporters, no network."""
import json
import os
import stat
import time

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from core import observability as obs
from core.observability import vocabulary as vocab_mod
from core.observability.emitter import Telemetry
from core.observability.vocabulary import TelemetryContractError


def make(tmp_path, *, enabled=True, strict=True, **kw):
    reader, logs = InMemoryMetricReader(), InMemoryLogRecordExporter()
    tel = Telemetry(service_name="minimoi-curator", environment="dev", local_dir=tmp_path / "tel",
                    enabled=enabled, strict=strict, metric_reader=reader if enabled else None,
                    log_exporter=logs if enabled else None, heartbeat_s=0, version="abc123", host_name="mac-dev",
                    **kw)
    return tel, reader, logs


def points(reader):
    out = {}
    data = reader.get_metrics_data()
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                for p in m.data.data_points:
                    out[(m.name, tuple(sorted(p.attributes.items())))] = p.value
    return out, (data.resource_metrics[0].resource.attributes if data and data.resource_metrics else {})


def test_vocabulary_file_is_valid_and_complete():
    v = vocab_mod.load()
    assert "minimoi_job_last_success_timestamp_seconds" in v.metrics
    assert v.metrics["minimoi_telemetry_dropped_total"].type == "counter"
    assert "minimoi-observability-bridge" in v.services


def test_gauge_exports_with_resource_attributes(tmp_path):
    tel, reader, _ = make(tmp_path)
    tel.gauge("minimoi_job_last_success_timestamp_seconds", 1761036000, job_id="curator-daily")
    got, resource = points(reader)
    assert got[("minimoi_job_last_success_timestamp_seconds", (("job_id", "curator-daily"),))] == 1761036000
    assert resource["service.name"] == "minimoi-curator"
    assert resource["deployment.environment.name"] == "dev"
    assert resource["service.version"] == "abc123" and resource["host.name"] == "mac-dev"
    assert resource["minimoi.telemetry.schema_version"] == 1


@pytest.mark.parametrize("call", [
    lambda t: t.gauge("minimoi_not_registered", 1),
    lambda t: t.gauge("minimoi_job_running", 1),                                   # missing label
    lambda t: t.gauge("minimoi_job_running", 1, job_id="x", extra="y"),            # extra label
    lambda t: t.gauge("minimoi_job_running", 1, job_id="robert@example.com"),      # email as a value
    lambda t: t.gauge("minimoi_job_running", 1, job_id="Hello, please call me"),   # free text
    lambda t: t.gauge("minimoi_service_up", 1, service="my-private-service"),      # not in the closed set
    lambda t: t.gauge("minimoi_job_running", float("nan"), job_id="x"),
    lambda t: t.gauge("minimoi_job_running", True, job_id="x"),
    lambda t: t.count("minimoi_telemetry_dropped_total", -1, signal="logs"),
    lambda t: t.count("minimoi_job_running", 1, job_id="x"),                       # wrong type
    lambda t: t.event(event_name="minimoi.job.failed", alert_key="job:x", state="opened", severity="major",
                      summary_code="the backup of Robert's taxes failed", component="jobs"),
    lambda t: t.event(event_name="minimoi.job.failed", alert_key="Robert said hi", state="opened",
                      severity="major", summary_code="exit_nonzero", component="jobs"),
])
def test_contract_errors_raise_in_strict_mode_and_nothing_is_exported(tmp_path, call):
    tel, reader, logs = make(tmp_path)
    with pytest.raises(TelemetryContractError):
        call(tel)
    got, _ = points(reader)
    assert not [k for k in got if k[0] != "minimoi_telemetry_dropped_total"]
    assert logs.get_finished_logs() == ()


def test_contract_errors_are_dropped_and_counted_in_prod(tmp_path):
    tel, reader, _ = make(tmp_path, strict=False)
    tel.gauge("minimoi_job_running", 1, job_id="robert@example.com")
    tel.gauge("minimoi_not_registered", 1)
    assert tel.dropped()["metrics"] == 2
    got, _ = points(reader)
    assert got[("minimoi_telemetry_dropped_total", (("signal", "metrics"),))] == 2


def test_event_exports_as_otel_log_with_severity_and_fixed_attributes(tmp_path):
    tel, _, logs = make(tmp_path)
    tel.event(event_name="minimoi.job.failed", alert_key="job:backup-s3", state="opened", severity="major",
              summary_code="exit_nonzero", component="jobs", job_id="backup-s3")
    deadline = time.time() + 5
    while not logs.get_finished_logs() and time.time() < deadline:
        tel.flush(1.0)
    record = logs.get_finished_logs()[0].log_record
    assert record.severity_number.value == 17 and record.body == "exit_nonzero"
    assert record.attributes["minimoi.alert_key"] == "job:backup-s3"
    assert record.attributes["minimoi.state"] == "opened" and record.attributes["job_id"] == "backup-s3"


def test_local_copy_is_written_first_and_private(tmp_path):
    tel, _, _ = make(tmp_path, enabled=False)
    tel.gauge("minimoi_host_memory_used_ratio", 0.42)
    tel.event(event_name="minimoi.deploy.failed", alert_key="deploy:last", state="opened", severity="major",
              summary_code="deploy_failed", component="deploy")
    d = tmp_path / "tel"
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700
    latest = json.loads((d / "metrics-latest.minimoi-curator.json").read_text())
    assert latest["minimoi_host_memory_used_ratio"]["value"] == 0.42
    events = list(d.glob("events-*.jsonl"))
    assert len(events) == 1 and stat.S_IMODE(os.stat(events[0]).st_mode) == 0o600
    row = json.loads(events[0].read_text().splitlines()[0])
    assert row["minimoi.alert_key"] == "deploy:last" and row["severity"] == "major"


def test_local_copy_failure_never_raises(tmp_path):
    blocker = tmp_path / "tel"
    blocker.write_text("a file where the directory should be")
    tel, _, _ = make(tmp_path, enabled=False)
    tel.gauge("minimoi_host_memory_used_ratio", 0.5)          # no exception
    assert tel._local_failures >= 1


def test_event_queue_overflow_never_blocks_and_is_counted(tmp_path):
    tel, reader, _ = make(tmp_path, queue_size=1)
    tel._stop.set()                                            # the drain thread stops consuming
    time.sleep(0.6)
    start = time.monotonic()
    for _ in range(50):
        tel.event(event_name="minimoi.job.failed", alert_key="job:x", state="opened", severity="major",
                  summary_code="exit_nonzero", component="jobs")
    assert time.monotonic() - start < 1.0
    assert tel.dropped()["logs"] >= 48


def test_unreachable_collector_never_blocks_the_caller(tmp_path):
    tel = Telemetry(service_name="minimoi-portal", environment="prod", local_dir=tmp_path / "t", enabled=True,
                    strict=False, endpoint="http://127.0.0.1:9", heartbeat_s=0, export_interval_s=0.2)
    start = time.monotonic()
    for i in range(200):
        tel.gauge("minimoi_host_memory_used_ratio", 0.1 + i / 1000)
        tel.event(event_name="minimoi.job.failed", alert_key="job:x", state="opened", severity="major",
                  summary_code="exit_nonzero", component="jobs")
    assert time.monotonic() - start < 1.0
    tel.shutdown()


def test_disabled_telemetry_exports_nothing_but_keeps_the_local_copy(tmp_path):
    tel, _, _ = make(tmp_path, enabled=False)
    tel.gauge("minimoi_host_memory_used_ratio", 0.3)
    assert tel._meter_provider is None
    assert (tmp_path / "tel" / "metrics-latest.minimoi-curator.json").exists()


def test_heartbeat_thread_sets_the_heartbeat_gauge(tmp_path):
    reader, logs = InMemoryMetricReader(), InMemoryLogRecordExporter()
    tel = Telemetry(service_name="minimoi-curator", environment="dev", local_dir=tmp_path / "t", enabled=True,
                    strict=True, metric_reader=reader, log_exporter=logs, heartbeat_s=0.05)
    time.sleep(0.2)
    got, _ = points(reader)
    assert ("minimoi_telemetry_heartbeat_timestamp_seconds", ()) in got
    tel.shutdown()


def test_module_api_is_a_no_op_until_configured_and_reads_the_environment(tmp_path):
    obs._reset_for_tests()
    obs.gauge("minimoi_host_memory_used_ratio", 0.5)         # ignored, no error
    env = {"MINIMOI_ENV": "prod", "MINIMOI_TELEMETRY_LOCAL_DIR": str(tmp_path / "x")}
    tel = obs.configure(service_name="minimoi-portal", environ=env, heartbeat_s=0)
    assert tel.enabled is False and tel.strict is False and obs.configure(service_name="other") is tel
    obs._reset_for_tests()


def test_environment_and_service_are_validated(tmp_path):
    with pytest.raises(TelemetryContractError):
        Telemetry(service_name="minimoi-portal", environment="staging", local_dir=tmp_path, enabled=False,
                  strict=True, heartbeat_s=0)
    with pytest.raises(TelemetryContractError):
        Telemetry(service_name="someone-else", environment="dev", local_dir=tmp_path, enabled=False,
                  strict=True, heartbeat_s=0)


def test_headers_file_must_be_private(tmp_path):
    f = tmp_path / "h"
    f.write_text("Authorization=Bearer x")
    os.chmod(f, 0o644)
    with pytest.raises(TelemetryContractError):
        obs._headers(str(f))
    os.chmod(f, 0o600)
    assert obs._headers(str(f)) == {"Authorization": "Bearer x"}
