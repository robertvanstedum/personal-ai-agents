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
            # OpenClaw 2026.9.6's real shape: usage is always zeros, even for a real answer.
            200, json.dumps({"id": "chatcmpl_x", "object": "chat.completion", "model": "openclaw/mc-agent",
                             "choices": [{"index": 0, "message": {"role": "assistant", "content": "Item 12 is in build."},
                                          "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}),
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
    # A 200 with no reply text (OpenClaw 9.6's own placeholder when a run produced none).
    (lambda b, h: Resp(200, '{"choices":[{"index":0,"message":{"role":"assistant","content":"No response from OpenClaw."},'
                            '"finish_reason":"stop"}],"usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}}'),
     "error", "no_run_status"),
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
    # Waiting is a transient line where the reply will appear, replaced in place;
    # never an "Asking Master Craftsman" platform entry (Robert, 2026-09-29).
    assert "Asking Master Craftsman" not in js and "waiting.replaceWith(noteLine(body.reply_note))" in js
    assert "waiting.replaceWith(platformLine(" in js and "waiting.stopTicking()" in js
    assert 'id="tpl-mc-waiting"' in page and "Waiting for a response…" in page
    assert 'data-slot="elapsed" aria-hidden="true"' in page                 # seconds are never read out
    # The note is shown before the turn is asked, and the turn is not awaited.
    assert js.index("appendNote(body.note)") < js.index("askMasterCraftsman(body.note)")
    assert "await askMasterCraftsman" not in js


# ── The turn trace: "Done in Ns" under a live reply, file first ─────────────

def _trace(portal):
    import pathlib
    folder = portal.app.extensions["guild_ui_next"]["services"].store.folder
    path = pathlib.Path(folder) / "mc_turns.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def test_a_live_reply_carries_its_measured_duration_and_it_survives_a_reload(turned):
    client = turned.owner()
    token = turned.csrf(client)
    note = _keep(client, token, "How long?")
    body = _ask(client, token, note["request_id"]).get_json()
    turn = body["reply_note"]["turn"]
    assert isinstance(turn["duration_ms"], int) and turn["done_text"].startswith("Done in ") and turn["done_text"].endswith("s")
    assert turn["usage"] is None                                   # tokens/cost come later, from the gateway
    lines = _trace(turned)
    assert len(lines) == 1 and lines[0]["turn_id"] == body["turn_id"] and lines[0]["status"] == "answered"
    assert lines[0]["reply_request_id"] == body["reply_note"]["request_id"] and lines[0]["backend_kind"] == "openclaw"
    assert "How long" not in json.dumps(lines)                     # no note text in the trace
    listed = client.get(f"{API}/notes").get_json()["notes"]
    reply = [n for n in listed if n["request_id"] == body["reply_note"]["request_id"]][0]
    assert reply["turn"]["done_text"] == turn["done_text"]
    assert all("turn" not in n for n in listed if n["who"] == "robert")
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert f'<span data-turn-done>{turn["done_text"]}</span>' in page and page.count("data-turn-foot") == 2   # the row + the template
    again = _ask(client, token, note["request_id"]).get_json()
    assert again["repeated"] is True and again["reply_note"]["turn"]["done_text"] == turn["done_text"]


def test_failed_turns_are_traced_but_get_no_footer_and_a_stub_reply_gets_none(floored):
    runtime = FakeRuntime(lambda body, headers: Resp(500, '{"error":{"message":"LLM request failed: 401 invalid api key"}}'))
    _turn_on(floored, _openclaw(runtime))
    client = floored.owner()
    token = floored.csrf(client)
    note = _keep(client, token, "hello")
    body = _ask(client, token, note["request_id"]).get_json()
    assert body["status"] == "unavailable" and body["reply_note"] is None
    lines = _trace(floored)
    assert len(lines) == 1 and lines[0]["failure_class"] == "key_refused" and lines[0]["reply_request_id"] is None
    _turn_on(floored, StubBackend())
    note2 = _keep(client, token, "stub please")
    stub = _ask(client, token, note2["request_id"]).get_json()
    assert stub["status"] == "answered" and "turn" not in stub["reply_note"]


def test_the_trace_never_blocks_a_turn(turned, monkeypatch):
    import os
    folder = turned.app.extensions["guild_ui_next"]["services"].store.folder
    os.makedirs(os.path.join(folder, "mc_turns.jsonl"))           # a directory where the file should be: writes fail
    client = turned.owner()
    token = turned.csrf(client)
    note = _keep(client, token, "still answered?")
    body = _ask(client, token, note["request_id"]).get_json()
    assert body["status"] == "answered" and body["reply_note"]["text"] == "Item 12 is in build."
    assert "turn" not in body["reply_note"]
    assert client.get(f"{API}/notes").status_code == 200


def test_turn_log_reads_only_live_answered_lines_and_skips_torn_ones(tmp_path):
    from minimoi_portal.guild_ui.mc.turn_log import TurnLog, done_text
    log = TurnLog(str(tmp_path))
    log.record(turn_id="t1", status="answered", backend_kind="openclaw", duration_ms=1234, reply_request_id="mc-a")
    log.record(turn_id="t2", status="answered", backend_kind="stub", duration_ms=5, reply_request_id="mc-b")
    log.record(turn_id="t3", status="error", backend_kind="openclaw", duration_ms=90000, reply_request_id=None)
    with open(tmp_path / "mc_turns.jsonl", "a") as f:
        f.write('{"torn": ')
    assert oct((tmp_path / "mc_turns.jsonl").stat().st_mode & 0o777) == "0o600"
    got = log.turns_for(["mc-a", "mc-b", "mc-c"])
    start, end = got["mc-a"].pop("window")                     # the turn's window (usage-record U3)
    assert abs((end - start).total_seconds() - 1.234) < 0.002
    assert got == {"mc-a": {"duration_ms": 1234, "done_text": "Done in 1.2s", "usage": None}}
    assert done_text(15400) == "Done in 15s" and done_text(900) == "Done in 0.9s"
    assert TurnLog(None).turns_for(["mc-a"]) == {} and TurnLog(str(tmp_path / "missing")).turns_for(["mc-a"]) == {}


# ── #251 review F1: one reply per note, and "kept" means kept ────────────────

def test_resending_a_note_with_a_reply_makes_no_second_mc_call(turned):
    client = turned.owner()
    token = turned.csrf(client)
    note = _keep(client, token, "What is stuck?")
    first = _ask(client, token, note["request_id"]).get_json()
    assert first["status"] == "answered" and len(turned.extra["runtime"].sent) == 1
    again = _ask(client, token, note["request_id"])
    body = again.get_json()
    assert again.status_code == 200 and body["repeated"] is True and body["status"] == "answered"
    assert body["reply_note"]["id"] == first["reply_note"]["id"]
    assert "nothing was sent again" in body["message"]
    assert len(turned.extra["runtime"].sent) == 1                       # MC was not called again
    rows = turned.extra["floor"].rows("floor_messages")
    assert [r["author"] for r in rows] == ["robert", "master_craftsman"]


@pytest.mark.parametrize("outcome", ["idempotency_mismatch", "not_found", None])
def test_a_reply_the_store_did_not_keep_is_reported_as_not_kept(turned, monkeypatch, outcome):
    from minimoi_portal.guild_ui import api
    from minimoi_portal.guild_ui.stores import WriteResult
    monkeypatch.setattr("minimoi_portal.guild_ui.mc.keep_reply",
                        lambda *a, **k: WriteResult(outcome, None, True) if outcome else None)
    client = turned.owner()
    token = turned.csrf(client)
    body = _ask(client, token, _keep(client, token, "q")["request_id"]).get_json()
    assert body["status"] == "error" and body["failure_class"] == "not_kept" and body["reply_note"] is None
    assert "could not be kept" in body["message"] and "kept on the record" not in body["message"]
    assert api  # imported for the patch target's module


def test_turn_lines_reach_the_portal_log_at_info_on_staging(turned, capfd):
    """Stage B evidence: the portal configures no logging, so INFO was dropped.
    On a staging origin the MC logger emits at INFO, ids and outcomes only."""
    import logging
    from minimoi_portal.guild_mounts import staging_mc_logging
    logger = logging.getLogger("guild_ui.mc")
    for h in [h for h in logger.handlers if getattr(h, "_minimoi_mc", False)]:
        logger.removeHandler(h)          # rebind to this test's captured stderr
    assert staging_mc_logging() is logger and logger.level == logging.INFO
    client = turned.owner()
    token = turned.csrf(client)
    body = _ask(client, token, _keep(client, token, "What is stuck?")["request_id"]).get_json()
    err = capfd.readouterr().err
    assert f"INFO guild_ui.mc: mc turn {body['turn_id']} start" in err
    assert f"mc turn {body['turn_id']} end status=answered" in err
    assert RELAY_TOKEN not in err and "What is stuck" not in err


def test_the_mc_logger_is_configured_on_staging_and_not_elsewhere(load_portal, monkeypatch):
    import logging
    logger = logging.getLogger("guild_ui.mc")
    for h in [h for h in logger.handlers if getattr(h, "_minimoi_mc", False)]:
        logger.removeHandler(h)
    logger.setLevel(logging.NOTSET)
    load_portal(base_url="https://minimoi.ai")
    assert not any(getattr(h, "_minimoi_mc", False) for h in logger.handlers)
    load_portal()                                       # dev.minimoi.ai
    assert sum(getattr(h, "_minimoi_mc", False) for h in logger.handlers) == 1 and logger.level == logging.INFO
    load_portal()                                       # idempotent: still one handler
    assert sum(getattr(h, "_minimoi_mc", False) for h in logger.handlers) == 1


# ── usage-record U3: the footer's output tokens, from the usage store ────────

def _usage_line(folder, *, at, output_tokens=96, actor="mc", status="ok", emitter="gateway"):
    import pathlib
    path = pathlib.Path(folder) / f"usage-{at[:7]}.jsonl"
    rec = {"v": 1, "record_id": "00000000-0000-4000-8000-%012d" % (hash(at) % 10**12), "occurred_at": at, "env": "staging",
           "emitter": emitter, "actor": actor, "kind": "model", "route": "minimoi-mc-agent", "status": status,
           "input_tokens": 900, "output_tokens": output_tokens, "cost_usd": 0.0012, "cost_source": "price_table"}
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")


def _now_iso(delta_s=0.0):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat(timespec="milliseconds")


def test_the_footer_joins_mcs_gateway_records_inside_the_turn_window(turned, tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMOI_USAGE_DIR", str(tmp_path))
    client = turned.owner()
    token = turned.csrf(client)
    _usage_line(tmp_path, at=_now_iso(-600), output_tokens=999)                 # an earlier turn: outside
    note = _keep(client, token, "tokens?")
    body = _ask(client, token, note["request_id"]).get_json()
    assert body["reply_note"]["turn"]["tokens_text"] is None                     # not recorded yet: asked again later
    _usage_line(tmp_path, at=_now_iso(0.5), output_tokens=60)                   # MC's calls for this turn
    _usage_line(tmp_path, at=_now_iso(0.6), output_tokens=36)
    _usage_line(tmp_path, at=_now_iso(0.7), output_tokens=500, actor="cos")    # CoS at the same time: not MC's
    _usage_line(tmp_path, at=_now_iso(0.8), output_tokens=None, status="refused")
    listed = client.get(f"{API}/notes").get_json()["notes"]
    turn = [n for n in listed if n["request_id"] == body["reply_note"]["request_id"]][0]["turn"]
    assert turn["output_tokens"] == 96 and turn["tokens_text"] == "96 output tokens"
    assert "window" not in turn
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert f'{turn["done_text"]}</span><span data-turn-usage> · 96 output tokens</span>' in page


def test_the_footer_says_tokens_unknown_with_no_store_or_no_match_once_the_turn_is_old(turned, tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from minimoi_portal.guild_ui.mc.usage_join import add_tokens
    monkeypatch.delenv("MINIMOI_USAGE_DIR", raising=False)
    client = turned.owner()
    token = turned.csrf(client)
    note = _keep(client, token, "no store")
    body = _ask(client, token, note["request_id"]).get_json()
    assert body["reply_note"]["turn"]["tokens_text"] == "tokens unknown"
    now = datetime.now(timezone.utc)
    old = {"r": {"window": (now - timedelta(minutes=5, seconds=2), now - timedelta(minutes=5))}}
    assert add_tokens(old, folder=str(tmp_path))["r"]["tokens_text"] == "tokens unknown"
    recent = {"r": {"window": (now - timedelta(seconds=2), now)}}
    assert add_tokens(recent, folder=str(tmp_path))["r"]["tokens_text"] is None
    _usage_line(tmp_path, at=(now - timedelta(seconds=1)).isoformat(timespec="milliseconds"), output_tokens=None)
    recent = {"r": {"window": (now - timedelta(seconds=2), now)}}
    assert add_tokens(recent, folder=str(tmp_path))["r"]["tokens_text"] == "tokens unknown"   # a record without a count


def test_the_page_asks_again_for_pending_tokens_a_few_times_only():
    import pathlib
    js = (pathlib.Path(__file__).resolve().parents[3] / "minimoi_portal/guild_ui/static/js/conversation.js").read_text()
    assert "apiGet('/notes?limit=20')" in js and "tokenAsks >= 4" in js and "turn.tokens_text == null" in js


# ── Markdown in the thread (Robert, September 29) ────────────────────────────

def test_notes_and_mc_replies_carry_sanitised_html_and_keep_their_markdown(floored):
    reply = "## Status\n**Two** items:\n1. #12 <script>alert(1)</script>\n2. #14"
    runtime = FakeRuntime(lambda body, headers: Resp(200, json.dumps({
        "id": "chatcmpl_md", "object": "chat.completion", "model": "openclaw/mc-agent",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}),
        {"X-MC-Correlation-Id": headers.get("X-MC-Correlation-Id")}))
    _turn_on(floored, _openclaw(runtime))
    client = floored.owner()
    token = floored.csrf(client)
    r = client.post(f"{API}/notes", json=keyed(text="Is **#12** done?"), headers=write_headers(token))
    note = r.get_json()["note"]
    assert note["text"] == "Is **#12** done?" and note["html"] == "<p>Is <strong>#12</strong> done?</p>"
    body = _ask(client, token, note["request_id"]).get_json()
    got = body["reply_note"]
    assert got["text"] == reply                                          # stored as Markdown
    assert "<h2>Status</h2>" in got["html"] and "<ol>" in got["html"] and "<script" not in got["html"]
    listed = client.get(f"{API}/notes").get_json()["notes"]
    assert all("html" in n for n in listed)
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert '<div class="msg-text msg-md"><h2>Status</h2>' in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)" not in page
    rows = floored.extra["floor"].rows("floor_messages")
    assert any(r_["text"] == reply for r_ in rows)                      # the store holds the original text
