"""Rooms R1 (docs/specs/minimoi-connected-work/ROOMS_R1.md v0.5.1): Records side.

One meeting between Robert and Master Craftsman, through a worker that claims
turns with a work-scoped credential and posts as MC with MC's own
membership-scoped credential. No model is called; the "worker" here is the
test, speaking the same HTTP contract the real worker uses.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import create_app
from manage import provision_rooms


def past(seconds=5):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def env(tmp_path):
    app = create_app(tmp_path / "private", testing=True)
    store = app.extensions["records_store"]
    out = tmp_path / "outbox"
    info = provision_rooms(store, "mc", "Master Craftsman", out)
    tokens = {name: (out / f"{name}.token").read_text().strip() for name in ("mc", "rooms-worker")}
    client = app.test_client()
    H = lambda token: {"Authorization": "Bearer " + token}
    e = SimpleNamespace(app=app, store=store, client=client, info=info, tmp=tmp_path,
                        owner=H(store.owner_key), mc=H(tokens["mc"]), worker=H(tokens["rooms-worker"]))

    def call(method, headers, path, body=None, key=None):
        h = dict(headers)
        if method != "GET":
            h["Idempotency-Key"] = key or str(uuid4())
        r = client.open("/api/v1" + path, method=method, headers=h, json=body if method != "GET" else None)
        return r
    e.call = call
    e.room = call("POST", e.owner, "/rooms", dict(title="Design chat", purpose="Synthetic R1 test",
                                                  recording_acknowledged=True)).json["result"]["id"]

    def db():
        return store.connect()
    e.db = db

    def prove():
        with db() as d:
            d.execute("UPDATE teammates SET proven_at=? WHERE principal='mc'", (past(),))
    e.prove = prove

    def join(room=None):
        room = room or e.room
        r = call("POST", e.owner, f"/rooms/{room}/invite", {"actor": "mc"})
        assert r.status_code == 201, r.json
        r = call("POST", e.mc, f"/rooms/{room}/rsvp", {"state": "accepted"})
        assert r.status_code == 200, r.json
        return r.json
    e.join = join

    def say(text, room=None, target=None, key=None):
        body = {"body": text}
        if target:
            body["target"] = target
        r = call("POST", e.owner, f"/rooms/{room or e.room}/events", body, key)
        assert r.status_code == 201, r.json
        return r.json["result"]
    e.say = say

    def claim():
        r = call("POST", e.worker, "/turns/claim", {"addressees": ["mc"]})
        assert r.status_code == 200, r.json
        return r.json["turn"]
    e.claim = claim

    def start(turn):
        return call("POST", e.worker, f"/turns/{turn['id']}/start", {"claim_id": turn["claim_id"]})
    e.start = start

    def reply(turn, text="One short point.", claim_id=None, key=None, **extra):
        body = {"kind": "message", "body": text, "context_class": "agent_draft", "turn_id": turn["id"],
                "claim_id": claim_id or turn["claim_id"],
                "expected_context": {"generation": turn["generation"], "trigger_seq": turn["trigger_seq"]},
                "origin": {"source_application": "rooms_worker", "mode": "agent_response", "agent_id": "mc-agent",
                           "runtime": "OpenClaw", "execution_id": "a" * 32}, **extra}
        return call("POST", e.mc, f"/rooms/{turn['room']}/events", body, key or turn["response_key"])
    e.reply = reply

    def turn(turn_id):
        with db() as d:
            return dict(d.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone())
    e.turn = turn

    def status(room=None):
        r = call("GET", e.owner, f"/rooms/{room or e.room}/turns")
        assert r.status_code == 200, r.json
        return r.json
    e.status = status

    def remaining(room=None):
        with db() as d:
            return d.execute("SELECT remaining FROM meetings WHERE room=?", (room or e.room,)).fetchone()[0]
    e.remaining = remaining
    return e


def event_count(e, room=None):
    with e.db() as d:
        return d.execute("SELECT COUNT(*) FROM events WHERE room=?", (room or e.room,)).fetchone()[0]


# ── schema, rollback compatibility ──────────────────────────────────────────

def test_migration_is_idempotent_and_keeps_schema_version(env):
    from store import Store
    Store(env.store.root)
    Store(env.store.root)
    with env.db() as d:
        assert d.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "5"
        assert d.execute("SELECT value FROM meta WHERE key='rooms_r1_schema'").fetchone()[0] == "1"
        assert d.execute("SELECT COUNT(*) FROM room_generations WHERE room=?", (env.room,)).fetchone()[0] == 1
        cols = {r[1] for r in d.execute("PRAGMA table_info(members)")}
        assert cols == {"room", "actor", "role"}          # no column added to an existing table
        assert {r[1] for r in d.execute("PRAGMA table_info(client_credentials)")} == {
            "id", "installation", "token_hash", "created", "expires", "revoked", "grants", "legacy"}


def _baseline_module_dir(tmp_path):
    """The Records modules exactly as at the frozen baseline (a7e2e6d)."""
    root = Path(__file__).resolve().parents[1]
    target = tmp_path / "baseline"
    target.mkdir()
    for name in ("store.py", "platform_access.py", "coordination.py", "contribution_view.py", "transcript_format.py"):
        try:
            text = subprocess.run(["git", "show", f"a7e2e6d:prototype-lab/projects/project-records-room-poc/{name}"],
                                  cwd=root, capture_output=True, text=True, check=True).stdout
        except (subprocess.CalledProcessError, FileNotFoundError):
            pytest.skip("baseline revision a7e2e6d not available in this checkout")
        (target / name).write_text(text)
    return target


def test_previous_image_runs_on_migrated_database_and_keeps_new_records(env, tmp_path):
    """Rollback, data-preserving (spec §7): the baseline code starts on the
    migrated database, writes with its positional INSERTs, and every record
    accepted after the migration, including an MC reply, is still there."""
    env.prove(); env.join(); env.say("A question for MC")
    t = env.claim(); assert env.start(t).json["dispatch"]
    assert env.reply(t, "MC's reply").status_code == 201
    baseline = _baseline_module_dir(tmp_path)
    code = f"""
import sys, json, uuid
sys.path.insert(0, {str(baseline)!r})
from store import Store
s = Store({str(env.store.root)!r})
room = s.create_room('robert', str(uuid.uuid4()), dict(title='After rollback', purpose='x', recording_acknowledged=True))['result']['id']
s.add_principal('robert', 'p1', dict(id='later-agent', label='Later'))
s.membership('robert', 'm1', room, dict(actor='later-agent'))
s.append('robert', 'a1', room, dict(body='written by the old image'))
old = s.room('robert', {env.room!r})
print(json.dumps([e['actor'] for e in old['events']]))
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "mc" in json.loads(out.stdout.strip().splitlines()[-1])
    # Forward again: the new code still opens it and the fence still holds.
    from store import Store
    again = Store(env.store.root)
    assert again.room("robert", env.room)["events"][-1]["actor"] == "mc"


# ── the normal flow ─────────────────────────────────────────────────────────

def test_one_honest_meeting_end_to_end(env):
    env.prove()
    r = env.call("POST", env.owner, f"/rooms/{env.room}/invite", {"actor": "mc"})
    assert r.status_code == 201 and r.json["result"]["rsvp"]["rsvp"] == "invited"
    hosted = env.call("GET", env.worker, "/hosted-teammates").json
    assert hosted["teammates"] == ["mc"] and hosted["pending_invites"][0]["room"] == env.room
    assert env.call("POST", env.mc, f"/rooms/{env.room}/rsvp", {"state": "accepted"}).status_code == 200
    env.say("Earlier context")
    env.call("POST", env.owner, f"/rooms/{env.room}/events", {"body": "What should we build first?"})
    t = env.claim()
    assert t["state"] == "claimed" and env.remaining() == 19
    assert t["trigger"]["text"] == "What should we build first?"
    assert all(x["text"] != t["trigger"]["text"] for x in t["transcript"])      # the trigger appears once
    assert t["coverage"]["through_seq"] == t["trigger_seq"] - 1
    s = env.start(t).json
    assert s == {"state": "running", "dispatch": True, "generation": t["generation"]}
    assert env.status()["participants"][1]["reach"]["state"] == "answering"
    r = env.reply(t, "Start with the meeting itself.", usage_evidence={"status": "reported", "prompt_tokens": 10,
                                                                       "completion_tokens": 5})
    assert r.status_code == 201 and r.json["result"]["actor"] == "mc"
    done = env.turn(t["id"])
    assert done["state"] == "committed"
    result = json.loads(done["result"])
    assert result["execution"]["coordinating_installation"] == env.info["hosted"]["installation_id"]
    assert result["execution"]["caller_correlation"] == "a" * 32 and result["execution"]["prompt_tokens"] == 10
    receipt = env.call("GET", env.mc, f"/operations/{t['response_key']}?destination={env.room}")
    assert receipt.status_code == 200 and receipt.json["receipt"]["actor"] == "mc"
    assert env.remaining() == 19                                                   # spent once, at claim


def test_lost_post_response_replays_without_a_second_row(env):
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t)
    first = env.reply(t, "Answer")
    count = event_count(env)
    again = env.reply(t, "Answer")
    assert again.status_code == 201 and again.json == first.json and event_count(env) == count


# ── the write fence ─────────────────────────────────────────────────────────

def test_fence_refuses_stale_generation_wrong_claim_and_missing_turn(env):
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t)
    before = event_count(env)
    assert env.reply(t, claim_id=str(uuid4())).status_code == 409
    plain = env.call("POST", env.mc, f"/rooms/{env.room}/events", {"body": "no turn named"})
    assert plain.status_code == 409                                                 # omitted origin cannot bypass
    with_origin = env.call("POST", env.mc, f"/rooms/{env.room}/events", {"body": "x", "origin": {
        "source_application": "x", "mode": "human"}})
    assert with_origin.status_code == 409
    version = env.call("GET", env.owner, f"/rooms/{env.room}").json["version"]
    env.call("POST", env.owner, f"/rooms/{env.room}/state", {"state": "paused", "version": version, "checkpoint": "p"})
    env.call("POST", env.owner, f"/rooms/{env.room}/state", {"state": "active", "version": version + 1, "checkpoint": "r"})
    assert env.reply(t).status_code == 409                                          # generation moved, state cancel_requested
    assert event_count(env) == before + 2                                           # only the two state changes


def test_fence_refuses_expired_lease_and_cancel_requested(env):
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t)
    env.call("POST", env.owner, f"/rooms/{env.room}/turns/{t['id']}/cancel", {})
    assert env.turn(t["id"])["state"] == "cancel_requested"
    assert env.reply(t).status_code == 409
    env.say("Q2")
    with env.db() as d:
        d.execute("UPDATE turns SET state='cancelled' WHERE id=?", (t["id"],))
    t2 = env.claim(); env.start(t2)
    with env.db() as d:
        d.execute("UPDATE turns SET lease_until=? WHERE id=?", (past(), t2["id"]))
    assert env.reply(t2).status_code == 409


def test_people_and_the_existing_cos_connector_are_not_fenced(env):
    """M6: an agent without a teammate card keeps today's path."""
    token = env.store.add_principal("robert", "p", dict(id="cos-dev", label="CoS (dev)"))["access_token"]
    env.store.membership("robert", "m", env.room, dict(actor="cos-dev"))
    r = env.call("POST", {"Authorization": "Bearer " + token}, f"/rooms/{env.room}/events", {"body": "CoS draft",
        "origin": {"source_application": "cos", "mode": "agent_response", "agent_id": "cos-agent-a",
                   "runtime": "OpenClaw", "execution_id": "run-1"}})
    assert r.status_code == 201
    assert env.call("POST", env.owner, f"/rooms/{env.room}/events", {"body": "Robert"}).status_code == 201


# ── credentials, membership scope, worker binding ───────────────────────────

def test_membership_credential_follows_current_membership(env):
    env.prove()
    assert env.call("GET", env.mc, f"/rooms/{env.room}").status_code == 403         # not a member
    env.call("POST", env.owner, f"/rooms/{env.room}/invite", {"actor": "mc"})
    assert env.call("GET", env.mc, f"/rooms/{env.room}").status_code == 403         # invited, not accepted
    assert env.call("POST", env.mc, f"/rooms/{env.room}/rsvp", {"state": "accepted"}).status_code == 200
    assert env.call("GET", env.mc, f"/rooms/{env.room}").status_code == 200
    assert [r["id"] for r in env.call("GET", env.mc, "/rooms").json["rooms"]] == [env.room]
    env.store.membership("robert", "rm", env.room, dict(actor="mc", role="remove"))
    assert env.call("GET", env.mc, f"/rooms/{env.room}").status_code == 403
    assert env.call("GET", env.mc, "/v2/rooms").status_code == 403


def test_work_credential_reads_no_room_and_owner_cannot_use_worker_routes(env):
    env.prove(); env.join(); env.say("Q")
    assert env.call("GET", env.worker, f"/rooms/{env.room}").status_code == 403
    assert env.call("GET", env.worker, "/rooms").status_code == 403
    assert env.call("POST", env.worker, f"/rooms/{env.room}/events", {"body": "x"}).status_code == 403
    assert env.call("POST", env.owner, "/turns/claim", {"addressees": ["mc"]}).status_code == 403
    assert env.call("POST", env.mc, "/turns/claim", {"addressees": ["mc"]}).status_code == 403
    assert env.call("GET", env.owner, "/hosted-teammates").status_code == 403


def test_worker_cannot_claim_unbound_or_touch_another_installations_claim(env):
    env.prove(); env.join(); env.say("Q")
    from store import now
    with env.db() as d:
        d.execute("INSERT INTO principals VALUES('other-worker','Other','service','h-other',?)", (now(),))
    other = env.store.platform_access.issue("robert", dict(principal="other-worker", label="o", scope="work",
        expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
    oh = {"Authorization": "Bearer " + other["access_token"]}
    assert env.call("POST", oh, "/turns/claim", {"addressees": ["mc"]}).json["turn"] is None
    assert env.call("POST", env.worker, "/turns/claim", {"addressees": ["codex"]}).json["turn"] is None
    t = env.claim()
    assert env.call("POST", oh, f"/turns/{t['id']}/start", {"claim_id": t["claim_id"]}).status_code == 404
    assert env.call("POST", env.worker, f"/turns/{t['id']}/start", {"claim_id": str(uuid4())}).status_code == 409


def test_claim_refuses_without_releasing_context(env):
    env.prove(); env.join(); env.say("Q1")
    env.store.membership("robert", "down", env.room, dict(actor="mc", role="observer"))
    assert env.claim() is None
    with env.db() as d:
        assert d.execute("SELECT state,disposition FROM turns").fetchone()[:] == ("cancelled", "membership_ended")
    env.store.membership("robert", "up", env.room, dict(actor="mc", role="contributor"))
    env.say("Q2")
    cred = env.info["credentials"]["mc"]["credential_id"]
    env.store.platform_access.revoke("robert", cred)
    assert env.claim() is None
    with env.db() as d:
        assert d.execute("SELECT disposition FROM turns ORDER BY created DESC").fetchone()[0] == "credential_invalid"


# ── budget reservation and dispatch admission (V03-01, M4) ──────────────────

def test_last_slot_runs_and_a_second_turn_cannot_claim(env):
    env.prove(); env.join()
    with env.db() as d:
        d.execute("UPDATE meetings SET remaining=1 WHERE room=?", (env.room,))
    env.say("Q1")
    t = env.claim()
    assert env.remaining() == 0
    assert env.start(t).json["dispatch"] is True
    assert env.reply(t).status_code == 201
    env.say("Q2")
    assert env.claim() is None
    assert env.status()["turns"][0]["disposition"] == "budget_exhausted"


def test_cancel_before_send_still_consumes_the_slot(env):
    env.prove(); env.join(); env.say("Q")
    t = env.claim()
    assert env.remaining() == 19
    env.call("POST", env.owner, f"/rooms/{env.room}/turns/{t['id']}/cancel", {})
    env.call("POST", env.owner, f"/rooms/{env.room}/turns/{t['id']}/cancel", {})
    ack = env.call("POST", env.worker, f"/turns/{t['id']}/cancel-ack", {"claim_id": t["claim_id"], "late_output": False})
    env.call("POST", env.worker, f"/turns/{t['id']}/cancel-ack", {"claim_id": t["claim_id"]})
    assert ack.json["state"] == "cancelled" and env.remaining() == 19              # never returned


@pytest.mark.parametrize("change", ["remove", "downgrade", "revoke", "window", "turn_expiry", "pause"])
def test_dispatch_refused_after_a_change_during_a_busy_relay_wait(env, change):
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t)
    hb = lambda: env.call("POST", env.worker, f"/turns/{t['id']}/heartbeat", {"claim_id": t["claim_id"], "intent": "dispatch"}).json
    assert hb()["dispatch"] is True
    if change == "remove":
        env.store.membership("robert", "x", env.room, dict(actor="mc", role="remove"))
    elif change == "downgrade":
        env.store.membership("robert", "x", env.room, dict(actor="mc", role="observer"))
    elif change == "revoke":
        env.store.platform_access.revoke("robert", env.info["credentials"]["mc"]["credential_id"])
    elif change == "window":
        with env.db() as d: d.execute("UPDATE meetings SET window_expires=?", (past(),))
    elif change == "turn_expiry":
        with env.db() as d: d.execute("UPDATE turns SET expires=?", (past(),))
    else:
        version = env.call("GET", env.owner, f"/rooms/{env.room}").json["version"]
        env.call("POST", env.owner, f"/rooms/{env.room}/state", {"state": "paused", "version": version, "checkpoint": "p"})
    out = hb()
    assert out["dispatch"] is False and out["state"] == "cancel_requested"
    assert env.remaining() == 19


def test_plain_heartbeat_renews_lease_only(env):
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t)
    before = env.turn(t["id"])
    out = env.call("POST", env.worker, f"/turns/{t['id']}/heartbeat", {"claim_id": t["claim_id"]}).json
    after = env.turn(t["id"])
    assert out["dispatch"] is False and after["expires"] == before["expires"] and after["lease_until"] >= before["lease_until"]
    assert env.remaining() == 19


# ── proof gate (M1) ─────────────────────────────────────────────────────────

def test_unproven_teammate_cannot_accept_claim_or_be_routed(env):
    r = env.call("POST", env.owner, f"/rooms/{env.room}/invite", {"actor": "mc"})
    assert r.json["result"]["rsvp"]["rsvp_reason"] == "not_proven"
    assert env.call("POST", env.mc, f"/rooms/{env.room}/rsvp", {"state": "accepted"}).status_code == 409
    env.say("Hello MC")
    assert env.claim() is None
    st = env.status()
    assert st["routing_notes"][0]["disposition"] == "not_proven" and st["turns"] == []
    mc = [p for p in st["participants"] if p["id"] == "mc"][0]
    assert mc["reach"]["reason"].startswith("not yet proven")


def test_prove_is_owner_only_idempotent_and_sets_proven(env):
    assert env.call("POST", env.owner, "/teammates/mc/prove", {}).status_code == 400            # needs confirm
    assert env.call("POST", env.mc, "/teammates/mc/prove", {"confirm": True}).status_code == 403
    a = env.call("POST", env.owner, "/teammates/mc/prove", {"confirm": True}, key="proof-1")
    b = env.call("POST", env.owner, "/teammates/mc/prove", {"confirm": True}, key="proof-1")
    assert a.status_code == 202 and a.json == b.json
    proof_room = a.json["result"]["proof_room"]
    invites = env.call("GET", env.worker, "/hosted-teammates").json["pending_invites"]
    assert invites == [{"room": proof_room, "principal": "mc", "proven": True}]
    assert env.call("POST", env.mc, f"/rooms/{proof_room}/rsvp", {"state": "accepted"}).status_code == 200
    t = env.claim()
    assert t["room"] == proof_room and env.remaining(proof_room) == 0
    assert env.start(t).json["dispatch"] is True                                # one slot, reserved, used
    assert env.reply(t, "Hello, I am MC.").status_code == 201
    status = env.call("GET", env.owner, "/teammates/mc/prove").json
    assert status["proven_at"] and status["turn"]["state"] == "committed"
    r = env.call("POST", env.owner, f"/rooms/{env.room}/invite", {"actor": "mc"})
    assert r.json["result"]["rsvp"]["rsvp_reason"] is None


def test_failed_proof_leaves_teammate_unproven(env):
    room = env.call("POST", env.owner, "/teammates/mc/prove", {"confirm": True}).json["result"]["proof_room"]
    env.call("POST", env.mc, f"/rooms/{room}/rsvp", {"state": "accepted"})
    t = env.claim(); env.start(t)
    env.call("POST", env.worker, f"/turns/{t['id']}/fail", {"claim_id": t["claim_id"], "outcome": "failed", "reason": "relay_busy"})
    assert env.call("GET", env.owner, "/teammates/mc/prove").json["proven_at"] is None


def test_pending_invitation_is_accepted_after_proof_and_the_waiting_turn_runs(env):
    env.call("POST", env.owner, f"/rooms/{env.room}/invite", {"actor": "mc"})
    room = env.call("POST", env.owner, "/teammates/mc/prove", {"confirm": True}).json["result"]["proof_room"]
    env.call("POST", env.mc, f"/rooms/{room}/rsvp", {"state": "accepted"})
    t = env.claim(); env.start(t); env.reply(t)
    # The original room's invitation is now acceptable on the worker's next pass.
    pending = env.call("GET", env.worker, "/hosted-teammates").json["pending_invites"]
    assert pending == [{"room": env.room, "principal": "mc", "proven": True}]
    env.say("Now in the original room")                                         # proven + invited: the turn waits
    assert env.claim() is None
    assert env.call("POST", env.mc, f"/rooms/{env.room}/rsvp", {"state": "accepted"}).status_code == 200
    t2 = env.claim()
    assert t2 and t2["room"] == env.room


# ── routing (M2) and queue rule (V02-05) ────────────────────────────────────

def test_unavailable_recipient_keeps_message_and_creates_no_turn(env):
    env.prove(); env.join()
    ev = env.say("@CoS can you check the calendar?")
    st = env.status()
    assert st["turns"] == [] and st["routing_notes"][0]["label"] == "CoS"
    assert st["routing_notes"][0]["disposition"] == "no_connector" and st["routing_notes"][0]["trigger_seq"] == ev["seq"]
    env.say("@MC and you?")
    assert env.status()["turns"][0]["addressee"] == "mc"


def test_queue_rule_running_plus_one_queued_and_answered_earlier(env):
    env.prove(); env.join()
    env.say("A")
    a = env.claim(); env.start(a)
    env.say("B")
    env.say("C")
    st = {t["trigger_seq"]: t for t in env.status()["turns"]}
    states = sorted(t["state"] for t in st.values())
    assert states == ["queued", "running", "superseded"]
    assert env.claim() is None                                                  # slot busy: C waits
    env.reply(a, "Answer to A")
    a_view = [t for t in env.status()["turns"] if t["id"] == a["id"]][0]
    assert a_view["answered_earlier"] is True
    c = env.claim()
    assert c["trigger"]["text"] == "C"


def test_pause_cancels_queued_and_fences_running(env):
    env.prove(); env.join()
    env.say("A"); a = env.claim(); env.start(a); env.say("C")
    version = env.call("GET", env.owner, f"/rooms/{env.room}").json["version"]
    env.call("POST", env.owner, f"/rooms/{env.room}/state", {"state": "paused", "version": version, "checkpoint": "p"})
    states = {t["trigger_seq"]: t["state"] for t in env.status()["turns"]}
    assert sorted(states.values()) == ["cancel_requested", "cancelled"]


def test_stop_pauses_and_fences(env):
    env.prove(); env.join(); env.say("A"); a = env.claim(); env.start(a)
    r = env.call("POST", env.owner, f"/rooms/{env.room}/stop", {})
    assert r.status_code == 200 and r.json["result"]["state"] == "paused"
    assert env.turn(a["id"])["state"] == "cancel_requested"
    ack = env.call("POST", env.worker, f"/turns/{a['id']}/cancel-ack", {"claim_id": a["claim_id"], "late_output": True})
    assert ack.json["state"] == "cancelled" and env.turn(a["id"])["stop_ack"] == "worker"


# ── Records-owned expiry and recovery (V03-02) ──────────────────────────────

def test_expiry_table(env):
    env.prove(); env.join()
    env.say("A"); a = env.claim()
    with env.db() as d: d.execute("UPDATE turns SET lease_until=? WHERE id=?", (past(), a["id"]))
    assert env.status()["turns"][0]["state"] == "uncertain"
    with env.db() as d: d.execute("UPDATE turns SET state='abandoned' WHERE id=?", (a["id"],))
    env.say("B"); b = env.claim(); env.start(b)
    env.call("POST", env.owner, f"/rooms/{env.room}/turns/{b['id']}/cancel", {})
    with env.db() as d: d.execute("UPDATE turns SET lease_until=? WHERE id=?", (past(), b["id"]))
    view = [t for t in env.status()["turns"] if t["id"] == b["id"]][0]
    assert (view["state"], view["stop_ack"], view["disposition"]) == ("cancelled", "none", "fenced_without_ack")
    assert "provider_termination" not in json.dumps(env.status())


def test_recovery_delivers_saved_reply_once_and_replaces_the_claim(env):
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t)
    with env.db() as d: d.execute("UPDATE turns SET lease_until=? WHERE id=?", (past(), t["id"]))
    hosted = env.call("GET", env.worker, "/hosted-teammates").json
    assert hosted["uncertain_turns"][0]["turn_id"] == t["id"]
    r = env.call("POST", env.worker, f"/turns/{t['id']}/recover", {"prior_claim_id": t["claim_id"]}).json
    assert r["state"] == "recovering"
    new = r["turn"]
    assert new["claim_id"] != t["claim_id"] and env.remaining() == 19            # delivery spends nothing
    assert env.reply(t).status_code == 409                                        # the old claim is dead
    assert env.reply(t, claim_id=new["claim_id"]).status_code == 201
    count = event_count(env)
    assert env.reply(t, claim_id=new["claim_id"]).status_code == 201 and event_count(env) == count
    assert env.turn(t["id"])["state"] == "committed"


def test_interrupted_recovery_can_be_recovered_again(env):
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t)
    with env.db() as d: d.execute("UPDATE turns SET lease_until=? WHERE id=?", (past(), t["id"]))
    first = env.call("POST", env.worker, f"/turns/{t['id']}/recover", {"prior_claim_id": t["claim_id"]}).json["turn"]
    with env.db() as d: d.execute("UPDATE turns SET lease_until=? WHERE id=?", (past(), t["id"]))
    assert env.turn(t["id"])["state"] in ("recovering", "uncertain")
    second = env.call("POST", env.worker, f"/turns/{t['id']}/recover", {"prior_claim_id": first["claim_id"]}).json
    assert second["state"] == "recovering"
    assert env.reply(t, claim_id=second["turn"]["claim_id"]).status_code == 201


def test_recovery_waits_for_a_busy_slot_and_pause_during_recovery_releases(env):
    env.prove(); env.join(); env.say("A")
    a = env.claim(); env.start(a)
    with env.db() as d: d.execute("UPDATE turns SET lease_until=? WHERE id=?", (past(), a["id"]))
    env.status()
    env.say("B"); b = env.claim(); env.start(b)                                   # uncertain A does not hold the slot
    assert env.call("POST", env.worker, f"/turns/{a['id']}/recover", {"prior_claim_id": a["claim_id"]}).json["wait"] is True
    env.reply(b)
    rec = env.call("POST", env.worker, f"/turns/{a['id']}/recover", {"prior_claim_id": a["claim_id"]}).json
    assert rec["state"] == "recovering"
    version = env.call("GET", env.owner, f"/rooms/{env.room}").json["version"]
    env.call("POST", env.owner, f"/rooms/{env.room}/state", {"state": "paused", "version": version, "checkpoint": "p"})
    assert env.turn(a["id"])["state"] == "cancelled" and env.turn(a["id"])["disposition"] == "paused_during_recovery"
    assert env.reply(a, claim_id=rec["turn"]["claim_id"]).status_code == 409


def test_recovery_after_generation_change_discards(env):
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t)
    with env.db() as d:
        d.execute("UPDATE turns SET state='uncertain' WHERE id=?", (t["id"],))
        d.execute("UPDATE room_generations SET generation='moved' WHERE room=?", (env.room,))
    r = env.call("POST", env.worker, f"/turns/{t['id']}/recover", {"prior_claim_id": t["claim_id"]}).json
    assert r["state"] == "cancelled" and r["reason"] == "stale_generation"


def test_late_fail_after_commit_is_refused_and_retry_rules(env):
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t); env.reply(t)
    late = env.call("POST", env.worker, f"/turns/{t['id']}/fail", {"claim_id": t["claim_id"], "outcome": "failed", "reason": "x"})
    assert late.status_code == 409 and env.turn(t["id"])["state"] == "committed"
    env.say("Q2"); t2 = env.claim(); env.start(t2)
    with env.db() as d: d.execute("UPDATE turns SET lease_until=? WHERE id=?", (past(), t2["id"]))
    env.status()
    assert env.call("POST", env.owner, f"/rooms/{env.room}/turns/{t2['id']}/retry", {}).status_code == 400
    # Not reconciled yet: a saved reply may still be delivered (review F3).
    assert env.call("POST", env.owner, f"/rooms/{env.room}/turns/{t2['id']}/retry", {"confirm": True}).status_code == 409
    rec = env.call("POST", env.worker, f"/turns/{t2['id']}/reconciled", {"prior_claim_id": t2["claim_id"], "finding": "started"})
    assert rec.json == {"state": "uncertain", "disposition": "unresolved_started"}
    r = env.call("POST", env.owner, f"/rooms/{env.room}/turns/{t2['id']}/retry", {"confirm": True})
    assert r.status_code == 201 and r.json["result"]["attempt"] == 2
    assert env.turn(t2["id"])["state"] == "abandoned"
    with env.db() as d: d.execute("UPDATE meetings SET remaining=0 WHERE room=?", (env.room,))
    assert env.claim() is None                                                    # a retry obeys the budget



def test_reconciling_a_started_turn_keeps_a_rejected_sign_in_as_its_cause(env):
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t)
    f = env.call("POST", env.worker, f"/turns/{t['id']}/fail",
                 {"claim_id": t["claim_id"], "outcome": "uncertain", "reason": "signed_out_after_start"})
    assert f.status_code == 200 and env.turn(t["id"])["state"] == "uncertain"
    rec = env.call("POST", env.worker, f"/turns/{t['id']}/reconciled", {"prior_claim_id": t["claim_id"], "finding": "started"})
    assert rec.json == {"state": "uncertain", "disposition": "signed_out_after_start"}


# ── presence, RSVP, continue ────────────────────────────────────────────────

def test_presence_here_is_the_callers_own_and_agents_cannot_report_it(env):
    env.prove(); env.join()
    assert env.call("PUT", env.owner, f"/rooms/{env.room}/presence", {"kind": "here"}).status_code == 200
    robert = [p for p in env.status()["participants"] if p["id"] == "robert"][0]
    assert robert["reach"]["state"] == "here"
    assert env.call("PUT", env.mc, f"/rooms/{env.room}/presence", {"kind": "here"}).status_code == 403
    assert env.call("PUT", env.owner, f"/rooms/{env.room}/presence", {"kind": "reachable"}).status_code == 400


def test_reachable_needs_proof_fresh_readyz_and_no_failure(env):
    now_iso = datetime.now(timezone.utc).isoformat()
    r = env.call("POST", env.worker, "/hosted-teammates/mc/reachable", {"readyz_at": now_iso}).json
    assert r["reachable"] is False and "not_proven" in r["reasons"]
    env.prove()
    assert env.call("POST", env.worker, "/hosted-teammates/mc/reachable", {"readyz_at": past(120)}).json["reachable"] is False
    assert env.call("POST", env.worker, "/hosted-teammates/mc/reachable", {"readyz_at": now_iso}).json["reachable"] is True
    env.join(); env.say("Q"); t = env.claim(); env.start(t)
    env.call("POST", env.worker, f"/turns/{t['id']}/fail", {"claim_id": t["claim_id"], "outcome": "failed", "reason": "relay_busy"})
    assert env.call("POST", env.worker, "/hosted-teammates/mc/reachable", {"readyz_at": now_iso}).json["reachable"] is False
    mc = [p for p in env.status()["participants"] if p["id"] == "mc"][0]
    assert mc["reach"] == {"state": "away", "reason": "last reply failed (relay busy) — Retry or Prove again"}


def test_auto_accept_when_reachable_and_owner_decline_and_no_response(env):
    env.prove()
    env.call("POST", env.worker, "/hosted-teammates/mc/reachable", {"readyz_at": datetime.now(timezone.utc).isoformat()})
    r = env.call("POST", env.owner, f"/rooms/{env.room}/invite", {"actor": "mc"})
    assert r.json["result"]["rsvp"]["rsvp"] == "accepted"
    other = env.call("POST", env.owner, "/rooms", dict(title="T2", purpose="p", recording_acknowledged=True)).json["result"]["id"]
    with env.db() as d: d.execute("DELETE FROM presence")
    env.call("POST", env.owner, f"/rooms/{other}/invite", {"actor": "mc"})
    with env.db() as d: d.execute("UPDATE member_rsvp SET rsvp_at=? WHERE room=?", (past(600), other))
    assert [p for p in env.status(other)["participants"] if p["id"] == "mc"][0]["rsvp"] == "no_response"
    assert env.call("POST", env.owner, f"/rooms/{other}/rsvp", {"actor": "mc", "state": "declined"}).status_code == 200
    assert env.call("POST", env.owner, f"/rooms/{other}/rsvp", {"actor": "mc", "state": "accepted"}).status_code == 403


def test_continue_conversation_opens_a_new_session_with_the_teammate_invited(env):
    env.prove(); env.join(); env.say("Before closing")
    version = env.call("GET", env.owner, f"/rooms/{env.room}").json["version"]
    assert env.call("POST", env.owner, f"/rooms/{env.room}/continue", {}).status_code == 409
    env.call("POST", env.owner, f"/rooms/{env.room}/state", {"state": "closed", "version": version, "checkpoint": "done"})
    r = env.call("POST", env.owner, f"/rooms/{env.room}/continue", {})
    assert r.status_code == 201
    new = r.json["result"]["session_id"]
    assert r.json["result"]["checkpoint_ref"]
    record = env.call("GET", env.owner, f"/rooms/{new}").json
    assert record["parent_room_id"] == env.call("GET", env.owner, f"/rooms/{env.room}").json["parent_room_id"]
    assert [p["rsvp"] for p in env.status(new)["participants"] if p["id"] == "mc"] == ["invited"]


def test_teammate_card_listing_is_owner_only(env):
    cards = env.call("GET", env.owner, "/teammates").json["teammates"]
    assert cards[0]["principal"] == "mc" and cards[0]["connected"] is True and cards[0]["proven_at"] is None
    assert env.call("GET", env.mc, "/teammates").status_code == 403
    assert env.call("PUT", env.owner, "/teammates/mc", {"tools_profile": "x", "bogus": 1}).status_code == 400


def test_provisioning_refuses_to_overwrite_token_files(env):
    with pytest.raises(SystemExit):
        provision_rooms(env.store, "mc", "Master Craftsman", env.tmp / "outbox")
    # The service principals cannot sign in with any legacy key.
    with env.db() as d:
        rows = d.execute("SELECT id,revoked FROM client_credentials WHERE id IN ('legacy:mc','legacy:rooms-worker')").fetchall()
    assert len(rows) == 2 and all(r["revoked"] for r in rows)


# ── export 1.1 (V02-06, M3, S5) ─────────────────────────────────────────────

def test_export_maps_a_committed_turn_and_keeps_history_and_legacy_valid(env):
    from copy import deepcopy
    from jsonschema import ValidationError
    from transcript_snapshot import capture
    from transcript_format import VERSION, VERSION_1_1, render, validate
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t)
    reply = env.reply(t, "MC's exact\nwords", usage_evidence={"status": "none"}).json["result"]
    data, stamp = capture(env.store, "robert", env.room)
    assert data["schema_version"] == VERSION_1_1
    rec = [r for r in data["raw_transcript"] if r["record_id"] == reply["id"]][0]
    assert rec["speaker_id"] == rec["submitted_by"] == "mc" and rec["text"] == "MC's exact\nwords"
    assert rec["origin_assurance"] == "declared" and rec["model"] is None
    assert rec["context_through_seq"] == t["trigger_seq"] - 1
    assert rec["execution"]["turn_id"] == t["id"] and rec["execution"]["upstream_execution_id"] is None
    assert rec["execution"]["coordinating_installation"] == env.info["hosted"]["installation_id"]
    assert {"raw_transcript.execution.upstream_execution_id", "raw_transcript.model"} <= set(data["coverage"]["unknown_fields"])
    assert b"Execution evidence" in render(data, snapshot_at=stamp)["transcript.md"]
    # A Rooms record missing claim_id fails 1.1.
    broken = deepcopy(data); del [r for r in broken["raw_transcript"] if r.get("execution")][0]["execution"]["claim_id"]
    with pytest.raises(ValidationError):
        validate(broken)
    # Legacy shapes stay valid under 1.0 and 1.1: only openclaw_run_id, and empty.
    other = env.call("POST", env.owner, "/rooms", dict(title="Old", purpose="p", recording_acknowledged=True)).json["result"]["id"]
    env.say("historical", room=other)
    old, _ = capture(env.store, "robert", other)
    assert old["schema_version"] == VERSION and all("execution" not in r for r in old["raw_transcript"])
    for execution in ({"openclaw_run_id": "run-7"}, {}):
        for version in (VERSION, VERSION_1_1):
            doc = deepcopy(old); doc["schema_version"] = version
            doc["raw_transcript"][-1]["execution"] = execution
            validate(doc)


def test_published_manifest_identity_matches_its_snapshot(env):
    from transcript_publish import publish, verify
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t); env.reply(t)
    bundle = publish(env.store, "robert", env.room)
    manifest = verify(bundle)
    data = json.loads((bundle / "transcript.json").read_text())
    assert manifest["source_instance_id"] == data["source_instance_id"]
    assert manifest["source_revision"] == data["source_revision"]
    assert manifest["schema_version"] == data["schema_version"] == "minimoi.transcript/1.1"


def test_teammate_reply_needs_a_32_hex_correlation(env):
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t)
    body = {"kind": "message", "body": "x", "turn_id": t["id"], "claim_id": t["claim_id"],
            "expected_context": {"generation": t["generation"], "trigger_seq": t["trigger_seq"]},
            "origin": {"source_application": "rooms_worker", "mode": "agent_response", "agent_id": "mc-agent",
                       "runtime": "OpenClaw", "execution_id": "not-hex"}}
    assert env.call("POST", env.mc, f"/rooms/{env.room}/events", body).status_code == 409



# ── Codex build review fixes (CODEX_REVIEW_ROOMS_R1_BUILD_2026-10-01) ────────

def test_every_turn_mutation_requires_the_current_claim(env):
    env.prove(); env.join(); env.say("Q")
    t = env.claim()
    for route, body in (("start", {}), ("heartbeat", {"intent": "dispatch"}),
                        ("fail", {"outcome": "failed", "reason": "x"}), ("cancel-ack", {})):
        assert env.call("POST", env.worker, f"/turns/{t['id']}/{route}", body).status_code == 409, route
    assert env.turn(t["id"])["state"] == "claimed"


def test_expired_window_is_refused_until_robert_renews_it(env):
    env.prove(); env.join()
    with env.db() as d:
        d.execute("UPDATE meetings SET window_expires=? WHERE room=?", (past(), env.room))
    env.say("Q")
    assert env.claim() is None
    assert env.status()["turns"][0]["disposition"] == "window_expired"
    assert env.call("POST", env.mc, f"/rooms/{env.room}/renew", {}).status_code == 403
    r = env.call("POST", env.owner, f"/rooms/{env.room}/renew", {})
    assert r.status_code == 200 and r.json["result"]["remaining"] == 20
    env.say("Q again")
    assert env.claim()["trigger"]["text"] == "Q again"


def test_teammates_join_only_through_invite(env):
    env.prove()
    r = env.call("POST", env.owner, f"/rooms/{env.room}/members", {"actor": "mc", "role": "contributor"})
    assert r.status_code == 409
    env.join()
    assert env.call("POST", env.owner, f"/rooms/{env.room}/members", {"actor": "mc", "role": "observer"}).status_code == 200


def test_lease_expiry_clears_reachable(env):
    env.prove()
    now_iso = datetime.now(timezone.utc).isoformat()
    env.call("POST", env.worker, "/hosted-teammates/mc/reachable", {"readyz_at": now_iso})
    env.join(); env.say("Q"); t = env.claim(); env.start(t)
    with env.db() as d: d.execute("UPDATE turns SET lease_until=? WHERE id=?", (past(), t["id"]))
    mc = [p for p in env.status()["participants"] if p["id"] == "mc"][0]
    assert mc["reach"]["state"] == "away"


def test_old_unresolved_turns_stay_in_the_status(env):
    env.prove(); env.join(); env.say("first")
    t = env.claim(); env.start(t)
    with env.db() as d: d.execute("UPDATE turns SET lease_until=? WHERE id=?", (past(), t["id"]))
    env.status()
    for n in range(25):
        env.say(f"later {n}")
        with env.db() as d:
            d.execute("UPDATE turns SET state='superseded' WHERE state='queued'")
    ids = [x["id"] for x in env.status()["turns"]]
    assert t["id"] in ids and len(ids) == 21


def test_reconciled_with_a_receipt_commits_and_stop_wording_is_a_request(env):
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t); env.reply(t)
    assert env.call("POST", env.worker, f"/turns/{t['id']}/reconciled",
                    {"prior_claim_id": t["claim_id"], "finding": "nothing"}).json["state"] == "committed"
    env.call("POST", env.owner, f"/rooms/{env.room}/stop", {})
    events = env.call("GET", env.owner, f"/rooms/{env.room}").json["events"]
    assert events[-1]["body"] == "active → paused. Stop requested for all replies."


# ── Codex recheck (CODEX_RECHECK_ROOMS_R1_BUILD_2026-10-01) ─────────────────

def test_reconciled_finds_a_receipt_for_an_uncertain_turn_without_deadlock(env):
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t); env.reply(t)
    with env.db() as d:                                                       # the worker never saw the commit
        d.execute("UPDATE turns SET state='uncertain',disposition='lease_expired' WHERE id=?", (t["id"],))
    r = env.call("POST", env.worker, f"/turns/{t['id']}/reconciled", {"prior_claim_id": t["claim_id"], "finding": "started"})
    assert r.status_code == 200 and r.json["state"] == "committed"
    assert env.turn(t["id"])["state"] == "committed"


@pytest.mark.parametrize("disposition", ["lease_expired", "relay_error", "relay_stopped", "journal_started", None])
def test_retry_accepts_only_reconciled_uncertain_turns(env, disposition):
    env.prove(); env.join(); env.say("Q")
    t = env.claim(); env.start(t)
    with env.db() as d:
        d.execute("UPDATE turns SET state='uncertain',disposition=? WHERE id=?", (disposition, t["id"]))
    assert env.call("POST", env.owner, f"/rooms/{env.room}/turns/{t['id']}/retry", {"confirm": True}).status_code == 409
    with env.db() as d:
        d.execute("UPDATE turns SET disposition='confirmed_absent' WHERE id=?", (t["id"],))
    assert env.call("POST", env.owner, f"/rooms/{env.room}/turns/{t['id']}/retry", {"confirm": True}).status_code == 201



def test_mc_is_the_facilitator_whatever_the_invitation_order(env):
    """Rooms R2: inviting another teammate first must not make it the facilitator."""
    from store import now
    env.prove()
    with env.db() as d:
        d.execute("INSERT INTO principals VALUES('claude-code','Claude Code','agent','h-cc',?)", (now(),))
    env.store.meetings.put_teammate("robert", "claude-code", {})
    env.call("POST", env.owner, f"/rooms/{env.room}/invite", {"actor": "claude-code"})
    env.call("POST", env.owner, f"/rooms/{env.room}/invite", {"actor": "mc"})
    assert env.status()["meeting"]["facilitator"] == "mc"
