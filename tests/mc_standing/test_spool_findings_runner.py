"""Standing P0b-2: durable spool, finding streams and the morning cycle runner."""
import fcntl
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from core.mc_standing import findings, registry, runner
from core.mc_standing.observe import Observation
from core.mc_standing.spool import Spool, SpoolConflict, SpoolError

# 05:40 America/Chicago on 21 Oct 2026 (CDT, UTC-5)
NOW = datetime(2026, 10, 21, 10, 40, tzinfo=timezone.utc)


def _watchlist():
    doc = {"schema": registry.SCHEMA, "watches": [{
        "id": "backup-local", "version": 1, "label": "Local backup", "environment": "prod",
        "host_scope": "ec2", "adapter_id": "job_status", "source_binding": "jobs/backup-local.json",
        "stale_after_s": 93600, "max_read_bytes": 8192, "timeout_s": 3, "required_for_coverage": True,
        "approved_by": "Robert", "approval_ref": "kickoff", "review_by": "2027-01-31",
        "expected_run_rule": {"expected_every_s": 86400, "grace_s": 3600,
                              "missed_after_s": 172800, "stuck_after_s": 7200}}]}
    return registry.parse(doc, raw_bytes=json.dumps(doc).encode())


def _status(root, state="ok", success_hours_ago=8, now=NOW):
    success = now - timedelta(hours=success_hours_ago)
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
    path = root / "jobs" / "backup-local.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "job_id": "backup-local", "host": "ec2",
                                "run_id": "r1", "state": state, "started_at": iso(success),
                                "finished_at": iso(success), "exit_code": 0, "summary": "",
                                "results": {}, "next_due_by": None, "last_success_at": iso(success),
                                "alert": None}))


class FakeIngress:
    def __init__(self, mode="accept"):
        self.mode, self.posts, self.store = mode, [], {}

    def post(self, event):
        self.posts.append(event)
        if self.mode == "uncertain_commit":          # committed, then the connection dropped
            self.store[event["event_id"]] = {"state": "accepted"}
            raise runner.IngressUncertain()
        if self.mode == "uncertain_lost":             # never reached the server
            raise runner.IngressUncertain()
        if self.mode == "defer":
            return {"state": "deferred"}
        if self.mode == "refuse":
            return {"state": "refused", "code": "destination_unbound"}
        state = "already_applied" if event["event_id"] in self.store else "accepted"
        self.store[event["event_id"]] = {"state": "accepted"}
        return {"state": state, "record_id": "evt-1"}

    def get(self, event_id):
        return self.store.get(event_id)


def run(tmp_path, ingress, now=NOW, **kw):
    spool = kw.pop("spool", None) or Spool(tmp_path / "state")
    return spool, runner.run_cycle(spool=spool, watchlist=_watchlist(), policy_id="p0", environment="prod",
                                   now=now, source_root=tmp_path / "src", ingress=ingress,
                                   destination_binding="owner:mc-operations", **kw)


# ── spool ───────────────────────────────────────────────────────────────────

def test_spool_is_private_and_immutable(tmp_path):
    s = Spool(tmp_path / "state")
    assert oct(os.stat(tmp_path / "state").st_mode & 0o777) == "0o700"
    assert s.put_immutable("findings", "a", {"x": 1}) is True
    assert s.put_immutable("findings", "a", {"x": 1}) is False
    with pytest.raises(SpoolConflict):
        s.put_immutable("findings", "a", {"x": 2})
    assert oct(os.stat(tmp_path / "state/findings/a.json").st_mode & 0o777) == "0o600"
    with pytest.raises(SpoolError):
        s.put_immutable("findings", "../escape", {})


def test_spool_refuses_symlinks_and_corrupt_records(tmp_path):
    s = Spool(tmp_path / "state")
    os.symlink(tmp_path / "elsewhere.json", tmp_path / "state/cycles/x.json")
    with pytest.raises(SpoolError):
        s.read("cycles", "x")
    (tmp_path / "state/cycles/y.json").write_text("{torn")
    with pytest.raises(SpoolError):
        s.read("cycles", "y")


# ── findings ────────────────────────────────────────────────────────────────

def _obs(cycle, state, reason, coverage="complete"):
    return Observation(cycle, "backup-local", 1, "prod", "ec2", state, reason, coverage,
                       f"{cycle[5:15]}T10:40:00Z")


def test_finding_stream_opens_continues_and_recovers(tmp_path):
    s = Spool(tmp_path / "state")
    d1, d2, d3, d4 = (f"prod.2026-10-{d}.p0.morning" for d in (21, 22, 23, 24))
    assert [e["event"] for e in findings.record(s, _obs(d1, "failed", "job_failed"))] == ["opened"]
    assert [e["event"] for e in findings.record(s, _obs(d2, "failed", "job_missed"))] == ["observed"]
    assert [e["event"] for e in findings.record(s, _obs(d3, "unknown", "source_missing", "unavailable"))] \
        == ["coverage_lost"]
    assert [e["event"] for e in findings.record(s, _obs(d4, "healthy", "job_ok"))] == ["recovered"]
    keys = {s.read("findings", n)["finding_key"] for n in s.names("findings")}
    assert keys == {"prod.ec2.backup-local.job"}          # one incident, not four tasks


def test_findings_rerun_of_same_cycle_adds_nothing(tmp_path):
    s = Spool(tmp_path / "state")
    c = "prod.2026-10-21.p0.morning"
    first = findings.record(s, _obs(c, "failed", "job_failed"))
    again = findings.record(s, _obs(c, "failed", "job_failed"))
    assert [e["event_id"] for e in again] == [e["event_id"] for e in first]
    assert len(s.names("findings")) == 1


def test_silence_never_recovers(tmp_path):
    s = Spool(tmp_path / "state")
    findings.record(s, _obs("prod.2026-10-21.p0.morning", "failed", "job_failed"))
    ev = findings.record(s, _obs("prod.2026-10-22.p0.morning", "unknown", "source_missing", "unavailable"))
    assert [e["event"] for e in ev] == ["coverage_lost"]


# ── runner ──────────────────────────────────────────────────────────────────

def test_normal_cycle_delivers_once(tmp_path):
    _status(tmp_path / "src")
    ing = FakeIngress()
    spool, res = run(tmp_path, ing)
    assert res["state"] == "delivered" and len(ing.posts) == 1
    ev = ing.posts[0]
    assert ev["schema"] == "mc-standing-event/1" and ev["kind"] == "brief"
    brief = spool.read("delivery", f"{res['cycle_key']}.brief")
    assert "MC operations brief, production, 2026-10-21." in brief["text"]
    _, again = run(tmp_path, ing, spool=spool)
    assert again["reason"] == "already_final" and len(ing.posts) == 1


def test_crash_after_collect_resumes_without_recollecting(tmp_path):
    _status(tmp_path / "src")
    spool = Spool(tmp_path / "state")
    _, res = run(tmp_path, FakeIngress("defer"), spool=spool)
    assert res["state"] == "delivery_pending"
    # the source changes after the cycle collected; the resumed cycle must not re-read it
    _status(tmp_path / "src", state="failed")
    ing = FakeIngress()
    _, res = run(tmp_path, ing, spool=spool, now=NOW + timedelta(minutes=5))
    assert res["state"] == "delivered"
    assert "All 1 checks healthy" in spool.read("delivery", f"{res['cycle_key']}.brief")["text"]


def test_unconfirmed_send_that_committed_is_not_resent(tmp_path):
    _status(tmp_path / "src")
    ing = FakeIngress("uncertain_commit")
    spool, res = run(tmp_path, ing)
    assert res["state"] == "delivery_unknown"
    ing.mode = "accept"
    _, res = run(tmp_path, ing, spool=spool)
    assert res["state"] == "delivered" and len(ing.posts) == 1    # readback found it


def test_unconfirmed_send_that_was_lost_is_reposted_with_same_id(tmp_path):
    _status(tmp_path / "src")
    ing = FakeIngress("uncertain_lost")
    spool, res = run(tmp_path, ing)
    assert res["state"] == "delivery_unknown"
    ing.mode = "accept"
    _, res = run(tmp_path, ing, spool=spool)
    assert res["state"] == "delivered"
    assert len({e["event_id"] for e in ing.posts}) == 1 and len(ing.posts) == 2


def test_refused_delivery_fails_visibly(tmp_path):
    _status(tmp_path / "src")
    _, res = run(tmp_path, FakeIngress("refuse"))
    assert res["state"] == "failed" and res["reason"] == "ingress_refused"


def test_kill_and_pause(tmp_path):
    _status(tmp_path / "src")
    _, res = run(tmp_path, FakeIngress(), paused=lambda: True)
    assert res["state"] == "paused"
    _, res = run(tmp_path, FakeIngress(), kill=lambda: True)
    assert res["state"] == "failed" and res["reason"] == "killed_before_start"


def test_kill_before_delivery_holds_the_outbox(tmp_path):
    _status(tmp_path / "src")
    calls = {"n": 0}

    def kill_late():
        calls["n"] += 1
        return calls["n"] > 2          # allow start and collection, stop before sending
    ing = FakeIngress()
    spool, res = run(tmp_path, ing, kill=kill_late)
    assert res["reason"] == "killed_before_delivery" and ing.posts == []
    _, res = run(tmp_path, ing, spool=spool)
    assert res["state"] == "delivered"


def test_late_start_is_delayed_then_missed_after_nine(tmp_path):
    _status(tmp_path / "src", now=NOW + timedelta(hours=2))
    spool, res = run(tmp_path, FakeIngress(), now=NOW + timedelta(hours=2))     # 07:40 local
    assert res["state"] == "delivered"
    assert "(delayed)" in spool.read("delivery", f"{res['cycle_key']}.brief")["text"]
    _, res = run(tmp_path / "other", FakeIngress(), now=NOW + timedelta(hours=4))  # 09:40 local
    assert res["state"] == "missed"


def test_overlapping_run_is_skipped(tmp_path):
    _status(tmp_path / "src")
    spool = Spool(tmp_path / "state")
    fd = os.open(spool.root / ".cycle.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        _, res = run(tmp_path, FakeIngress(), spool=spool)
    finally:
        os.close(fd)
    assert (res["state"], res["reason"]) == ("skipped", "overlap")


def test_failing_source_opens_a_finding_and_shows_in_brief(tmp_path):
    _status(tmp_path / "src", state="failed")
    spool, res = run(tmp_path, FakeIngress())
    text = spool.read("delivery", f"{res['cycle_key']}.brief")["text"]
    assert "Local backup: failed" in text and "proposed:" in text
    assert [spool.read("findings", n)["event"] for n in spool.names("findings")] == ["opened"]
