"""Streaming S1 (streaming spec v0.2 §3, §5, §6, §9; v0.3 §2, §4, §5, §6): the
streamed Master Craftsman turn on /guild-next, end to end through the real
portal (owner guard, CSRF, floor store on SQLite) and the real OpenClaw
adapter, with a scripted relay behind it (its NDJSON, a stop route, gates).
No network, no model call.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from test_mc_turns import RELAY_TOKEN, Resp, _keep, _openclaw, _turn_on

from minimoi_portal.guild_ui.mc import stream as st
from minimoi_portal.guild_ui.mc.openclaw import OpenClawMasterCraftsman
from minimoi_portal.guild_ui.mc.stub import StubBackend

API = "/guild-next/api/v1"
REPO = Path(__file__).resolve().parents[3]


def nd(obj) -> str:
    return json.dumps(obj) + "\n"


HAPPY = [nd({"t": "delta", "text": "Item **12** "}), nd({"t": "delta", "text": "is in build."}),
         nd({"t": "finish", "reason": "stop"}), nd({"t": "usage", "prompt_tokens": 50, "completion_tokens": 7})]


class StreamResp:
    def __init__(self, lines, runtime, status=200, ctype="application/x-ndjson; charset=utf-8", text=""):
        self.lines, self.runtime = lines, runtime
        self.status_code, self.text = status, text
        self.headers = {"Content-Type": ctype}
        self.closed = False

    def iter_content(self, chunk_size=None):
        for item in self.lines:
            if item == "WAIT":                            # hold until the test releases it, or Stop
                self.runtime.waiting.set()
                end = time.monotonic() + 5
                while not (self.runtime.gate.is_set() or self.runtime.stopped.is_set()) and time.monotonic() < end:
                    self.runtime.gate.wait(0.02)
                released = self.runtime.gate.is_set() or self.runtime.stopped.is_set()
                if self.runtime.stopped.is_set():
                    yield nd({"t": "error", "class": "stopped"}).encode()
                    return
                assert released
                continue
            if isinstance(item, tuple) and item[0] == "sleep":     # ("sleep", seconds): the runtime is thinking
                time.sleep(item[1])
                continue
            if isinstance(item, bytes):
                yield item
            else:
                yield item.encode()

    def close(self):
        self.closed = True


class StreamRuntime:
    """What the relay would answer; records what the portal sent."""

    def __init__(self, script=None):
        self.script = list(HAPPY if script is None else script)
        self.posts = []
        self.status, self.error_text = 200, ""
        self.gate, self.stopped, self.waiting = threading.Event(), threading.Event(), threading.Event()

    def get(self, url, **kw):
        return Resp(200, '{"ready":true}')

    def post(self, url, data=None, headers=None, stream=False, **kw):
        body = json.loads(data)
        self.posts.append({"url": url, "body": body, "headers": dict(headers or {}), "stream": stream})
        if url.endswith("/turns/stop"):
            self.stopped.set()
            return Resp(200, '{"stopped":true}')
        if self.status != 200:
            return StreamResp([], self, status=self.status, ctype="application/json", text=self.error_text)
        if not stream:                                    # the non-streaming path: today's JSON answer
            return Resp(200, json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}))
        return StreamResp(self.script, self)


@pytest.fixture(autouse=True)
def _no_busy_wait_by_default(monkeypatch):
    """The wait for a busy Master Craftsman is its own feature (tested below); everywhere else a "relay busy" answer is reported at once, as before."""
    import minimoi_portal.guild_ui.mc.openclaw as oc
    monkeypatch.setattr(oc, "IN_FLIGHT_WAIT_S", 0)


@pytest.fixture(autouse=True)
def _fresh_dispatch_state():
    """The dispatched-set and the in-flight set live for the process: each test starts clean."""
    from minimoi_portal.guild_ui.api import _MC_INFLIGHT
    from minimoi_portal.guild_ui.mc.streaming import DISPATCHED
    DISPATCHED._items.clear()
    _MC_INFLIGHT.clear()
    yield
    DISPATCHED._items.clear()
    _MC_INFLIGHT.clear()


@pytest.fixture
def streaming(floored, tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMOI_USAGE_DIR", str(tmp_path / "usage"))
    (tmp_path / "usage").mkdir()
    runtime = StreamRuntime()
    services = _turn_on(floored, _openclaw(runtime))
    services.mc_stream = True
    floored.extra.update(runtime=runtime, services=services, usage=tmp_path / "usage")
    return floored


def _stream(client, token, note_id, request_id="req-" + "a" * 20, mode="on_record", **extra):
    return client.post(f"{API}/mc/turns/stream", json={"note_request_id": note_id, "record_mode": mode,
                                                       "request_id": request_id, **extra},
                       headers=write_headers(token, mode=mode))


def _events(resp) -> list[dict]:
    return [json.loads(line) for line in resp.get_data(as_text=True).splitlines() if line.strip()]


def _usage_lines(folder) -> list[dict]:
    """The portal's own usage records: usage/portal/, never the shared monthly file."""
    from services.usage import usage_record
    usage_record.flush()
    assert not list(Path(folder).glob("usage-*.jsonl")), "the portal wrote the shared monthly file"
    out = []
    for f in sorted((Path(folder) / "portal").glob("usage-*.jsonl")):
        out += [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
    return out


def _turn_lines(portal) -> list[dict]:
    path = Path(portal.extra["services"].store.folder) / "mc_turns.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _wait_idle(timeout=5):
    """Until no streamed turn is running (the worker has released the lock)."""
    from minimoi_portal.guild_ui.api import _MC_INFLIGHT
    end = time.monotonic() + timeout
    while _MC_INFLIGHT and time.monotonic() < end:
        time.sleep(0.02)
    assert not _MC_INFLIGHT


# ── the happy path, and the order: ack before dispatch ────────────────────────

def test_a_streamed_turn_acks_streams_renders_and_keeps_the_final_text(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "What is in build?")
    resp = _stream(client, token, note["request_id"])
    assert resp.status_code == 200 and resp.headers["Content-Type"] == "application/x-ndjson; charset=utf-8"
    assert resp.headers["Cache-Control"] == "no-store, no-transform" and resp.headers["X-Accel-Buffering"] == "no"
    events = _events(resp)
    kinds = [e["t"] for e in events]
    assert kinds[0] == "ack" and kinds[-1] == "done" and kinds.count("delta") == 2 and "render" in kinds
    ack, done = events[0], events[-1]
    assert [e["text"] for e in events if e["t"] == "delta"] == ["Item **12** ", "is in build."]
    render = next(e for e in events if e["t"] == "render")
    assert "<strong>12</strong>" in render["html"] and render["deltas"] >= 1
    assert done["status"] == "answered" and done["reply_note"]["text"] == "Item **12** is in build."
    assert "<strong>12</strong>" in done["reply_note"]["html"] and done["output_tokens"] == 7
    assert done["reply_note"]["turn"]["tokens_text"] == "7 output tokens"
    assert done["reply_note"]["turn"]["done_text"].startswith("Done in ")
    assert done["reply_note"]["author_label"] == "Master Craftsman" and done["mc_state"] == "live"
    sent = streaming.extra["runtime"].posts[0]
    assert sent["body"]["stream"] is True and sent["stream"] is True
    assert sent["headers"]["X-MC-Correlation-Id"] == ack["turn_id"] == done["turn_id"]
    _wait_idle()
    line = _turn_lines(streaming)[-1]
    assert line["mode"] == "stream" and line["status"] == "answered" and line["turn_id"] == ack["turn_id"]
    usage = [u for u in _usage_lines(streaming.extra["usage"]) if u["correlation_id"] == ack["turn_id"]]
    assert len(usage) == 1 and usage[0]["emitter"] == "runtime-stream" and usage[0]["actor"] == "mc"
    assert usage[0]["route"] == "openclaw/mc-agent" and usage[0]["output_tokens"] == 7 and usage[0]["input_tokens"] == 50
    assert usage[0]["cost_usd"] is None and usage[0]["cost_source"] == "none" and usage[0]["status"] == "ok"


def test_ack_is_sent_before_anything_is_dispatched(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    resp = _stream(client, token, note["request_id"])
    stream = iter(resp.response)
    first = json.loads(next(stream))
    assert first["t"] == "ack" and streaming.extra["runtime"].posts == []      # nothing sent yet
    rest = b"".join(stream).decode()
    assert '"t":"done"' in rest and len(streaming.extra["runtime"].posts) == 1
    resp.close()
    _wait_idle()


def test_after_a_reload_the_footer_reads_the_streamed_runs_record(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    done = _events(_stream(client, token, note["request_id"]))[-1]
    _wait_idle()
    _usage_lines(streaming.extra["usage"])                      # flushed
    notes = client.get(f"{API}/notes").get_json()["notes"]
    reply = next(n for n in notes if n["id"] == done["reply_note"]["id"])
    assert reply["turn"]["tokens_text"] == "7 output tokens"


# ── guards and pre-dispatch refusals (every one before ack, as JSON) ─────────

def test_guards_and_refusals_come_before_ack_and_send_nothing(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    runtime, services = streaming.extra["runtime"], streaming.extra["services"]
    off = _stream(client, token, note["request_id"], mode="off_record")
    assert off.status_code == 409 and off.get_json()["error"] == "not_listening"
    no_csrf = client.post(f"{API}/mc/turns/stream", json={"note_request_id": note["request_id"], "request_id": "r" * 20},
                          headers={"X-Record-Mode": "on_record"})
    assert no_csrf.status_code == 403
    no_id = client.post(f"{API}/mc/turns/stream", json={"note_request_id": note["request_id"], "record_mode": "on_record"},
                        headers=write_headers(token))
    assert no_id.status_code == 422
    services.mc_stream = False
    r = _stream(client, token, note["request_id"])
    assert r.status_code == 409 and r.get_json()["error"] == "stream_off"
    services.mc_stream = True

    class NoStream(OpenClawMasterCraftsman):
        supports_streaming = False
    _turn_on(streaming, NoStream("http://mc-relay:8790/v1", RELAY_TOKEN, http_get=runtime.get, http_post=runtime.post))
    r = _stream(client, token, note["request_id"])
    assert r.status_code == 409 and r.get_json()["error"] == "stream_unsupported"
    _turn_on(streaming, OpenClawMasterCraftsman(None, None, http_get=runtime.get, http_post=runtime.post))
    r = _stream(client, token, note["request_id"])
    assert r.status_code == 503 and "ack" not in r.get_data(as_text=True)
    assert runtime.posts == []


def test_a_stream_off_refusal_lets_the_non_streaming_path_use_the_same_request_id(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    streaming.extra["services"].mc_stream = False
    assert _stream(client, token, note["request_id"], request_id="rid-" + "f" * 20).status_code == 409
    r = client.post(f"{API}/mc/turns", json={"note_request_id": note["request_id"], "record_mode": "on_record",
                                             "request_id": "rid-" + "f" * 20}, headers=write_headers(token))
    assert r.status_code == 200 and r.get_json()["status"] == "answered", r.get_json()   # nothing was dispatched before


# ── one dispatch per turn ─────────────────────────────────────────────────────

def test_a_dispatched_request_id_is_never_sent_again_on_either_endpoint(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    runtime = streaming.extra["runtime"]
    runtime.script = [nd({"t": "delta", "text": "partial"}), nd({"t": "error", "class": "idle"})]
    events = _events(_stream(client, token, note["request_id"], request_id="once-" + "b" * 20))
    assert events[-1]["t"] == "error" and events[-1]["failure_class"] == "idle"
    _wait_idle()
    again = _stream(client, token, note["request_id"], request_id="once-" + "b" * 20)
    assert again.status_code == 409 and again.get_json()["error"] == "already_dispatched"
    assert again.get_json()["result"]["failure_class"] == "idle"                 # the earlier result, for its owner
    other = client.post(f"{API}/mc/turns", json={"note_request_id": note["request_id"], "record_mode": "on_record",
                                                 "request_id": "once-" + "b" * 20}, headers=write_headers(token))
    assert other.status_code == 409 and other.get_json()["error"] == "already_dispatched"
    assert len([p for p in runtime.posts if p["url"].endswith("/chat/completions")]) == 1


def test_an_answered_note_is_answered_from_the_record_without_a_call(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    _events(_stream(client, token, note["request_id"]))
    _wait_idle()
    r = _stream(client, token, note["request_id"], request_id="new-" + "c" * 20)
    assert r.status_code == 200 and r.get_json()["repeated"] is True
    assert len(streaming.extra["runtime"].posts) == 1


def test_one_turn_in_flight_and_the_worker_owns_the_lock(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    first, second = _keep(client, token, "one"), _keep(client, token, "two")
    runtime = streaming.extra["runtime"]
    runtime.script = [nd({"t": "delta", "text": "a"}), "WAIT"] + HAPPY
    resp = _stream(client, token, first["request_id"], request_id="lock-" + "1" * 20)
    stream = iter(resp.response)
    assert json.loads(next(stream))["t"] == "ack"
    assert json.loads(next(stream))["t"] == "delta"          # the worker has started
    assert runtime.waiting.wait(5)
    busy = _stream(client, token, second["request_id"], request_id="lock-" + "2" * 20)
    assert busy.status_code == 409 and busy.get_json()["error"] == "busy"
    resp.close()                                       # the browser leaves: the worker still holds the lock
    busy = _stream(client, token, second["request_id"], request_id="lock-" + "3" * 20)
    assert busy.status_code == 409
    runtime.gate.set()
    _wait_idle()
    assert _stream(client, token, second["request_id"], request_id="lock-" + "4" * 20).status_code == 200


# ── disconnects keep a real answer; Stop ends it ──────────────────────────────

def test_a_browser_that_leaves_does_not_lose_the_answer(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    runtime = streaming.extra["runtime"]
    runtime.script = [nd({"t": "delta", "text": "The answer "}), "WAIT", nd({"t": "delta", "text": "arrives."}),
                      nd({"t": "finish", "reason": "stop"}), nd({"t": "usage", "prompt_tokens": 9, "completion_tokens": 3})]
    resp = _stream(client, token, note["request_id"])
    stream = iter(resp.response)
    ack = json.loads(next(stream))
    assert json.loads(next(stream))["t"] == "delta"
    assert runtime.waiting.wait(5)
    resp.close()                                       # the tab closed mid-stream
    runtime.gate.set()
    _wait_idle()
    notes = client.get(f"{API}/notes").get_json()["notes"]
    assert any(n["text"] == "The answer arrives." and n["author_label"] == "Master Craftsman" for n in notes)
    assert _turn_lines(streaming)[-1]["status"] == "answered" and _turn_lines(streaming)[-1]["turn_id"] == ack["turn_id"]


def test_stop_ends_the_turn_tells_the_relay_and_keeps_nothing_even_off_the_record(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    runtime = streaming.extra["runtime"]
    runtime.script = [nd({"t": "delta", "text": "partial "}), "WAIT"] + HAPPY
    resp = _stream(client, token, note["request_id"])
    stream = iter(resp.response)
    turn_id = json.loads(next(stream))["turn_id"]
    assert json.loads(next(stream))["t"] == "delta"
    assert runtime.waiting.wait(5)
    other = streaming.guest().post(f"{API}/mc/turns/{turn_id}/stop", json={}, headers=write_headers(token))
    assert other.status_code in (401, 403)
    no_csrf = client.post(f"{API}/mc/turns/{turn_id}/stop", json={})
    assert no_csrf.status_code == 403
    stop = client.post(f"{API}/mc/turns/{turn_id}/stop", json={}, headers=write_headers(token, mode="off_record"))
    assert stop.status_code == 200 and stop.get_json()["result"] == "stopping"
    assert "billed" in stop.get_json()["message"]
    rest = [json.loads(line) for line in b"".join(stream).decode().splitlines() if line.strip()]
    end = rest[-1]
    assert end["t"] == "error" and end["failure_class"] == "stopped" and end["status"] == "stopped"
    assert end["partial"] is True and end["reply_note"] is None and "billed" in end["message"]
    assert end["stop_acknowledged"] is True
    assert any(p["url"].endswith("/turns/stop") and p["body"] == {"correlation_id": turn_id} for p in runtime.posts)
    _wait_idle()
    assert not any(n["author_kind"] != "owner" for n in client.get(f"{API}/notes").get_json()["notes"])
    assert _turn_lines(streaming)[-1]["status"] == "stopped"
    usage = [u for u in _usage_lines(streaming.extra["usage"]) if u["correlation_id"] == turn_id]
    assert usage and usage[0]["status"] == "error" and usage[0]["output_tokens"] is None
    assert usage[0]["cost_source"] == "unrecorded-abort"          # the gateway logs nothing for a cancelled stream
    assert client.post(f"{API}/mc/turns/{turn_id}/stop", json={}, headers=write_headers(token)).status_code == 404


def test_the_stop_route_revokes_tool_admission_before_it_asks_the_relay(streaming, monkeypatch):
    """Spec v1.2 §7.4 on the real Stop route: the turn's tools are revoked first; the relay hears about it after."""
    import minimoi_portal.guild_ui.api as api
    runtime, order = streaming.extra["runtime"], []

    class Binding:
        def __call__(self):
            pass

        def attach_run(self, run_id):
            pass

        def revoke(self):
            order.append(("revoke", len([p for p in runtime.posts if p["url"].endswith("/turns/stop")])))
    monkeypatch.setattr(api, "_tool_binder", lambda *a: Binding())
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    runtime.script = [nd({"t": "delta", "text": "partial "}), "WAIT"] + HAPPY
    resp = _stream(client, token, note["request_id"])
    stream = iter(resp.response)
    turn_id = json.loads(next(stream))["turn_id"]
    assert json.loads(next(stream))["t"] == "delta"
    assert runtime.waiting.wait(5)
    assert client.post(f"{API}/mc/turns/{turn_id}/stop", json={}, headers=write_headers(token)).status_code == 200
    b"".join(stream)
    _wait_idle()
    assert order == [("revoke", 0)]                                # revoked while the relay had not yet been told
    assert any(p["url"].endswith("/turns/stop") for p in runtime.posts)


@pytest.mark.parametrize("script,cls,status", [
    ([nd({"t": "delta", "text": "half"}), nd({"t": "error", "class": "deadline"})], "deadline", "interrupted"),
    ([nd({"t": "delta", "text": "half"}), nd({"t": "error", "class": "too_large"})], "too_large", "interrupted"),
    ([nd({"t": "delta", "text": "no finish"})], "truncated", "interrupted"),
    ([nd({"t": "delta", "text": "calls"}), nd({"t": "finish", "reason": "tool_calls"})], "no_run_status", "error"),
    ([nd({"t": "delta", "text": "No response from OpenClaw."}), nd({"t": "finish", "reason": "stop"})],
     "no_run_status", "error"),
    ([nd({"t": "finish", "reason": "stop"})], "no_run_status", "error"),
])
def test_a_partial_or_failed_stream_is_never_kept(streaming, script, cls, status):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    streaming.extra["runtime"].script = script
    end = _events(_stream(client, token, note["request_id"]))[-1]
    assert end["t"] == "error" and end["failure_class"] == cls and end["reply_note"] is None
    _wait_idle()
    assert _turn_lines(streaming)[-1]["status"] == status
    assert all(n["author_kind"] == "owner" for n in client.get(f"{API}/notes").get_json()["notes"])


def test_a_relay_refusal_is_classified_as_the_non_streaming_path_and_records_no_usage(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    runtime = streaming.extra["runtime"]
    runtime.status, runtime.error_text = 401, '{"error":{"type":"authentication_error"}}'
    end = _events(_stream(client, token, note["request_id"]))[-1]
    assert end["failure_class"] == "key_refused" and end["mc_state"] == "unavailable"
    _wait_idle()
    assert _usage_lines(streaming.extra["usage"]) == []


def test_no_token_url_or_runtime_id_reaches_the_browser(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    streaming.extra["runtime"].script = [nd({"t": "delta", "text": "ok", "id": "chatcmpl_secret", "model": "m"}),
                                         nd({"t": "tool", "name": "exec"}), nd({"t": "finish", "reason": "stop"})]
    text = _stream(client, token, note["request_id"]).get_data(as_text=True)
    for leak in (RELAY_TOKEN, "mc-relay", "chatcmpl_secret", "exec", "http://"):
        assert leak not in text


RUN = "chatcmpl_12345678-1234-4234-8234-123456789abc"


def test_the_relay_run_event_is_parsed_only_in_openclaws_exact_shape():
    assert st.parse_relay_line(nd({"t": "run", "id": RUN})) == st.RunStarted(RUN)
    for bad in ("chatcmpl_x", RUN + "0", RUN.upper(), 7, None):
        assert st.parse_relay_line(nd({"t": "run", "id": bad})) is None


def test_the_streamed_run_is_attached_once_to_this_turns_tool_binding_and_never_shown(streaming, monkeypatch):
    """Spec v1.2 §7.1: the relay's run event binds that OpenClaw run to this owner turn; a second one is ignored."""
    import minimoi_portal.guild_ui.api as api
    seen = []

    class Binding:
        def __call__(self):
            seen.append("record")

        def attach_run(self, run_id):
            seen.append(run_id)

        def revoke(self):
            seen.append("revoke")
    monkeypatch.setattr(api, "_tool_binder", lambda *a: Binding())
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    other = "chatcmpl_87654321-1234-4234-8234-123456789abc"
    streaming.extra["runtime"].script = [nd({"t": "run", "id": RUN}), nd({"t": "run", "id": other})] + HAPPY
    events = _events(_stream(client, token, note["request_id"]))
    _wait_idle()
    assert seen == ["record", RUN]
    assert events[-1]["status"] == "answered" and RUN not in json.dumps(events) and all(e["t"] != "run" for e in events)


def test_a_failed_run_attach_never_stops_the_turn(streaming, monkeypatch):
    import minimoi_portal.guild_ui.api as api

    class Binding:
        def __call__(self):
            pass

        def attach_run(self, run_id):
            raise OSError("db down")

        def revoke(self):
            pass
    monkeypatch.setattr(api, "_tool_binder", lambda *a: Binding())
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    streaming.extra["runtime"].script = [nd({"t": "run", "id": RUN})] + HAPPY
    assert _events(_stream(client, token, note["request_id"]))[-1]["status"] == "answered"
    _wait_idle()


def test_the_stub_streams_its_own_scripted_reply(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    stub = StubBackend()
    stub.stream_delay_s = 0
    services = _turn_on(streaming, stub)
    services.mc_stream = True
    done = _events(_stream(client, token, note["request_id"]))[-1]
    assert done["status"] == "answered" and done["reply_note"]["author_label"].startswith("Master Craftsman stub")
    assert not done["reply_note"].get("turn")                                # a stub reply gets no footer
    _wait_idle()
    assert _usage_lines(streaming.extra["usage"]) == []                     # stub usage is never recorded


def test_the_page_says_whether_it_streams(streaming):
    client = streaming.owner()
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert 'data-mc-stream="true"' in page
    streaming.extra["services"].mc_stream = False
    assert 'data-mc-stream="false"' in client.get("/guild-next/guild/build").get_data(as_text=True)


# ── render events: throttled, on change, none past 32 KB ────────────────────

class _FakeRun:
    def __init__(self, items):
        import queue as q
        from minimoi_portal.guild_ui.mc.streaming import StreamContext
        self.queue = q.Queue()
        for item in items:
            self.queue.put(item)
        self.ctx = StreamContext(turn_id="t" * 32, principal="p", backend=None, events=None, floor=None,
                                 reply_key="k", context={}, release=lambda: None)
        self.consumer_gone = threading.Event()
        self.started = False

    def start(self):
        self.started = True


def test_render_events_are_throttled_and_stop_above_32_kb():
    from minimoi_portal.guild_ui.mc.streaming import browser_events
    now = [0.0]
    items = [("delta", "a")] * 5 + [("tick", 0.6)] + [("delta", "b")] + [("tick", 0.6)] + \
        [("delta", "x" * 40_000)] + [("tick", 0.6)] + [("delta", "c")] + [("end", {"t": "done", "status": "answered"})]

    class Run(_FakeRun):
        pass
    run = Run([])
    for kind, payload in items:
        if kind == "tick":
            continue
        run.queue.put((kind, payload))
    ticks = iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.7, 1.4, 2.1, 2.8])
    renders, seen = [], []
    for line in browser_events(run, render=lambda text: f"<p>{len(text)}</p>", clock=lambda: next(ticks, 9.0)):
        event = json.loads(line)
        seen.append(event["t"])
        if event["t"] == "render":
            renders.append(event)
    assert seen[0] == "ack" and run.started and seen[-1] == "done"
    assert [r["deltas"] for r in renders] == [1, 6]              # at most every 500 ms; none past 32 KB
    assert run.consumer_gone.is_set()


def test_ping_every_ten_seconds_of_silence():
    from minimoi_portal.guild_ui.mc.streaming import browser_events
    run = _FakeRun([])
    gen = browser_events(run, render=lambda t: t, ping_s=0.05)
    assert json.loads(next(gen))["t"] == "ack"
    assert json.loads(next(gen))["t"] == "ping"
    run.queue.put(("end", {"t": "error", "status": "error"}))
    assert [json.loads(line)["t"] for line in gen] == ["error"]


# ── the reader and the adapter, as units ─────────────────────────────────────

def test_the_reader_keeps_utf8_whole_and_drops_what_is_not_allowed():
    raw = (json.dumps({"t": "delta", "text": "ä€"}, ensure_ascii=False) + "\n").encode()
    cut = raw.index("ä".encode()) + 1
    chunks = [raw[:cut], raw[cut:], nd({"t": "tool_call", "name": "x"}).encode(), b"not json\n",
              nd({"t": "finish", "reason": "stop"}).encode()]
    assert list(st.read_stream(chunks, limits=st.Limits())) == [st.Delta("ä€"), st.Finish("stop")]


@pytest.mark.parametrize("chunks,limits,cls", [
    ([nd({"t": "delta", "text": "x" * 60}).encode()] * 3, st.Limits(text_max=100), "too_large"),
    ([b'{"t":"delta","text":"' + b"y" * 200], st.Limits(line_max=100), "too_large"),
])
def test_the_reader_trips_each_limit_once(chunks, limits, cls):
    events = list(st.read_stream(chunks, limits=limits))
    assert events[-1] == st.Failure(cls) and sum(isinstance(e, st.Failure) for e in events) == 1


def test_the_reader_deadline_idle_and_stop():
    clock = iter([0.0, 0.0, 200.0])
    events = list(st.read_stream([b"a", b"b"], limits=st.Limits(deadline_s=125), clock=lambda: next(clock, 300.0)))
    assert events == [st.Failure("local_timeout", "timeout_uncertain")]       # the portal's own clock: nothing was cancelled

    def stalls():
        yield nd({"t": "delta", "text": "a"}).encode()
        raise TimeoutError("Read timed out")
    assert list(st.read_stream(stalls(), limits=st.Limits()))[-1] == st.Failure("local_timeout", "timeout_uncertain")
    # a relay-sent idle or deadline event is the relay saying IT cancelled its call: a different, confirmed class
    assert list(st.read_stream([nd({"t": "error", "class": "idle"}).encode()], limits=st.Limits()))[-1] == st.Failure("idle")
    assert list(st.read_stream([nd({"t": "error", "class": "deadline"}).encode()], limits=st.Limits()))[-1] == st.Failure("deadline")
    cancel = threading.Event()
    cancel.set()
    assert list(st.read_stream([b"x"], limits=st.Limits(), cancel=cancel)) == [st.Failure("stopped", "cancelled")]


def test_the_sse_parser_for_later_surfaces():
    line = 'data: ' + json.dumps({"id": "x", "choices": [{"delta": {"content": "hi", "tool_calls": [1]},
                                                          "finish_reason": "stop"}], "usage": {"prompt_tokens": 3,
                                                                                               "completion_tokens": 1}})
    assert st.parse_sse_line(line) == [st.Delta("hi"), st.Finish("stop"), st.Usage(3, 1)]
    assert st.parse_sse_line("data: [DONE]") == ["done"] and st.parse_sse_line(": keepalive") == []


def test_the_adapter_refuses_before_dispatch_and_stop_reaches_the_relay():
    runtime = StreamRuntime()
    mc = _openclaw(runtime)
    from minimoi_portal.guild_ui.mc import TurnRequest
    req = TurnRequest(conversation_id="c", text="x" * 300_000, note_request_id="n", correlation_id="d" * 32)
    with pytest.raises(st.StreamRefused):
        mc.stream_turn(req)
    with pytest.raises(st.StreamRefused):
        OpenClawMasterCraftsman(None, None, http_get=runtime.get, http_post=runtime.post).stream_turn(req)
    assert runtime.posts == []
    assert mc.stop("d" * 32) is True and runtime.posts[-1]["url"] == "http://mc-relay:8790/v1/turns/stop"


def test_backends_that_cannot_stream_say_so_by_construction():
    from minimoi_portal.guild_ui.mc import OffBackend, UnavailableBackend
    from minimoi_portal.guild_ui.mc.grok import GrokMasterCraftsman
    for backend in (OffBackend(), UnavailableBackend("openclaw", "x"), GrokMasterCraftsman()):
        assert backend.supports_streaming is False
        with pytest.raises(NotImplementedError):
            backend.stream_turn(None)
    assert OpenClawMasterCraftsman.supports_streaming and StubBackend.supports_streaming


# ── the portal's usage writes, and the worker's independence ─────────────────

def test_the_portal_writes_only_its_own_runtime_stream_records():
    portal = REPO / "minimoi_portal"
    writers = [p for p in portal.rglob("*.py") if "usage_record" in p.read_text()]
    assert [p.relative_to(REPO).as_posix() for p in writers] == ["minimoi_portal/guild_ui/mc/stream_usage.py"]
    source = (portal / "guild_ui/mc/stream_usage.py").read_text()
    assert 'EMITTER = "runtime-stream"' in source and 'ACTOR = "mc"' in source
    assert '"emitter": EMITTER, "actor": ACTOR' in source


def test_the_portal_writer_is_refused_by_the_library_for_anything_else(tmp_path):
    from services.usage import usage_record
    with pytest.raises(ValueError):
        usage_record.validate({"occurred_at": "2026-09-29T00:00:00+00:00", "env": "t", "emitter": "openclaw-stream",
                               "actor": "mc", "kind": "model", "route": "r", "status": "ok", "cost_source": "none"})
    ok = usage_record.validate({"occurred_at": "2026-09-29T00:00:00+00:00", "env": "t", "emitter": "runtime-stream",
                                "actor": "mc", "kind": "model", "route": "openclaw/mc-agent", "status": "ok",
                                "cost_source": "none"})
    assert ok["emitter"] == "runtime-stream"


def test_the_worker_never_reads_the_request_context():
    import ast
    tree = ast.parse((REPO / "minimoi_portal/guild_ui/mc/streaming.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
            assert not any(n.split(".")[0] == "flask" for n in names), names
        if isinstance(node, ast.Name):
            assert node.id not in ("request", "current_app", "cfg", "session", "g"), node.id


def test_staging_turns_streaming_on_and_mounts_only_the_portals_own_usage_folder_read_write():
    compose = (REPO / "docker-compose.staging.yml").read_text()
    assert "MINIMOI_GUILD_MC_STREAM=${MINIMOI_GUILD_MC_STREAM:-on}" in compose
    assert "${MINIMOI_ROOT}/data/usage:/app/data/usage:ro\n" in compose
    assert "${MINIMOI_ROOT}/data/usage/portal:/app/data/usage/portal\n" in compose
    from minimoi_portal.guild_ui.mc import stream_enabled
    assert stream_enabled({}) is False and stream_enabled({"MINIMOI_GUILD_MC_STREAM": "on"}) is True


def test_the_portal_folder_is_its_own(monkeypatch, tmp_path):
    from minimoi_portal.guild_ui.mc import stream_usage
    monkeypatch.delenv("MINIMOI_USAGE_PORTAL_DIR", raising=False)
    monkeypatch.setenv("MINIMOI_USAGE_DIR", "/app/data/usage")
    assert stream_usage.portal_folder() == "/app/data/usage/portal"
    monkeypatch.setenv("MINIMOI_USAGE_PORTAL_DIR", str(tmp_path))
    assert stream_usage.portal_folder() == str(tmp_path)
    monkeypatch.delenv("MINIMOI_USAGE_PORTAL_DIR")
    monkeypatch.delenv("MINIMOI_USAGE_DIR")
    assert stream_usage.portal_folder() is None
    assert stream_usage.record_run(turn_id="t" * 32, status="ok") is False            # nowhere to write: nothing


@pytest.mark.parametrize("status,text,trace", [
    (409, '{"error":{"type":"relay_stopped"}}', True),          # stopped before MC's headers: after dispatch
    (504, '{"error":{"type":"relay_timeout"}}', True),
    (502, '{"error":{"type":"relay_upstream_down"}}', True),
    (401, '{"error":{"type":"relay_unauthorized"}}', False),    # refused before MC was called
    (429, '{"error":{"type":"relay_busy"}}', False),
])
def test_relay_answers_after_dispatch_leave_a_usage_trace_with_null_tokens(streaming, status, text, trace):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    runtime = streaming.extra["runtime"]
    runtime.status, runtime.error_text = status, text
    end = _events(_stream(client, token, note["request_id"]))[-1]
    assert end["t"] == "error" and end["reply_note"] is None
    _wait_idle()
    lines = [u for u in _usage_lines(streaming.extra["usage"]) if u["correlation_id"] == end["turn_id"]]
    if trace:
        assert len(lines) == 1 and lines[0]["status"] == "error"
        assert lines[0]["output_tokens"] is None and lines[0]["input_tokens"] is None
    else:
        assert lines == []


def test_the_portal_idle_limit_outlasts_the_relays():
    from minimoi_portal.guild_ui.mc.openclaw import STREAM_LIMITS, TURN_DEADLINE_S
    relay = (REPO / "docker/mc-agent/relay_v2.mjs").read_text()
    assert "MC_RELAY_IDLE_MS || 300000" in relay and "MC_RELAY_DEADLINE_MS || 300000" in relay
    assert STREAM_LIMITS.idle_s > 300 and STREAM_LIMITS.deadline_s > STREAM_LIMITS.idle_s
    assert TURN_DEADLINE_S > 300



def test_an_all_zero_runtime_usage_is_unknown_and_the_footer_falls_back_to_the_gateway(streaming):
    """Staging, 2026-09-29: OpenClaw 2026.9.6's stream usage chunk carries zeros
    (as its compat API does, #252/#253) while the gateway recorded 190 output
    tokens. Zero is recorded as unknown (null), and the footer joins the
    gateway's record instead."""
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    streaming.extra["runtime"].script = [nd({"t": "delta", "text": "A real answer."}), nd({"t": "finish", "reason": "stop"}),
                                         nd({"t": "usage", "prompt_tokens": 0, "completion_tokens": 0})]
    done = _events(_stream(client, token, note["request_id"]))[-1]
    assert done["status"] == "answered" and done["output_tokens"] is None
    assert done["reply_note"]["turn"]["tokens_text"] in (None, "tokens unknown")   # never "0 output tokens"
    _wait_idle()
    [rec] = [u for u in _usage_lines(streaming.extra["usage"]) if u["correlation_id"] == done["turn_id"]]
    assert rec["status"] == "ok" and rec["input_tokens"] is None and rec["output_tokens"] is None
    from datetime import datetime, timezone
    gateway = {"v": 1, "record_id": "a" * 32, "occurred_at": datetime.now(timezone.utc).isoformat(), "env": "staging",
               "emitter": "gateway", "actor": "mc", "kind": "model", "route": "minimoi-mc-agent", "status": "ok",
               "input_tokens": 900, "output_tokens": 190, "cost_usd": 0.002, "cost_source": "price_table"}
    month = gateway["occurred_at"][:7]
    with open(streaming.extra["usage"] / f"usage-{month}.jsonl", "a") as f:
        f.write(json.dumps(gateway) + "\n")
    reply = next(n for n in client.get(f"{API}/notes").get_json()["notes"] if n["id"] == done["reply_note"]["id"])
    assert reply["turn"]["tokens_text"] == "190 output tokens"


def test_the_reader_reads_runtime_zeros_as_unknown():
    assert st.parse_relay_line(json.dumps({"t": "usage", "prompt_tokens": 0, "completion_tokens": 0})) == st.Usage(None, None)
    assert st.parse_relay_line(json.dumps({"t": "usage", "prompt_tokens": 12, "completion_tokens": 0})) == st.Usage(12, None)
    line = "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 0, "completion_tokens": 0}})
    assert st.parse_sse_line(line) == [st.Usage(None, None)]


def test_a_relay_timeout_before_headers_is_a_deadline_and_an_unrecorded_abort(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    runtime = streaming.extra["runtime"]
    runtime.status, runtime.error_text = 504, '{"error":{"type":"relay_timeout"}}'
    end = _events(_stream(client, token, note["request_id"]))[-1]
    assert end["failure_class"] == "deadline"
    _wait_idle()
    [rec] = [u for u in _usage_lines(streaming.extra["usage"]) if u["correlation_id"] == end["turn_id"]]
    assert rec["status"] == "error" and rec["cost_source"] == "unrecorded-abort"


# ── a turn that arrives while Master Craftsman is busy (a Rooms turn counts) waits its turn ──

def _busy_then_free(busy_posts):
    """A runtime that answers "one turn at a time" (429) for the first ``busy_posts`` posts, then streams the happy path."""
    runtime = StreamRuntime()
    real = runtime.post

    def post(url, data=None, headers=None, stream=False, **kw):
        runtime.status = 429 if len([p for p in runtime.posts if not p["url"].endswith("/turns/stop")]) < busy_posts else 200
        runtime.error_text = "another turn is running" if runtime.status == 429 else ""
        return real(url, data=data, headers=headers, stream=stream, **kw)
    runtime.post = post
    return runtime


def _backend(runtime):
    from minimoi_portal.guild_ui.mc.openclaw import OpenClawMasterCraftsman
    b = OpenClawMasterCraftsman("http://mc-relay:8790/v1", RELAY_TOKEN, http_get=runtime.get, http_post=runtime.post)
    b.slept = []
    b.sleep = b.slept.append
    return b


def _run(backend, cancel=None):
    return list(backend._stream(b'{"model":"m","messages":[]}', {}, cancel))


def test_a_turn_that_arrives_while_mc_is_busy_waits_and_is_then_answered(monkeypatch):
    monkeypatch.setattr("minimoi_portal.guild_ui.mc.openclaw.IN_FLIGHT_WAIT_S", 45)
    runtime = _busy_then_free(2)
    b = _backend(runtime)
    out = _run(b)
    assert len(runtime.posts) == 3 and len(b.slept) == 2                        # two refusals waited out, the third went through
    assert not any(type(x).__name__ == "Failure" for x in out) and out


def test_the_wait_is_bounded_and_then_it_says_another_turn_is_still_running(monkeypatch):
    monkeypatch.setattr("minimoi_portal.guild_ui.mc.openclaw.IN_FLIGHT_WAIT_S", 45)
    runtime = _busy_then_free(10_000)
    b = _backend(runtime)
    now = {"t": 0.0}
    b.clock = lambda: now["t"]
    b.sleep = lambda s: now.__setitem__("t", now["t"] + s)
    out = _run(b)
    assert len(out) == 1 and out[0].cls == "one_turn_in_flight" and out[0].turn_status == "duplicate_in_progress"
    assert now["t"] >= 45 and len(runtime.posts) < 60                          # it waited about the limit, not forever and not in a hot loop


def test_a_real_rate_limit_is_not_waited_out_and_the_owner_can_stop_the_wait(monkeypatch):
    monkeypatch.setattr("minimoi_portal.guild_ui.mc.openclaw.IN_FLIGHT_WAIT_S", 45)
    runtime = StreamRuntime()
    runtime.status, runtime.error_text = 429, "rate limit exceeded"
    b = _backend(runtime)
    _run(b)
    assert len(runtime.posts) == 1 and b.slept == []                           # a rate limit is a different failure; nothing is retried
    runtime2 = _busy_then_free(10_000)
    b2 = _backend(runtime2)
    stop = threading.Event()
    stop.set()
    out = _run(b2, stop)
    assert len(runtime2.posts) == 0                                            # already stopped: nothing is even sent


def test_stop_revokes_tool_admission_before_asking_the_runtime_and_even_if_that_fails():
    """Spec v1.2 §7.4 (Codex M1 review P1-1): the revocation comes first; a failing revocation is logged and the
    runtime is still asked to abort."""
    from types import SimpleNamespace
    from minimoi_portal.guild_ui.mc.streaming import StreamContext, StreamRun
    order = []

    def runtime_stop(turn_id):
        order.append("runtime")
        return False
    for revoke in (lambda: order.append("revoke"), lambda: (order.append("revoke"), 1 / 0)):
        order.clear()
        ctx = StreamContext(turn_id="t" * 32, principal="p", backend=SimpleNamespace(kind="openclaw", stop=runtime_stop),
                            events=None, floor=None, reply_key="k", context={}, release=lambda: None, on_stop=revoke)
        assert StreamRun(ctx, record_usage=lambda **kw: None).stop() is False
        assert order == ["revoke", "runtime"] and ctx.cancel.is_set()


# ── a buffered frontend streams internally when MC's tools are on (spec v1.2 §7.1 step 1; Codex M1 review P2-3) ──

class _FakeStreamBackend:
    kind = "openclaw"

    def __init__(self, events, refuse=False):
        self.events, self.refuse, self.settled, self.turned = events, refuse, [], 0

    def stream_turn(self, req, cancel=None):
        if self.refuse:
            raise st.StreamRefused("not_connected")
        return iter(self.events)

    def turn(self, req):
        from minimoi_portal.guild_ui.mc import TurnResult
        self.turned += 1
        return TurnResult("answered", self.kind, text="buffered")

    def settle(self, result):
        self.settled.append(result.status)


def _collect(events, **kw):
    from minimoi_portal.guild_ui.mc import TurnRequest
    from minimoi_portal.guild_ui.mc.internal_stream import collect
    backend, runs, usage = _FakeStreamBackend(events, **kw), [], []
    req = TurnRequest(conversation_id="c", text="x", note_request_id="n", correlation_id="d" * 32)
    result = collect(backend, req, on_run=runs.append, record_usage=lambda **f: usage.append(f))
    return result, backend, runs, usage


def test_the_internal_stream_attaches_the_run_once_and_answers_as_the_buffered_route_expects():
    result, backend, runs, usage = _collect([st.RunStarted(RUN), st.RunStarted(RUN), st.Delta("Item 12 "),
                                             st.Delta("is in build."), st.Finish("stop"), st.Usage(50, 7)])
    assert (result.status, result.text, result.trace["finish_reason"]) == ("answered", "Item 12 is in build.", "stop")
    assert runs == [RUN] and backend.settled == ["answered"]
    assert usage == [{"turn_id": "d" * 32, "status": "ok", "prompt_tokens": 50, "completion_tokens": 7,
                      "latency_ms": usage[0]["latency_ms"], "error_class": None}]


@pytest.mark.parametrize("events,cls", [
    ([st.Delta("half"), st.Failure("deadline")], "deadline"),
    ([st.Delta("no finish")], "truncated"),
    ([st.Delta("No response from OpenClaw."), st.Finish("stop")], "no_run_status"),
    ([st.Finish("tool_calls")], "no_run_status"),
])
def test_the_internal_stream_keeps_the_streaming_answer_rules(events, cls):
    result, _, _, usage = _collect(events)
    assert result.status != "answered" and result.failure_class == cls
    assert usage[0]["status"] == "error" and usage[0]["error_class"] == cls


def test_a_backend_that_will_not_stream_goes_the_ordinary_buffered_way_with_no_run():
    result, backend, runs, usage = _collect([], refuse=True)
    assert (result.text, backend.turned, runs, usage) == ("buffered", 1, [], [])


def test_the_buffered_route_streams_internally_and_binds_the_run_when_tools_are_on(streaming, monkeypatch):
    import minimoi_portal.guild_ui.api as api
    seen = []

    class Binding:
        def __call__(self):
            seen.append("record")

        def attach_run(self, run_id):
            seen.append(run_id)

        def revoke(self):
            pass
    monkeypatch.setattr(api, "_tool_binder", lambda *a: Binding())
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "What is in build?")
    streaming.extra["runtime"].script = [nd({"t": "run", "id": RUN})] + HAPPY
    resp = client.post(f"{API}/mc/turns", json={"note_request_id": note["request_id"], "request_id": "req-" + "b" * 20,
                                                "record_mode": "on_record"}, headers=write_headers(token))
    body = resp.get_json()
    assert resp.status_code == 200 and body["status"] == "answered", body
    assert body["reply_note"]["text"] == "Item **12** is in build."
    assert seen == ["record", RUN]
    sent = streaming.extra["runtime"].posts[-1]
    assert sent["stream"] is True and sent["body"]["stream"] is True             # internal stream, buffered answer
    assert RUN not in json.dumps(body)


def test_stop_terminal_waits_for_delayed_ack_without_reordering_revocation(streaming, monkeypatch):
    from minimoi_portal.guild_ui.mc.streaming import RUNS
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "synthetic stop race")
    runtime = streaming.extra["runtime"]
    runtime.script = [nd({"t": "delta", "text": "partial"}), "WAIT"] + HAPPY
    response = _stream(client, token, note["request_id"])
    stream = iter(response.response)
    turn_id = json.loads(next(stream))["turn_id"]
    assert json.loads(next(stream))["t"] == "delta"
    assert runtime.waiting.wait(5)
    run = RUNS.get(turn_id)
    entered, release = threading.Event(), threading.Event()
    order, terminal = [], []
    run.ctx.on_stop = lambda: order.append("revoke")
    run.ctx.on_end = lambda *args: terminal.append(args)

    def slow_ack(_):
        order.append("relay")
        runtime.stopped.set()  # the worker ends BEFORE the HTTP acknowledgement
        entered.set()
        assert release.wait(3)
        return True
    monkeypatch.setattr(run.ctx.backend, "stop", slow_ack)
    stopper = threading.Thread(target=run.stop)
    stopper.start()
    try:
        assert entered.wait(2)
        assert not run.done.wait(.1)
    finally:
        release.set()
        stopper.join(3)
    events = [json.loads(line) for line in b"".join(stream).decode().splitlines() if line.strip()]
    assert run.join(3)
    assert events[-1]["stop_acknowledged"] is True
    assert terminal[0][-1] is True and order == ["revoke", "relay"]


def test_failed_admission_revocation_cannot_release_the_queue_as_confirmed():
    from types import SimpleNamespace
    from minimoi_portal.guild_ui.mc.streaming import StreamContext, StreamRun
    terminal = []
    ctx = StreamContext(turn_id="f" * 32, principal="test", events=None, floor=None, reply_key="k",
        context={}, release=lambda: None, on_stop=lambda: 1/0, on_end=lambda *args: terminal.append(args),
        backend=SimpleNamespace(kind="openclaw", stop=lambda _: True))
    run = StreamRun(ctx, record_usage=lambda **kw: None)
    assert run.stop() is True
    run._notify_end("stopped", "stopped", True)
    assert terminal[0][-1] is False
