"""Rooms R3a (docs/specs/minimoi-connected-work/ROOMS_R3.md §2, v0.3 + Codex's
clarification): addressed rounds. One turn per teammate, in order; each sees
the earlier replies of its round; a lost teammate is skipped, fenced or
released by Records so it can never start late; delivery-only recovery of a
saved reply survives the inference deadline."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import create_app
from manage import provision_rooms


def past(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def env(tmp_path):
    app = create_app(tmp_path / "private", testing=True)
    store = app.extensions["records_store"]
    provision_rooms(store, "mc", "Master Craftsman", tmp_path / "mc")
    provision_rooms(store, "claude-code", "Claude Code", tmp_path / "cc", worker="rooms-connector-mac")
    tok = lambda d, n: {"Authorization": "Bearer " + (tmp_path / d / f"{n}.token").read_text().strip()}
    e = SimpleNamespace(app=app, store=store, client=app.test_client(), owner={"Authorization": "Bearer " + store.owner_key},
                        mc=tok("mc", "mc"), mcw=tok("mc", "rooms-worker"), cc=tok("cc", "claude-code"),
                        ccw=tok("cc", "rooms-connector-mac"))

    def call(method, headers, path, body=None):
        h = dict(headers)
        if method != "GET":
            h["Idempotency-Key"] = str(uuid4())
        return e.client.open("/api/v1" + path, method=method, headers=h, json=body if method != "GET" else None)
    e.call = call
    e.room = call("POST", e.owner, "/rooms", dict(title="Round", purpose="Synthetic", recording_acknowledged=True)).json["result"]["id"]
    with store.connect() as d:
        d.execute("UPDATE teammates SET proven_at=?", (past(60),))
    for who, h in (("mc", e.mc), ("claude-code", e.cc)):
        assert call("POST", e.owner, f"/rooms/{e.room}/invite", {"actor": who}).status_code == 201
        assert call("POST", h, f"/rooms/{e.room}/rsvp", {"state": "accepted"}).status_code == 200

    def say(text):
        r = call("POST", e.owner, f"/rooms/{e.room}/events", {"body": text})
        assert r.status_code == 201, r.json
        return r.json["result"]
    e.say = say

    def claim(worker, who):
        return call("POST", worker, "/turns/claim", {"addressees": [who]}).json["turn"]
    e.claim = claim

    def run(worker, member, t, text, claim_id=None):
        if claim_id is None:
            assert call("POST", worker, f"/turns/{t['id']}/start", {"claim_id": t["claim_id"]}).json["dispatch"]
        body = {"kind": "message", "body": text, "turn_id": t["id"], "claim_id": claim_id or t["claim_id"],
                "expected_context": {"generation": t["generation"], "trigger_seq": t["trigger_seq"]},
                "origin": {"source_application": "rooms_worker", "mode": "agent_response", "agent_id": t["addressee"],
                           "runtime": "test", "execution_id": "a" * 32}}
        return call("POST", member, f"/rooms/{t['room']}/events", body)
    e.run = run

    def turns():
        return {t["addressee"]: t for t in call("GET", e.owner, f"/rooms/{e.room}/turns").json["turns"]}
    e.turns = turns

    def sql(q, *a):
        with store.connect() as d:
            d.execute(q, a)
    e.sql = sql
    return e


def test_everyone_is_one_turn_each_in_strip_order_and_the_second_sees_the_first(env):
    trigger = env.say("@everyone what first?")
    assert env.claim(env.ccw, "claude-code") is None                    # not its turn yet
    mc = env.claim(env.mcw, "mc")
    assert mc["trigger"]["text"] == "@everyone what first?"
    assert env.run(env.mcw, env.mc, mc, "MC: start with the meeting").status_code == 201
    cc = env.claim(env.ccw, "claude-code")
    assert cc and cc["trigger"]["seq"] == trigger["seq"]
    earlier = [r for r in cc["transcript"] if r.get("note") == "earlier in this round"]
    assert [r["text"] for r in earlier] == ["MC: start with the meeting"] and earlier[0]["speaker_id"] == "mc"
    assert cc["coverage"]["earlier_in_round"] == 1
    assert all(r["text"] != "@everyone what first?" for r in cc["transcript"])   # the trigger once
    assert env.run(env.ccw, env.cc, cc, "Claude: agreed, then the door").status_code == 201
    t = env.turns()
    assert t["mc"]["round"]["position"] == 0 and t["claude-code"]["round"]["position"] == 1


def test_a_name_list_runs_in_the_order_written(env):
    env.say("@Claude then @MC please")
    assert env.claim(env.mcw, "mc") is None
    cc = env.claim(env.ccw, "claude-code")
    env.run(env.ccw, env.cc, cc, "first")
    assert env.claim(env.mcw, "mc") is not None


def test_a_skipped_away_teammate_never_starts_later(env):
    env.say("@everyone go")
    env.sql("UPDATE turn_order SET claimable_at=? WHERE position=0", past(100))
    env.call("GET", env.owner, f"/rooms/{env.room}/turns")              # Records-owned sweep
    t = env.turns()
    assert (t["mc"]["state"], t["mc"]["disposition"]) == ("cancelled", "skipped_away")
    cc = env.claim(env.ccw, "claude-code")
    assert cc is not None
    assert env.claim(env.mcw, "mc") is None                              # MC's connector returns: nothing starts
    with env.store.connect() as d:
        assert d.execute("SELECT remaining FROM meetings WHERE room=?", (env.room,)).fetchone()[0] == 19   # no slot taken


def test_an_expired_predecessor_is_fenced_and_its_late_post_refused(env):
    env.say("@everyone go")
    mc = env.claim(env.mcw, "mc")
    env.call("POST", env.mcw, f"/turns/{mc['id']}/start", {"claim_id": mc["claim_id"]})
    env.sql("UPDATE turns SET expires=? WHERE id=?", past(5), mc["id"])
    env.call("GET", env.owner, f"/rooms/{env.room}/turns")
    t = env.turns()
    assert (t["mc"]["state"], t["mc"]["disposition"]) == ("cancel_requested", "round_deadline")
    hb = env.call("POST", env.mcw, f"/turns/{mc['id']}/heartbeat", {"claim_id": mc["claim_id"], "intent": "dispatch"}).json
    assert hb["dispatch"] is False
    late = env.run(env.mcw, env.mc, mc, "too late", claim_id=mc["claim_id"])
    assert late.status_code == 409
    assert env.claim(env.ccw, "claude-code") is not None


def test_an_uncertain_predecessor_releases_the_round_and_its_saved_reply_arrives_labelled_late(env):
    env.say("@everyone go")
    mc = env.claim(env.mcw, "mc")
    env.call("POST", env.mcw, f"/turns/{mc['id']}/start", {"claim_id": mc["claim_id"]})
    env.sql("UPDATE turns SET lease_until=? WHERE id=?", past(5), mc["id"])
    env.call("GET", env.owner, f"/rooms/{env.room}/turns")              # → uncertain
    assert env.claim(env.ccw, "claude-code") is None                    # still waiting (unreconciled, < 120 s)
    env.sql("UPDATE turns SET updated=? WHERE id=?", past(130), mc["id"])
    env.call("GET", env.owner, f"/rooms/{env.room}/turns")              # released; MC stays uncertain
    assert env.turns()["mc"]["state"] == "uncertain"
    cc = env.claim(env.ccw, "claude-code")
    cc_transcript = json.dumps(cc["transcript"])
    env.run(env.ccw, env.cc, cc, "Claude goes on")
    rec = env.call("POST", env.mcw, f"/turns/{mc['id']}/recover", {"prior_claim_id": mc["claim_id"]}).json
    assert rec["state"] == "recovering"                                  # delivery only; no new inference
    assert env.run(env.mcw, env.mc, mc, "MC's saved reply", claim_id=rec["turn"]["claim_id"]).status_code == 201
    t = env.turns()
    assert t["mc"]["state"] == "committed" and t["mc"]["late_in_round"] is True
    assert "MC's saved reply" not in cc_transcript                       # the successor's snapshot was fixed at its claim


def test_the_budget_cuts_a_round(env):
    env.sql("UPDATE meetings SET remaining=1 WHERE room=?", env.room)
    env.say("@everyone go")
    mc = env.claim(env.mcw, "mc")
    env.run(env.mcw, env.mc, mc, "the last slot")
    assert env.claim(env.ccw, "claude-code") is None
    assert env.turns()["claude-code"]["disposition"] == "budget_exhausted"


def test_pause_fences_the_whole_round_and_a_new_message_supersedes_unclaimed_round_turns(env):
    env.say("@everyone go")
    mc = env.claim(env.mcw, "mc")
    env.call("POST", env.mcw, f"/turns/{mc['id']}/start", {"claim_id": mc["claim_id"]})
    env.say("never mind, just thinking")                                 # unaddressed → facilitator MC (queued behind its running turn)
    t = env.turns()
    assert t["claude-code"]["state"] == "superseded"
    version = env.call("GET", env.owner, f"/rooms/{env.room}").json["version"]
    env.call("POST", env.owner, f"/rooms/{env.room}/state", {"state": "paused", "version": version, "checkpoint": "p"})
    states = {x["id"]: x["state"] for x in env.call("GET", env.owner, f"/rooms/{env.room}/turns").json["turns"]}
    assert states[mc["id"]] == "cancel_requested"


def test_agent_replies_start_nothing(env):
    env.say("@everyone go")
    mc = env.claim(env.mcw, "mc")
    env.run(env.mcw, env.mc, mc, "@everyone let us all chime in")
    with env.store.connect() as d:
        count = d.execute("SELECT COUNT(*) FROM turns WHERE room=?", (env.room,)).fetchone()[0]
    assert count == 2                                                   # only the round Robert started


def test_saved_reply_recovery_survives_the_inference_deadline_outside_rounds(env):
    env.say("@MC alone")
    mc = env.claim(env.mcw, "mc")
    env.call("POST", env.mcw, f"/turns/{mc['id']}/start", {"claim_id": mc["claim_id"]})
    env.sql("UPDATE turns SET expires=? WHERE id=?", past(5), mc["id"])
    assert env.run(env.mcw, env.mc, mc, "after the deadline", claim_id=mc["claim_id"]).status_code == 409
    env.sql("UPDATE turns SET lease_until=? WHERE id=?", past(5), mc["id"])
    env.call("GET", env.owner, f"/rooms/{env.room}/turns")
    rec = env.call("POST", env.mcw, f"/turns/{mc['id']}/recover", {"prior_claim_id": mc["claim_id"]}).json
    assert env.run(env.mcw, env.mc, mc, "saved reply", claim_id=rec["turn"]["claim_id"]).status_code == 201


def test_round_replies_export_under_transcript_1_1(env):
    from transcript_snapshot import capture
    env.say("@everyone go")
    mc = env.claim(env.mcw, "mc"); env.run(env.mcw, env.mc, mc, "one")
    cc = env.claim(env.ccw, "claude-code"); env.run(env.ccw, env.cc, cc, "two")
    data, _ = capture(env.store, "robert", env.room)
    assert data["schema_version"] == "minimoi.transcript/1.1"
    assert {r["speaker_id"] for r in data["raw_transcript"] if r.get("execution")} == {"mc", "claude-code"}
