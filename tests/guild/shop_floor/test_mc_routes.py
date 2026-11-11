"""A conversation's model route through the real portal: owner scope, revisions, next-turn switching, pinning, plan failover."""
from __future__ import annotations

import json
import threading
import uuid

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
    monkeypatch.setenv(routes.CATALOGUE_VAR, str(routes.CATALOGUE_PATH))
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


def test_a_host_that_has_not_opted_in_has_no_routes_and_sends_no_header(routed, monkeypatch):
    import core.model_routes as routes
    monkeypatch.delenv(routes.CATALOGUE_VAR)
    client = routed.owner()
    token = routed.csrf(client)
    note = _keep(client, token, "hello")
    cid = _conversation_of(client, note)
    assert client.get(f"{API}/conversations/{cid}/route").status_code == 404
    _stream(client, token, note["request_id"]).get_data()
    assert "X-MC-Route" not in routed.runtime.posts[-1]["headers"]


def test_a_buffered_tool_turn_keeps_the_runtimes_evidence():
    from minimoi_portal.guild_ui.mc.internal_stream import collect
    runtime = StreamRuntime(HAPPY + [nd({"t": "served", "provider": "openai", "model": "gpt-6-astra"})])
    adapter = OpenClawMasterCraftsman("http://r/v1", "t" * 32, http_get=runtime.get, http_post=runtime.post)
    result = collect(adapter, TurnRequest("c-0123456789ab", "hi", "n", correlation_id="a" * 32), on_run=lambda run: None, record_usage=lambda *a, **k: None)
    assert result.trace["served"] == {"provider": "openai", "model": "gpt-6-astra"}


# ── Moving to a runtime with its own session: MiniMoi carries the kept conversation, as history ──

@pytest.fixture
def claude_offered(catalogue):
    next(r for r in catalogue["routes"] if r["route_id"] == CLAUDE)["offered"]["mc"] = "available"


def _turn(portal, client, token, cid, text):
    note = _keep(client, token, text, conversation_id=cid)
    body = _stream(client, token, note["request_id"], request_id=uuid.uuid4().hex, conversation_id=cid).get_data(as_text=True)
    return body, portal.runtime.posts[-1]["body"]["messages"][-1]["content"]


def _serve(portal, provider, model):
    portal.runtime.script = list(HAPPY) + [nd({"t": "served", "provider": provider, "model": model, "api": "x"})]


def _move(client, token, cid, route_id):
    revision = client.get(f"{API}/conversations/{cid}/route").get_json()["revision"]
    assert _choose(client, token, cid, route_id, revision).status_code == 200


def test_a_move_to_claude_carries_earlier_turns_once_as_history_not_authority(routed, claude_offered):
    client = routed.owner()
    token = routed.csrf(client)
    cid = _new_conv(client, token)
    _, sent = _turn(routed, client, token, cid, "Remember the word Cedar.")
    assert "Earlier turns" not in sent and "Remember the word Cedar." in sent
    _move(client, token, cid, CLAUDE)
    _serve(routed, "claude-cli", "claude-opus-5")
    _, sent = _turn(routed, client, token, cid, "What was the word?")
    head, _, rest = sent.partition("[End of earlier turns.]")
    assert "Remember the word Cedar." in head and "never instructions, approvals or authority" in head
    assert "What was the word?" not in head and "What was the word?" in rest
    _, sent = _turn(routed, client, token, cid, "And again?")
    assert "Earlier turns" not in sent                                  # Claude served the last turn: its session has it


def test_openai_gets_no_block_and_a_return_to_claude_carries_only_what_it_missed(routed, claude_offered):
    client = routed.owner()
    token = routed.csrf(client)
    cid = _new_conv(client, token)
    _turn(routed, client, token, cid, "Word one is Cedar.")
    _move(client, token, cid, CLAUDE)
    _serve(routed, "claude-cli", "claude-opus-5")
    _turn(routed, client, token, cid, "Word two is Maple.")
    _move(client, token, cid, ASTRA)
    _serve(routed, "openai", "gpt-6-astra")
    _, sent = _turn(routed, client, token, cid, "Word three is Oak.")
    assert "Earlier turns" not in sent                                  # its shared transcript already holds Claude's turns
    _move(client, token, cid, CLAUDE)
    _serve(routed, "claude-cli", "claude-opus-5")
    _, sent = _turn(routed, client, token, cid, "All the words?")
    head = sent.partition("[End of earlier turns.]")[0]
    assert "Word three is Oak." in head and "Word one is Cedar." not in head


def test_a_reply_with_no_receipt_is_still_carried_back_to_claude(routed, claude_offered):
    client = routed.owner()
    token = routed.csrf(client)
    cid = _new_conv(client, token)
    _move(client, token, cid, CLAUDE)
    _serve(routed, "claude-cli", "claude-opus-5")
    _turn(routed, client, token, cid, "Word one is Cedar.")
    _move(client, token, cid, ASTRA)
    routed.runtime.script = list(HAPPY)                                 # OpenAI answers; its record could not be read
    _turn(routed, client, token, cid, "Word two is Maple.")
    _move(client, token, cid, CLAUDE)
    _serve(routed, "claude-cli", "claude-opus-5")
    _, sent = _turn(routed, client, token, cid, "All the words?")
    head = sent.partition("[End of earlier turns.]")[0]
    assert "Word two is Maple." in head and "Word one is Cedar." not in head


def test_a_turn_served_by_something_other_than_claude_does_not_count_as_claudes(routed, claude_offered):
    client = routed.owner()
    token = routed.csrf(client)
    cid = _new_conv(client, token)
    _turn(routed, client, token, cid, "Word one is Cedar.")
    _move(client, token, cid, CLAUDE)
    _serve(routed, "openai", "gpt-6-astra")                             # selected Claude, the runtime's record says otherwise
    _turn(routed, client, token, cid, "Word two is Maple.")
    _serve(routed, "claude-cli", "claude-opus-5")
    _, sent = _turn(routed, client, token, cid, "All the words?")
    head = sent.partition("[End of earlier turns.]")[0]
    assert "Word one is Cedar." in head and "Word two is Maple." in head


def test_only_this_conversations_kept_notes_are_carried(routed, claude_offered):
    client = routed.owner()
    token = routed.csrf(client)
    cid, other = _new_conv(client, token), _new_conv(client, token)
    _turn(routed, client, token, cid, "This chat says Cedar.")
    _keep(client, token, "Another chat says Willow.", conversation_id=other)
    client.post(f"{API}/mc/private", json={"text": "Off the record: Hazel.", "session": "p" + "a1b2c3d4" * 4},
                headers=write_headers(token, mode="off_record"))           # never stored, whatever the answer
    _keep(client, token, "Deleted later: Rowan.", conversation_id=cid)
    base = routed.extra["services"].floor
    floor = base.for_floor(f"{base.floor}/{cid}")

    def delete(q):
        q("DELETE FROM guild.floor_messages WHERE floor=%s AND text=%s RETURNING id", [floor.floor, "Deleted later: Rowan."])
        return None
    floor._run(delete, write=True)
    _move(client, token, cid, CLAUDE)
    _serve(routed, "claude-cli", "claude-opus-5")
    _, sent = _turn(routed, client, token, cid, "Words?")
    head = sent.partition("[End of earlier turns.]")[0]
    assert "This chat says Cedar." in head
    assert not any(word in head for word in ("Willow", "Hazel", "Rowan"))


def test_when_history_cannot_be_read_the_turn_refuses_and_nothing_is_sent(routed, claude_offered, monkeypatch):
    from minimoi_portal.guild_ui import mc_tools_wiring
    client = routed.owner()
    token = routed.csrf(client)
    cid = _new_conv(client, token)
    _turn(routed, client, token, cid, "Word one is Cedar.")
    _move(client, token, cid, CLAUDE)

    def broken(*_):
        def read(*_):
            raise ValueError("notes_snapshot_unavailable")
        return read
    monkeypatch.setattr(mc_tools_wiring, "continuity_reader", broken)
    sent_before = len(routed.runtime.posts)
    body, _ = _turn(routed, client, token, cid, "What was the word?")
    assert "route_history_unavailable" in body and len(routed.runtime.posts) == sent_before


def test_the_legacy_shared_thread_refuses_a_move_to_claude_rather_than_losing_history(routed, claude_offered):
    client = routed.owner()
    token = routed.csrf(client)
    note = _keep(client, token, "first")
    cid = _conversation_of(client, note)
    _move(client, token, cid, CLAUDE)
    sent_before = len(routed.runtime.posts)
    second = _keep(client, token, "second")
    body = _stream(client, token, second["request_id"], request_id=uuid.uuid4().hex).get_data(as_text=True)
    assert "route_history_unavailable" in body and len(routed.runtime.posts) == sent_before


# ── Plan failover, Robert's decision of 6 Nov 2026: the seven rules through the real portal and the real adapter ──

RUN_A, RUN_B = "chatcmpl_11111111-1111-4111-8111-111111111111", "chatcmpl_22222222-2222-4222-8222-222222222222"
API_ROUTE = "api-openai-strong"
API_ANSWER = [nd({"t": "run", "id": RUN_B}), nd({"t": "delta", "text": "Answer."}), nd({"t": "finish", "reason": "stop"}),
              nd({"t": "served", "provider": "minimoi-gateway", "model": "minimoi-openai-strong"})]


def plan_down(signal="rate_limit", tools="none"):
    return [nd({"t": "run", "id": RUN_A}), nd({"t": "error", "class": "upstream", "signal": signal, "tools": tools})]


def _scripted(runtime, *scripts):
    """Each model turn takes the next script; a tuple (status, text, headers) answers that attempt before any stream."""
    queue, real = list(scripts), runtime.post

    def post(url, data=None, headers=None, stream=False, **kw):
        if url.endswith("/chat/completions") and queue:
            step = queue.pop(0)
            if isinstance(step, tuple):
                runtime.posts.append({"url": url, "body": json.loads(data), "headers": dict(headers or {}), "stream": stream})
                from test_mc_streaming import StreamResp
                resp = StreamResp([], runtime, status=step[0], ctype="application/json", text=step[1])
                resp.headers.update(step[2] if len(step) > 2 else {})
                return resp
            runtime.script = step
        return real(url, data=data, headers=headers, stream=stream, **kw)
    return post


@pytest.fixture
def switching(routed):
    return routed


def _run(floored, *scripts, text="hello"):
    floored.extra["services"].mc._post = _scripted(floored.runtime, *scripts)
    floored.runtime.posts.clear()
    client = floored.owner()
    token = floored.csrf(client)
    note = _keep(client, token, text)
    body = _stream(client, token, note["request_id"], request_id="rq-" + uuid.uuid4().hex).get_data(as_text=True)
    cid = _conversation_of(client, note)
    sent = [p["headers"].get("X-MC-Route") for p in floored.runtime.posts if p["url"].endswith("/chat/completions")]
    return body, sent, client.get(f"{API}/conversations/{cid}/route").get_json(), client, token


@pytest.mark.parametrize("signal", ["rate_limit", "plan_exhausted", "signin_expired"])
def test_rule_1_each_signal_moves_the_turn_to_its_api_route(switching, signal):
    body, sent, view, *_ = _run(switching, plan_down(signal), API_ANSWER)
    assert sent == [ASTRA, API_ROUTE] and "Answer." in body


def test_rule_1_an_unknown_error_never_switches_and_reads_as_it_always_did(switching):
    body, sent, *_ = _run(switching, [nd({"t": "run", "id": RUN_A}), nd({"t": "error", "class": "upstream"})], API_ANSWER)
    assert sent == [ASTRA] and "run failed part way" in body


def test_rule_1_a_non_signal_http_error_never_switches(switching):
    _, sent, *_ = _run(switching, (500, '{"error":{"message":"internal error","type":"api_error"}}'), API_ANSWER)
    assert sent == [ASTRA]


def test_rule_1_a_signal_before_the_stream_opens_switches_from_the_relays_headers(switching):
    head = {"X-MC-Plan-Signal": "signin_expired", "X-MC-Plan-Tools": "none"}
    _, sent, *_ = _run(switching, (401, '{"error":{"type":"authentication_error"}}', head), API_ANSWER)
    assert sent == [ASTRA, API_ROUTE]


@pytest.mark.parametrize("tools", ["may_have_begun", "unknown", None, "something new"])
def test_rule_2_any_doubt_about_tools_stops_the_turn_and_says_so(switching, tools):
    script = [nd({"t": "run", "id": RUN_A}), nd({k: v for k, v in {"t": "error", "class": "upstream", "signal": "rate_limit", "tools": tools}.items() if v})]
    body, sent, view, *_ = _run(switching, script, API_ANSWER)
    assert sent == [ASTRA] and "a tool may already have started" in body and "Answer." not in body
    assert view["last_turn"]["route_id"] == ASTRA


def test_rule_2_text_already_shown_stops_the_turn_too(switching):
    script = [nd({"t": "run", "id": RUN_A}), nd({"t": "delta", "text": "Part"}), nd({"t": "error", "class": "upstream", "signal": "rate_limit", "tools": "may_have_begun"})]
    _, sent, *_ = _run(switching, script, API_ANSWER)
    assert sent == [ASTRA]


def test_rule_3_a_runtime_without_the_capped_route_means_both_unavailable(switching):
    refused = (409, '{"error":{"type":"relay_route_refused"}}')
    body, sent, *_ = _run(switching, plan_down(), refused)
    assert sent == [ASTRA, API_ROUTE] and "the API route is too" in body


def test_rule_3_the_cap_reached_means_both_unavailable(switching):
    cap = [nd({"t": "run", "id": RUN_B}), nd({"t": "error", "class": "upstream", "cap": True, "tools": "none"})]
    body, sent, *_ = _run(switching, plan_down(), cap)
    assert sent == [ASTRA, API_ROUTE] and "the API route is too" in body


def test_rule_4_the_receipt_and_the_answer_say_api_and_which_model(switching):
    body, _, view, *_ = _run(switching, plan_down("plan_exhausted"), API_ANSWER)
    last = view["last_turn"]
    assert (last["route_id"], last["billing_mode"], last["billing_label"]) == (API_ROUTE, "metered_api", "API")
    assert last["served"]["model"] == "minimoi-openai-strong" and last["served_matches"] is True
    assert last["switched_from"] == ASTRA and last["switch_signal"] == "plan_exhausted"
    assert "quota is used up" in body and "ran on the OpenAI API: minimoi-openai-strong" in body and "tries the OpenAI plan again" in body


def test_rule_4_no_evidence_is_said_as_no_evidence_and_still_never_plan(switching):
    body, _, view, *_ = _run(switching, plan_down(), API_ANSWER[:-1])
    assert view["last_turn"]["served"] is None and view["last_turn"]["billing_label"] == "API"
    assert "was sent to the API route" in body and "ran on the" not in body


def test_rule_5_the_same_session_is_kept_the_choice_stays_and_the_next_turn_tries_the_plan(switching):
    _, sent, view, client, token = _run(switching, plan_down(), API_ANSWER)
    posts = [p for p in switching.runtime.posts if p["url"].endswith("/chat/completions")]
    assert posts[0]["body"]["user"] == posts[1]["body"]["user"] and posts[0]["body"]["messages"] == posts[1]["body"]["messages"]
    assert view["selected_route_id"] == ASTRA and view["revision"] == 0
    switching.runtime.script = list(HAPPY)
    switching.extra["services"].mc._post = switching.runtime.post
    second = _keep(client, token, "second")
    _stream(client, token, second["request_id"], request_id="rid-" + "d" * 20).get_data()
    assert _route_sent(switching.runtime) == ASTRA


def test_rule_5_a_claude_conversation_carries_the_api_turn_when_it_goes_back(switching):
    from core.model_routes import served_receipt, snapshot, switched_route
    from minimoi_portal.guild_ui import mc_continuity
    conv = {"routes": {"mc": {"route_id": CLAUDE}}}
    from core.model_routes import load_catalogue
    catalogue = json.loads(json.dumps(load_catalogue()))
    next(r for r in catalogue["routes"] if r["route_id"] == CLAUDE)["offered"]["mc"] = "available"
    plan = snapshot(catalogue, "mc-conversation", conv)
    assert plan["failover"]["route_id"] == "api-anthropic-strong"
    claude_turn = {**served_receipt(plan, {"provider": "claude-cli", "model": "claude-opus-5"}), "through_note_id": 41}
    api_turn = {**served_receipt(switched_route(plan, "rate_limit"), {"provider": "minimoi-gateway", "model": "minimoi-anthropic-strong"}), "through_note_id": 42}
    assert mc_continuity.carry_after(plan, {"turns": [claude_turn, api_turn]}) == 41
    assert mc_continuity.carry_after(switched_route(plan, "rate_limit"), {"turns": [claude_turn, api_turn]}) is None


def test_rule_6_a_host_that_removed_the_line_reports_as_it_always_did(switching, catalogue):
    next(r for r in catalogue["routes"] if r["route_id"] == ASTRA).pop("failover")
    body, sent, *_ = _run(switching, plan_down(), API_ANSWER)
    assert sent == [ASTRA] and "run failed part way" in body


def test_rule_7_an_api_route_that_also_fails_ends_the_turn_with_no_third_attempt(switching):
    body, sent, *_ = _run(switching, plan_down(), plan_down("plan_exhausted"), API_ANSWER)
    assert sent == [ASTRA, API_ROUTE] and "the API route is too" in body


def test_rule_7_an_api_failure_after_text_is_reported_as_a_failed_run_not_as_nothing_happened(switching):
    broke = [nd({"t": "run", "id": RUN_B}), nd({"t": "delta", "text": "Half"}), nd({"t": "error", "class": "upstream"})]
    body, sent, *_ = _run(switching, plan_down(), broke, API_ANSWER)
    assert sent == [ASTRA, API_ROUTE] and "the API route is too" not in body


def test_rule_7_the_api_route_when_chosen_or_tried_never_goes_back_to_plan(switching):
    _, sent, *_ = _run(switching, plan_down(), [nd({"t": "error", "class": "upstream", "signal": "signin_expired", "tools": "none"})], HAPPY)
    assert sent.count(ASTRA) == 1 and sent[-1] == API_ROUTE


def test_a_stop_during_the_plan_attempt_sends_nothing_to_the_api():
    from minimoi_portal.guild_ui.mc.stream import Failure
    cancel = threading.Event()
    posts = []

    def post(url, **kw):
        posts.append(url)
        cancel.set()
        from test_mc_streaming import StreamResp
        return StreamResp([*plan_down()], None)
    mc = OpenClawMasterCraftsman("http://mc-relay:8790/v1", "t" * 32, http_get=lambda *a, **k: None, http_post=post)
    events = list(mc.stream_turn(_request(), cancel))
    assert len(posts) == 1 and any(isinstance(e, Failure) and e.cls == "stopped" for e in events)


def _request():
    from core.model_routes import load_catalogue, snapshot
    return TurnRequest(conversation_id="c", text="hi", note_request_id="n", correlation_id="a" * 32,
                       route=snapshot(load_catalogue(), "mc-conversation"))


class _Plain:
    def __init__(self, status, payload, headers=None):
        self.status_code, self.text, self.headers = status, json.dumps(payload), headers or {}


def _buffered(*answers):
    sent, queue = [], list(answers)

    def post(url, data=None, headers=None, **kw):
        sent.append(headers.get("X-MC-Route"))
        return queue.pop(0)
    return OpenClawMasterCraftsman("http://mc-relay:8790/v1", "t" * 32, http_get=lambda *a, **k: None, http_post=post), sent


DOWN = {"X-MC-Plan-Signal": "rate_limit", "X-MC-Plan-Tools": "none"}
OK = {"id": RUN_B, "choices": [{"message": {"content": "Answer."}, "finish_reason": "stop"}]}


def test_the_buffered_path_switches_once_and_names_it():
    mc, sent = _buffered(_Plain(429, {"error": {"type": "rate_limit_error"}}, DOWN),
                         _Plain(200, OK, {"X-MC-Served": json.dumps({"provider": "minimoi-gateway", "model": "minimoi-openai-strong"})}))
    result = mc.turn(_request())
    assert sent == [ASTRA, API_ROUTE] and result.status == "answered" and "ran on the OpenAI API: minimoi-openai-strong" in result.text
    assert result.trace["route"]["route_id"] == API_ROUTE and result.trace["route"]["billing_mode"] == "metered_api"


def test_the_buffered_path_stops_on_doubt_and_on_no_signal():
    mc, sent = _buffered(_Plain(429, {"error": {"type": "rate_limit_error"}}, {**DOWN, "X-MC-Plan-Tools": "unknown"}))
    assert mc.turn(_request()).failure_class == "plan_switch_uncertain" and sent == [ASTRA]
    mc, sent = _buffered(_Plain(500, {"error": {"type": "api_error"}}))
    assert mc.turn(_request()).status == "error" and sent == [ASTRA]


def test_the_buffered_path_says_both_unavailable_and_never_tries_a_third():
    mc, sent = _buffered(_Plain(429, {"error": {"type": "rate_limit_error"}}, DOWN),
                         _Plain(402, {"error": {"type": "insufficient_quota"}}, {"X-MC-Plan-Signal": "plan_exhausted", "X-MC-Plan-Tools": "none"}))
    assert mc.turn(_request()).failure_class == "plan_and_api_unavailable" and sent == [ASTRA, API_ROUTE]


def test_the_internal_stream_binds_the_api_run_and_keeps_the_moved_route():
    from minimoi_portal.guild_ui.mc.internal_stream import collect
    from minimoi_portal.guild_ui.mc.stream import RunStarted, Switched, Delta, Finish
    from core.model_routes import switched_route
    moved = switched_route(_request().route, "rate_limit")

    class Backend:
        kind = "openclaw"
        settle = staticmethod(lambda result: None)

        def stream_turn(self, req):
            return iter([RunStarted(RUN_A), Switched(moved), RunStarted(RUN_B, replaces=RUN_A), Delta("ok"), Finish("stop")])
    runs = []
    result = collect(Backend(), _request(), on_run=runs.append, record_usage=lambda **kw: None)
    assert runs == [RUN_A, RUN_B] and result.trace["route"]["route_id"] == API_ROUTE


def test_saved_route_warning_is_not_repeated_but_changed_signal_is(switching):
    first, *_ = _run(switching, plan_down("rate_limit"), API_ANSWER)
    assert "tries the OpenAI plan again" in first
    second, *_ = _run(switching, plan_down("rate_limit"), API_ANSWER)
    assert "Answer." in second and "tries the OpenAI plan again" not in second
    changed, *_ = _run(switching, plan_down("plan_exhausted"), API_ANSWER)
    assert "quota is used up" in changed
