"""Master Craftsman's backend switch on /guild-next (separate-container plan, PR 1: dormant).

MINIMOI_GUILD_MC selects off | stub | openclaw | grok. Honest states only: an
unavailable backend is "unavailable" (never the stub, never a fake answer),
the Grok backend is labelled "not built yet", and a stub reply can never be
kept as Master Craftsman's. Nothing here makes a network or model call.
"""
from __future__ import annotations

import threading

import pytest

from minimoi_portal.guild_ui.mc import (MASTER_CRAFTSMAN_STUB, CachedHealth, Health, NotAnAnswer, OffBackend,
                                        TurnRequest, TurnResult, UnavailableBackend, backend_from_env, keep_reply,
                                        reply_author, switch_value, view)
from minimoi_portal.guild_ui.mc import backend as mc_backend
from minimoi_portal.guild_ui.mc.grok import GrokMasterCraftsman
from minimoi_portal.guild_ui.mc.openclaw import MODEL, OpenClawMasterCraftsman
from minimoi_portal.guild_ui.mc.stub import StubBackend
from minimoi_portal.guild_ui.stores import MASTER_CRAFTSMAN

from floor_helpers import load_portal  # noqa: F401  (pytest fixture)

API = "/guild-next/api/v1"
REQ = TurnRequest(conversation_id="guild:robert:2026-09-28:0", text="what is stuck?", note_request_id="n-1",
                  context={"about": "queue", "item_ref": 12, "page": "floor"})


# ── the switch ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (None, "off"), ("", "off"), ("off", "off"), ("0", "off"), ("stub", "stub"), ("STUB", "stub"),
    ("openclaw", "openclaw"), (" grok ", "grok"), ("on", "off"), ("1", "off"), ("claude", "off"),
])
def test_switch_values_default_to_off(raw, expected):
    environ = {} if raw is None else {"MINIMOI_GUILD_MC": raw}
    assert switch_value(environ) == expected


def test_each_switch_value_builds_its_backend_without_any_call():
    def no_http(*_a, **_k):
        raise AssertionError("building or asking health must not call the network when not connected")

    assert isinstance(backend_from_env({}), OffBackend)
    assert isinstance(backend_from_env({"MINIMOI_GUILD_MC": "stub"}), StubBackend)
    assert isinstance(backend_from_env({"MINIMOI_GUILD_MC": "grok"}), GrokMasterCraftsman)
    oc = backend_from_env({"MINIMOI_GUILD_MC": "openclaw"}, http_get=no_http, http_post=no_http)
    assert isinstance(oc, OpenClawMasterCraftsman)
    assert oc.health().state == "unavailable" and oc.health().reason == "not_connected"


def test_a_backend_that_cannot_be_built_is_unavailable_never_off_or_stub():
    backend = backend_from_env({"MINIMOI_GUILD_MC": "openclaw", "MC_RUNTIME_URL": "ftp://nope"})
    assert isinstance(backend, UnavailableBackend)
    assert backend.health() == Health("unavailable", "misconfigured", backend.health().observed_at)
    assert backend.turn(REQ).status == "unavailable"


def test_grok_backend_is_labelled_not_built_and_never_answers():
    grok = GrokMasterCraftsman()
    assert grok.built is False
    assert grok.health().reason == "not_built"
    result = grok.turn(REQ)
    assert result.status == "unavailable" and result.failure_class == "not_built" and result.text is None
    shown = view(grok.health(), notes_ok=True)
    assert shown["state"] == "unavailable"
    assert shown["header"] == "Master Craftsman is unavailable · the Grok backend is not built yet"
    assert shown["turns"] is False


# ── honest states on the floor ────────────────────────────────────────────────

def test_view_per_state():
    off = view(Health("off"), notes_ok=True)
    assert off["state"] == "off" and off["header"] == "Master Craftsman is off · your messages are kept as notes"
    assert off["notes_text"].endswith("Master Craftsman does not reply.")
    stub = view(Health("stub"), notes_ok=True)
    assert stub["state"] == "stub" and stub["header"] == "Master Craftsman stub · scripted replies, not an agent"
    unavailable = view(Health("unavailable", "not_connected"), notes_ok=True)
    assert unavailable["state"] == "unavailable"
    assert unavailable["header"] == "Master Craftsman is unavailable · not connected yet"
    assert unavailable["notes_text"].endswith("Master Craftsman does not reply.")
    live = view(Health("ready"), notes_ok=True)
    assert live["state"] == "live" and live["header"] == "Master Craftsman is live · your messages are kept as notes"
    assert "does not reply yet" in live["notes_text"]   # turn wiring is stage B
    for health in (Health("off"), Health("stub"), Health("ready"), Health("unavailable", "x")):
        assert view(health, notes_ok=True)["turns"] is False                   # no turn is sent in 1a


def test_notes_down_means_no_turns_and_says_so():
    assert view(Health("off"), notes_ok=False)["header"] == \
        "Master Craftsman is off · notes unavailable, nothing you send is kept"
    for health in (Health("stub"), Health("ready"), Health("unavailable", "not_connected")):
        shown = view(health, notes_ok=False)
        assert shown["header"] == "Master Craftsman can't take turns · notes unavailable, nothing you send is kept"
        assert shown["turns"] is False


def test_unknown_unavailable_reason_still_reads_unavailable():
    shown = view(Health("unavailable", "something_new"), notes_ok=True)
    assert shown["state"] == "unavailable" and shown["header"].startswith("Master Craftsman is unavailable · ")


def test_health_is_cached_and_a_broken_check_is_unavailable():
    calls = []

    class Flaky(OffBackend):
        def health(self):
            calls.append(1)
            raise RuntimeError("boom")

    now = [0.0]
    cached = CachedHealth(Flaky(), ttl=60, clock=lambda: now[0])
    assert cached.get().state == "unavailable" and cached.get().reason == "health_check_failed"
    assert len(calls) == 1
    now[0] = 61
    cached.get()
    assert len(calls) == 2


@pytest.mark.parametrize("switch,state,header", [
    (None, "off", "Master Craftsman is off · your messages are kept as notes"),
    ("stub", "stub", "Master Craftsman stub · scripted replies, not an agent"),
    ("openclaw", "unavailable", "Master Craftsman is unavailable · not connected yet"),
    ("grok", "unavailable", "Master Craftsman is unavailable · the Grok backend is not built yet"),
])
def test_staging_floor_and_session_show_the_switch_state(load_portal, monkeypatch, switch, state, header):
    if switch is None:
        monkeypatch.delenv("MINIMOI_GUILD_MC", raising=False)
    else:
        monkeypatch.setenv("MINIMOI_GUILD_MC", switch)
    monkeypatch.delenv("MC_RUNTIME_URL", raising=False)
    monkeypatch.delenv("MC_RUNTIME_TOKEN", raising=False)
    client = load_portal().owner()
    assert client.get(f"{API}/session").get_json()["mc_state"] == state
    floor = client.get(f"{API}/floor").get_json()
    assert floor["mc_state"] == state
    assert floor["mc"]["turns"] is False
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert f'data-mc-state="{state}"' in page
    if floor["notes"]["state"] == "ok":
        assert floor["mc_header"] == header
    else:   # no floor database in this test: the notes-down header wins
        assert "notes unavailable" in floor["mc_header"]


def test_openclaw_mode_without_a_runtime_never_shows_an_answer_or_the_stub(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_MC", "openclaw")
    monkeypatch.delenv("MC_RUNTIME_URL", raising=False)
    monkeypatch.delenv("MC_RUNTIME_TOKEN", raising=False)
    body = load_portal().owner().get("/guild-next/guild/build").get_data(as_text=True)
    assert "stub" not in body.lower()
    assert "Master Craftsman is live" not in body


def test_the_switch_is_ignored_off_a_staging_origin(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_MC", "stub")
    portal = load_portal(base_url="https://minimoi.ai")
    assert portal.module.GUILD_MOUNTS["guild_next"] == "refused_not_staging"
    assert portal.owner().get(f"{API}/session").status_code == 404


# ── the OpenClaw adapter: errors are never answers ────────────────────────────

class Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


def _oc(post=None, get=None):
    calls = {"post": [], "get": []}

    def http_post(url, **kw):
        calls["post"].append((url, kw))
        return post(url, **kw) if callable(post) else post

    def http_get(url, **kw):
        calls["get"].append((url, kw))
        return get(url, **kw) if callable(get) else get

    backend = OpenClawMasterCraftsman("http://mc-agent:18789/v1", "mc-caller-token",
                                      http_get=http_get, http_post=http_post)
    return backend, calls


ANSWER = '{"choices":[{"message":{"role":"assistant","content":"Item 12 is in build."}}],"usage":{"prompt_tokens":9,"completion_tokens":5}}'


def test_not_connected_refuses_every_turn_without_a_call():
    backend = OpenClawMasterCraftsman(None, None, http_get=lambda *a, **k: 1 / 0, http_post=lambda *a, **k: 1 / 0)
    result = backend.turn(REQ)
    assert result.status == "unavailable" and result.failure_class == "not_connected" and result.text is None


def test_answered_only_with_text_and_usage_and_pinned_to_mc_agent():
    backend, calls = _oc(post=Resp(200, ANSWER))
    result = backend.turn(REQ)
    assert result.status == "answered" and result.text == "Item 12 is in build." and result.usage["prompt_tokens"] == 9
    url, kw = calls["post"][0]
    assert url == "http://mc-agent:18789/v1/chat/completions"
    import json
    body = json.loads(kw["data"])
    assert body["model"] == MODEL == "openclaw/mc-agent"
    assert body["user"].startswith("guild-mc:") and "robert" not in body["user"]
    assert kw["headers"]["Authorization"] == "Bearer mc-caller-token"


@pytest.mark.parametrize("status,text,turn_status,failure", [
    (200, '{"choices":[{"message":{"content":"hi"}}]}', "error", "no_run_status"),
    # Stage C honesty guard: text with zero completion tokens is not an answer.
    (200, '{"choices":[{"message":{"content":"Budget has been exceeded"}}],"usage":{"prompt_tokens":5,"completion_tokens":0}}',
     "error", "no_run_status"),
    (200, '{"choices":[{"message":{"content":""}}],"usage":{}}', "error", "no_run_status"),
    (200, "not json", "error", "malformed_answer"),
    (408, '{"error":{"message":"upstream provider timeout"}}', "unavailable", "model_gateway_down"),
    (500, '{"error":{"message":"LLM request failed: 401 invalid api key"}}', "unavailable", "key_refused"),
    (500, '{"error":{"message":"Budget has been exceeded"}}', "unavailable", "cap_reached"),
    (401, '{"error":{"message":"Unauthorized","type":"unauthorized"}}', "unavailable", "caller_refused"),
    # What OpenClaw 9.6 returns when the model gateway refuses MC's key (the 1a placeholder):
    (401, '{"error":{"message":"401 invalid key","type":"authentication_error"}}', "unavailable", "key_refused"),
    (429, "", "duplicate_in_progress", "one_turn_in_flight"),
    (429, '{"error":{"message":"Rate limit reached: rpm 10"}}', "unavailable", "rate_limited"),
    (429, '{"error":{"message":"Budget has been exceeded"}}', "unavailable", "cap_reached"),
    (502, "bad gateway", "error", "runtime_error"),
])
def test_errors_are_never_answers(status, text, turn_status, failure):
    backend, _ = _oc(post=Resp(status, text))
    result = backend.turn(REQ)
    assert (result.status, result.failure_class) == (turn_status, failure)
    assert result.text is None


def test_timeouts_and_connection_errors():
    class ReadTimeout(Exception):
        pass

    class ConnectionError_(Exception):
        pass

    def raise_(exc):
        def f(*_a, **_k):
            raise exc
        return f

    assert _oc(post=raise_(ReadTimeout()))[0].turn(REQ).status == "timeout_uncertain"
    result = _oc(post=raise_(ConnectionError_()))[0].turn(REQ)
    assert result.status == "unavailable" and result.failure_class == "not_ready"


def test_cancel_and_size_cap_send_nothing():
    backend, calls = _oc(post=Resp(200, ANSWER))
    cancel = threading.Event()
    cancel.set()
    assert backend.turn(REQ, cancel).status == "cancelled"
    big = TurnRequest("c", "x" * 270_000, "n-2")
    result = backend.turn(big)
    assert result.status == "refused" and result.failure_class == "request_too_large"
    assert calls["post"] == []


def test_health_needs_readyz_and_is_live_only_after_an_answer():
    def get(ready, marker):
        return lambda url, **_k: Resp(ready if url.endswith("/readyz") else marker)

    backend, calls = _oc(get=get(200, 404), post=Resp(200, ANSWER))
    first = backend.health()
    # Reachable, but never "live" before a real answer (PR 249 review 3b).
    assert (first.state, first.reason, first.reachable) == ("unavailable", "not_verified", True)
    assert [u for u, _ in calls["get"]] == ["http://mc-agent:18789/readyz"]
    assert calls["get"][0][1]["headers"]["Authorization"] == "Bearer mc-caller-token"   # the relay needs its caller token
    assert backend.turn(REQ).status == "answered"
    assert backend.health().state == "ready"
    refused, _ = _oc(get=get(200, 404), post=Resp(500, '{"error":{"message":"LLM request failed: 400 No connected db."}}'))
    refused.turn(REQ)
    after = refused.health()
    assert (after.state, after.reason, after.reachable) == ("unavailable", "key_refused", True)
    assert _oc(get=get(503, 200))[0].health().reason == "not_ready"

    marked = OpenClawMasterCraftsman("http://mc-agent:18789/v1", "mc-token", ready_path="/mc-ready",
                                     http_get=get(200, 404), http_post=lambda *a, **k: None)
    assert marked.health().reason == "starting"
    with pytest.raises(ValueError):
        OpenClawMasterCraftsman("http://mc-agent:18789/v1", "t", ready_path="mc-ready",
                                http_get=lambda *a, **k: None, http_post=lambda *a, **k: None)


# ── kept replies: a stub reply is never Master Craftsman's ────────────────────

class FakeFloor:
    def __init__(self):
        self.notes = []

    def add_note(self, request_id, text, author, **kw):
        self.notes.append((request_id, text, author, kw))
        return "kept"


def test_stub_replies_are_kept_as_the_stub_never_as_master_craftsman():
    result = StubBackend().turn(REQ)
    assert result.status == "answered" and result.backend_kind == "stub"
    assert reply_author(result) == MASTER_CRAFTSMAN_STUB
    assert reply_author(result).id == "master_craftsman_stub" and reply_author(result).kind == "platform"
    floor = FakeFloor()
    keep_reply(floor, result, request_id="r-1", area="queue")
    assert floor.notes[0][2] == MASTER_CRAFTSMAN_STUB
    assert floor.notes[0][2] != MASTER_CRAFTSMAN


def test_only_real_answers_are_master_craftsmans_and_failures_are_never_kept():
    assert reply_author(TurnResult("answered", "openclaw", text="ok")) == MASTER_CRAFTSMAN
    assert reply_author(TurnResult("answered", "grok", text="ok")) == MASTER_CRAFTSMAN
    floor = FakeFloor()
    for result in (TurnResult("unavailable", "openclaw", failure_class="not_connected"),
                   TurnResult("error", "openclaw"), TurnResult("timeout_uncertain", "openclaw"),
                   TurnResult("refused", "off")):
        with pytest.raises(NotAnAnswer):
            keep_reply(floor, result, request_id="r-2")
    with pytest.raises(NotAnAnswer):
        reply_author(TurnResult("answered", "off", text="?"))
    assert floor.notes == []
    with pytest.raises(ValueError):
        TurnResult("answered", "openclaw", text="  ")
    with pytest.raises(ValueError):
        TurnResult("maybe", "openclaw")


def test_the_turn_gate_is_explicit_and_off_by_default():
    assert mc_backend.turns_enabled({}) is False
    for value in ("0", "off", "no", "", "maybe"):
        assert mc_backend.turns_enabled({"MINIMOI_GUILD_MC_TURNS": value}) is False
    for value in ("1", "on", "true", "YES"):
        assert mc_backend.turns_enabled({"MINIMOI_GUILD_MC_TURNS": value}) is True
    reachable = Health("unavailable", "not_verified", reachable=True)
    assert view(reachable, notes_ok=True, turns_on=False)["turns"] is False
    assert view(reachable, notes_ok=True, turns_on=True)["turns"] is True
    assert view(Health("unavailable", "not_connected"), notes_ok=True, turns_on=True)["turns"] is False
    assert view(reachable, notes_ok=False, turns_on=True)["turns"] is False
    assert view(Health("off"), notes_ok=True, turns_on=True)["turns"] is False


# ── the owner route with the gate off ─────────────────────────────────────────

def _write(client, token, body=None, **headers):
    return client.post(f"{API}/mc/turns", json=body or {"note_request_id": "n-12345678", "idempotency_key": "turn-abcdefgh"},
                       headers={"X-CSRF-Token": token, "X-Record-Mode": "on_record", **headers})


@pytest.mark.parametrize("switch", [None, "stub", "openclaw", "grok"])
def test_with_the_gate_off_the_route_refuses_and_calls_nothing(load_portal, monkeypatch, switch):
    monkeypatch.delenv("MINIMOI_GUILD_MC_TURNS", raising=False)
    if switch is None:
        monkeypatch.delenv("MINIMOI_GUILD_MC", raising=False)
    else:
        monkeypatch.setenv("MINIMOI_GUILD_MC", switch)
    calls = []
    monkeypatch.setattr(StubBackend, "turn", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(OpenClawMasterCraftsman, "turn", lambda *a, **k: calls.append(a))
    portal = load_portal()
    client = portal.owner()
    token = portal.csrf(client)
    response = _write(client, token)
    assert response.status_code == 409
    body = response.get_json()
    assert body["error"] == "mc_turns_off" and body["message"].endswith("Nothing was sent to Master Craftsman.")
    assert "kept" not in body["message"]     # the route never claims a note it did not check (PR 249 review 3a)
    assert calls == []


def test_owner_route_is_guarded_like_every_floor_write(load_portal):
    portal = load_portal()
    anonymous = portal.client().post(f"{API}/mc/turns", json={})
    assert anonymous.status_code == 401
    guest = portal.guest().post(f"{API}/mc/turns", json={})
    assert guest.status_code == 403
    owner = portal.owner()
    token = portal.csrf(owner)
    assert _write(owner, "wrong-token").status_code == 403
    assert _write(owner, token, **{"X-Record-Mode": "off_record"}).status_code == 409
    assert owner.get(f"{API}/mc/turns").status_code in (404, 405)
