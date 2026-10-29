"""S1-H: the gate M instruments and checks, without a database. Each §9.4 check is shown to fail on the case it
exists for and on no facts at all (no vacuous pass), and to pass on a clean case."""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from harness_acceptance import checks as C, effect_sink, instruments as I  # noqa: E402

T = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def _post(url, body):
    req = urllib.request.Request(url + "/effects", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _get(url, op):
    with urllib.request.urlopen(f"{url}/effects/{op}", timeout=5) as r:
        return json.loads(r.read())


def test_the_effect_sink_deduplicates_by_operation_and_refuses_a_send_after_its_lease(tmp_path):
    srv, _ = effect_sink.serve(tmp_path / "sink")
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        op, later = str(uuid.uuid4()), (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        body = {"operation_id": op, "item_id": "i", "generation": 1, "lease_until": later, "digest": "d1"}
        assert _get(url, op)["answer"] == "absent"
        assert _post(url, body) == (200, {"answer": "committed"})
        assert _post(url, body) == (200, {"answer": "duplicate"})
        assert _post(url, {**body, "digest": "d2"}) == (200, {"answer": "operation_reused"})
        assert _get(url, op)["answer"] == "committed"
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        assert _post(url, {**body, "operation_id": str(uuid.uuid4()), "lease_until": past}) == \
            (200, {"answer": "lease_expired"})
        assert _post(url, {**body, "extra": 1})[0] == 400
        lines = [json.loads(x) for x in (tmp_path / "sink" / "effects.jsonl").read_text().splitlines()]
        assert [x["answer"] for x in lines] == ["committed", "duplicate", "operation_reused", "lease_expired"]
    finally:
        srv.shutdown()
    srv, _ = effect_sink.serve(tmp_path / "sink", lookup=False)                    # --no-lookup
    try:
        assert _get(f"http://127.0.0.1:{srv.server_address[1]}", op)["answer"] == "unsupported"
    finally:
        srv.shutdown()


def test_the_tripwire_is_first_on_path_logs_its_name_only_and_exits_97(tmp_path):
    log = tmp_path / "tripwire.jsonl"
    folder = I.install_tripwire(tmp_path / "bin", log)
    env = {"PATH": f"{folder}:/usr/bin:/bin"}
    for name in I.TRIPWIRE:
        out = subprocess.run([name, "-p", "a secret prompt"], env=env, capture_output=True)
        assert out.returncode == 97
    lines = I.tripwire_lines(log)
    assert [x["tool"] for x in lines] == list(I.TRIPWIRE)
    assert "secret" not in log.read_text()


def test_planted_values_are_synthetic_and_the_egress_commands_need_sudo():
    v = I.planted_values()
    assert set(v) == set(I.PLANTED) and len(set(v.values())) == len(v)
    assert all(x.startswith(("test-planted-", "/nonexistent/test-planted-")) for x in v.values())
    cmds = I.egress_commands()
    assert all(c.startswith("sudo pfctl") or "sudo pfctl" in c for c in cmds["load"] + cmds["read"])
    assert "api.anthropic.com" in cmds["rules"]


# ── DUP checks ──────────────────────────────────────────────────────────────────────────────────────────────────
def _line(op, answer, *, at=T, lease=T + timedelta(seconds=30), g=1):
    return {"operation_id": op, "item_id": "i", "generation": g, "lease_until": lease.isoformat(),
            "digest": "d", "answer": answer, "at": at.isoformat()}


def _ev(seq, kind, *, item="i1", g=1, host="mac-test-1", source="authority", body=None, at=T):
    return {"seq": seq, "kind": kind, "item_id": item, "generation": g, "host_id": host, "source": source,
            "environment": "dev", "body": body or {}, "at": at}


def test_no_check_passes_on_no_facts_when_its_scenario_must_produce_them():
    for fn, args in ((C.dup1, ([],)), (C.dup2, ([], [])), (C.dup3, ([],)), (C.dup4, ([],)), (C.dup6, ([],)),
                     (C.dup7, ([], [])), (C.dup8, ([], {"h"}))):
        ok, n, detail = fn(*args, need=1)
        assert (ok, n) == (False, 0) and "no_facts" in detail, fn


def test_dup1_and_dup2():
    assert C.dup1([_line("a", "committed"), _line("a", "duplicate")], need=1)[0]
    assert not C.dup1([_line("a", "committed"), _line("a", "committed")])[0]
    dispatched = [_ev(1, "effect.dispatched", body={"operation_id": "a"})]
    assert C.dup2([_line("a", "committed")], dispatched, need=1)[0]
    assert not C.dup2([_line("a", "committed")], [])[0]                               # no dispatched receipt
    late = _line("b", "committed", at=T + timedelta(seconds=40))
    assert not C.dup2([late], [_ev(1, "effect.dispatched", body={"operation_id": "b"})])[0]
    assert C.dup2([_line("c", "lease_expired", at=T + timedelta(seconds=40))], [])[0]
    assert not C.dup2([_line("c", "duplicate", at=T + timedelta(seconds=40))], [])[0]


def test_dup3_finds_overlapping_leases_of_two_hosts_on_one_item():
    a = [_ev(1, "item.claimed", host="h1", body={"lease_until": (T + timedelta(seconds=60)).isoformat()}),
         _ev(2, "item.claimed", host="h2", g=2, at=T + timedelta(seconds=60),
             body={"lease_until": (T + timedelta(seconds=120)).isoformat()})]
    assert C.dup3(a, need=1)[0]
    b = [a[0], {**a[1], "at": T + timedelta(seconds=30)}]
    ok, n, detail = C.dup3(b)
    assert not ok and detail["overlapping"]


def test_dup4_dup6_dup7_dup8():
    assert C.dup4([_ev(1, "result.accepted")], need=1)[0]
    assert not C.dup4([_ev(1, "result.accepted"), _ev(2, "result.accepted")])[0]
    unknown = [_ev(1, "item.unknown"), _ev(2, "decision.queued")]
    assert C.dup6(unknown + [_ev(3, "decision.resolved", source="owner"), _ev(4, "item.resumed", g=2)], need=1)[0]
    assert not C.dup6(unknown + [_ev(3, "item.resumed", g=2)])[0]                   # a generation before a decision
    raised = [_ev(1, "notice.raised", body={"key": "k1", "text": "the words", "rule": "stalled"})]
    assert C.dup7(raised, [{"text": "the words"}], need=1)[0]
    assert not C.dup7(raised * 2, [{"text": "the words"}])[0]
    assert not C.dup7(raised, [{"text": "the words"}, {"text": "again: the words"}])[0]
    assert C.dup8([_ev(1, "x.y", source="host")], {"mac-test-1"}, need=1)[0]
    assert not C.dup8([_ev(1, "x.y", source="host", host="prod-ec2-1")], {"mac-test-1"})[0]
    assert not C.dup8([{**_ev(1, "x.y"), "environment": "prod"}], {"mac-test-1"})[0]


def test_shown_sequence_and_never_running_while_unreachable():
    snaps = [{"n": i, "doc": {"items": [{"item_id": "x", "shown_as": s, "owner_reachability": r}]}}
             for i, (s, r) in enumerate((("running", "reachable"), ("held", "unreachable"), ("held", "unreachable"),
                                         ("lapsed", "unreachable")))]
    assert C.shown_sequence(snaps, "x") == ["running", "held", "lapsed"]
    assert C.never_running_while_unreachable(snaps, "x") == []
    bad = snaps + [{"n": 9, "doc": {"items": [{"item_id": "x", "shown_as": "running",
                                               "owner_reachability": "unreachable"}]}}]
    assert C.never_running_while_unreachable(bad, "x") == [9]


# ── CEN and LCH checks ─────────────────────────────────────────────────────────────────────────────────────────
def _agent(aid, parent, state, reason=None, ended=None, role="explore"):
    return {"agent_id": aid, "parent_id": parent, "state": state, "reason": reason, "ended_at": ended, "role": role}


def test_cen1_and_cen4():
    agents = [_agent("r", None, "running")] + [_agent(f"r/t{i}", "r", "done", "exit_0", "x") for i in range(5)] + \
        [_agent("r/long1", "r", "running", role="worker")]
    assert C.cen1(agents, "r", transient=5, long_ids={"long1"})[0]
    assert not C.cen1(agents[:-2], "r", transient=5, long_ids={"long1"})[0]
    assert not C.cen4(agents, "r")[0]                                                # long1 still running
    ended = agents[:-1] + [_agent("r/long1", "r", "unknown", "end_unobserved")]
    assert C.cen4(ended, "r")[0]
    assert not C.cen4(agents[:-1] + [_agent("r/long1", "r", "unknown", "capture_gap")], "r")[0]


def test_cen5_parents_resolve_and_no_content():
    spans = {"resourceSpans": [{"scopeSpans": [{"spans": [
        {"spanId": "a", "attributes": [{"key": "gen_ai.agent.id"}]},
        {"spanId": "b", "parentSpanId": "a", "attributes": []}]}]}]}
    assert C.cen5(spans, [])[0]
    dangling = json.loads(json.dumps(spans))
    dangling["resourceSpans"][0]["scopeSpans"][0]["spans"][1]["parentSpanId"] = "z"
    assert not C.cen5(dangling, [])[0]
    content = json.loads(json.dumps(spans))
    content["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"].append({"key": "gen_ai.prompt"})
    assert not C.cen5(content, [])[0]
    assert not C.cen5({}, [])[0]


def test_lch1_and_lch6(tmp_path):
    def denied(n):
        return n.endswith("_KEY") or n.endswith("_TOKEN")
    rec = {"task": "t", "env_names": ["PATH", "HOME"], "key": None, "policy": {"version": 1, "sha256": "s"}}
    api = {"task": "u", "env_names": ["PATH", "FAKEWORK_API_KEY"], "key": {"name": "FAKEWORK_API_KEY"},
           "policy": {"version": 1, "sha256": "s"}}
    starts = [{"pid": 1, "names": ["PATH"]}, {"pid": 2, "names": ["PATH", "FAKEWORK_API_KEY"]}]
    assert C.lch1([rec, api], starts, denied=denied, reviewed=set())[0]
    leak = [{"pid": 3, "names": ["PATH", "GH_TOKEN"]}]
    assert not C.lch1([rec, api], starts + leak, denied=denied, reviewed=set())[0]
    assert not C.lch1([rec], starts, denied=denied, reviewed=set())[0]                   # a key in a non-API child
    (tmp_path / "a.log").write_text("nothing here")
    assert C.lch6([rec, api], [tmp_path / "a.log"], ["test-planted-x"])[0]
    (tmp_path / "b.log").write_text("oops test-planted-x")
    ok, n, detail = C.lch6([rec, api], list(tmp_path.iterdir()), ["test-planted-x"])
    assert not ok and detail["planted_found"] and "test-planted-x" not in json.dumps(detail)
    other = {**api, "policy": {"version": 1, "sha256": "other"}}
    assert not C.lch6([rec, other], [tmp_path / "a.log"], ["v"])[0]                     # two policy digests


def test_the_controller_refuses_a_server_that_is_not_the_throwaway_one(tmp_path):
    from harness_acceptance import controller
    run = controller.Plan(tmp_path / "ev", server_dsn="host=db.example.com dbname=personal_agents user=x",
                          acceptance=tmp_path / "acc")
    with pytest.raises(SystemExit, match="throwaway"):
        run._create_database()
    os.makedirs(tmp_path / "ev2")
    (tmp_path / "ev2" / "x").write_text("an earlier run")
    with pytest.raises(SystemExit, match="not empty"):
        controller.Plan(tmp_path / "ev2", server_dsn="x")


def test_sf6_the_end_of_run_cleanup_signals_only_a_group_whose_recorded_identity_it_verifies(tmp_path):
    """S1-H review SF6: the controller's clean-up once killed any group whose leader's command line held "fakework".
    On the shared Mac a stage file's pid can lead someone else's process by then (another agent's pytest of
    test_fakework.py, say). Now it verifies as the runtime does: a stage still started, its group alive, and its
    recorded identity (group and start time) the one ps shows now. Every process here is this test's own."""
    import signal
    from types import SimpleNamespace
    from harness_acceptance import controller
    from core.mc_delegation_runner.runner import _identity

    def group():                                    # a group whose leader's command line names fakework
        return subprocess.Popen(["/bin/sh", "-c", "sleep 60; : fakework"], start_new_session=True)
    import time
    started = time.time() - 1                         # this run started just before its own children
    reused, ours, ended = group(), group(), group()
    try:
        stages = {"reused": {"stage": "started", "pgid": reused.pid, "identity": "1 Thu Jan  1 00:00:00 1970"},
                  "ours": {"stage": "started", "pgid": ours.pid, "identity": _identity(ours.pid)},
                  "ended": {"stage": "stopped", "pgid": ended.pid, "identity": _identity(ended.pid)}}
        for name, doc in stages.items():
            (tmp_path / f"{name}-g1.stage.json").write_text(json.dumps(doc))
        said = []
        run = SimpleNamespace(artifacts=tmp_path, log=lambda kind, what, **f: said.append(f["pgid"]),
                              started_wall=started)
        controller.Run._reap_orphans(run)
        assert ours.wait(timeout=10) == -signal.SIGKILL           # verified: stopped
        assert reused.poll() is None and ended.poll() is None     # never signalled
        assert said == [ours.pid]
    finally:
        for p in (reused, ours, ended):
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()


def test_p2_2_the_clean_up_is_bound_to_this_run_and_refuses_a_missing_or_foreign_identity(tmp_path, monkeypatch):
    """Codex part 2 P2-2. (1) Codex's own case: a stage record with no stage and an identity that is not ps's,
    while ps would show a fakework command line: never signalled (no signal is sent at all here). (2) A group whose
    recorded identity is exactly what ps shows, but which started before this run (an earlier run's fakework, or a
    pid reused by one): never signalled. (3) A run with no start on record signals nothing."""
    import signal
    import time
    from types import SimpleNamespace
    from harness_acceptance import controller
    from core.mc_delegation_runner.runner import _identity
    killed = []
    real_killpg = os.killpg

    def recording(pgid, sig):
        if sig == 0:
            return real_killpg(pgid, 0)                            # the liveness probe, as the runtime makes it
        killed.append(pgid)
    monkeypatch.setattr(controller.os, "killpg", recording)
    (tmp_path / "old.stage.json").write_text(json.dumps({"pgid": 43210, "identity": {"start": "old"}}))
    (tmp_path / "none-g1.stage.json").write_text(json.dumps({"stage": "started", "pgid": 43211}))
    earlier = subprocess.Popen(["/bin/sh", "-c", "sleep 60; : fakework"], start_new_session=True)
    try:
        time.sleep(1.1)                                            # lstart is whole seconds
        (tmp_path / "earlier-g1.stage.json").write_text(json.dumps(
            {"stage": "started", "pgid": earlier.pid, "identity": _identity(earlier.pid)}))
        run = SimpleNamespace(artifacts=tmp_path, log=lambda *a, **k: None, started_wall=time.time())
        controller.Run._reap_orphans(run)
        controller.Run._reap_orphans(SimpleNamespace(artifacts=tmp_path, log=lambda *a, **k: None))   # no start
        assert killed == [] and earlier.poll() is None
        assert controller._started_at(_identity(earlier.pid), earlier.pid) is not None
        assert controller._started_at(_identity(earlier.pid), earlier.pid + 1) is None   # another group's
    finally:
        real_killpg(earlier.pid, signal.SIGKILL)
        earlier.wait()
