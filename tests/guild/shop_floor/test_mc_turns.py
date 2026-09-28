"""Master Craftsman turns on /guild-next (stage B; MC spec v0.9 §4).

The owner route POST /mc/turns sends one kept, on-the-record note to the
backend, server side, only with the environment's gate on. These tests drive
the real portal (its owner guard, CSRF, floor store on SQLite) with a fake
runtime behind the real OpenClaw adapter, so the adapter's error mapping and
the route's keeping rules run as they will against the relay. No network, no
model call.
"""
from __future__ import annotations

import json
import logging

import pytest

from minimoi_portal.guild_ui.mc.openclaw import OpenClawMasterCraftsman
from minimoi_portal.guild_ui.mc.stub import StubBackend
from minimoi_portal.guild_ui.mc import CachedHealth

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

API = "/guild-next/api/v1"
RELAY_TOKEN = "relay-caller-token-" + "9" * 24
CARD = "5555 5555 5555 4444"


class Resp:
    def __init__(self, status, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}


class FakeRuntime:
    """What the relay would answer; records what the portal sent."""

    def __init__(self, answer=None):
        self.sent = []
        self.answer = answer or (lambda body, headers: Resp(
            200, json.dumps({"id": "chatcmpl_x", "choices": [{"message": {"content": "Item 12 is in build."}}],
                             "usage": {"prompt_tokens": 9, "completion_tokens": 5}}),
            {"X-MC-Correlation-Id": headers.get("X-MC-Correlation-Id")}))

    def get(self, url, **kw):
        return Resp(200, '{"ready":true}')

    def post(self, url, data=None, headers=None, **kw):
        body = json.loads(data)
        self.sent.append({"url": url, "body": body, "headers": dict(headers or {})})
        return self.answer(body, headers or {})


def _turn_on(portal, backend, turns=True):
    services = portal.app.extensions["guild_ui_next"]["services"]
    services.mc = backend
    services.mc_health = CachedHealth(backend)
    services.mc_turns = turns
    return services


def _openclaw(runtime):
    return OpenClawMasterCraftsman("http://mc-relay:8790/v1", RELAY_TOKEN, http_get=runtime.get, http_post=runtime.post)


def _keep(client, token, text, **extra):
    r = client.post(f"{API}/notes", json=keyed(text=text, **extra), headers=write_headers(token))
    assert r.status_code == 200, r.get_json()
    return r.get_json()["note"]


def _ask(client, token, note_id, mode="on_record"):
    return client.post(f"{API}/mc/turns", json={"note_request_id": note_id, "record_mode": mode},
                       headers=write_headers(token, mode=mode))


@pytest.fixture
def turned(floored):
    runtime = FakeRuntime()
    _turn_on(floored, _openclaw(runtime))
    floored.extra["runtime"] = runtime
    return floored


def test_a_kept_note_reaches_mc_through_the_relay_and_the_answer_is_kept_as_mcs(turned, caplog):
    caplog.set_level(logging.INFO)
    client = turned.owner()
    token = turned.csrf(client)
    assert client.get(f"{API}/floor").get_json()["mc_header"] == "Master Craftsman is unavailable · connected, no answer yet"
    note = _keep(client, token, "What is stuck on #12?", context={"area": "Build Queue", "item_ref": 12, "page": "item"})
    r = _ask(client, token, note["request_id"])
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "answered" and body["backend_kind"] == "openclaw"
    assert body["reply_note"]["text"] == "Item 12 is in build."
    assert body["reply_note"]["who"] == "master_craftsman" and body["reply_note"]["author_label"] == "Master Craftsman"
    assert body["reply_note"]["context"] == {"area": "Build Queue", "item_ref": 12, "page": "item"}
    assert body["mc_state"] == "live" and body["mc_header"].startswith("Master Craftsman is live")
    sent = turned.extra["runtime"].sent[0]
    assert sent["url"] == "http://mc-relay:8790/v1/chat/completions"
    assert sent["body"]["model"] == "openclaw/mc-agent" and sent["body"]["user"].startswith("guild-mc:")
    assert "robert" not in sent["body"]["user"]
    assert "What is stuck on #12?" in sent["body"]["messages"][0]["content"]
    assert sent["headers"]["X-MC-Correlation-Id"] == body["turn_id"]           # correlation
    log = caplog.text
    assert f"mc turn {body['turn_id']} start" in log and f"mc turn {body['turn_id']} end status=answered" in log
    assert "echo=True" in log and "What is stuck" not in log and RELAY_TOKEN not in log
    for key in ("trace", "url", "token", "response_id"):
        assert key not in body
    assert "chatcmpl_x" not in json.dumps(body)     # the runtime's id stays on the server


def test_on_staging_a_refused_key_is_shown_honestly_and_nothing_is_kept_as_an_answer(floored):
    runtime = FakeRuntime(lambda body, headers: Resp(500, '{"error":{"message":"LLM request failed: 400 No connected db."}}'))
    _turn_on(floored, _openclaw(runtime))
    db = floored.extra["floor"]
    client = floored.owner()
    token = floored.csrf(client)
    note = _keep(client, token, "hello")
    body = _ask(client, token, note["request_id"]).get_json()
    assert body["status"] == "unavailable" and body["failure_class"] == "key_refused" and body["reply_note"] is None
    assert body["mc_state"] == "unavailable"
    assert body["mc_header"] == "Master Craftsman is unavailable · its model key was refused"
    assert body["message"].endswith("Your note is kept; Master Craftsman did not answer.")
    assert [r["author"] for r in db.rows("floor_messages")] == ["robert"]            # only the note
    assert client.get(f"{API}/floor").get_json()["mc_header"] == "Master Craftsman is unavailable · its model key was refused"


@pytest.mark.parametrize("answer,status,failure", [
    (lambda b, h: Resp(408, '{"error":{"message":"upstream provider timeout"}}'), "unavailable", "model_gateway_down"),
    (lambda b, h: Resp(504, '{"error":{"type":"relay_timeout"}}'), "error", "runtime_error"),
    (lambda b, h: Resp(200, '{"choices":[{"message":{"content":"hi"}}]}'), "error", "no_run_status"),
    (lambda b, h: Resp(429, '{"error":{"type":"relay_busy"}}'), "duplicate_in_progress", "one_turn_in_flight"),
    (lambda b, h: Resp(401, '{"error":{"type":"relay_unauthorized"}}'), "unavailable", "caller_refused"),
])
def test_every_failure_is_shown_as_a_failure_and_never_kept(floored, answer, status, failure):
    _turn_on(floored, _openclaw(FakeRuntime(answer)))
    client = floored.owner()
    token = floored.csrf(client)
    body = _ask(client, token, _keep(client, token, "q")["request_id"]).get_json()
    assert (body["status"], body["failure_class"], body["reply_note"]) == (status, failure, None)
    assert len(floored.extra["floor"].rows("floor_messages")) == 1


def test_a_timeout_is_shown_as_uncertain(floored):
    class ReadTimeout(Exception):
        pass

    def boom(*_a, **_k):
        raise ReadTimeout()

    runtime = FakeRuntime()
    backend = OpenClawMasterCraftsman("http://mc-relay:8790/v1", RELAY_TOKEN, http_get=runtime.get, http_post=boom)
    _turn_on(floored, backend)
    client = floored.owner()
    token = floored.csrf(client)
    body = _ask(client, token, _keep(client, token, "q")["request_id"]).get_json()
    assert body["status"] == "timeout_uncertain" and body["mc_header"] == "Master Craftsman is unavailable · no answer within the deadline"


def test_the_payment_scrub_runs_again_before_the_note_leaves(turned):
    """A note written straight into the store (bypassing the API's scrub) is
    still scrubbed before it reaches Master Craftsman."""
    store = turned.app.extensions["guild_ui_next"]["services"].floor
    from minimoi_portal.guild_ui.stores import Author
    store.add_note("n-raw-12345678", f"pay with {CARD} please", Author("robert", "owner", "Robert"))
    client = turned.owner()
    token = turned.csrf(client)
    assert _ask(client, token, "n-raw-12345678").get_json()["status"] == "answered"
    text = turned.extra["runtime"].sent[0]["body"]["messages"][0]["content"]
    assert CARD not in text and "5555" not in text


def test_off_the_record_never_reaches_mc(turned):
    client = turned.owner()
    token = turned.csrf(client)
    note = _keep(client, token, "on the record")
    r = _ask(client, token, note["request_id"], mode="off_record")
    assert r.status_code == 409 and r.get_json()["error"] == "not_listening"
    assert turned.extra["runtime"].sent == []


def test_only_a_kept_note_of_this_owner_is_sent(turned):
    client = turned.owner()
    token = turned.csrf(client)
    assert _ask(client, token, "n-nothing-12345678").status_code == 404
    store = turned.app.extensions["guild_ui_next"]["services"].floor
    from minimoi_portal.guild_ui.stores import GUILD_PLATFORM
    store.add_note("n-platform-1234", "platform line", GUILD_PLATFORM)
    assert _ask(client, token, "n-platform-1234").status_code == 403
    assert _ask(client, token, "bad id!").status_code == 422
    assert turned.extra["runtime"].sent == []


def test_with_notes_down_nothing_is_sent(load_portal):
    portal = load_portal()          # no floor database: notes unavailable
    runtime = FakeRuntime()
    _turn_on(portal, _openclaw(runtime))
    client = portal.owner()
    token = portal.csrf(client)
    r = _ask(client, token, "n-some-12345678")
    assert r.status_code == 503 and "nothing was sent" in r.get_json()["message"]
    assert runtime.sent == []


def test_logged_out_and_guest_get_json_401_and_403(turned):
    assert turned.client().post(f"{API}/mc/turns", json={}).status_code == 401
    guest = turned.guest().post(f"{API}/mc/turns", json={})
    assert guest.status_code == 403 and guest.is_json
    assert turned.extra["runtime"].sent == []


def test_one_turn_in_flight_per_owner(turned, monkeypatch):
    from minimoi_portal.guild_ui import api
    client = turned.owner()
    token = turned.csrf(client)
    note = _keep(client, token, "q")
    monkeypatch.setattr(api, "_MC_INFLIGHT", {"robert"})
    r = _ask(client, token, note["request_id"])
    assert r.status_code == 409 and r.get_json()["error"] == "busy"
    assert turned.extra["runtime"].sent == []


def test_a_stub_reply_is_kept_as_the_stubs_never_as_master_craftsmans(floored):
    _turn_on(floored, StubBackend())
    client = floored.owner()
    token = floored.csrf(client)
    body = _ask(client, token, _keep(client, token, "q")["request_id"]).get_json()
    assert body["status"] == "answered" and body["backend_kind"] == "stub"
    assert body["reply_note"]["who"] == "master_craftsman_stub"
    assert body["reply_note"]["author_label"] == "Master Craftsman stub · scripted"
    assert "master_craftsman" not in [r["author"] for r in floored.extra["floor"].rows("floor_messages")]


def test_mc_reply_keys_are_the_platforms_own(turned):
    client = turned.owner()
    token = turned.csrf(client)
    r = client.post(f"{API}/notes", json={"request_id": "mc-12345678", "text": "x"}, headers=write_headers(token))
    assert r.status_code == 422


def test_no_token_or_relay_url_in_any_page_or_api_answer(turned):
    client = turned.owner()
    token = turned.csrf(client)
    body = _ask(client, token, _keep(client, token, "q")["request_id"]).get_data(as_text=True)
    pages = [client.get(u).get_data(as_text=True) for u in
             ("/guild-next/guild/build", "/guild-next/guild/build/queue", f"{API}/floor", f"{API}/session", f"{API}/notes")]
    for text in [body, *pages]:
        assert RELAY_TOKEN not in text and "mc-relay:8790" not in text and "chatcmpl_x" not in text


def test_the_gate_off_means_no_turns_whatever_the_backend(floored):
    runtime = FakeRuntime()
    _turn_on(floored, _openclaw(runtime), turns=False)
    client = floored.owner()
    token = floored.csrf(client)
    note = _keep(client, token, "q")
    assert _ask(client, token, note["request_id"]).status_code == 409
    assert client.get(f"{API}/floor").get_json()["mc"]["turns"] is False
    assert runtime.sent == []


def test_the_page_tells_the_front_end_whether_turns_are_on(turned):
    page = turned.owner().get("/guild-next/guild/build").get_data(as_text=True)
    assert 'data-mc-turns="true"' in page
    js = (__import__("pathlib").Path(__file__).resolve().parents[3]
          / "minimoi_portal/guild_ui/static/js/conversation.js").read_text()
    assert "document.body.dataset.mcTurns !== 'true'" in js and "if (live.off" in js
    assert "apiPost('/mc/turns'" in js
