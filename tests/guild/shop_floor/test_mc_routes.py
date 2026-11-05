"""A conversation's model route through the real portal: owner scope, revisions, next-turn switching, pinning, no fallback."""
from __future__ import annotations

import json
import threading

import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from test_mc_turns import _keep, _openclaw, _turn_on
from test_mc_streaming import HAPPY, StreamRuntime, _fresh_dispatch_state, _no_busy_wait_by_default, _stream, nd  # noqa: F401  (autouse fixtures)
from minimoi_portal.guild_ui.mc.backend import TurnRequest
from minimoi_portal.guild_ui.mc.openclaw import OpenClawMasterCraftsman

API = "/guild-next/api/v1"
ASTRA, CLAUDE = "plan-openai-strong", "plan-anthropic-strong"
SOL = "plan-openai-mid"


@pytest.fixture
def catalogue(monkeypatch):
    """The real catalogue with one more proved route, so a switch can be exercised without offering Claude."""
    import core.model_routes as routes
    real = routes.load_catalogue()
    real = json.loads(json.dumps(real))
    next(r for r in real["routes"] if r["route_id"] == SOL)["offered"]["mc"] = "available"
    monkeypatch.setattr(routes, "load_catalogue", lambda *a, **k: real)
    return real


@pytest.fixture
def routed(floored, catalogue):
    runtime = StreamRuntime(HAPPY + [nd({"t": "served", "provider": "openai", "model": "gpt-6-astra", "api": "openai-chatgpt-responses", "harness": "openclaw"})])
    services = _turn_on(floored, _openclaw(runtime))
    services.mc_stream = True
    floored.extra.update(runtime=runtime, services=services)
    floored.runtime = runtime
    return floored


def _new_conv(client, token):
    r = client.post(f"{API}/conversations", json=keyed(), headers=write_headers(token))
    assert r.status_code in (200, 201), r.get_json()
    return r.get_json()["conversation"]["id"]


def _choose(client, token, cid, route_id, revision, **extra):
    return client.post(f"{API}/conversations/{cid}/route", json=keyed(route_id=route_id, revision=revision, **extra),
                       headers=write_headers(token))


def _route_sent(runtime, index=-1):
    return runtime.posts[index]["headers"].get("X-MC-Route")


def test_a_new_conversation_shows_the_default_and_the_unavailable_routes_with_reasons(routed):
    client = routed.owner()
    cid = _new_conv(client, routed.csrf(client))
    view = client.get(f"{API}/conversations/{cid}/route").get_json()
    assert view["selected_route_id"] == ASTRA and view["revision"] == 0 and view["selectable"] is True
    shown = {r["route_id"]: r for r in view["routes"]}
    assert shown[ASTRA]["billing_label"] == "Plan" and shown[ASTRA]["tier_letter"] == "A"
    assert not shown[CLAUDE]["available"] and shown[CLAUDE]["reason"]


def test_a_choice_survives_a_reload_and_stays_in_its_own_conversation(routed):
    client = routed.owner()
    token = routed.csrf(client)
    one, two = _new_conv(client, token), _new_conv(client, token)
    assert _choose(client, token, one, SOL, 0).status_code == 200
    assert client.get(f"{API}/conversations/{one}/route").get_json()["selected_route_id"] == SOL
    assert client.get(f"{API}/conversations/{two}/route").get_json()["selected_route_id"] == ASTRA


def test_a_stale_page_changes_nothing(routed):
    client = routed.owner()
    token = routed.csrf(client)
    cid = _new_conv(client, token)
    assert _choose(client, token, cid, SOL, 0).status_code == 200
    stale = _choose(client, token, cid, ASTRA, 0)
    assert stale.status_code == 409 and stale.get_json()["error"] == "route_changed" and stale.get_json()["revision"] == 1
    assert client.get(f"{API}/conversations/{cid}/route").get_json()["selected_route_id"] == SOL


@pytest.mark.parametrize("route_id,code", [(CLAUDE, "route_unavailable"), ("gateway-cos-role", "route_unknown"),
                                           ("openai/gpt-6-astra", "route_unknown"), ("http://evil/v1", "route_unknown")])
def test_only_offered_routes_can_be_chosen(routed, route_id, code):
    client = routed.owner()
    token = routed.csrf(client)
    cid = _new_conv(client, token)
    r = _choose(client, token, cid, route_id, 0)
    assert r.status_code == 409 and r.get_json()["error"] == code
    assert client.get(f"{API}/conversations/{cid}/route").get_json()["revision"] == 0


def test_browser_fields_beyond_the_route_id_are_ignored(routed):
    client = routed.owner()
    token = routed.csrf(client)
    cid = _new_conv(client, token)
    assert _choose(client, token, cid, SOL, 0, model="anthropic/x", endpoint="http://evil", api_key="k").status_code == 200
    stored = client.get(f"{API}/conversations/{cid}/route").get_json()
    assert stored["selected"]["model"] == "gpt-6-sol"


def test_a_route_change_needs_csrf_and_someone_elses_conversation_is_not_found(routed):
    client = routed.owner()
    token = routed.csrf(client)
    cid = _new_conv(client, token)
    assert client.post(f"{API}/conversations/{cid}/route", json=keyed(route_id=SOL, revision=0)).status_code in (400, 403)
    assert client.get(f"{API}/conversations/c-000000000000/route").status_code == 404


def test_the_turn_sends_only_the_route_id_and_keeps_the_runtimes_evidence_apart(routed):
    client = routed.owner()
    token = routed.csrf(client)
    note = _keep(client, token, "hello")
    r = _stream(client, token, note["request_id"])
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "openai-chatgpt-responses" not in body and '"served"' not in body       # evidence is not a browser event
    assert _route_sent(routed.runtime) == ASTRA
    assert "x-openclaw-model" not in {k.lower() for k in routed.runtime.posts[-1]["headers"]}


def _conversation_of(client, note):
    return note.get("conversation_id") or client.get(f"{API}/conversations").get_json()["conversations"][0]["id"]


def test_a_switch_during_a_turn_leaves_that_turn_alone_and_applies_to_the_next(routed):
    client, other = routed.owner(), routed.owner()
    token = routed.csrf(client)
    note = _keep(client, token, "first")
    cid = _conversation_of(client, note)
    runtime = routed.runtime
    runtime.script = [HAPPY[0], "WAIT"] + HAPPY[1:]
    out = {}
    first = threading.Thread(target=lambda: out.update(first=_stream(client, token, note["request_id"]).get_data(as_text=True)))
    first.start()
    assert runtime.waiting.wait(5)
    revision = other.get(f"{API}/conversations/{cid}/route").get_json()["revision"]
    assert _choose(other, routed.csrf(other), cid, SOL, revision).status_code == 200
    runtime.gate.set()
    first.join(10)
    turn_posts = [p for p in runtime.posts if p["url"].endswith("/chat/completions")]
    assert [p["headers"]["X-MC-Route"] for p in turn_posts] == [ASTRA]
    runtime.script = list(HAPPY)
    second = _keep(client, token, "second")
    _stream(client, token, second["request_id"], request_id="rid-" + "c" * 20).get_data()
    assert _route_sent(runtime) == SOL


def test_the_receipt_keeps_selected_and_served_and_does_not_touch_the_selection(routed):
    client = routed.owner()
    token = routed.csrf(client)
    note = _keep(client, token, "hello")
    cid = _conversation_of(client, note)
    _stream(client, token, note["request_id"]).get_data()
    view = client.get(f"{API}/conversations/{cid}/route").get_json()
    last = view["last_turn"]
    assert last["route_id"] == ASTRA and last["billing_mode"] == "subscription" and last["served_matches"] is True
    assert last["served"]["api"] == "openai-chatgpt-responses" and view["revision"] == 0


def test_no_evidence_stays_no_evidence(routed):
    client = routed.owner()
    token = routed.csrf(client)
    routed.runtime.script = list(HAPPY)
    note = _keep(client, token, "hello")
    cid = _conversation_of(client, note)
    _stream(client, token, note["request_id"]).get_data()
    last = client.get(f"{API}/conversations/{cid}/route").get_json()["last_turn"]
    assert last["served"] is None and last["served_matches"] is None


def test_waiting_out_a_busy_relay_resends_the_same_route(monkeypatch):
    import minimoi_portal.guild_ui.mc.openclaw as oc
    monkeypatch.setattr(oc, "IN_FLIGHT_WAIT_S", 5)
    monkeypatch.setattr(oc, "IN_FLIGHT_POLL_S", 0)
    runtime = StreamRuntime()
    answers = iter([429, 200])

    def post(url, data=None, headers=None, stream=False, **kw):
        runtime.status = next(answers)
        runtime.error_text = "relay_busy" if runtime.status == 429 else ""
        return runtime.post(url, data=data, headers=headers, stream=stream, **kw)
    adapter = OpenClawMasterCraftsman("http://r/v1", "t" * 32, http_get=runtime.get, http_post=post)
    route = {"route_id": ASTRA, "runtime_override": True}
    list(adapter.stream_turn(TurnRequest("c-0123456789ab", "hi", "n", correlation_id="a" * 32, route=route)))
    assert [p["headers"]["X-MC-Route"] for p in runtime.posts] == [ASTRA, ASTRA]


def test_cos_sends_no_route_override(monkeypatch):
    runtime = StreamRuntime()
    adapter = OpenClawMasterCraftsman("http://r/v1", "t" * 32, http_get=runtime.get, http_post=runtime.post)
    assert "X-MC-Route" not in adapter._headers(TurnRequest("c", "t", "n", route={"route_id": "gateway-cos-role", "runtime_override": False}))
    assert "X-MC-Route" not in adapter._headers(TurnRequest("c", "t", "n"))


def test_a_withdrawn_route_refuses_with_nothing_sent(routed, catalogue):
    client = routed.owner()
    token = routed.csrf(client)
    note = _keep(client, token, "hello")
    next(r for r in catalogue["routes"] if r["route_id"] == ASTRA)["offered"]["mc"] = "Sign-in expired"
    before = len(routed.runtime.posts)
    r = _stream(client, token, note["request_id"])
    assert r.status_code == 409 and r.get_json()["error"] == "route_unavailable" and "Sign-in expired" in r.get_json()["message"]
    assert len(routed.runtime.posts) == before


def test_the_runtimes_refusal_of_a_route_is_a_clear_stop_not_a_retry(routed):
    client = routed.owner()
    token = routed.csrf(client)
    note = _keep(client, token, "hello")
    routed.runtime.status, routed.runtime.error_text = 409, '{"error":{"code":"relay_route_refused"}}'
    before = len(routed.runtime.posts)
    _stream(client, token, note["request_id"]).get_data()
    assert len([p for p in routed.runtime.posts[before:] if p["url"].endswith("/chat/completions")]) == 1


def test_a_broken_catalogue_stops_the_conversation_turn_with_nothing_sent(routed, monkeypatch):
    import core.model_routes as routes
    client = routed.owner()
    token = routed.csrf(client)
    note = _keep(client, token, "hello")

    def broken(*a, **k):
        raise ValueError("bad")
    monkeypatch.setattr(routes, "load_catalogue", broken)
    before = len(routed.runtime.posts)
    r = _stream(client, token, note["request_id"])
    assert r.status_code == 409 and r.get_json()["error"] == "route_unavailable" and len(routed.runtime.posts) == before
