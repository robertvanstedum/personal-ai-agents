"""The agent census (spec v0.5 §6.6, R6.6-a to R6.6-g, R6.7-g; CEN-1 to CEN-6, LCH-5, T18a/b/d/e at the host), its
OTLP/JSON file (R6.6-d) and the Claude Code hook (built, never installed).

Children are real fakework/1 processes run by the version 2 runtime against test_host_runtime's in-memory authority;
spawned subagents write hook-shaped census events, no real subagent runs. The authority's own side (each event
applied once into the events table, CEN-6 against its audit, retention never touching authority events) is
tests/mc_delegation_dispatcher/test_census_postgres.py. No provider, no network, no Keychain."""
import io
import os
import json
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_host_runtime as H  # noqa: E402

from core.mc_delegation_dispatcher import door  # noqa: E402
from core.mc_delegation_runner import census as C, claude_hook, otel, runner  # noqa: E402

ROOT = H.ROOT
mirror = H.mirror               # the fixtures of test_host_runtime: a git mirror, and every runtime reaped
_reap = H._reap
VALUE = "planted-census-value-3c9e"
PLANTED = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY",
           "TELEGRAM_BOT_TOKEN")


def at(seconds_ago=0.0, base=None):
    return ((base or datetime.now(timezone.utc)) - timedelta(seconds=seconds_ago)).isoformat()


def root_start(aid, *, source=C.RUNTIME_SOURCE, children="exposed", item=None):
    return C.start(aid, source=source, verified=True, children=children, vendor="minimoi", model="fakework/1",
                   role="build", item_id=item, start_path="runtime", route="test",
                   launch={"launch_id": str(uuid.uuid4()), "tool": "fakework", "profile": "unattended",
                           "route": "fakework", "binary": {"path": "/b/fakework.py", "sha256": "sha256:" + "a" * 64,
                                                           "pinned_by": "bundle"},
                           "policy_sha256": "sha256:" + "b" * 64, "env_names": ["HOME", "PATH"],
                           "removed": ["GH_TOKEN"], "governed": True, "names_truncated": 0},
                   transcript_ref=C.not_kept("fakework_keeps_no_session"))


def sub_start(aid, parent, role="explore"):
    return C.start(aid, parent_id=parent, source="fakework.spawn", verified=True, children="not_exposed",
                   vendor="minimoi", role=role)


def rows_of(folder, host="mac-test-1"):
    return {r["agent_id"]: r for r in C.CensusFile(folder).rows(host_id=host, environment="dev")}


def wait_for(check, timeout=30.0, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = check()
        if got:
            return got
        time.sleep(step)
    raise AssertionError("not reached in time")


def pass_in_thread(rt):
    box = {}
    t = threading.Thread(target=lambda: box.update(out=rt.pass_once()), daemon=True)
    t.start()
    return t, box


def drain(rt, fake, rounds=20):
    """Contact until the census holds nothing unsent: each batch applied once at the authority."""
    for _ in range(rounds):
        assert rt.contact()
        if not rt.census.unsent():
            return
    raise AssertionError("census still unsent")


# ── event shapes: names, ids, digests, paths, numbers and fixed words only ─────────────────────────────────────────
def test_events_hold_no_free_text_and_unknown_stays_unknown(tmp_path):
    f = C.CensusFile(tmp_path / "census")
    ev = f.append("census.start", root_start("runtime:x-g1"))
    assert C.check_event(ev) is ev and ev["body"]["usage"] is None
    good = root_start("a1")
    bad = [({**good, "prompt": VALUE}, "census_body_invalid"),                         # no field outside the shape
           ({**good, "role": "write me a poem"}, "census_body_invalid"),                # a word, never a sentence
           ({**good, "state": "unknown"}, "census_unknown_without_reason"),
           ({**good, "usage": C.usage(cost=1.0)}, "census_usage_unit_missing"),
           ({**good, "usage": C.usage(input_tokens=True)}, "census_usage_invalid"),
           ({**good, "usage": C.usage(output_tokens=-1)}, "census_usage_invalid"),
           ({**good, "launch": {**good["launch"], "profile": "desktop_app"}}, "census_launch_desktop_governed"),
           ({**good, "transcript_ref": {"kept": False}}, "census_transcript_invalid"),
           ({**good, "parent_id": "a1"}, "census_body_invalid")]
    for body, code in bad:
        with pytest.raises(C.CensusRefused, match=code):
            f.append("census.start", body)
    # The 8 KiB limit never stops work (S1): names are kept within NAMES_BYTES_MAX and the rest counted, and an event
    # still too large is summarised by field, never refused.
    many = {"launch_id": None, "tool": "fakework", "profile": "unattended", "route": "fakework",
            "binary": {"path": "/b/" + "p" * 900, "sha256": None, "pinned_by": "bundle"},
            "env_names": ["PATH"], "removed": ["N" + "x" * 60 + "%03d" % i for i in range(128)]}
    summary = C.launch_summary(many)
    assert summary["names_truncated"] > 100 and len(json.dumps(summary["removed"])) <= C.NAMES_BYTES_MAX
    fits = f.append("census.start", {**good, "launch": summary})
    assert fits["body"]["launch"]["names_truncated"] == summary["names_truncated"]
    huge = {**good, "launch": {**summary, "binary": {**summary["binary"], "path": "/b/" + "\u00e9" * 1000}},
            "transcript_ref": C.kept("s-0123456789", "/t/" + "\u00e9" * 1000)}
    got = f.append("census.start", huge)["body"]
    assert got["launch"]["binary"]["path"] is None and got["launch"]["binary"]["pinned_by"] == "bundle"
    assert len(json.dumps(got)) <= C.EVENT_MAX_BYTES                     # summarised as far as needed, never refused
    assert got["launch"]["names_truncated"] >= summary["names_truncated"] + 2
    odd = f.append("census.start", {**good, "launch": {**good["launch"], "env_names": ["A=" + VALUE],
                                                       "removed": ["N%d" % i for i in range(129)]}})["body"]["launch"]
    assert odd["env_names"] == [] and len(odd["removed"]) == 128 and odd["names_truncated"] == 2   # a value never kept
    assert VALUE not in json.dumps(odd)
    with pytest.raises(C.CensusRefused):
        C.check_event({"event_id": "e-1", "at": at(), "kind": "census.start", "body": good})
    assert len(C.CensusFile(tmp_path / "census").unsent()) == 4                          # nothing else was written
    assert C.CensusFile(tmp_path / "census").acknowledge([{"event_id": ev["event_id"], "state": "applied"}]) == 1
    assert len(C.CensusFile(tmp_path / "census").unsent()) == 3                         # a fresh reader: answered


# ── CEN-1 / T18a, CEN-4, CEN-5 with a real fakework root ────────────────────────────────────────────────────────────
def test_cen1_t18a_a_root_with_five_transient_and_one_long_subagent_and_cen4_after_it_ends(tmp_path, mirror):
    fake = H.FakeAuthority(mirror[1])
    rt = H.make_rt(tmp_path, mirror[0], fake)
    rt.otlp_every_s = 0
    rid = fake.add(H.program(*[f"spawn t{i} role=explore hold_s=1" for i in range(5)],
                             "spawn long1 role=worker hold_s=600 beat_s=1 long", "checkpoint s1 hold_s=4", "result ok"))
    root = runner.census_agent_id(f"{rid}-g1")
    t, box = pass_in_thread(rt)
    folder = rt.census.root

    def six():
        rows = rows_of(folder)
        kids = [r for r in rows.values() if r["parent_id"] == root]
        live = rows.get(root, {}).get("state") == "running"
        return rows if live and len(kids) == 6 and sum(r["state"] == "done" for r in kids) == 5 else None
    during = wait_for(six)
    kids = {a: r for a, r in during.items() if r["parent_id"] == root}
    assert {r["role"] for a, r in kids.items() if a != f"{root}/long1"} == {"explore"}
    assert during[f"{root}/long1"]["state"] == "running" and during[f"{root}/long1"]["role"] == "worker"
    assert all(r["ended_at"] for a, r in kids.items() if a != f"{root}/long1")
    assert all(r["item_id"] == rid and r["start_path"] == "runtime" and r["route"] == "test" for r in kids.values())
    t.join(60)
    assert box["out"]["state"] == "completed", box
    after = rows_of(folder)
    assert len(after) == 7                                                      # CEN-2: each agent once
    assert (after[root]["state"], after[root]["reason"]) == ("done", "exit_0") and after[root]["ended_at"]
    assert (after[f"{root}/long1"]["state"], after[f"{root}/long1"]["reason"], after[f"{root}/long1"]["ended_at"]) \
        == ("unknown", "end_unobserved", None)                                  # CEN-4: never left running
    assert not [r for r in after.values() if r["state"] == "running"]
    drain(rt, fake)                                                             # every event reaches the authority once
    sent = [json.loads(x)["event_id"] for x in Path(folder).glob("*.jsonl") for x in x.read_text().splitlines()
            if json.loads(x)["type"] == "event"]
    assert sorted(sent) == sorted(fake.census) and len(set(sent)) == len(sent)
    rt.write_state(force=True)                                                  # the host's own view and its OTLP file
    state = json.loads((tmp_path / "artifacts" / "host-state.json").read_text())
    assert {r["agent_id"] for r in state["agents"]} == set(after) and state["census"]["unsent"] == 0
    traces = json.loads((folder / "otlp" / "traces.json").read_text())
    spans = traces["resourceSpans"][0]["scopeSpans"][0]["spans"]
    ids = {s["spanId"] for s in spans}
    assert len(spans) == 7 and all(s.get("parentSpanId", next(iter(ids))) in ids for s in spans)      # CEN-5
    assert len({s["traceId"] for s in spans}) == 1                              # one trace per work item


def test_cen4_t18d_cancelling_the_root_mid_run_leaves_no_child_running(tmp_path, mirror):
    fake = H.FakeAuthority(mirror[1])
    rt = H.make_rt(tmp_path, mirror[0], fake)
    flag = {"on": False}
    fake.cancel_requested = lambda request_id, generation: flag["on"]
    rid = fake.add(H.program("spawn a role=explore hold_s=0", "spawn b role=explore hold_s=0",
                             "spawn long1 role=worker hold_s=600 beat_s=1 long", "checkpoint s1 hold_s=30", "result ok"))
    root = runner.census_agent_id(f"{rid}-g1")
    t, box = pass_in_thread(rt)
    wait_for(lambda: rows_of(rt.census.root).get(f"{root}/long1", {}).get("state") == "running")
    flag["on"] = True
    t.join(60)
    assert box["out"]["state"] == "cancelled", box
    rows = rows_of(rt.census.root)
    assert (rows[root]["state"], rows[root]["reason"]) == ("done", "stopped:cancelled") and rows[root]["ended_at"]
    assert [rows[f"{root}/{x}"]["state"] for x in ("a", "b")] == ["done", "done"]
    assert (rows[f"{root}/long1"]["state"], rows[f"{root}/long1"]["reason"]) == ("unknown", "end_unobserved")
    assert rows[f"{root}/long1"]["ended_at"] is None
    assert not [r for r in rows.values() if r["state"] == "running"]


def test_t18e_fifty_transient_spawns_within_ten_seconds_are_all_recorded_with_no_limit(tmp_path, mirror):
    fake = H.FakeAuthority(mirror[1])
    rt = H.make_rt(tmp_path, mirror[0], fake)
    rid = fake.add(H.program(*[f"spawn s{i} role=swarm hold_s=0" for i in range(50)], "result ok"))
    began = time.monotonic()
    out = H.run_pass(rt)
    assert out["state"] == "completed" and time.monotonic() - began < 10, out
    root = runner.census_agent_id(f"{rid}-g1")
    rows = rows_of(rt.census.root)
    kids = [r for r in rows.values() if r["parent_id"] == root]
    assert len(kids) == 50 and all(r["state"] == "done" and r["ended_at"] for r in kids)
    drain(rt, fake)
    assert len([e for e in fake.census.values() if e["body"].get("parent_id") == root]) == 50
    assert fake.items[rid]["result"]["usage"]["amount"] == 0.0             # the spend gate saw no census at all


# ── CEN-2 / T18b: rebuilt from file, each agent once, capture gaps ─────────────────────────────────────────────────
def test_cen2_t18b_rebuilt_from_file_each_agent_once_and_a_capture_gap_until_the_next_event(tmp_path):
    folder = tmp_path / "census"
    f = C.CensusFile(folder)
    f.append("census.start", root_start("runtime:r-g1"), at=at(60))
    f.append("census.start", sub_start("runtime:r-g1/long1", "runtime:r-g1"), at=at(59))
    f.append("census.update", C.update("runtime:r-g1/long1", source="fakework.spawn", state="running"), at=at(50))
    conn = C.start("sess-1234567890", source="connection.start", verified=True, children="not_exposed",
                   vendor="anthropic", role="session", start_path="connection", route="subscription")
    f.append("census.start", conn, at=at(55))
    f.append("census.gap", C.gap("restart", at(10), at(50)), at=at(10))            # the runtime restarted
    again = rows_of(folder)                                                            # a new process: from the file
    assert sorted(again) == ["runtime:r-g1", "runtime:r-g1/long1", "sess-1234567890"]
    assert (again["runtime:r-g1/long1"]["state"], again["runtime:r-g1/long1"]["reason"]) == ("unknown", "capture_gap")
    assert again["runtime:r-g1"]["reason"] == "capture_gap"
    assert again["sess-1234567890"]["state"] == "running"     # its own writer was never interrupted by the restart
    f.append("census.update", C.update("runtime:r-g1/long1", source="fakework.spawn", state="running"), at=at(1))
    assert rows_of(folder)["runtime:r-g1/long1"]["state"] == "running"               # its next event
    f.append("census.gap", C.gap("wake", at(0.5), at(5)), at=at(0.5))                  # the whole host slept
    assert rows_of(folder)["sess-1234567890"]["reason"] == "capture_gap"
    f.append("census.end", C.end("runtime:r-g1", source=C.RUNTIME_SOURCE, state="done", reason="stopped:x"), at=at(0))
    assert rows_of(folder)["runtime:r-g1/long1"]["reason"] == "end_unobserved"       # or until its parent ends


def test_cen2_t18b_a_restart_ends_the_orphaned_root_it_stops_and_its_subagent_is_end_unobserved(tmp_path, mirror):
    fake = H.FakeAuthority(mirror[1])
    rt = H.make_rt(tmp_path, mirror[0], fake)
    task = f"{uuid.uuid4()}-g1"
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        art = tmp_path / "artifacts"
        art.mkdir(parents=True, exist_ok=True)
        (art / f"{task}.claim.json").write_text(json.dumps({"request_id": task[:-3], "generation": 1, "task": task}))
        (art / f"{task}.stage.json").write_text(json.dumps({"stage": "started", "pgid": proc.pid,
                                                            "identity": runner._identity(proc.pid)}))
        rid = runner.census_agent_id(task)
        rt.census.append("census.start", root_start(rid), at=at(30))
        rt.census.append("census.start", sub_start(rid + "/long1", rid), at=at(29))
        H.RUNTIMES.remove(rt)
        rt2 = H.make_rt(tmp_path, mirror[0], fake)                        # a new process: nothing in memory
        assert rt2.freeze_left_children() == {task: "stopped"}
        proc.wait(10)
        rows = rows_of(art / "census")
        assert sorted(rows) == [rid, rid + "/long1"]                                   # each agent once
        assert (rows[rid]["state"], rows[rid]["reason"]) == ("done", "stopped:orphan_after_restart")
        assert (rows[rid + "/long1"]["state"], rows[rid + "/long1"]["reason"]) == ("unknown", "end_unobserved")
        gaps = [json.loads(x) for p in (art / "census").glob("*.jsonl") for x in p.read_text().splitlines()
                if '"census.gap"' in x]
        assert [g["body"]["cause"] for g in gaps] == ["restart"]           # the new runtime's start: a capture gap
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


# ── CEN-3: never running while the host is unreachable or the process frozen ────────────────────────────────────────
def test_cen3_no_agent_shows_running_while_its_host_is_unreachable_or_its_process_frozen():
    f = C.Fold()
    now = datetime.now(timezone.utc)
    for ev in ({"kind": "census.start", "body": root_start("r1"), "at": at(30, now)},
               {"kind": "census.start", "body": sub_start("r1/s", "r1"), "at": at(29, now)},
               {"kind": "census.start", "body": C.start("other", source="claude.hook", verified=True,
                                                        children="not_exposed"), "at": at(28, now)}):
        f.apply({"event_id": str(uuid.uuid4()), **ev}, "mac-1")
    for reach, reason in (("unreachable", "host_unreachable"), ("unknown", "host_unreachable"),
                          ("stopped", "host_stopped")):
        rows = f.rows(reachability={"mac-1": reach}, environment="dev")
        assert {(r["state"], r["reason"]) for r in rows} == {("unknown", reason)}
    f.apply({"event_id": str(uuid.uuid4()), "kind": "census.update", "at": at(5, now),
             "body": C.update("r1", source=C.RUNTIME_SOURCE, state="idle", reason="frozen")}, "mac-1")
    rows = {r["agent_id"]: r for r in f.rows(reachability={"mac-1": "reachable"}, environment="dev")}
    assert [(rows[a]["state"], rows[a]["reason"]) for a in ("r1", "r1/s")] == [("idle", "frozen")] * 2
    assert rows["other"]["state"] == "running"                       # outside the frozen group: as it reported
    assert C.host_reachability(now - timedelta(seconds=91), "online", now, 90) == "unreachable"
    assert C.host_reachability(now - timedelta(seconds=10), "stopping", now, 90) == "stopping"
    assert C.host_reachability(None, "online", now, 90) == "unknown"


def test_cen3_a_wake_freezes_root_and_subagent_then_reconcile_thaws_and_the_next_beat_runs(tmp_path, mirror):
    fake = H.FakeAuthority(mirror[1])
    rt = H.make_rt(tmp_path, mirror[0], fake)
    flag = {"on": False}
    fake.cancel_requested = lambda request_id, generation: flag["on"]
    rid = fake.add(H.program("spawn long1 role=worker hold_s=600 beat_s=1 long", "checkpoint s1 hold_s=30",
                             "result ok"))
    root = runner.census_agent_id(f"{rid}-g1")
    t, box = pass_in_thread(rt)
    wait_for(lambda: rows_of(rt.census.root).get(f"{root}/long1", {}).get("state") == "running")
    with rt._lock:
        child = rt.child
    with child.lock:                                   # the wake and its freeze, before the pass can reconcile
        rt.on_wake({"gap_s": 120.0, "wall_minus_mono_s": 0.0, "hint": None})
        frozen = rows_of(rt.census.root)
    assert [(frozen[a]["state"], frozen[a]["reason"]) for a in (root, f"{root}/long1")] == [("idle", "frozen")] * 2
    assert not [r for r in frozen.values() if r["state"] == "running"]
    thawed = wait_for(lambda: (lambda r: r if r[root]["state"] == "running" else None)(rows_of(rt.census.root)))
    assert thawed[f"{root}/long1"]["state"] in ("unknown", "running")       # capture_gap until its next beat
    wait_for(lambda: rows_of(rt.census.root)[f"{root}/long1"]["state"] == "running")
    assert [r["answer"] for r in rt.journal(f"{rid}-g1").notes("reconcile") for r in [r["fields"]]][0]["answer"] \
        == "continue"
    flag["on"] = True
    t.join(60)


# ── CEN-6 at the host: seeing grants nothing ───────────────────────────────────────────────────────────────────────
def test_cen6_census_events_change_no_item_and_never_cost_the_contact(tmp_path, mirror):
    fake = H.FakeAuthority(mirror[1])
    rt = H.make_rt(tmp_path, mirror[0], fake)
    rid = fake.add(H.program("result ok"))
    before = json.dumps(fake.items, sort_keys=True)
    for i in range(30):
        rt.census.append("census.end", C.end(f"x{i}", source="claude.hook", state="failed", reason="exit_1"))
    drain(rt, fake)
    assert json.dumps(fake.items, sort_keys=True) == before and fake.queue == [rid]
    assert [c[0] for c in fake.log].count("claim") == 0                 # nothing was claimed, admitted or stopped


# ── batches: within the door's limit, byte-faithful across a lost connection, never costing the contact ───────────
class Door:
    """A heartbeat over the door's own rules: the body as HttpDispatcherV2 would send it, within BODY_MAX."""

    def __init__(self, *, lose=0, refuse=None):
        self.lose, self.refuse, self.sent, self.applied = lose, refuse, [], {}

    def heartbeat(self, runner_id, state, capabilities, facts, census=None):
        body = {"op": "heartbeat", "protocol": 2, "runner_id": runner_id, "state": state,
                "capabilities": capabilities, "facts": facts, **({} if census is None else {"census": census})}
        assert len(json.dumps(body).encode()) <= door.BODY_MAX
        self.sent.append(None if census is None else json.dumps(census, sort_keys=True))
        if census and self.lose:
            self.lose -= 1
            raise ConnectionError("lost")                          # a transport failure: nothing was applied
        if census and self.refuse and any(e["event_id"] == self.refuse for e in census):
            raise ValueError("runner_request_invalid")             # the door refused the whole body
        if census is None:
            return {"state": "recorded"}
        for e in census:
            self.applied[e["event_id"]] = self.applied.get(e["event_id"], 0) + 1
        return {"state": "recorded", "census": {"state": "applied",
                                                "events": [{"event_id": e["event_id"], "state": "applied"}
                                                           for e in census]}}


def test_batches_stay_within_the_door_limit_and_a_lost_one_is_resent_byte_for_byte(tmp_path, mirror):
    d = Door(lose=1)
    rt = H.make_rt(tmp_path, mirror[0], d)
    big = root_start("r0")
    big["launch"]["removed"] = sorted("NAME_%03d_" % i + "X" * 20 for i in range(100))
    for i in range(450):
        rt.census.append("census.start", {**big, "agent_id": f"r{i}"})
    assert not rt.contact()                                           # lost: offline, every event still unsent
    first = d.sent[-1]
    assert rt.contact() and d.sent[-1] == first                       # the same events, exactly as written
    while rt.census.unsent():
        assert rt.contact()
    sizes = [len(json.loads(s)) for s in d.sent if s]
    assert max(sizes) <= C.BATCH_MAX_EVENTS and all(len(s.encode()) <= C.BATCH_MAX_BYTES + 1024 for s in d.sent if s)
    assert len(d.applied) == 450 and set(d.applied.values()) == {1}   # each applied once
    assert C.BATCH_MAX_BYTES + 16 * 1024 < door.BODY_MAX              # room for the facts and capabilities


def test_a_heartbeat_refused_for_its_census_is_sent_again_without_it_and_the_cause_is_set_aside(tmp_path, mirror):
    rt = H.make_rt(tmp_path, mirror[0], Door())
    ids = [rt.census.append("census.start", root_start(f"r{i}"))["event_id"] for i in range(8)]
    rt.dispatcher.refuse = ids[5]
    for _ in range(14):
        assert rt.contact()                                           # the contact always counts (CEN-6)
    sent = [s for s in rt.dispatcher.sent if s is not None]
    assert rt.dispatcher.sent.count(None) >= 1 and not rt.census.unsent()
    assert sorted(rt.dispatcher.applied) == sorted(ids[:5] + ids[6:])
    acks = [json.loads(x) for p in rt.census.root.glob("*.jsonl") for x in p.read_text().splitlines()
            if '"ack"' in x and ids[5] in x]
    assert acks[0]["answer"] == {"state": "rejected", "code": "authority_error:runner_request_invalid"}
    assert len(sent) >= 2


def test_a_refusal_that_holds_without_the_census_leaves_the_census_untouched(tmp_path, mirror):
    class Refusing(Door):
        def heartbeat(self, *a, census=None):
            self.sent.append(census)
            raise ValueError("runner_host_mismatch")              # the facts, not the census
    rt = H.make_rt(tmp_path, mirror[0], Refusing())
    rt.census.append("census.start", root_start("r1"))
    for _ in range(3):
        with pytest.raises(runner._AuthorityError):
            rt.contact()
    assert len(rt.census.unsent()) == 1 and rt._census_limit is None
    assert not [x for p in rt.census.root.glob("*.jsonl") for x in p.read_text().splitlines() if '"ack"' in x]


def test_a_torn_line_is_sealed_and_shown_never_sent_and_never_swallows_the_next(tmp_path):
    f = C.CensusFile(tmp_path / "census")
    a = f.append("census.start", root_start("r1"))
    day = next((tmp_path / "census").glob("*.jsonl"))
    with open(day, "ab") as fh:
        fh.write(b'{"v":1,"type":"event","event_id":"torn')            # a crash mid-write
    b = f.append("census.start", root_start("r2"))
    g = C.CensusFile(tmp_path / "census")
    assert [e["event_id"] for e in g.unsent()] == [a["event_id"], b["event_id"]] and g.damaged == {day.name: 1}


# ── retention (N3) on the host ─────────────────────────────────────────────────────────────────────────────────────
def test_retention_drops_only_whole_ended_trees_past_30_days_never_unsent_or_open_or_the_latest_gap(tmp_path):
    now = datetime.now(timezone.utc)
    f = C.CensusFile(tmp_path / "census")
    old = 45 * 86400
    f.append("census.start", root_start("done-tree"), at=at(old + 60, now))
    f.append("census.start", sub_start("done-tree/s", "done-tree"), at=at(old + 50, now))
    f.append("census.end", C.end("done-tree", source=C.RUNTIME_SOURCE, state="done", reason="exit_0"), at=at(old, now))
    f.append("census.start", root_start("open-tree"), at=at(40 * 86400, now))       # never ended: kept whole
    f.append("census.gap", C.gap("restart", at(38 * 86400, now)), at=at(38 * 86400, now))   # the latest gap: kept
    f.append("census.start", root_start("unsent-tree"), at=at(36 * 86400, now))
    f.append("census.end", C.end("unsent-tree", source=C.RUNTIME_SOURCE, state="done", reason="exit_0"),
             at=at(36 * 86400 - 1, now))
    sent = [e for e in f.unsent() if e["body"].get("agent_id") not in ("unsent-tree",)]
    f.acknowledge([{"event_id": e["event_id"], "state": "applied"} for e in sent])
    gone = f.retain(now=now)
    assert gone == sorted({at(old + d, now)[:10] + ".jsonl" for d in (60, 50, 0)})        # its days, whole
    left = rows_of(tmp_path / "census")
    assert sorted(left) == ["open-tree", "unsent-tree"]
    assert any('"census.gap"' in p.read_text() for p in (tmp_path / "census").glob("*.jsonl"))
    assert C.RETENTION_DAYS == 30


# ── no value, secret or content in any census or OTLP line ─────────────────────────────────────────────────────────
def test_no_environment_value_secret_or_content_reaches_the_census_or_the_otlp_file(tmp_path, mirror, monkeypatch):
    for name in PLANTED:
        monkeypatch.setenv(name, VALUE)
    fake = H.FakeAuthority(mirror[1])
    rt = H.make_rt(tmp_path, mirror[0], fake)
    rt.otlp_every_s = 0
    fake.add(H.program("spawn s1 role=explore hold_s=0", "report_usage 0.5", "result ok"))
    assert H.run_pass(rt)["state"] == "completed"
    folder = str(rt.census.root)
    for name in ("SessionStart", "PreToolUse", "Stop", "SubagentStart"):
        payload = {"hook_event_name": name, "session_id": "sess-0123456789", "transcript_path": "/t/s.jsonl",
                   "prompt": VALUE, "tool_input": {"command": VALUE}, "message": VALUE, "agent_id": "sub-1",
                   "agent_type": "general-purpose", "cwd": "/" + VALUE}
        assert claude_hook.main(["--census", folder, "--start-path", "fallback", "--route", "subscription"],
                                stdin=io.StringIO(json.dumps(payload))) == 0
    drain(rt, fake)
    rt.write_state(force=True)
    files = [p for p in (tmp_path / "artifacts").rglob("*") if p.is_file()]
    assert any(p.name == "traces.json" for p in files) and any(p.name == "host-state.json" for p in files)
    assert [str(p) for p in files if VALUE.encode() in p.read_bytes()] == []
    assert VALUE not in json.dumps(fake.census)
    rows = rows_of(rt.census.root)
    assert rows["sess-0123456789"]["transcript_ref"] == {"kept": True, "session_id": "sess-0123456789",
                                                         "path": "/t/s.jsonl"}
    assert "sub-1" not in rows                                         # subagent hooks are off (N4)


# ── LCH-5 for the runtime's roots; the connections' and F1's are in tests/agent_requests/test_census_rows.py ────────
def test_lch5_every_runtime_root_has_start_path_route_launch_and_a_transcript_ref_or_not_kept(tmp_path, mirror):
    fake = H.FakeAuthority(mirror[1])
    rt = H.make_rt(tmp_path, mirror[0], fake)
    fake.add(H.program("checkpoint s1", "report_usage 0.25", "result ok"))
    assert H.run_pass(rt)["state"] == "completed"
    (root,) = [r for r in rows_of(rt.census.root).values() if r["parent_id"] is None]
    assert (root["start_path"], root["route"], root["source"], root["verified"]) == ("runtime", "test",
                                                                                     C.RUNTIME_SOURCE, True)
    assert root["launch"]["profile"] == "unattended" and root["launch"]["governed"] is True
    assert root["launch"]["binary"]["sha256"].startswith("sha256:") and root["launch"]["policy_sha256"]
    assert root["transcript_ref"] == {"kept": False, "reason": "fakework_keeps_no_session"}
    assert root["usage"] == {"input_tokens": None, "output_tokens": None, "cost": 0.25, "unit": "test_units",
                             "complete": True}
    assert root["children"] == "exposed" and root["vendor"] == "minimoi"


def test_runtime_children_of_real_vendors_say_their_subagents_are_not_exposed_never_none():
    for actor, (vendor, _model, children, transcript) in runner._CENSUS_ACTOR.items():
        assert children == ("exposed" if actor == "fakework" else "not_exposed")
        assert transcript["kept"] is False and transcript["reason"]
    assert runner._CENSUS_ACTOR["codex"][3]["reason"] == "codex_exec_ephemeral_unverified"


# ── the Claude Code hook: built, never installed; subagent events off until confirmed (N4) ─────────────────────────
def test_the_claude_hook_writes_session_rows_only_and_never_fails_a_turn(tmp_path):
    assert claude_hook.SUBAGENT_HOOKS_CONFIRMED is False
    kinds = [k for name in ("SessionStart", "PreToolUse", "Stop", "SubagentStart", "SubagentStop", "UserPromptSubmit")
             for k, _ in claude_hook.events({"hook_event_name": name, "session_id": "sess-0123456789",
                                             "agent_id": "sub-1"}, start_path="fallback", route="subscription")]
    assert kinds == ["census.start", "census.update", "census.update"]
    (_, body), = claude_hook.events({"hook_event_name": "SessionStart", "session_id": "sess-0123456789"},
                                    start_path="fallback", route="subscription")
    assert (body["children"], body["verified"], body["start_path"]) == ("not_exposed", True, "fallback")
    on = C.hook_events({"hook_event_name": "SubagentStart", "session_id": "s-0123456789", "agent_id": "a-1",
                        "agent_type": "Explore"}, source="claude.hook", verified=True, subagents=True,
                       vendor="anthropic")
    assert on[0][1]["verified"] is False and on[0][1]["parent_id"] == "s-0123456789"   # once on: still unverified
    for stdin in ("not json", json.dumps({"hook_event_name": "SessionStart"}), "x" * (70 * 1024)):
        assert claude_hook.main(["--census", str(tmp_path / "c"), "--start-path", "fallback", "--route",
                                 "subscription"], stdin=io.StringIO(stdin)) == 0
    assert claude_hook.main(["--bogus"], stdin=io.StringIO("{}")) == 0
    assert not list((tmp_path / "c").glob("*.jsonl")) if (tmp_path / "c").exists() else True
    # Never installed: no settings file in the repository names it.
    named = [p for p in ROOT.rglob("*.json") if ".worktrees" not in p.parts and "node_modules" not in p.parts
             and p.stat().st_size < 1_000_000 and "claude_hook.py" in p.read_text(errors="ignore")]
    assert named == []


# ── the OTLP/JSON file (R6.6-d, CEN-5) and its mapping ─────────────────────────────────────────────────────────────
def test_cen5_every_span_parent_resolves_and_no_content_attribute_is_written(tmp_path):
    f = C.Fold()
    item = str(uuid.uuid4())
    events = [("census.start", root_start("r1", item=item)), ("census.start", sub_start("r1/a", "r1")),
              ("census.start", sub_start("x/b", "never-seen")),                       # a parent never recorded
              ("census.update", C.update("r1", source=C.RUNTIME_SOURCE,
                                         usage=C.usage(input_tokens=1200, output_tokens=300, cost=0.4, unit="usd",
                                                       complete=False)))]
    for i, (k, b) in enumerate(events):
        f.apply({"event_id": str(uuid.uuid4()), "kind": k, "body": b, "at": at(10 - i)}, "mac-1")
    rows = f.rows(reachability={"mac-1": "reachable"}, environment="dev")
    assert {r["agent_id"]: r["reason"] for r in rows}["never-seen"] == "not_captured"
    out = otel.write(tmp_path / "otlp", rows, host_id="mac-1", environment="dev")
    doc = json.loads(Path(out["traces"]).read_text())
    spans = doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
    by = {s["spanId"]: s for s in spans}
    assert len(spans) == len(rows) == 4
    assert all(s["parentSpanId"] in by for s in spans if "parentSpanId" in s)
    keys = {a["key"] for s in spans for a in s["attributes"]}
    assert keys <= set(otel.GEN_AI_ATTRIBUTES + otel.MINIMOI_ATTRIBUTES) and not keys & set(otel.CONTENT_ATTRIBUTES)
    r1 = {a["key"]: a["value"] for a in by[otel.span_id("r1")]["attributes"]}
    assert r1["gen_ai.operation.name"] == {"stringValue": "invoke_agent"} and r1["gen_ai.usage.input_tokens"] == \
        {"intValue": "1200"} and r1["minimoi.cost.unit"] == {"stringValue": "usd"} and "gen_ai.provider.name" not in r1
    assert by[otel.span_id("r1/a")]["traceId"] == by[otel.span_id("r1")]["traceId"] == otel.trace_id("item:" + item)
    sub = {a["key"] for a in by[otel.span_id("r1/a")]["attributes"]}
    assert "gen_ai.usage.input_tokens" not in sub                   # unknown is left out, never written as zero
    metrics = json.loads(Path(out["metrics"]).read_text())["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    tokens, agents = metrics
    assert tokens["name"] == "gen_ai.client.token.usage" and len(tokens["histogram"]["dataPoints"]) == 2
    assert {p["attributes"][-1]["value"]["stringValue"]: p["sum"] for p in tokens["histogram"]["dataPoints"]} == \
        {"input": 1200.0, "output": 300.0}
    assert {p["attributes"][0]["value"]["stringValue"]: p["asInt"] for p in agents["gauge"]["dataPoints"]} == \
        {"running": "3", "unknown": "1"}
    assert doc["resourceSpans"][0]["schemaUrl"] == otel.SCHEMA_URL


def test_the_otlp_names_are_pinned_to_one_semantic_conventions_version_in_the_mapping_file():
    text = (ROOT / "core" / "mc_delegation_dispatcher" / "mappings" / "otel_genai.md").read_text()
    assert f"**Pinned:** OpenTelemetry semantic conventions {otel.SEMCONV_VERSION}" in text
    assert otel.SCHEMA_URL in text
    import re
    named = set(re.findall(r"`((?:gen_ai|minimoi|service)\.[a-z_.]+)`", text))
    written = set(otel.GEN_AI_ATTRIBUTES + otel.MINIMOI_ATTRIBUTES + otel.RESOURCE_ATTRIBUTES + otel.METRICS)
    assert written <= named and set(otel.CONTENT_ATTRIBUTES) <= named
    assert named <= written | set(otel.CONTENT_ATTRIBUTES) | {otel.SCOPE["name"]}       # and the scope's own name
    try:                                                       # checked offline against the installed conventions
        from opentelemetry.semconv import schemas
        from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as G
        from opentelemetry.semconv._incubating.metrics import gen_ai_metrics as M
        from opentelemetry.semconv.attributes import service_attributes as S
    except ImportError:
        pytest.skip("opentelemetry-semantic-conventions is not installed here")
    known = {v for k, v in vars(G).items() if k.startswith("GEN_AI_") and isinstance(v, str)}
    assert {n for n in written | set(otel.CONTENT_ATTRIBUTES) if n.startswith("gen_ai.")} - \
        {"gen_ai.client.token.usage"} <= known
    assert M.GEN_AI_CLIENT_TOKEN_USAGE == "gen_ai.client.token.usage" and S.SERVICE_NAME == "service.name"
    assert G.GenAiOperationNameValues.INVOKE_AGENT.value == "invoke_agent"
    assert {G.GenAiProviderNameValues.ANTHROPIC.value, G.GenAiProviderNameValues.OPENAI.value,
            G.GenAiProviderNameValues.X_AI.value} == set(otel.PROVIDERS.values())
    assert schemas.Schemas.V1_37_0.value == otel.SCHEMA_URL


def test_the_exporter_is_off_by_default_and_reaches_no_network_in_tests(tmp_path, mirror):
    rt = H.make_rt(tmp_path, mirror[0], H.FakeAuthority(mirror[1]))
    assert not [k for k in vars(rt) if "export" in k.lower()]          # the runtime builds no exporter
    sent = []

    class Answer:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        sent.append((req.full_url, json.loads(req.data)))
        return Answer()
    ex = otel.OtlpHttpExporter("https://collector.example.test", opener=opener)
    assert ex.export([], host_id="h", environment="dev") == {"traces": 200, "metrics": 200}
    assert [u for u, _ in sent] == ["https://collector.example.test/v1/traces",
                                    "https://collector.example.test/v1/metrics"]
    with pytest.raises(ValueError):
        otel.OtlpHttpExporter("http://collector.example.test")


# ── fix round: B1 (a frozen writer never stalls the host), S1 (the census never gates work), S2, S3, N5 ─────────────
def _hold_lock(folder):
    """The census lock held from a second descriptor, as a writer frozen inside its write would hold it."""
    import fcntl
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(folder / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _timed(fn, bound):
    began = time.monotonic()
    out = fn()
    took = time.monotonic() - began
    assert took < bound, f"took {took:.2f} s"
    return out


def test_b1_a_writer_frozen_holding_the_census_lock_never_stalls_contact_freeze_thaw_or_stop(tmp_path, mirror):
    fake = H.FakeAuthority(mirror[1])
    rt = H.make_rt(tmp_path, mirror[0], fake)
    flag = {"on": False}
    fake.cancel_requested = lambda request_id, generation: flag["on"]
    rid = fake.add(H.program("spawn long1 role=worker hold_s=600 beat_s=1 long", "checkpoint s1 hold_s=30",
                             "result ok"))
    root = runner.census_agent_id(f"{rid}-g1")
    t, box = pass_in_thread(rt)
    wait_for(lambda: rows_of(rt.census.root).get(f"{root}/long1", {}).get("state") == "running")
    fd = _hold_lock(rt.census.root)
    try:
        time.sleep(1.5)                                   # the child's beat is now inside its write, waiting
        with rt._lock:
            child = rt.child
        assert _timed(lambda: child.freeze("authority_unreachable"), 1.0) == "frozen"   # the frozen writer
        assert child.lock.acquire(timeout=1)
        child.lock.release()
        assert _timed(rt.contact, 1.0) is True                                       # the heartbeat leaves
        frozen = [e for e in fake.census.values() if e["kind"] == "census.update"
                  and e["body"]["agent_id"] == root and e["body"]["reason"] == "frozen"]
        assert len(frozen) == 1                           # sent from memory: the authority sees it frozen at once
        assert rt.census_status()["status"] == "deferred" and rt.census.waiting() >= 1
        _timed(lambda: rt.write_state(force=True), 1.0)
        assert json.loads((tmp_path / "artifacts" / "host-state.json").read_text())["census"]["reason"] == "lock_busy"
        _timed(child.thaw, 1.0)
        assert not child.frozen
    finally:
        os.close(fd)                                      # the writer goes on
    assert rt.census.flush() and rt.census.waiting() == 0 and rt.census_status()["status"] == "ok"
    on_file = [json.loads(x) for p in rt.census.root.glob("*.jsonl") for x in p.read_text().splitlines()]
    assert [x for x in on_file if x.get("event_id") == frozen[0]["event_id"] and x["type"] == "event"]
    assert [x for x in on_file if x.get("event_id") == frozen[0]["event_id"] and x["type"] == "ack"]
    flag["on"] = True
    t.join(30)
    assert not t.is_alive() and box["out"]["state"] == "cancelled"


def test_b1_a_restart_never_waits_on_a_lock_its_frozen_child_held(tmp_path, mirror):
    fake = H.FakeAuthority(mirror[1])
    rt = H.make_rt(tmp_path, mirror[0], fake)
    rt.census.append("census.start", root_start("runtime:t-g1"))
    fd = _hold_lock(rt.census.root)
    try:
        _timed(rt.freeze_left_children, 1.0)
        assert _timed(rt.contact, 1.0) is True
        assert [e for e in fake.census.values() if e["kind"] == "census.gap"]       # the restart gap, from memory
    finally:
        os.close(fd)


def test_s1_the_census_never_gates_work_an_unwritable_census_is_shown_not_refused(tmp_path, mirror, capsys):
    fake = H.FakeAuthority(mirror[1])
    rt = H.make_rt(tmp_path, mirror[0], fake)
    (tmp_path / "a-file").write_text("")
    rt.census = C.CensusFile(tmp_path / "a-file" / "census")          # cannot be written: not a folder
    rid = fake.add(H.program("spawn s1 role=explore hold_s=0", "checkpoint s1", "result ok"))
    out = H.run_pass(rt)
    assert out["state"] == "completed" and fake.items[rid]["state"] == "completed"     # no item refused
    root = runner.census_agent_id(f"{rid}-g1")
    status = rt.census_status()
    assert (status["status"], status["reason"]) == ("unwritable", "os_error_20") and status["waiting_in_memory"] >= 2
    assert rt.contact()
    got = {e["body"].get("agent_id"): e for e in fake.census.values() if e["kind"] == "census.start"}
    assert root in got                                              # its row reached the authority all the same
    rt.write_state(force=True)
    doc = json.loads((tmp_path / "artifacts" / "host-state.json").read_text())
    assert doc["census"]["status"] == "unwritable"
    capsys.readouterr()
    cli_rc = H.cli.main(["state", "--host-config", str(tmp_path / "host.yaml"), "--json"])
    assert cli_rc == 0 and json.loads(capsys.readouterr().out)["census"]["status"] == "unwritable"
    H.cli.main(["doctor", "--host-config", str(tmp_path / "host.yaml")])
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["census"]["status"] == "unwritable" and "census_unwritable:os_error_20" in doctor["problems"]


def test_s2_a_backlog_never_hides_a_freeze_the_first_heartbeat_carries_gaps_and_ends_first(tmp_path, mirror):
    fake = H.FakeAuthority(mirror[1])
    rt = H.make_rt(tmp_path, mirror[0], fake)
    rt.census.append("census.start", root_start("r1"))
    for i in range(250):
        rt.census.append("census.start", sub_start(f"r1/s{i}", "r1"))
    rt.census.append("census.gap", C.gap("wake", at(), at(120)))
    rt.census.append("census.update", C.update("r1", source=C.RUNTIME_SOURCE, state="idle", reason="frozen"))
    assert rows_of(rt.census.root)["r1"]["state"] == "idle"
    assert rt.contact()
    assert len(fake.census) == C.BATCH_MAX_EVENTS                      # one batch: more is waiting
    f = C.Fold()
    for e in sorted(fake.census.values(), key=lambda e: e["at"]):
        f.apply(e, "mac-test-1")
    seen = {r["agent_id"]: r for r in f.rows(reachability={"mac-test-1": "reachable"}, environment="dev")}
    assert (seen["r1"]["state"], seen["r1"]["reason"]) == ("idle", "frozen")
    assert not [r for r in seen.values() if r["state"] == "running"]   # CEN-3 at the authority, from the first beat


def test_s3_an_unseen_or_unverified_end_closes_the_subtree(tmp_path, mirror):
    from types import SimpleNamespace
    fake = H.FakeAuthority(mirror[1])
    rt = H.make_rt(tmp_path, mirror[0], fake)
    journal = SimpleNamespace(notes=lambda kind=None: [])

    def child(aid, **over):
        rt.census.append("census.start", root_start(aid))
        rt.census.append("census.start", sub_start(aid + "/long1", aid))
        return SimpleNamespace(**{"agent_id": aid, "census_started": True, "census_ended": False, "lost": False,
                                  "signalled": False, "proc": SimpleNamespace(returncode=None),
                                  "claim": {"unit": "test_units"}, "journal": journal, **over})
    rt.census_child(child("runtime:lost-g1", lost=True), "ended")                   # reaped elsewhere (N-D1)
    rt.census_child(child("runtime:unver-g1"), "ended")                             # may still run
    rows = rows_of(rt.census.root)
    assert (rows["runtime:lost-g1"]["state"], rows["runtime:lost-g1"]["reason"], rows["runtime:lost-g1"]["ended_at"]) \
        == ("unknown", "process_identity_unverified", None)
    assert rows["runtime:lost-g1/long1"]["reason"] == "end_unobserved"                # CEN-4: closed with it
    assert rows["runtime:unver-g1/long1"]["reason"] == "parent_unverified"            # never its last "running"
    rt.census.scan()
    closed = rt.census.fold.closed_trees(datetime.now(timezone.utc) + timedelta(days=1))
    assert {"runtime:lost-g1", "runtime:lost-g1/long1"} <= closed and "runtime:unver-g1" not in closed
    # An orphan found dead after a restart: an end, unseen, so its subtree closes too.
    task = f"{uuid.uuid4()}-g1"
    proc = subprocess.Popen(["true"], start_new_session=True)
    proc.wait()
    art = tmp_path / "artifacts"
    (art / f"{task}.claim.json").write_text(json.dumps({"request_id": task[:-3], "generation": 1, "task": task}))
    (art / f"{task}.stage.json").write_text(json.dumps({"stage": "started", "pgid": proc.pid, "identity": "x"}))
    rid = runner.census_agent_id(task)
    rt.census.append("census.start", root_start(rid))
    rt.census.append("census.start", sub_start(rid + "/long1", rid))
    H.RUNTIMES.remove(rt)
    rt2 = H.make_rt(tmp_path, mirror[0], fake)
    rt2.freeze_left_children()
    rows = rows_of(art / "census")
    assert (rows[rid]["state"], rows[rid]["reason"], rows[rid]["ended_at"]) == ("unknown", "end_unobserved", None)
    assert rows[rid + "/long1"]["reason"] == "end_unobserved"


def test_n5_a_lone_event_refused_once_is_not_set_aside_it_is_sent_again(tmp_path, mirror):
    class Once(Door):
        def heartbeat(self, runner_id, state, capabilities, facts, census=None):
            if census and not getattr(self, "refused", False):
                self.refused = True
                raise ValueError("runner_request_invalid")      # a passing refusal
            return super().heartbeat(runner_id, state, capabilities, facts, census)
    rt = H.make_rt(tmp_path, mirror[0], Once())
    eid = rt.census.append("census.start", root_start("r1"))["event_id"]
    assert rt.contact() and rt.census.unsent()                 # set aside? no: still unsent
    assert rt.contact() and not rt.census.unsent() and rt.dispatcher.applied == {eid: 1}


def test_n4_a_day_file_of_any_size_is_read_whole_in_chunks(tmp_path, monkeypatch):
    f = C.CensusFile(tmp_path / "census")
    ids = [f.append("census.start", root_start(f"r{i}"))["event_id"] for i in range(20)]
    monkeypatch.setattr(C, "READ_CHUNK", 4096)                 # a few lines a chunk, lines across chunk edges
    h = C.CensusFile(tmp_path / "census")
    assert [e["event_id"] for e in h.unsent()] == ids and not h.damaged
