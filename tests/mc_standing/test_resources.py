"""Per-cycle resource evidence for non-interference (P-07) and the optional envelope check."""
from core.mc_standing.spool import Spool
from tests.mc_standing.test_spool_findings_runner import NOW, FakeIngress, _status, run


def test_each_invocation_records_bounded_resource_evidence(tmp_path):
    _status(tmp_path / "src")
    spool, res = run(tmp_path, FakeIngress(mode="defer"))
    m = res["resources"]
    assert set(m) == {"at", "state", "wall_ms", "cpu_ms", "max_rss_kb"}
    assert m["wall_ms"] >= 0 and m["cpu_ms"] >= 0 and m["max_rss_kb"] > 1000
    for _ in range(12):
        run(tmp_path, FakeIngress(mode="defer"), spool=spool)
    record = spool.read("cycles", res["cycle_key"])
    assert len(record["resources"]) == 10                 # bounded history
    assert record["state"] == "delivery_pending"          # measurement never changes the outcome


def test_envelope_excess_is_named_but_delivery_stands(tmp_path):
    _status(tmp_path / "src")
    _, res = run(tmp_path, FakeIngress(), envelope={"max_rss_kb": 1, "max_wall_ms": 10**9})
    assert res["state"] == "delivered" and res["envelope_exceeded"] == ["max_rss_kb"]
    _, ok = run(tmp_path / "b", FakeIngress(), envelope={"max_wall_ms": 10**9})
    assert "envelope_exceeded" not in ok
