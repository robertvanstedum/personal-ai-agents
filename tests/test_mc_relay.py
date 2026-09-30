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


class Upstream:
    def __init__(self):
        self.seen = []
        self.delay = 0
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
                self._answer(self.rfile.read(n))

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


def _await_relay(proc, env, base, *, attempts=5, deadline_s=20.0):
    """Wait until the relay answers /healthz, and return (proc, base).

    The port is picked by binding port 0 and closing it, so by the time node
    binds 0.0.0.0 on it another socket can hold it (on macOS, typically a
    client connection in TIME_WAIT): node then exits with EADDRINUSE. That is
    the flake this replaces (a fixed 5 s loop that never noticed the exit).
    Now the relay is started again on a fresh port, a few times; any other
    exit, or no answer within the deadline, fails with the relay's own error."""
    for _ in range(attempts):
        started = time.monotonic()
        while time.monotonic() - started < deadline_s:
            if proc.poll() is not None:
                break
            try:
                urllib.request.urlopen(base + "/healthz", timeout=1)
                return proc, base
            except Exception:
                time.sleep(0.05)
        else:
            proc.terminate()
            proc.wait(timeout=5)
            pytest.fail(f"the relay did not answer /healthz within {deadline_s:.0f} s")
        err = proc.stderr.read()
        if "EADDRINUSE" not in err:
            pytest.fail(f"the relay exited ({proc.returncode}): {err[-400:]}")
        port = _port()
        env = {**env, "MC_RELAY_PORT": str(port)}
        proc = subprocess.Popen(["node", str(RELAY)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        base = f"http://127.0.0.1:{port}"
    pytest.fail(f"the relay could not bind a free port in {attempts} attempts")


@pytest.fixture
def relay():
    up = Upstream()
    port = _port()
    env = {"PATH": os.environ["PATH"], "MC_RELAY_PORT": str(port), "MC_RELAY_TARGET": up.url,
           "MC_RELAY_TOKEN": CALLER, "MC_OPENCLAW_GATEWAY_TOKEN": MC_TOKEN, "MC_RELAY_DEADLINE_MS": "3000"}
    proc = subprocess.Popen(["node", str(RELAY)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{port}"
    proc, base = _await_relay(proc, env, base)
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
    ({**GOOD, "stream": True}, "stream"),
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
