"""#553 review: the cache hook works on LiteLLM 1.106.6's real proxy call path, so it can't silently no-op.

Runs the actual LiteLLM proxy (the gateway image's version) with the hook loaded from the config, in front of a fake
upstream, and sends the request body OpenClaw 2026.9.6 sends to a custom OpenAI-compatible provider with
compat.supportsPromptCacheKey: a streamed chat completion with a top-level ``prompt_cache_key`` (OpenClaw's
openai-completions transport sets ``params.prompt_cache_key``). Skipped where LiteLLM 1.106.6 with its proxy extras is not
installed (the repo venv); run it with that version installed, as the gateway image has it."""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

try:
    import importlib.metadata as _md
    _VERSION = _md.version("litellm")
    import fastapi  # noqa: F401  (proxy extras)
    import uvicorn  # noqa: F401
except Exception:                                # not installed here
    _VERSION = None

pytestmark = pytest.mark.skipif(_VERSION != "1.106.6", reason="needs litellm[proxy]==1.106.6, the gateway image's version")


class _Upstream(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).seen.append(({k.lower(): v for k, v in self.headers.items()}, body))
        if body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for chunk in ({"choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}]},
                          {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}):
                self.wfile.write(b"data: " + json.dumps({"id": "u", "object": "chat.completion.chunk", "created": 1,
                                                          "model": body["model"], **chunk}).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            return
        raw = json.dumps({"id": "u", "object": "chat.completion", "created": 1, "model": body["model"],
                          "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def proxy(tmp_path):
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{upstream.server_port}/v1"
    shutil.copy(REPO / "services/model_gateway/cache_affinity.py", tmp_path / "cache_affinity.py")
    (tmp_path / "config.yaml").write_text(f"""
model_list:
  - model_name: minimoi-xai-strong
    litellm_params: {{model: xai/grok-4.7, api_key: synthetic, api_base: "{base}", reasoning_effort: low}}
  - model_name: minimoi-openai-strong
    litellm_params: {{model: openai/gpt-6-astra, api_key: synthetic, api_base: "{base}"}}
litellm_settings:
  drop_params: true
  callbacks:
    - cache_affinity.cache_affinity
general_settings:
  master_key: sk-synthetic-master-key
""")
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    child = subprocess.Popen([sys.executable, "-m", "litellm.proxy.proxy_cli", "--config", str(tmp_path / "config.yaml"),
                              "--port", str(port), "--num_workers", "1"], cwd=tmp_path, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 120
    while True:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health/liveliness", timeout=2)
            break
        except Exception:
            if child.poll() is not None or time.time() > deadline:
                child.kill()
                raise RuntimeError("proxy did not start: " + (child.stdout.read() if child.stdout else ""))
            time.sleep(0.5)
    _Upstream.seen = []
    yield port, child
    child.terminate()
    try:
        child.wait(10)
    except subprocess.TimeoutExpired:
        child.kill()
    upstream.shutdown()


def _post(port: int, body: dict) -> None:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"authorization": "Bearer sk-synthetic-master-key", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()


def _openclaw_body(model: str, key: str | None) -> dict:
    """The shape OpenClaw 2026.9.6's openai-completions transport sends (model, messages, stream, stream_options,
    prompt_cache_key at the top level), plus the portal's user."""
    body = {"model": model, "messages": [{"role": "system", "content": "persona"}, {"role": "user", "content": "hi"}],
            "stream": True, "stream_options": {"include_usage": True}, "user": "guild-mc:abc"}
    if key is not None:
        body["prompt_cache_key"] = key
    return body


def test_openclaws_cache_key_reaches_xai_as_a_stable_conversation_header(proxy):
    port, child = proxy
    sys.path.insert(0, str(REPO / "services/model_gateway"))
    import cache_affinity as ca
    _post(port, _openclaw_body("minimoi-xai-strong", "sess-a:0"))
    _post(port, _openclaw_body("minimoi-xai-strong", "sess-a:0"))
    _post(port, _openclaw_body("minimoi-xai-strong", "sess-b:0"))
    _post(port, _openclaw_body("minimoi-xai-strong", None))
    _post(port, _openclaw_body("minimoi-openai-strong", "sess-a:0"))
    got = [(h.get("x-grok-conv-id"), "prompt_cache_key" in b, b.get("model")) for h, b in _Upstream.seen]
    assert got == [
        (ca.conversation_id("sess-a:0"), False, "grok-4.7"),   # same conversation, same id; the body key is dropped
        (ca.conversation_id("sess-a:0"), False, "grok-4.7"),
        (ca.conversation_id("sess-b:0"), False, "grok-4.7"),   # another conversation, another id
        (None, False, "grok-4.7"),                             # no key: nothing sent
        (None, True, "gpt-6-astra"),                           # OpenAI keeps prompt_cache_key, gets no xAI header
    ]
    child.terminate()
    out = child.communicate(timeout=20)[0]
    assert "xai call with x-grok-conv-id (with=3 without=0)" in out
    assert "xai call without x-grok-conv-id (with=3 without=1)" in out
    assert "sess-a" not in out and ca.conversation_id("sess-a:0") not in out
