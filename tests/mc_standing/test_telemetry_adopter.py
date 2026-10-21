"""Standing checks as the first adopter of spec 161 telemetry: facts only, never a threshold, and
telemetry can never change the cycle's outcome."""
from tests.mc_standing.test_spool_findings_runner import NOW, FakeIngress, _status, run


class Recorder:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def gauge(self, name, value, **labels):
        if self.fail:
            raise RuntimeError("collector down")
        self.calls.append((name, value, labels))


def test_cycle_reports_source_readability_and_run_time_per_check(tmp_path):
    _status(tmp_path / "src")
    rec = Recorder()
    _, res = run(tmp_path, FakeIngress(), telemetry=rec)
    assert res["state"] == "delivered"
    assert ("minimoi_check_source_readable", 1, {"check_id": "backup-local"}) in rec.calls
    assert ("minimoi_check_last_run_timestamp_seconds", NOW.timestamp(), {"check_id": "backup-local"}) in rec.calls
    assert all(name.startswith("minimoi_") for name, _, _ in rec.calls)


def test_missing_source_is_reported_unreadable_not_healthy(tmp_path):
    rec = Recorder()
    _, res = run(tmp_path, FakeIngress(), telemetry=rec)          # no status file written
    assert ("minimoi_check_source_readable", 0, {"check_id": "backup-local"}) in rec.calls


def test_telemetry_failure_never_changes_the_outcome(tmp_path):
    _status(tmp_path / "src")
    _, res = run(tmp_path, FakeIngress(), telemetry=Recorder(fail=True))
    assert res["state"] == "delivered"


def test_without_telemetry_nothing_is_emitted(tmp_path):
    _status(tmp_path / "src")
    _, res = run(tmp_path, FakeIngress())
    assert res["state"] == "delivered"
