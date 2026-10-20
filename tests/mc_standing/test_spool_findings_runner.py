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


# ── Codex review S-F1 / S-F2 and gaps (20 Oct) ──────────────────────────────

def test_resumed_pending_delivery_honours_pause(tmp_path):
    _status(tmp_path / "src")
    ing = FakeIngress("defer")
    spool, res = run(tmp_path, ing)
    assert res["state"] == "delivery_pending" and len(ing.posts) == 1
    ing.mode = "accept"
    _, res = run(tmp_path, ing, spool=spool, paused=lambda: True)
    assert res["reason"] == "paused_before_delivery" and len(ing.posts) == 1


def test_resumed_pending_delivery_is_held_after_cutoff(tmp_path):
    _status(tmp_path / "src")
    ing = FakeIngress("defer")
    spool, res = run(tmp_path, ing)
    ing.mode = "accept"
    _, res = run(tmp_path, ing, spool=spool, now=NOW + timedelta(hours=4))   # 09:40 local
    assert res["state"] == "held" and res["reason"] == "send_window_closed" and len(ing.posts) == 1
    _, res = run(tmp_path, ing, spool=spool, now=NOW + timedelta(days=1))    # next day: its own brief
    old = [e for e in ing.posts if e["cycle_key"] == "prod.2026-10-21.p0.morning"]
    assert len(old) == 1                                                      # yesterday's never resent
    assert spool.read("cycles", "prod.2026-10-21.p0.morning")["state"] == "held"


def test_resumed_unknown_may_confirm_but_not_resend_after_cutoff(tmp_path):
    _status(tmp_path / "src")
    ing = FakeIngress("uncertain_lost")
    spool, res = run(tmp_path, ing)
    ing.mode = "accept"
    _, res = run(tmp_path, ing, spool=spool, now=NOW + timedelta(hours=4))
    assert res["state"] == "held" and len(ing.posts) == 1
    # but a send that actually committed is still recognised by read-back after the cutoff
    ing2 = FakeIngress("uncertain_commit")
    spool2, res = run(tmp_path / "b", ing2)
    _status(tmp_path / "b" / "src")
    _, res = run(tmp_path / "b", ing2, spool=spool2, now=NOW + timedelta(hours=4))
    assert res["state"] == "delivered" and len(ing2.posts) == 1


def test_crash_between_outbox_and_stage_update_resumes(tmp_path, monkeypatch):
    _status(tmp_path / "src")
    spool = Spool(tmp_path / "state")
    real = spool.put_state

    def crash_on_pending(kind, name, doc):
        if doc.get("state") == "delivery_pending":
            raise OSError("crash after outbox write")
        real(kind, name, doc)

    monkeypatch.setattr(spool, "put_state", crash_on_pending)
    with pytest.raises(OSError):
        run(tmp_path, FakeIngress(), spool=spool)
    monkeypatch.setattr(spool, "put_state", real)
    outbox_before = spool.read("outbox", "prod.2026-10-21.p0.morning.site")
    ing = FakeIngress()
    _, res = run(tmp_path, ing, spool=spool, now=NOW + timedelta(minutes=6))
    assert res["state"] == "delivered"
    assert ing.posts[0] == outbox_before        # original timestamps and digests preserved


@pytest.mark.parametrize("fail_on", ["delivery", "outbox"])
def test_crash_at_each_artifact_write_resumes(tmp_path, monkeypatch, fail_on):
    _status(tmp_path / "src")
    spool = Spool(tmp_path / "state")
    real = spool.put_immutable

    def crash(kind, name, doc):
        if kind == fail_on:
            raise OSError(f"crash writing {kind}")
        return real(kind, name, doc)

    monkeypatch.setattr(spool, "put_immutable", crash)
    with pytest.raises(OSError):
        run(tmp_path, FakeIngress(), spool=spool)
    monkeypatch.setattr(spool, "put_immutable", real)
    _, res = run(tmp_path, FakeIngress(), spool=spool, now=NOW + timedelta(minutes=6))
    assert res["state"] == "delivered"


def test_watchlist_change_mid_cycle_is_recorded_not_conflicting(tmp_path):
    _status(tmp_path / "src")
    spool = Spool(tmp_path / "state")
    _, res = run(tmp_path, FakeIngress("defer"), spool=spool)
    wl = _watchlist()
    changed = registry.Watchlist(wl.watches, "f" * 64)
    res = runner.run_cycle(spool=spool, watchlist=changed, policy_id="p0", environment="prod", now=NOW,
                           source_root=tmp_path / "src", ingress=FakeIngress(),
                           destination_binding="owner:mc-operations")
    assert res["state"] == "delivered"


def test_watch_for_another_environment_is_not_read(tmp_path):
    _status(tmp_path / "src")
    spool = Spool(tmp_path / "state")
    res = runner.run_cycle(spool=spool, watchlist=_watchlist(), policy_id="p0", environment="dev", now=NOW,
                           source_root=tmp_path / "src", ingress=FakeIngress(),
                           destination_binding="owner:mc-operations")
    obs = spool.read("observations", "dev.2026-10-21.p0.morning.backup-local")
    assert obs["reason"] == "watch_environment_mismatch" and obs["state"] == "unknown"


def test_finding_on_another_host_is_not_recovered_here(tmp_path):
    s = Spool(tmp_path / "state")
    c1, c2 = "prod.2026-10-21.p0.morning", "prod.2026-10-22.p0.morning"
    findings.record(s, Observation(c1, "backup-local", 1, "prod", "mac", "failed", "job_failed",
                                   "complete", "2026-10-21T10:40:00Z"))
    ev = findings.record(s, Observation(c2, "backup-local", 1, "prod", "ec2", "healthy", "job_ok",
                                        "complete", "2026-10-22T10:40:00Z"))
    assert ev == []


def test_review_by_passed_is_named_in_the_brief(tmp_path):
    _status(tmp_path / "src")
    doc = json.loads(json.dumps({"schema": registry.SCHEMA, "watches": [dict(
        _watchlist().watches[0].__dict__, review_by="2026-10-01", expected_run_rule=_watchlist().watches[0].expected_run_rule)]}, default=str))
    for k in ("review_by",):
        doc["watches"][0][k] = "2026-10-01"
    wl = registry.parse(doc)
    spool = Spool(tmp_path / "state")
    res = runner.run_cycle(spool=spool, watchlist=wl, policy_id="p0", environment="prod", now=NOW,
                           source_root=tmp_path / "src", ingress=FakeIngress(),
                           destination_binding="owner:mc-operations")
    text = spool.read("delivery", f"{res['cycle_key']}.brief")["text"]
    assert "Watch review due (review-by date passed): Local backup." in text
    assert "Nothing needs you" not in text
