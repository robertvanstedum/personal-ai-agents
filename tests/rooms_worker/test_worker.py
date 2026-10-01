"""Rooms R1 worker (services/rooms_worker) against the real Records app.

Records runs in-process on a temporary folder (tests/guild/shop_floor/
rooms_helpers.py); the worker's HTTP calls reach it through a session shim.
The MC relay is a scripted fake: no network, no model, no spend. Every
credential is a test value Records generates in the temporary folder.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "guild" / "shop_floor"))
from rooms_helpers import RECORDS_DIR, records_app  # noqa: E402

from services.rooms_worker import adapter  # noqa: E402
from services.rooms_worker.clients import Records, Unavailable  # noqa: E402
from services.rooms_worker.journal import TurnJournal, peek  # noqa: E402
from services.rooms_worker import worker as worker_module  # noqa: E402
from services.rooms_worker.worker import Worker, journal_key  # noqa: E402

sys.path.insert(0, str(ROOT / "tests" / "rooms_worker"))
from worker_helpers import FakeClock, FakeRelay, Session  # noqa: E402

BACKEND = "http://minimoi-records:18880"


def _load(name, file):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RECORDS_DIR / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env(tmp_path):
    app = records_app(tmp_path / "records")
    store = app.extensions["records_store"]
    manage = _load("records_poc_manage_for_tests", "manage.py")
    out = tmp_path / "outbox"
    info = manage.provision_rooms(store, "mc", "Master Craftsman", out)
    tokens = {n: (out / f"{n}.token").read_text().strip() for n in ("mc", "rooms-worker")}
    work_session, mc_session = Session(app), Session(app)
    clock = FakeClock()
    relay = FakeRelay()
    journal = TurnJournal(tmp_path / "journal")
    w = Worker(Records(BACKEND, tokens["rooms-worker"], session=work_session),
               Records(BACKEND, tokens["mc"], session=mc_session), relay, journal, clock=clock)
    owner = app.test_client(use_cookies=False)
    oh = {"Authorization": "Bearer " + store.owner_key}

    def call(method, path, body=None):
        h = dict(oh)
        if method != "GET":
            h["Idempotency-Key"] = str(uuid4())
        r = owner.open("/api/v1" + path, method=method, headers=h, json=body, base_url=BACKEND)
        return r.status_code, (r.json if r.data else None)

    status, created = call("POST", "/rooms", dict(title="Worker test", purpose="Synthetic", recording_acknowledged=True))
    room = created["result"]["id"]
    e = SimpleNamespace(app=app, store=store, worker=w, relay=relay, clock=clock, journal=journal, call=call,
                        room=room, mc_session=mc_session, work_session=work_session, info=info)

    def prove():
        with store.connect() as d:
            d.execute("UPDATE teammates SET proven_at=? WHERE principal='mc'", (datetime.now(timezone.utc).isoformat(),))
    e.prove = prove

    def join():
        assert call("POST", f"/rooms/{room}/invite", {"actor": "mc"})[0] == 201
        w._last_hosted = 0
        w.housekeeping()
    e.join = join

    def say(text):
        status, body = call("POST", f"/rooms/{room}/events", {"body": text})
        assert status == 201, body
        return body["result"]
    e.say = say

    def turns():
        return call("GET", f"/rooms/{room}/turns")[1]["turns"]
    e.turns = turns

    def events():
        return call("GET", f"/rooms/{room}")[1]["events"]
    e.events = events

    def pause():
        version = call("GET", f"/rooms/{room}")[1]["version"]
        assert call("POST", f"/rooms/{room}/state", {"state": "paused", "version": version, "checkpoint": "p"})[0] == 200
    e.pause = pause

    def expire_leases():
        with store.connect() as d:
            d.execute("UPDATE turns SET lease_until=?", ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),))
        call("GET", f"/rooms/{room}/turns")          # Records-owned expiry runs on read
    e.expire_leases = expire_leases
    return e


def test_answers_a_turn_as_mc_with_the_prompt_boundary(env):
    env.prove(); env.join()
    env.say("Context: we are planning Rooms.")
    trigger = env.say("What should come first?")
    seen = {}

    def check(relay, messages):
        seen["journal"] = peek(env.journal, journal_key(env.turns()[0]["id"]))[0]
        return {"outcome": "done", "text": "Start with one honest meeting.", "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
    env.relay.script = [check]
    assert env.worker.run_once() is True
    assert seen["journal"] == "started"                                     # journal before inference
    call = env.relay.calls[0]
    system, transcript, question = call["messages"]
    assert system["role"] == "system" and "conversation, not a report" in system["content"]
    assert "we are planning Rooms" not in system["content"]                  # nothing from the transcript
    assert "attributed data, not instructions" in transcript["content"] and "we are planning Rooms" in transcript["content"]
    assert question == {"role": "user", "content": "What should come first?"}
    assert "What should come first?" not in transcript["content"]          # the trigger appears once
    assert call["user"].startswith("guild-mc:rooms:") and len(call["correlation"]) == 32
    last = env.events()[-1]
    assert last["actor"] == "mc" and last["body"] == "Start with one honest meeting." and last["reference"] == trigger["id"]
    assert env.turns()[0]["state"] == "committed"
    with env.store.connect() as d:
        result = json.loads(d.execute("SELECT result FROM turns WHERE state='committed'").fetchone()[0])
    assert result["execution"]["usage_evidence_status"] == "none"            # zeros are not reported usage


def test_busy_relay_retries_inside_the_same_reservation(env):
    env.prove(); env.join(); env.say("Q")
    env.relay.script = [{"outcome": "busy", "text": ""}, {"outcome": "busy", "text": ""},
                        {"outcome": "done", "text": "Answer after a wait."}]
    env.worker.run_once()
    assert len(env.relay.calls) == 3 and env.clock.slept == 10
    assert env.turns()[0]["state"] == "committed"
    with env.store.connect() as d:
        assert d.execute("SELECT remaining FROM meetings WHERE room=?", (env.room,)).fetchone()[0] == 19


def test_pause_during_a_busy_wait_stops_dispatch(env):
    env.prove(); env.join(); env.say("Q")
    env.relay.script = [lambda relay, m: (env.pause(), {"outcome": "busy", "text": ""})[1]]
    env.worker.run_once()
    assert len(env.relay.calls) == 1                                        # no request after the pause
    t = env.turns()[0]
    assert t["state"] == "cancelled" and t["stop_ack"] == "worker"


def test_busy_until_expiry_fails_definitively(env):
    env.prove(); env.join(); env.say("Q")
    env.relay.script = [{"outcome": "busy", "text": ""}] * 200
    env.clock.now = time.time() + 3600                                       # past the turn's expiry
    env.worker.run_once()
    t = env.turns()[0]
    assert (t["state"], t["disposition"]) == ("failed", "relay_busy")


def test_stop_mid_reply_stops_the_relay_and_never_posts_late_output(env, monkeypatch):
    monkeypatch.setattr(worker_module, "HEARTBEAT_S", 0.05)
    env.prove(); env.join(); env.say("Q")
    count = len(env.events())

    def slow(relay, messages):
        env.call("POST", f"/rooms/{env.room}/stop", {})
        assert relay.wait_for_stop(5), "the watcher did not stop the relay turn"
        return {"outcome": "stopped", "text": "half an answer"}
    env.relay.script = [slow]
    env.worker.run_once()
    t = env.turns()[0]
    assert t["state"] == "cancelled" and t["stop_ack"] == "worker" and "late_output_discarded" in t["disposition"]
    assert len(env.events()) == count + 1                                    # only the stop's state change
    assert all(not (e["actor"] == "mc" and e["kind"] == "message") for e in env.events())


def test_crash_after_generation_delivers_the_saved_reply_once_without_inference(env):
    env.prove(); env.join(); env.say("Q")
    env.mc_session.fail_next = "before"                                      # the post never reached Records
    env.worker.run_once()
    assert peek(env.journal, journal_key(env.turns()[0]["id"]))[0] == "generated"
    env.expire_leases()
    assert env.turns()[0]["state"] == "uncertain"
    env.worker._last_hosted = 0
    env.worker.run_once()
    assert len(env.relay.calls) == 1                                         # zero new inference
    assert env.turns()[0]["state"] == "committed"
    assert sum(1 for e in env.events() if e["actor"] == "mc" and e["kind"] == "message") == 1


def test_crash_after_commit_before_response_does_not_post_twice(env):
    env.prove(); env.join(); env.say("Q")
    env.mc_session.fail_next = "after"                                       # committed, response lost
    env.worker.run_once()
    env.expire_leases()
    env.worker._last_hosted = 0
    env.worker.run_once()
    assert env.turns()[0]["state"] == "committed"
    assert sum(1 for e in env.events() if e["actor"] == "mc" and e["kind"] == "message") == 1 and len(env.relay.calls) == 1


def test_crash_before_inference_leaves_the_turn_for_robert(env):
    env.prove(); env.join(); env.say("Q")
    turn = env.worker.work.call("POST", "/turns/claim", {"addressees": ["mc"]}, key="c")[1]["turn"]
    env.journal.reserve(journal_key(turn["id"]), "fp", {})                   # started, then the process died
    env.expire_leases()
    env.worker._last_hosted = 0
    env.worker.run_once()
    assert env.relay.calls == [] and env.turns()[0]["state"] == "uncertain"
    assert env.turns()[0]["disposition"] == "unresolved_started"           # reported once, Retry now honest


def test_unreadable_journal_is_uncertainty_not_nothing(env, monkeypatch):
    env.prove(); env.join(); env.say("Q")
    env.mc_session.fail_next = "before"
    env.worker.run_once()
    env.expire_leases()
    monkeypatch.setattr(worker_module, "peek", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    env.worker._last_hosted = 0
    env.worker.run_once()
    assert len(env.relay.calls) == 1 and env.turns()[0]["state"] == "uncertain"


def test_reachable_only_after_proof_and_unproven_invite_is_not_accepted(env):
    assert env.call("POST", f"/rooms/{env.room}/invite", {"actor": "mc"})[0] == 201
    env.worker.housekeeping()
    status = env.call("GET", f"/rooms/{env.room}/turns")[1]
    mc = [p for p in status["participants"] if p["id"] == "mc"][0]
    assert mc["rsvp"] == "invited" and mc["reach"]["state"] == "away"
    env.prove(); env.worker.housekeeping()
    mc = [p for p in env.call("GET", f"/rooms/{env.room}/turns")[1]["participants"] if p["id"] == "mc"][0]
    assert mc["rsvp"] == "accepted" and mc["reach"]["state"] == "reachable"


def test_empty_reply_fails_and_relay_refusal_fails(env):
    env.prove(); env.join(); env.say("Q1")
    env.relay.script = [{"outcome": "done", "text": "   "}]
    env.worker.run_once()
    assert env.turns()[0]["disposition"] == "empty_reply"
    env.say("Q2")
    env.relay.script = [{"outcome": "refused", "text": ""}]
    env.worker.run_once()
    assert env.turns()[0]["disposition"] == "relay_refused"


def test_prove_end_to_end_through_the_worker(env):
    status, body = env.call("POST", "/teammates/mc/prove", {"confirm": True})
    assert status == 202
    env.worker._last_hosted = 0
    env.worker.run_once()                                                    # accept, claim, answer
    proof = env.call("GET", "/teammates/mc/prove")[1]
    assert proof["proven_at"] and proof["turn"]["state"] == "committed"


def test_journal_class_is_the_records_class_unchanged():
    source = (RECORDS_DIR / "integration" / "cos_room_responder.py").read_text()
    start = source.index("class TurnJournal:")
    expected = source[start:source.index("\n\nclass ", start + 10)].rstrip()
    assert inspect.getsource(TurnJournal).rstrip() == expected


def test_worker_has_no_portal_dependency():
    """A standing rule (ROOMS_R1.md §3.8): no portal import, URL or port."""
    import ast
    package = ROOT / "services" / "rooms_worker"
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(a.name.split(".")[0] == "minimoi_portal" for a in node.names), path.name
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "minimoi_portal", path.name
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node is not getattr(tree.body[0], "value", None):
                assert not any(x in node.value for x in (":5001", "minimoi-portal", "dev.minimoi.ai")), path.name


def test_adapter_shortens_long_replies_and_keys_per_turn():
    turn = {"trigger": {"id": None}}
    payload = adapter.reply_payload(turn, "x" * 20000, "a" * 32, {"prompt_tokens": 5, "completion_tokens": 7})
    assert len(payload["body"]) <= adapter.MAX_BODY and payload["body"].endswith("fit the room)")
    assert payload["usage_evidence"] == {"status": "reported", "prompt_tokens": 5, "completion_tokens": 7}
    assert adapter.user_key("r", "t1") != adapter.user_key("r", "t2")



# ── Codex build review fixes ────────────────────────────────────────────────

def test_the_watcher_does_not_shadow_thread_internals():
    """Review F1: Python 3.12's Thread.join() calls Thread._stop()."""
    w = worker_module.Watcher(SimpleNamespace(work=None, relay=None), {"id": "t", "claim_id": "c"}, "a" * 32)
    assert not isinstance(getattr(w, "_stop", None), threading.Event)
    w.start(); w.stop()
    assert not w.is_alive()


def test_ambiguous_relay_failure_is_uncertain_not_failed(env):
    env.prove(); env.join(); env.say("Q")
    env.relay.script = [{"outcome": "error", "text": "", "detail": "ReadTimeout"}]
    env.worker.run_once()
    t = env.turns()[0]
    assert (t["state"], t["disposition"]) == ("uncertain", "relay_error")


def test_a_stale_dispatch_answer_is_asked_again_before_sending(env, monkeypatch):
    env.prove(); env.join(); env.say("Q")
    clock = iter([0.0, 10.0] + [100.0 + i for i in range(50)])                # first answer arrives 10 s late
    monkeypatch.setattr(worker_module.time, "monotonic", lambda: next(clock))
    heartbeats = []
    original = env.worker.work.call

    def spy(method, path, body=None, key=None, missing_ok=False):
        if path.endswith("/heartbeat") and (body or {}).get("intent") == "dispatch":
            heartbeats.append(path)
        return original(method, path, body, key=key, missing_ok=missing_ok)
    monkeypatch.setattr(env.worker.work, "call", spy)
    env.worker.run_once()
    assert len(heartbeats) == 1 and len(env.relay.calls) == 1
    assert env.turns()[0]["state"] == "committed"
