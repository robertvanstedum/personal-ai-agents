"""docker/mc-agent/relay.mjs: the one-way relay between the portal and MC
(MC spec v0.9 §4). Runs the real relay against a fake MC upstream that records
what it receives. No network beyond 127.0.0.1, no model call.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RELAY = REPO / "docker" / "mc-agent" / "relay.mjs"
CALLER = "relay-caller-" + "a" * 32
MC_TOKEN = "mc-openclaw-" + "b" * 32

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sse(obj) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()


def chunk(text=None, finish=None, **extra):
    return {"id": "chatcmpl_up", "model": "secret-model", "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": ({"content": text} if text is not None else {}), "finish_reason": finish}],
            **extra}


# A scripted OpenClaw SSE answer: (bytes, seconds to wait before them).
HAPPY = [(sse(chunk(None) | {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}), 0),
         (sse(chunk("Hello ")), 0.05), (sse(chunk("wörld")), 0.05),
         (sse(chunk(None, "stop")), 0.02),
         (sse({"id": "x", "choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 7, "total_tokens": 127}}), 0.02),
         (b"data: [DONE]\n\n", 0.01)]


class Upstream:
    def __init__(self):
        self.seen = []
        self.delay = 0
        self.script = None           # a list of (bytes, delay): answer POSTs as text/event-stream
        self.status = 200
        self.finished = threading.Event()
        self.aborted = threading.Event()
        outer = self

        class H(BaseHTTPRequestHandler):
            def _answer(self, body=b""):
                outer.seen.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                   "body": body.decode() if body else ""})
                time.sleep(outer.delay)
                data = json.dumps({"id": "chatcmpl_up", "choices": [{"message": {"content": "ok"}}],
                                   "usage": {"prompt_tokens": 1}}).encode() if self.command == "POST" else b'{"ready":true}'
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._answer()

            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(n)
                if outer.script is None:
                    return self._answer(body)
                outer.seen.append({"method": "POST", "path": self.path, "headers": dict(self.headers),
                                   "body": body.decode()})
                if outer.status != 200:
                    data = json.dumps({"error": {"message": "budget exceeded"}}).encode()
                    self.send_response(outer.status)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                try:
                    for data, wait in outer.script:
                        time.sleep(wait)
                        self.wfile.write(data)
                        self.wfile.flush()
                    outer.finished.set()
                except (BrokenPipeError, ConnectionResetError):
                    outer.aborted.set()          # the relay closed MC's connection

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def relay():
    up = Upstream()
    port = _port()
    env = {"PATH": os.environ["PATH"], "MC_RELAY_PORT": str(port), "MC_RELAY_TARGET": up.url,
           "MC_RELAY_TOKEN": CALLER, "MC_OPENCLAW_GATEWAY_TOKEN": MC_TOKEN, "MC_RELAY_DEADLINE_MS": "3000",
           "MC_RELAY_IDLE_MS": "1000", "MC_RELAY_STREAM_TEXT_MAX": "2000", "MC_RELAY_LINE_MAX": "1000"}
    proc = subprocess.Popen(["node", str(RELAY)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/healthz", timeout=1)
            break
        except Exception:
            time.sleep(0.05)
    yield base, up, proc
    proc.terminate()
    proc.wait(timeout=5)
    up.server.shutdown()


def call(base, path, method="GET", body=None, token=CALLER, headers=None, raw=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    try:
        r = urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=h, method=method), timeout=10)
        return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


GOOD = {"model": "openclaw/mc-agent", "user": "guild-mc:abc", "messages": [{"role": "user", "content": "hi"}]}


def test_a_good_turn_goes_to_mc_with_mcs_token_and_nothing_else(relay):
    base, up, _ = relay
    corr = "a" * 32
    status, headers, text = call(base, "/v1/chat/completions", "POST",
                                 {**GOOD, "temperature": 2, "tools": [{"x": 1}], "stream": False},
                                 headers={"X-MC-Correlation-Id": corr, "x-openclaw-agent-id": "cos-agent-a",
                                          "x-openclaw-model": "minimoi-gateway/minimoi-cos-agent", "X-Forwarded-For": "1.2.3.4"})
    assert status == 200 and json.loads(text)["id"] == "chatcmpl_up"
    assert headers.get("x-mc-correlation-id") == corr
    seen = up.seen[-1]
    assert seen["path"] == "/v1/chat/completions"
    lower = {k.lower(): v for k, v in seen["headers"].items()}
    assert lower["authorization"] == f"Bearer {MC_TOKEN}"
    assert not any(k.startswith("x-openclaw") for k in lower)
    assert "x-forwarded-for" not in lower and "x-mc-correlation-id" not in lower
    assert json.loads(seen["body"]) == GOOD                      # re-serialized: only model, user, messages


@pytest.mark.parametrize("path,method,expect", [
    ("/tools/invoke", "POST", 403), ("/v1/embeddings", "POST", 403), ("/v1/models", "GET", 403),
    ("/v1/responses", "POST", 403), ("/", "GET", 403), ("/readyz", "GET", 200),
])
def test_only_the_two_paths_pass(relay, path, method, expect):
    base, up, _ = relay
    status, _, _ = call(base, path, method, {} if method == "POST" else None)
    assert status == expect
    if expect == 403:
        assert up.seen == []


@pytest.mark.parametrize("body,why", [
    ({**GOOD, "model": "openclaw/default"}, "model"),
    ({**GOOD, "model": "openclaw"}, "model"),
    ({**GOOD, "model": "openclaw/cos-agent-a"}, "model"),
    ({**GOOD, "stream": "true"}, "stream"),
    ({**GOOD, "stream": True, "stream_options": {"include_usage": False}}, "stream_options"),
    ({**GOOD, "user": "cos-probe"}, "user"),
    ({**GOOD, "messages": []}, "messages"),
    ({**GOOD, "messages": [{"role": "tool", "content": "x"}]}, "role"),
])
def test_bodies_outside_the_rules_are_refused(relay, body, why):
    base, up, _ = relay
    status, _, text = call(base, "/v1/chat/completions", "POST", body)
    assert status == 403 and why in text
    assert up.seen == []


def test_callers_without_the_token_are_refused(relay):
    base, up, _ = relay
    assert call(base, "/v1/chat/completions", "POST", GOOD, token=None)[0] == 401
    assert call(base, "/v1/chat/completions", "POST", GOOD, token=MC_TOKEN)[0] == 401   # MC's own token is not a caller token
    assert call(base, "/readyz", token=None)[0] == 401
    assert up.seen == []


def test_duplicate_keys_oversize_and_bad_json(relay):
    base, up, _ = relay
    dup = b'{"model":"openclaw/mc-agent","model":"openclaw/cos-agent-a","user":"guild-mc:x","messages":[{"role":"user","content":"x"}]}'
    assert call(base, "/v1/chat/completions", "POST", raw=dup)[0] == 400
    big = {**GOOD, "messages": [{"role": "user", "content": "x" * 300_000}]}
    assert call(base, "/v1/chat/completions", "POST", big)[0] == 413
    assert call(base, "/v1/chat/completions", "POST", raw=b"{nope")[0] == 400
    assert up.seen == []


def test_one_request_in_flight(relay):
    base, up, _ = relay
    up.delay = 1.5
    results = []
    t = threading.Thread(target=lambda: results.append(call(base, "/v1/chat/completions", "POST", GOOD)[0]))
    t.start()
    time.sleep(0.4)
    second = call(base, "/v1/chat/completions", "POST", GOOD)
    t.join()
    assert results == [200] and second[0] == 429 and "relay_busy" in second[2]


def test_websocket_upgrades_are_refused(relay):
    base, _, _ = relay
    host, port = base.replace("http://", "").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as s:
        s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n")
        assert s.recv(64).startswith(b"HTTP/1.1 403")


def test_logs_never_carry_a_token_or_message_text(relay):
    base, _, proc = relay
    call(base, "/v1/chat/completions", "POST", {**GOOD, "messages": [{"role": "user", "content": "secret-note-text"}]},
         headers={"X-MC-Correlation-Id": "b" * 32})
    call(base, "/nope", token=None)
    proc.terminate()
    out, err = proc.communicate(timeout=5)
    logs = out + err
    assert "b" * 32 in logs                                   # the correlation id is traceable
    for secret in (CALLER, MC_TOKEN, "secret-note-text"):
        assert secret not in logs


def test_the_relay_refuses_to_start_without_distinct_tokens():
    for env in ({"MC_RELAY_TOKEN": CALLER}, {"MC_RELAY_TOKEN": CALLER, "MC_OPENCLAW_GATEWAY_TOKEN": CALLER}):
        r = subprocess.run(["node", str(RELAY)], env={"PATH": os.environ["PATH"], "MC_RELAY_PORT": str(_port()), **env},
                           capture_output=True, text=True, timeout=10)
        assert r.returncode == 1 and "refusing to start" in r.stderr


def test_the_caller_token_is_compared_in_constant_time():
    text = RELAY.read_text()
    assert "timingSafeEqual" in text and "got.length === EXPECTED.length" in text
    assert "header === `Bearer" not in text


def test_a_token_of_the_right_length_but_wrong_value_is_refused(relay):
    base, up, _ = relay
    wrong = CALLER[:-1] + ("x" if CALLER[-1] != "x" else "y")
    assert call(base, "/readyz", token=wrong)[0] == 401
    assert call(base, "/readyz", token=CALLER + "extra")[0] == 401
    assert up.seen == []



# ── Streaming (spec v0.2 §3, v0.3 §2) ─────────────────────────────────────────
import http.client  # noqa: E402

STREAM = {**GOOD, "stream": True}


def stream_call(base, body=STREAM, corr="c" * 32, read_lines=None):
    """POST a streaming turn; return (status, headers, [events]). With
    read_lines, disconnect after that many lines."""
    host, port = base.replace("http://", "").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    conn.request("POST", "/v1/chat/completions", body=json.dumps(body),
                 headers={"Authorization": f"Bearer {CALLER}", "Content-Type": "application/json",
                          "X-MC-Correlation-Id": corr})
    r = conn.getresponse()
    headers = {k.lower(): v for k, v in r.getheaders()}
    if r.status != 200 or "ndjson" not in headers.get("content-type", ""):
        return r.status, headers, r.read().decode()
    events = []
    while True:
        line = r.readline()
        if not line:
            break
        events.append(json.loads(line))
        if read_lines is not None and len(events) >= read_lines:
            conn.sock.close()
            break
    return r.status, headers, events


def test_a_stream_is_passed_on_as_the_relays_own_minimal_ndjson(relay):
    base, up, _ = relay
    up.script = HAPPY + [(sse(chunk(None) | {"choices": [{"index": 0, "delta": {"tool_calls": [{"id": "t"}]}}]}), 0)]
    status, headers, events = stream_call(base)
    assert status == 200 and headers["content-type"].startswith("application/x-ndjson")
    assert headers["cache-control"] == "no-cache, no-transform" and headers["x-accel-buffering"] == "no"
    assert headers["x-mc-correlation-id"] == "c" * 32
    assert events == [{"t": "delta", "text": "Hello "}, {"t": "delta", "text": "wörld"},
                      {"t": "finish", "reason": "stop"},
                      {"t": "usage", "prompt_tokens": 120, "completion_tokens": 7}]
    sent = json.loads(up.seen[-1]["body"])
    assert sent["stream"] is True and sent["stream_options"] == {"include_usage": True}
    text = json.dumps(events)
    assert "chatcmpl_up" not in text and "secret-model" not in text and "tool_calls" not in text


def test_utf8_split_across_chunks_and_a_tool_call_chunk_is_dropped(relay):
    base, up, _ = relay
    raw = sse(chunk("ä€"))
    cut = raw.index("ä".encode()) + 1                              # split inside a multi-byte character
    up.script = [(raw[:cut], 0), (raw[cut:], 0.05),
                 (sse(chunk(None) | {"choices": [{"index": 0, "delta": {"tool_calls": [{"id": "t1"}]}}]}), 0),
                 (sse(chunk(None, "stop")), 0), (b"data: [DONE]\n\n", 0)]
    status, _, events = stream_call(base)
    assert events == [{"t": "delta", "text": "ä€"}, {"t": "finish", "reason": "stop"}]


def test_a_non_200_keeps_the_json_error_path(relay):
    base, up, _ = relay
    up.script, up.status = HAPPY, 400
    status, headers, text = stream_call(base)
    assert status == 400 and "application/json" in headers["content-type"] and "budget" in text


@pytest.mark.parametrize("script,cls", [
    ([(sse(chunk("a")), 0), (sse(chunk("b")), 1.6)], "idle"),                  # 1 s idle in the fixture
    ([(sse(chunk("x" * 900)), 0), (sse(chunk("x" * 900)), 0), (sse(chunk("x" * 900)), 0)], "too_large"),
    ([(b"data: " + b"y" * 1500, 0)], "too_large"),                             # a 1,000-byte line cap in the fixture
    ([(sse({"error": {"message": "upstream exploded"}}), 0)], "upstream"),
])
def test_each_limit_trips_cleanly_with_one_error_event(relay, script, cls):
    base, up, _ = relay
    up.script = script
    status, _, events = stream_call(base)
    assert status == 200 and events[-1] == {"t": "error", "class": cls}
    assert sum(1 for e in events if e["t"] == "error") == 1


def test_the_deadline_trips_even_while_bytes_keep_coming(relay):
    base, up, _ = relay
    up.script = [(sse(chunk("t")), 0.5) for _ in range(10)]                    # 5 s of trickle, 3 s deadline
    status, _, events = stream_call(base)
    assert events[-1] == {"t": "error", "class": "deadline"}


def test_stop_aborts_mcs_call_and_ends_the_stream(relay):
    base, up, _ = relay
    up.script = [(sse(chunk("a")), 0)] + [(sse(chunk("b")), 0.3) for _ in range(20)]
    results = []
    t = threading.Thread(target=lambda: results.append(stream_call(base, corr="d" * 32)))
    t.start()
    time.sleep(0.6)
    assert call(base, "/v1/turns/stop", "POST", {"correlation_id": "e" * 32})[0] == 404     # not that turn
    status, _, text = call(base, "/v1/turns/stop", "POST", {"correlation_id": "d" * 32})
    assert status == 200 and json.loads(text) == {"stopped": True}
    t.join(timeout=5)
    events = results[0][2]
    assert events[-1] == {"t": "error", "class": "stopped"}
    assert up.aborted.wait(3)                                                  # MC's connection was closed
    assert call(base, "/v1/turns/stop", "POST", {"correlation_id": "d" * 32})[0] == 404     # finished: nothing to stop


def test_stop_needs_the_caller_token_and_a_turn_id(relay):
    base, _, _ = relay
    assert call(base, "/v1/turns/stop", "POST", {"correlation_id": "d" * 32}, token=None)[0] == 401
    assert call(base, "/v1/turns/stop", "POST", {"correlation_id": "nope"})[0] == 400


def test_a_caller_that_leaves_does_not_stop_the_run(relay):
    base, up, proc = relay
    up.script = [(sse(chunk("a")), 0)] + [(sse(chunk("b")), 0.2) for _ in range(5)] + \
        [(sse(chunk(None, "stop")), 0), (b"data: [DONE]\n\n", 0)]
    status, _, events = stream_call(base, read_lines=1)                        # the caller goes after one line
    assert events == [{"t": "delta", "text": "a"}]
    time.sleep(0.2)
    busy = call(base, "/v1/chat/completions", "POST", GOOD)                    # still in flight: MC is still running
    assert busy[0] == 429
    assert up.finished.wait(5) and not up.aborted.is_set()                     # MC was read to the end
    time.sleep(0.3)
    up.script = None
    assert call(base, "/v1/chat/completions", "POST", GOOD)[0] == 200           # settled: free again


def test_the_stream_path_logs_no_text_or_token(relay):
    base, up, proc = relay
    up.script = [(sse(chunk("private-reply-text")), 0), (sse(chunk(None, "stop")), 0), (b"data: [DONE]\n\n", 0)]
    stream_call(base, body={**STREAM, "messages": [{"role": "user", "content": "private-note-text"}]}, corr="f" * 32)
    proc.terminate()
    out, err = proc.communicate(timeout=5)
    logs = out + err
    assert "f" * 32 in logs and '"event":"stream"' in logs
    for secret in (CALLER, MC_TOKEN, "private-note-text", "private-reply-text"):
        assert secret not in logs
