"""#296 slice 1: the limits registry, the Claude process cost tracking, the restart before the lifetime cap, and the daily ceiling. Test doubles only: no real Claude process,
no network, no model."""
from __future__ import annotations

import json
import os
import queue
import threading
import time

import pytest

from core import agent_requests as ar
from core.agent_requests.claude_stream import ClaudeStreamProvider
from core.limits import AUDIT, DEFINITIONS, FILE, Limits

from test_connection import CONV, Clock, Fake, SID, ask, deliveries, fake_cli, mgr, root, wait_for  # noqa: F401  (fixtures and helpers)


# ── the registry ──────────────────────────────────────────────────────────────

def test_defaults_when_there_is_no_file_and_every_default_is_inside_its_own_bounds(tmp_path):
    lim = Limits(str(tmp_path))
    assert lim.value("claude.session_cap_usd") == 5.0 and lim.value("claude.daily_ceiling_usd") == 20.0
    for d in DEFINITIONS.values():
        assert d["hard_min"] <= d["default"] <= d["hard_max"]
    snap = lim.snapshot()
    assert snap["error"] == "" and all(r["is_default"] for r in snap["limits"])


def test_a_value_in_the_file_is_clamped_into_the_hard_bounds_and_junk_means_the_default(tmp_path):
    (tmp_path / FILE).write_text(json.dumps({"version": 1, "values": {"claude.session_cap_usd": 9999, "claude.daily_ceiling_usd": "lots"}}))
    lim = Limits(str(tmp_path))
    assert lim.value("claude.session_cap_usd") == DEFINITIONS["claude.session_cap_usd"]["hard_max"]
    assert lim.value("claude.daily_ceiling_usd") == 20.0
    (tmp_path / FILE).write_text(json.dumps({"version": 1, "values": {"claude.session_cap_usd": -3}}))
    assert Limits(str(tmp_path)).value("claude.session_cap_usd") == DEFINITIONS["claude.session_cap_usd"]["hard_min"]


@pytest.mark.parametrize("text", ["{not json", "[]", json.dumps({"version": 2, "values": {}}), json.dumps({"version": 1, "values": []}), ""])
def test_a_damaged_file_means_the_defaults_and_says_so_never_no_limit(tmp_path, text):
    (tmp_path / FILE).write_text(text)
    lim = Limits(str(tmp_path))
    assert lim.value("claude.session_cap_usd") == 5.0 and lim.value("claude.daily_ceiling_usd") == 20.0
    assert "defaults in use" in lim.snapshot()["error"]


def test_the_owner_changes_a_limit_inside_its_bounds_and_it_is_audited_before_and_after(tmp_path):
    lim = Limits(str(tmp_path))
    out = lim.set("claude.daily_ceiling_usd", 35, by="robert", reason="reviews today")
    assert out == {"id": "claude.daily_ceiling_usd", "before": 20.0, "after": 35.0}
    assert Limits(str(tmp_path)).value("claude.daily_ceiling_usd") == 35.0
    line = json.loads((tmp_path / AUDIT).read_text().splitlines()[-1])
    assert line["event"] == "set" and line["before"] == 20.0 and line["after"] == 35.0 and line["by"] == "robert" and line["reason"] == "reviews today"
    assert oct(os.stat(tmp_path / FILE).st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("limit_id,value", [("claude.daily_ceiling_usd", 0.5), ("claude.daily_ceiling_usd", 1000), ("claude.daily_ceiling_usd", True),
                                            ("claude.daily_ceiling_usd", "9"), ("claude.daily_ceiling_usd", float("nan")), ("mc.relay_idle_s", 60), ("nope", 1)])
def test_a_change_outside_the_bounds_or_to_a_fixed_or_unknown_limit_is_refused_and_changes_nothing(tmp_path, limit_id, value):
    lim = Limits(str(tmp_path))
    with pytest.raises(ValueError):
        lim.set(limit_id, value, by="robert")
    assert not (tmp_path / FILE).exists() and not (tmp_path / AUDIT).exists()


def test_the_snapshot_describes_each_limit_for_operate_and_for_master_craftsman(tmp_path):
    lim = Limits(str(tmp_path))
    lim.set("claude.session_cap_usd", 8, by="robert")
    rows = {r["id"]: r for r in lim.snapshot()["limits"]}
    assert rows["claude.session_cap_usd"]["value"] == 8.0 and rows["claude.session_cap_usd"]["is_default"] is False
    assert rows["mc.relay_idle_s"]["editable"] is False and rows["mc.relay_idle_s"]["value"] == 300.0
    assert all(r["what"] and r["unit"] and r["enforced_by"] for r in rows.values())


# ── cost tracking and the restart before the cap (the provider) ───────────────

class Proc:
    """A fake Claude child: results are pushed by the test; poll() is None until it is terminated."""
    def __init__(self, *a, **k):
        self.q = queue.Queue()
        self.stdin = self
        self.stdout = iter(self.q.get, None)
        self.alive = True
        self.argv = a[0]

    def write(self, s):
        return len(s)

    def flush(self):
        pass

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False
        self.q.put(None)

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminate()

    def close(self):
        pass

    def result(self, total, subtype="success"):
        self.q.put(json.dumps({"type": "result", "subtype": subtype, "total_cost_usd": total, "is_error": subtype != "success"}) + "\n")


def build(fake_cli, tmp_path, cap, procs, **kw):
    def popen(*a, **k):
        p = Proc(*a, **k)
        procs.append(p)
        return p
    return ClaudeStreamProvider(claude_bin=fake_cli, session_id=SID, cwd=str(tmp_path), allowed_tools=["Read"], ledger_root=str(tmp_path / "ledger"),
                                max_budget_usd=cap, popen=popen, **kw)


def test_the_cost_is_the_sessions_running_total_so_a_restart_continues_it_and_never_adds_it_up_again(fake_cli, tmp_path):
    procs, got = [], []
    p = build(fake_cli, tmp_path, 5.0, procs)
    p.usage_sink = got.append
    p.start()
    procs[0].result(0.13)
    procs[0].result(0.18)
    procs[0].result(0.50)
    assert wait_for(lambda: len(got) == 3)
    assert [round(r["delta_usd"], 4) for r in got] == [0.13, 0.05, 0.32] and [r["seq"] for r in got] == [1, 2, 3] and len({r["child"] for r in got}) == 1
    assert p.usage()["process_cap_usd"] == 5.0 and not p.usage_unknown() and got[0]["origin_usd"] == 0.0
    first_child = got[0]["child"]
    p.session_total_hint = lambda: 0.50                                                          # what the manager reads back from the records
    p.recycle()
    second = procs[1].argv
    assert second[second.index("--max-budget-usd") + 1] == "5.5"                                 # the budget flag is compared with the SESSION total, so the allowance sits on top of 0.50
    procs[1].result(0.70)                                                                        # the resumed session reports 0.70 in all: this process added 0.20, not 0.70
    assert wait_for(lambda: len(got) == 4)
    assert got[3]["child"] != first_child and round(got[3]["delta_usd"], 4) == 0.20 and got[3]["origin_usd"] == 0.5
    assert p.usage()["process_spent_usd"] == 0.2 and p.usage()["process_cap_usd"] == 5.0
    p.close()


def test_a_resumed_session_with_no_total_on_record_takes_its_first_result_as_the_baseline_not_as_new_spend(fake_cli, tmp_path):
    procs, got = [], []
    p = build(fake_cli, tmp_path, 5.0, procs, resume=True)
    p.usage_sink = got.append
    p.start()
    assert "5" == procs[0].argv[procs[0].argv.index("--max-budget-usd") + 1]
    procs[0].result(11.89)                                                                       # the old history of a long session: not counted as today's cost
    procs[0].result(12.26)
    assert wait_for(lambda: len(got) == 2)
    assert [round(r["delta_usd"], 4) for r in got] == [0.0, 0.37]
    p.close()


def test_a_total_that_goes_down_is_a_new_session_and_all_of_it_is_new_spend(fake_cli, tmp_path):
    procs, got = [], []
    p = build(fake_cli, tmp_path, 5.0, procs, resume=True)
    p.session_total_hint = lambda: 9.0
    p.usage_sink = got.append
    p.start()
    procs[0].result(0.40)
    assert wait_for(lambda: len(got) == 1) and round(got[0]["delta_usd"], 4) == 0.40
    p.close()


def test_a_result_without_a_readable_cost_is_recorded_as_unknown_never_zero_and_flags_the_process(fake_cli, tmp_path):
    procs, got = [], []
    p = build(fake_cli, tmp_path, 5.0, procs)
    p.usage_sink = got.append
    p.start()
    procs[0].q.put(json.dumps({"type": "result", "subtype": "success"}) + "\n")
    assert wait_for(lambda: len(got) == 1)
    assert got[0]["unknown"] is True and got[0]["cumulative_usd"] is None and p.usage_unknown()
    p.recycle()
    assert not p.usage_unknown()                                                                  # a replaced process starts known again
    procs[1].q.put(json.dumps({"type": "result", "subtype": "success", "total_cost_usd": "free"}) + "\n")
    assert wait_for(lambda: len(got) == 2) and got[1]["unknown"] is True
    p.close()


def test_a_sink_that_fails_makes_the_usage_unknown_not_lost_silently(fake_cli, tmp_path):
    procs = []
    p = build(fake_cli, tmp_path, 5.0, procs)

    def boom(rec):
        raise OSError("disk full")
    p.usage_sink = boom
    p.start()
    procs[0].result(0.1)
    assert wait_for(lambda: p.usage_unknown())
    p.close()


def test_a_busy_process_is_never_recycled_and_an_idle_one_is_at_sixty_percent_of_its_cap(fake_cli, tmp_path):
    procs = []
    p = build(fake_cli, tmp_path, 5.0, procs)
    p.start()
    assert not p.recycle_due()
    procs[0].result(2.9)
    assert wait_for(lambda: p.usage()["turns"] == 1)
    assert not p.recycle_due()                                                                  # 58%: not yet
    procs[0].result(3.1)
    assert wait_for(lambda: p.usage()["turns"] == 2)
    assert p.recycle_due()                                                                      # 62%
    p.deliver("hello", "r-1")                                                                   # a turn is in flight
    assert not p.recycle_due()
    p.close()


def test_recycling_starts_a_fresh_process_on_the_same_session_with_a_fresh_cap_read_from_the_registry(fake_cli, tmp_path):
    procs = []
    lim = Limits(str(tmp_path / "limits"))
    p = build(fake_cli, tmp_path, lambda: lim.value("claude.session_cap_usd"), procs)
    p.start()
    first = procs[0].argv
    assert "--max-budget-usd" in first and first[first.index("--max-budget-usd") + 1] == "5" and "--session-id" in first
    procs[0].result(3.5)
    assert wait_for(lambda: p.usage()["turns"] == 1)
    lim.set("claude.session_cap_usd", 8, by="robert")                                            # a change applies at the next start, not to the running process
    assert p.usage()["process_cap_usd"] == 5.0
    p.recycle()
    assert len(procs) == 2 and procs[0].alive is False
    second = procs[1].argv
    assert second[second.index("--max-budget-usd") + 1] == "8" and "--resume" in second and "--session-id" not in second
    u = p.usage()
    assert u["process_cap_usd"] == 8.0 and u["process_spent_usd"] == 0.0 and u["recycles"] == 1 and not p.recycle_due()
    p.close()


# ── the manager: recycle between turns, the daily reserve and ceiling, records that survive a restart ──

class Costly(Fake):
    """A scripted provider with cost tracking: the test spends through the same sink the manager gives the real provider."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.usage_sink = None
        self.due, self.recycled, self.unknown, self.child, self.seq = False, 0, False, "c1", 0
        self.total = 0.0                                                  # the session's running total, as Claude reports it

    def spend(self, delta, *, unknown=False, child=None, seq=None):
        self.seq += 1
        origin = self.total
        if not unknown:
            self.total += float(delta)
        rec = {"child": child or self.child, "seq": seq if seq is not None else self.seq, "cumulative_usd": None if unknown else self.total, "delta_usd": 0.0 if unknown else float(delta),
               "unknown": unknown, "origin_usd": origin if self.seq == 1 else None}
        self.usage_sink(rec)
        if unknown:
            self.unknown = True

    def usage(self):
        return {"process_spent_usd": 1.0, "process_cap_usd": 5.0, "turns": 1, "recycles": self.recycled}

    def usage_unknown(self):
        return self.unknown

    def recycle_due(self):
        return self.due

    def recycle(self):
        self.recycled += 1
        self.due = False
        self.unknown = False


def status(root):
    return json.load(open(os.path.join(root, "connection", "claude-code", "status.json")))


def usage_lines(root):
    d = os.path.join(root, "connection", "claude-code")
    out = []
    for f in sorted(os.listdir(d)):
        if f.startswith("usage-"):
            out += [json.loads(x) for x in open(os.path.join(d, f)).read().splitlines()]
    return out


def test_an_idle_process_past_its_threshold_is_recycled_before_anything_is_delivered(root):
    rid = ask(root)
    p = Costly()
    p.due = True
    m = mgr(root, p)
    out = m.step()
    assert out.get("recycled") is True and out["delivered"] is None and p.delivered == [] and p.recycled == 1
    again = m.step()
    assert again["delivered"] == {"request_id": rid, "status": "accepted"}                      # the next pass delivers on the fresh process
    assert status(root)["usage"]["recycles_today"] == 1
    m.close()


def test_a_busy_process_is_not_recycled_even_when_due(root):
    p = Costly(state="busy")
    p.due = True
    m = mgr(root, p)
    assert "recycled" not in m.step() and p.recycled == 0
    m.close()


def test_every_cost_is_an_append_only_record_the_day_total_is_recomputed_from_them_and_a_replayed_result_counts_once(root, tmp_path):
    lim = Limits(str(tmp_path / "limits"))
    clock = Clock(1_000_000.0)
    p = Costly()
    m = mgr(root, p, clock, limits=lim)
    p.spend(1.25)
    p.spend(0.75)
    p.usage_sink({"child": "c1", "seq": 2, "cumulative_usd": 2.0, "delta_usd": 0.75, "unknown": False})   # the SAME result delivered again: same event id
    m.step()
    u = status(root)["usage"]
    assert u["spent_today_usd"] == 2.0 and u["daily_ceiling_usd"] == 20.0 and u["process_cap_usd"] == 5.0
    assert len(usage_lines(root)) == 3 and all(oct(os.stat(os.path.join(root, "connection", "claude-code", f)).st_mode & 0o777) == "0o600"
                                                for f in os.listdir(os.path.join(root, "connection", "claude-code")) if f.startswith("usage-"))
    m.close()
    m2 = mgr(root, Costly(), clock, limits=lim)                                                  # a restarted manager reads the same total from the records
    m2.step()
    assert status(root)["usage"]["spent_today_usd"] == 2.0
    clock.advance(26 * 3600)                                                                     # a new Chicago day starts again from zero, in its own file
    m2.step()
    assert status(root)["usage"]["spent_today_usd"] == 0.0 and len({r["day"] for r in usage_lines(root)}) == 1
    m2.close()


def test_the_accounting_day_is_the_chicago_day_not_utc_and_not_the_hosts(root):
    c = Clock(1_791_800_000.0)                              # 2026-10-12 08:53:20 UTC = 2026-10-12 03:53:20 CDT
    m = mgr(root, Costly(), c)
    assert m._today() == "2026-10-12"
    c.t = 1_791_766_800.0                                    # 2026-10-11 23:40:00 CDT = 2026-10-12 04:40:00 UTC
    assert m._today() == "2026-10-11"
    c.t = 1_794_300_000.0                                    # after the clocks go back (2026-11-01): 2026-11-10 ... still Chicago midnight to midnight
    assert m._today() == "2026-11-10"
    m.close()


def test_a_damaged_usage_line_is_skipped_counted_and_holds_new_work_until_the_owner_looks(root, tmp_path):
    rid = ask(root)
    p = Costly()
    m = mgr(root, p, Clock(), limits=Limits(str(tmp_path / "limits")))
    p.spend(1.0)
    path = [os.path.join(root, "connection", "claude-code", f) for f in os.listdir(os.path.join(root, "connection", "claude-code")) if f.startswith("usage-")][0]
    with open(path, "a") as fh:
        fh.write("{not json\n")
    out = m.step()
    assert out["delivered"] is None and out["held"] == {rid: "usage_log_damaged"} and p.delivered == []
    assert status(root)["attention"] == "usage_log_damaged" and status(root)["usage"]["damaged_usage_lines"] == 1
    m.close()


def test_a_result_without_a_cost_holds_new_work_until_it_is_resolved_on_the_record(root, tmp_path):
    rid = ask(root)
    p = Costly()
    m = mgr(root, p, Clock(), limits=Limits(str(tmp_path / "limits")))
    p.spend(0, unknown=True)
    first = m.step()
    assert first["delivered"] is None and first.get("recycled") is True and p.recycled == 1 and p.delivered == []       # replaced once, so no further cost goes unseen
    assert status(root)["usage"]["unknown_results_today"] == 1
    second = m.step()
    assert second["delivered"] is None and second["held"] == {rid: "usage_unknown"} and p.delivered == []             # the replacement does NOT lift the hold
    assert status(root)["attention"] == "usage_unknown" and status(root)["usage"]["unknown_hold"]["recycled"] is True
    m.close()


def test_the_hold_survives_a_restart_of_the_manager_and_a_new_provider_and_the_next_day(root, tmp_path):
    rid = ask(root)
    p = Costly()
    c = Clock(1_791_766_800.0)
    lim = Limits(str(tmp_path / "limits"))
    m = mgr(root, p, c, limits=lim)
    p.spend(0, unknown=True)
    m.step()
    m.close()
    p2 = Costly()                                            # a fresh manager and provider: nothing in memory says "unknown"
    c.t += 86400 * 2                                         # and two accounting days later, whose usage file is empty
    m2 = mgr(root, p2, c, limits=lim)
    out = m2.step()
    assert out["delivered"] is None and out["held"] == {rid: "usage_unknown"} and p2.delivered == []
    m2.close()


def test_only_an_explicit_resolution_lifts_the_hold_and_it_is_written_down(root, tmp_path):
    from core.agent_requests.connection import resolve_usage_hold
    rid = ask(root)
    p = Costly()
    lim = Limits(str(tmp_path / "limits"))
    m = mgr(root, p, Clock(), limits=lim)
    p.spend(0, unknown=True)
    m.step()
    m.step()
    with pytest.raises(ValueError):
        resolve_usage_hold(root, "claude-code", "  ")
    assert resolve_usage_hold(root, "claude-code", "checked the Claude account usage page; nothing unexpected", by="robert") is True
    assert resolve_usage_hold(root, "claude-code", "again") is False
    assert any(r.get("kind") == "unknown_resolved" and r["by"] == "robert" and "account usage" in r["note"] for r in usage_lines(root))
    assert m.step()["delivered"] == {"request_id": rid, "status": "accepted"}
    m.close()


def test_an_unreadable_hold_record_still_holds(root, tmp_path):
    rid = ask(root)
    p = Costly()
    m = mgr(root, p, Clock(), limits=Limits(str(tmp_path / "limits")))
    open(os.path.join(root, "connection", "claude-code", "usage-hold.json"), "w").write("{garbage")
    out = m.step()
    assert out["delivered"] is None and out["held"] == {rid: "usage_unknown"}
    m.close()


def test_a_usage_file_too_big_to_read_whole_fails_closed_never_as_a_valid_prefix(root, tmp_path, monkeypatch):
    import core.agent_requests.connection as C
    rid = ask(root)
    p = Costly()
    m = mgr(root, p, Clock(), limits=Limits(str(tmp_path / "limits")))
    p.spend(1.0)
    monkeypatch.setattr(C, "USAGE_LOG_MAX", 40)              # the one record written is longer than this: the file holds more than can be counted
    out = m.step()
    assert out["delivered"] is None and out["held"] == {rid: "usage_log_damaged"} and status(root)["usage"]["damaged_usage_lines"] >= 1
    m.close()


def test_the_daily_reserve_stops_new_work_before_the_ceiling_and_a_raised_limit_resumes_live(root, tmp_path):
    lim = Limits(str(tmp_path / "limits"))
    lim.set("claude.daily_ceiling_usd", 10, by="robert")
    rid = ask(root)
    p = Costly()
    m = mgr(root, p, Clock(), limits=lim)
    p.spend(8.5)                                            # the hold line is 10 * (1 - 0.1) = 9: not yet
    assert m.step()["delivered"] == {"request_id": rid, "status": "accepted"}
    rid2 = ask(root, "b")
    p.state = "idle"
    p.spend(0.6)                                            # 9.1 spent: past the line, still under the ceiling
    out = m.step()
    assert out["delivered"] is None and out["held"][rid2] == "daily_ceiling_reached" and status(root)["usage"]["daily_hold_line_usd"] == 9.0
    st = status(root)
    assert st["attention"].startswith("daily_ceiling_reached:") and "of 10" in st["attention"]
    assert ar.load(root, rid2)["stage"] == "queued" and ar.pending(root, "claude-code")             # not lost, not closed
    lim.set("claude.daily_ceiling_usd", 30, by="robert", reason="reviews running")                   # the owner raises it: live, no restart
    out = m.step()
    assert out["delivered"] == {"request_id": rid2, "status": "accepted"}
    assert not status(root)["attention"].startswith("daily_ceiling_reached")
    m.close()


def test_the_status_carries_what_was_requested_and_what_is_applied(root, tmp_path):
    lim = Limits(str(tmp_path / "limits"))
    lim.set("claude.session_cap_usd", 8, by="robert")
    p = Costly()
    m = mgr(root, p, Clock(), limits=lim)
    m.step()
    applied = status(root)["usage"]["applied"]
    assert applied["claude.session_cap_usd"] == 5.0                                                  # the running process still has the cap it started with
    assert applied["claude.daily_ceiling_usd"] == 20.0 and applied["claude.daily_headroom_fraction"] == 0.1
    m.close()


def test_without_a_registry_or_a_cost_tracking_provider_nothing_changes(root):
    p = Fake()
    m = mgr(root, p)
    m.step()
    assert status(root)["usage"] == {}
    assert usage_lines(root) == [] if os.path.isdir(os.path.join(root, "connection", "claude-code")) else True
    m.close()


def test_a_cost_record_that_could_not_be_written_holds_new_work_even_after_a_restart(root, tmp_path, monkeypatch):
    """Codex's P1: the cost WAS readable but the append failed. The hold must be on disk before the append, survive a new manager and provider, and lift only on resolution."""
    import core.agent_requests.connection as C
    rid = ask(root)
    p = Costly()
    lim = Limits(str(tmp_path / "limits"))
    m = mgr(root, p, Clock(), limits=lim)
    real_open = os.open

    def failing(path, flags, *a, **k):
        if str(path).endswith(".jsonl") and flags & os.O_APPEND:
            raise OSError(28, "no space left on device")
        return real_open(path, flags, *a, **k)
    monkeypatch.setattr(C.os, "open", failing)
    with pytest.raises(OSError):
        p.spend(1.25)                                                      # a readable cost whose record cannot be appended
    monkeypatch.setattr(C.os, "open", real_open)
    m.close()
    p2 = Costly()                                                          # a fresh manager and provider: memory knows nothing of it
    m2 = mgr(root, p2, Clock(), limits=lim)
    out = m2.step()
    assert out["delivered"] is None and out["held"] == {rid: "usage_unknown"} and p2.delivered == []
    assert status(root)["usage"]["unknown_hold"]["reason"].startswith("a cost record may be missing")
    assert os.path.exists(os.path.join(root, "connection", "claude-code", "usage-pending.json"))
    assert C.resolve_usage_hold(root, "claude-code", "checked the account usage page and reconciled the missing record", by="robert") is True
    assert not os.path.exists(os.path.join(root, "connection", "claude-code", "usage-pending.json"))
    assert m2.step()["delivered"] == {"request_id": rid, "status": "accepted"}
    m2.close()


def test_a_successful_append_leaves_no_pending_marker(root, tmp_path):
    p = Costly()
    m = mgr(root, p, Clock(), limits=Limits(str(tmp_path / "limits")))
    p.spend(0.5)
    assert not os.path.exists(os.path.join(root, "connection", "claude-code", "usage-pending.json"))
    m.close()


def _write_usage(root, day, rows):
    d = os.path.join(root, "connection", "claude-code")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"usage-{day}.jsonl"), "a") as fh:
        for i, (child, seq, cum, extra) in enumerate(rows):
            rec = {"event_id": f"{child}:{seq}", "at": "2026-10-11T02:00:00Z", "day": day, "child": child, "seq": seq, "cumulative_usd": cum, "delta_usd": cum, "unknown": False, **extra}
            fh.write(json.dumps(rec) + "\n")


def test_records_written_across_restarts_total_the_change_in_the_session_total_not_the_sum_of_the_totals(root, tmp_path):
    """The bug seen live on 11 Oct: seven restarts reported 11.89, 12.26, 12.55, 12.77, ...; the old day total added them up to 232 dollars when the real spend was about 5."""
    p = Costly()
    m = mgr(root, p, Clock(1_791_800_000.0), limits=Limits(str(tmp_path / "limits")))       # 2026-10-12 in Chicago
    day = m._today()
    _write_usage(root, day, [("c1", 1, 11.892007, {}), ("c1", 2, 12.258647, {}), ("c2", 1, 12.5507644, {}), ("c3", 1, 12.7724998, {}), ("c4", 1, 13.0042282, {})])
    # first record has no origin (an old record): not counted; everything after it is the change in the total
    assert round(m._day_totals()["spent_usd"], 3) == round(13.0042282 - 11.892007, 3)
    m.close()


def test_a_days_first_record_is_measured_from_its_recorded_origin_or_the_previous_days_last_total(root, tmp_path):
    p = Costly()
    c = Clock(1_791_800_000.0)
    m = mgr(root, p, c, limits=Limits(str(tmp_path / "limits")))
    today = m._today()
    _write_usage(root, today, [("c1", 1, 20.4, {"origin_usd": 20.0}), ("c1", 2, 20.9, {"origin_usd": 20.0})])
    assert round(m._day_totals()["spent_usd"], 3) == 0.9                                      # from the origin the process recorded
    prev_day = "2026-10-11"
    _write_usage(root, prev_day, [("c0", 1, 19.0, {})])
    assert round(m._day_totals()["spent_usd"], 3) == round(20.9 - 19.0, 3)                    # an earlier day's last total wins: the change since then, including the overnight gap
    assert m._last_cumulative() == 20.9 and m._last_cumulative(before_day=today) == 19.0
    m.close()


def test_a_manager_hands_the_providers_a_resumed_session_the_last_total_on_record(root, tmp_path):
    p = Costly()
    m = mgr(root, p, Clock(1_791_800_000.0), limits=Limits(str(tmp_path / "limits")))
    assert p.session_total_hint() is None                                                      # nothing on record yet
    _write_usage(root, m._today(), [("c1", 1, 3.5, {}), ("c1", 2, 3.9, {})])
    assert p.session_total_hint() == 3.9
    m.close()


# ── Codex's review of f21c84f4: an unknown starting cost is never a silent known zero; the latest total comes from the END of an oversized log ──

def test_a_resumed_result_with_no_baseline_says_its_cost_is_not_separable(fake_cli, tmp_path):
    procs, got = [], []
    p = build(fake_cli, tmp_path, 5.0, procs, resume=True)
    p.usage_sink = got.append
    p.start()
    procs[0].result(11.89)
    procs[0].result(12.26)
    assert wait_for(lambda: len(got) == 2)
    assert got[0]["baseline_unknown"] is True and got[0]["delta_usd"] == 0.0 and "baseline_unknown" not in got[1] and round(got[1]["delta_usd"], 4) == 0.37
    p.close()


def test_a_lone_legacy_record_is_reported_as_an_approximate_total_and_holds_only_when_a_ceiling_could_be_understated(root, tmp_path):
    from core.agent_requests.connection import resolve_usage_hold
    lim = Limits(str(tmp_path / "limits"))
    rid = ask(root)
    m = mgr(root, Costly(), Clock(time.time()))                                          # no registry: no ceiling at all (how Dev runs today)
    _write_usage(root, m._today(), [("c1", 1, 11.89, {})])                                   # no earlier day, no origin: the history behind 11.89 is unknown
    t = m._day_totals()
    assert t["spent_usd"] == 0.0 and t["baseline_unknown"] is True                           # reported as unknown, not as a trustworthy zero
    out = m.step()                                                                           # no ceiling: nothing could be understated against it, so work flows, flagged
    assert out["delivered"] == {"request_id": rid, "status": "accepted"} and status(root)["usage"]["baseline_unknown"] is True
    m.close()
    rid2 = ask(root, "b")
    m2 = mgr(root, Costly(), Clock(time.time()), limits=lim)
    out = m2.step()
    assert out["delivered"] is None and out["held"].get(rid2) == "usage_unknown" and status(root)["usage"]["unknown_hold"]["reason"].startswith("the session's earlier cost is not on record")
    assert resolve_usage_hold(root, "claude-code", "reconciled against the account usage page", by="robert") is True
    assert m2._day_totals()["baseline_unknown"] is False                                     # the resolution is on the record
    assert m2.step()["delivered"]["status"] == "accepted"
    m2.close()


def test_the_latest_total_is_read_from_the_end_of_an_oversized_log_never_from_its_start(root, tmp_path, monkeypatch):
    import core.agent_requests.connection as C
    p = Costly()
    m = mgr(root, p, Clock(1_791_800_000.0), limits=Limits(str(tmp_path / "limits")))
    _write_usage(root, "2026-10-11", [("c1", i, 10.0 + i, {}) for i in range(1, 12)])        # ends at 21.0
    size = os.path.getsize(os.path.join(root, "connection", "claude-code", "usage-2026-10-11.jsonl"))
    monkeypatch.setattr(C, "USAGE_LOG_MAX", size // 3)                                       # far smaller than the file: only the tail can be read
    assert m._last_cumulative(before_day=m._today()) == 21.0 and p.session_total_hint() == 21.0
    m.close()
