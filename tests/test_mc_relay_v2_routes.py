"""The relay alone turns a route id into a model, and reads OpenClaw's own record of what served a turn."""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
import urllib.error
import urllib.request

import pytest

from test_mc_relay_v2 import CALLER, Env, chat

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")

ROUTES = {"plan-openai-strong": "openai/gpt-6-astra"}
USER = "guild-mc:test"
KEY = f"agent:mc-chat:openai-user:{USER}"


def _chat_db(path, *, provider="openai", model="gpt-6-astra", stamp=None, harness="openclaw", compressed=False):
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE session_windows (session_id TEXT, session_key TEXT, agent_harness_id TEXT, updated_at INT)")
    db.execute("CREATE TABLE transcript_events (session_id TEXT, seq INT, event_json TEXT, event_zstd BLOB)")
    db.execute("INSERT INTO session_windows VALUES ('s1', ?, ?, 1)", (KEY, harness))
    event = {"type": "message", "timestamp": stamp or "2100-01-01T00:00:00Z",
             "message": {"role": "assistant", "provider": provider, "model": model, "api": "openai-chatgpt-responses"}}
    db.execute("INSERT INTO transcript_events VALUES ('s1', 2, ?, NULL)", (json.dumps(event),))
    db.commit()
    db.close()


@pytest.fixture
def relay(tmp_path):
    made = []

    def build(**extra):
        env = Env(tmp_path, extra={"MC_RELAY_ROUTES": json.dumps(ROUTES), **extra})
        env.start() if env.proc is None else None
        made.append(env)
        return env
    yield build
    for env in made:
        env.gateway.release.set()
        env.stop()


def post(env, body, **headers):
    req = urllib.request.Request(env.base + "/v1/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "authorization": f"Bearer {CALLER}", **headers})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


def test_a_known_route_goes_on_as_the_maps_model_and_a_callers_header_never_does(relay):
    env = relay()
    status, _, _ = post(env, chat("openclaw/mc-chat"), **{"X-MC-Route": "plan-openai-strong", "x-openclaw-model": "anthropic/evil"})
    assert status == 200
    sent = {k.lower(): v for k, v in env.gateway.seen[-1]["headers"].items()}
    assert sent["x-openclaw-model"] == "openai/gpt-6-astra"


@pytest.mark.parametrize("route", ["anthropic/claude-opus-5", "nope", ""])
def test_an_unknown_route_is_refused_with_nothing_sent(relay, route):
    env = relay()
    status, _, text = post(env, chat("openclaw/mc-chat"), **{"X-MC-Route": route})
    assert status == 409 and "relay_route_refused" in text and env.gateway.seen == []


def test_no_route_header_means_no_override(relay):
    env = relay()
    assert post(env, chat("openclaw/mc-chat"), **{"x-openclaw-model": "anthropic/evil"})[0] == 200
    assert "x-openclaw-model" not in {k.lower() for k in env.gateway.seen[-1]["headers"]}


def test_the_runtimes_record_comes_back_as_evidence(relay, tmp_path):
    _chat_db(tmp_path / "chat.sqlite")
    env = relay(MC_RELAY_CHAT_DB=str(tmp_path / "chat.sqlite"))
    status, headers, _ = post(env, chat("openclaw/mc-chat", user=USER), **{"X-MC-Route": "plan-openai-strong"})
    assert status == 200
    served = json.loads({k.lower(): v for k, v in headers.items()}["x-mc-served"])
    assert served == {"provider": "openai", "model": "gpt-6-astra", "api": "openai-chatgpt-responses", "harness": "openclaw"}


def test_a_reply_older_than_this_turn_is_no_evidence(relay, tmp_path):
    _chat_db(tmp_path / "chat.sqlite", stamp="2001-01-01T00:00:00Z")
    env = relay(MC_RELAY_CHAT_DB=str(tmp_path / "chat.sqlite"))
    started = time.monotonic()
    status, headers, _ = post(env, chat("openclaw/mc-chat", user=USER))
    assert status == 200 and "x-mc-served" not in {k.lower() for k in headers} and time.monotonic() - started < 5


def test_an_unreadable_record_is_no_evidence_and_the_answer_still_arrives(relay, tmp_path):
    env = relay(MC_RELAY_CHAT_DB=str(tmp_path / "missing.sqlite"))
    status, headers, _ = post(env, chat("openclaw/mc-chat", user=USER))
    assert status == 200 and "x-mc-served" not in {k.lower() for k in headers}
