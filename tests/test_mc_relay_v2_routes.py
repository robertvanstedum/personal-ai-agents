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


RUN, OTHER_RUN = "chatcmpl_11111111-1111-4111-8111-111111111111", "chatcmpl_22222222-2222-4222-8222-222222222222"


def _chat_db(path, *, provider="openai", model="gpt-6-astra", run=RUN, harness="openclaw", later_run=None):
    """OpenClaw's record: the reply stamped with its run. ``later_run``: another run's reply written after it."""
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE session_windows (session_id TEXT, session_key TEXT, agent_harness_id TEXT, updated_at INT)")
    db.execute("CREATE TABLE transcript_events (session_id TEXT, seq INT, event_json TEXT, event_zstd BLOB)")
    db.execute("INSERT INTO session_windows VALUES ('s1', ?, ?, 1)", (KEY, harness))

    def reply(seq, run_id, reply_model):
        message = {"role": "assistant", "provider": provider, "model": reply_model, "api": "openai-chatgpt-responses"}
        if run_id:
            message["__openclaw"] = {"runId": run_id}
        db.execute("INSERT INTO transcript_events VALUES ('s1', ?, ?, NULL)", (seq, json.dumps({"type": "message", "message": message})))
    reply(2, run, model)
    if later_run:
        reply(3, later_run, "gpt-6-luna")
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


def with_db(relay, tmp_path, mode=None):
    env = relay(MC_RELAY_CHAT_DB=str(tmp_path / "chat.sqlite"))
    env.gateway.run_id = RUN
    if mode:
        env.gateway.mode = mode
    return env


def served_of(headers):
    raw = {k.lower(): v for k, v in headers.items()}.get("x-mc-served")
    return json.loads(raw) if raw else None


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


def test_the_record_of_this_exact_run_comes_back_as_evidence(relay, tmp_path):
    _chat_db(tmp_path / "chat.sqlite")
    status, headers, _ = post(with_db(relay, tmp_path), chat("openclaw/mc-chat", user=USER), **{"X-MC-Route": "plan-openai-strong"})
    assert status == 200
    assert served_of(headers) == {"provider": "openai", "model": "gpt-6-astra", "api": "openai-chatgpt-responses", "harness": "openclaw"}


def test_a_streamed_turn_reports_the_record_of_its_own_run(relay, tmp_path):
    _chat_db(tmp_path / "chat.sqlite")
    status, _, text = post(with_db(relay, tmp_path, "stream"), chat("openclaw/mc-chat", user=USER) | {"stream": True})
    events = [json.loads(line) for line in text.splitlines()]
    assert status == 200 and events[0] == {"t": "run", "id": RUN}
    assert [e for e in events if e["t"] == "served"] == [{"t": "served", "provider": "openai", "model": "gpt-6-astra",
                                                          "api": "openai-chatgpt-responses", "harness": "openclaw"}]


@pytest.mark.parametrize("mode", [None, "stream"])
def test_a_late_reply_from_an_earlier_run_is_never_this_turns_evidence(relay, tmp_path, mode):
    _chat_db(tmp_path / "chat.sqlite", run=OTHER_RUN)      # this session's newest reply, but another run's
    started = time.monotonic()
    status, headers, text = post(with_db(relay, tmp_path, mode), chat("openclaw/mc-chat", user=USER) | ({"stream": True} if mode else {}))
    assert status == 200 and served_of(headers) is None and '"served"' not in text and time.monotonic() - started < 5


def test_another_runs_later_reply_does_not_displace_this_runs_record(relay, tmp_path):
    _chat_db(tmp_path / "chat.sqlite", later_run=OTHER_RUN)
    _, headers, _ = post(with_db(relay, tmp_path), chat("openclaw/mc-chat", user=USER))
    assert served_of(headers)["model"] == "gpt-6-astra"


def test_a_reply_with_no_run_stamp_is_no_evidence(relay, tmp_path):
    _chat_db(tmp_path / "chat.sqlite", run=None)
    _, headers, _ = post(with_db(relay, tmp_path), chat("openclaw/mc-chat", user=USER))
    assert served_of(headers) is None


def test_an_answer_with_no_exact_run_id_is_no_evidence(relay, tmp_path):
    _chat_db(tmp_path / "chat.sqlite")
    env = with_db(relay, tmp_path)
    env.gateway.run_id = "chatcmpl_x"
    status, headers, _ = post(env, chat("openclaw/mc-chat", user=USER))
    assert status == 200 and served_of(headers) is None


def test_an_unreadable_record_is_no_evidence_and_the_answer_still_arrives(relay, tmp_path):
    env = relay(MC_RELAY_CHAT_DB=str(tmp_path / "missing.sqlite"))
    status, headers, _ = post(env, chat("openclaw/mc-chat", user=USER))
    assert status == 200 and "x-mc-served" not in {k.lower() for k in headers}


def test_a_strange_model_name_is_no_evidence_and_never_breaks_the_answer(relay, tmp_path):
    _chat_db(tmp_path / "chat.sqlite", model="gpt\u00e9\u2603")
    status, headers, _ = post(with_db(relay, tmp_path), chat("openclaw/mc-chat", user=USER))
    assert status == 200 and served_of(headers) is None
