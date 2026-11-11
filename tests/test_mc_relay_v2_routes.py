"""The relay alone turns a route id into a model, and reads OpenClaw's own record of what served a turn."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
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


def test_deep_alias_is_forwarded_only_from_host_route_map(relay):
    model = "minimoi-gateway/minimoi-xai-strong-deep"
    env = relay(MC_RELAY_ROUTES=json.dumps({"api-xai-strong-deep": model}))
    status, _, _ = post(env, chat("openclaw/mc-chat"), **{"X-MC-Route": "api-xai-strong-deep"})
    assert status == 200
    sent = env.gateway.seen[-1]
    assert {k.lower(): v for k, v in sent["headers"].items()}["x-openclaw-model"] == model


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


def test_an_offered_grok_tier_goes_on_as_the_maps_model(relay):
    env = relay(MC_RELAY_ROUTES=json.dumps({**ROUTES, "api-xai-low": "minimoi-gateway/minimoi-xai-low"}))
    assert post(env, chat("openclaw/mc-chat"), **{"X-MC-Route": "api-xai-low"})[0] == 200
    assert {k.lower(): v for k, v in env.gateway.seen[-1]["headers"].items()}["x-openclaw-model"] == "minimoi-gateway/minimoi-xai-low"


def test_the_grok_tier_is_refused_while_the_host_has_not_offered_it(relay):
    env = relay()
    status, _, text = post(env, chat("openclaw/mc-chat"), **{"X-MC-Route": "api-xai-low"})
    assert status == 409 and "relay_route_refused" in text and env.gateway.seen == []


@pytest.mark.parametrize("ref", ["xai/grok-4.3", "minimoi-gateway/minimoi-cos-agent", "minimoi-gateway/minimoi-xai-pro"])
def test_the_relay_will_not_start_on_any_other_paid_reference(ref):
    relay_file = Path(__file__).resolve().parents[1] / "docker/mc-agent/relay_v2.mjs"
    run = subprocess.run(["node", str(relay_file)], capture_output=True, text=True, timeout=20,
                         env={"PATH": os.environ["PATH"], "MC_RELAY_TOKEN": "x" * 32, "MC_RELAY_ROUTES": json.dumps({"bad": ref})})
    assert run.returncode != 0 and "bad route" in run.stderr


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


def _claude_reply(path, key):
    db = sqlite3.connect(path)
    message = {"role": "assistant", "provider": "claude-cli", "model": "claude-opus-5", "api": "cli", "idempotencyKey": key}
    db.execute("INSERT INTO transcript_events VALUES ('s1', 9, ?, NULL)", (json.dumps({"type": "message", "message": message}),))
    db.commit()
    db.close()


def test_a_claude_cli_reply_is_bound_to_its_run_by_its_key(relay, tmp_path):
    _chat_db(tmp_path / "chat.sqlite", run=OTHER_RUN, harness=None)
    _claude_reply(tmp_path / "chat.sqlite", f"cli-assistant:{RUN}")
    _, headers, _ = post(with_db(relay, tmp_path), chat("openclaw/mc-chat", user=USER))
    assert served_of(headers) == {"provider": "claude-cli", "model": "claude-opus-5", "api": "cli"}


def test_another_runs_claude_cli_reply_is_no_evidence(relay, tmp_path):
    _chat_db(tmp_path / "chat.sqlite", run=OTHER_RUN)
    _claude_reply(tmp_path / "chat.sqlite", f"cli-assistant:{OTHER_RUN}")
    _, headers, _ = post(with_db(relay, tmp_path), chat("openclaw/mc-chat", user=USER))
    assert served_of(headers) is None


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


# ── Plan failover: the relay reports the declared signal and the tool evidence; it never retries ──

def _marks(tmp_path, *names, ready=True, age=0):
    folder = tmp_path / "mc-tool-starts"
    folder.mkdir(exist_ok=True)
    if ready:
        (folder / ".ready").touch()
    for name in names:
        path = folder / name
        path.touch()
        if age:
            os.utime(path, (time.time() - age, time.time() - age))
    return folder


def _lines(text):
    return [json.loads(line) for line in text.splitlines() if line]


def _failing(relay, tmp_path, error, *, folder=None, partial=""):
    env = relay(**({"MC_RELAY_TOOL_STARTS": str(folder)} if folder else {}))
    env.gateway.run_id, env.gateway.mode, env.gateway.error, env.gateway.partial = RUN, "sse_error", error, partial
    return env


@pytest.mark.parametrize("kind,signal", [("rate_limit_error", "rate_limit"), ("insufficient_quota", "plan_exhausted"),
                                         ("authentication_error", "signin_expired")])
def test_rule_1_each_declared_type_is_reported_with_no_retry(relay, tmp_path, kind, signal):
    env = _failing(relay, tmp_path, {"type": kind, "message": "whatever"}, folder=_marks(tmp_path))
    _, _, text = post(env, chat("openclaw/mc-chat", user=USER, stream=True), **{"X-MC-Route": "plan-openai-strong"})
    assert _lines(text)[-1] == {"t": "error", "class": "upstream", "signal": signal, "tools": "none"}
    assert len(env.gateway.seen) == 1


@pytest.mark.parametrize("error", [{"type": "api_error", "message": "rate limit reached"}, {"type": "permission_error"},
                                   {"type": "invalid_request_error", "message": "insufficient_quota"}, {"message": "internal error"}])
def test_rule_1_an_ambiguous_error_carries_no_signal_whatever_it_says(relay, tmp_path, error):
    env = _failing(relay, tmp_path, error, folder=_marks(tmp_path))
    _, _, text = post(env, chat("openclaw/mc-chat", user=USER, stream=True))
    assert _lines(text)[-1] == {"t": "error", "class": "upstream"}


def test_rule_2_a_tool_marked_during_this_turn_means_may_have_begun(relay, tmp_path):
    env = _failing(relay, tmp_path, {"type": "rate_limit_error"}, folder=_marks(tmp_path, RUN))
    assert _lines(post(env, chat("openclaw/mc-chat", user=USER, stream=True))[2])[-1]["tools"] == "may_have_begun"


def test_rule_2_an_old_mark_from_an_earlier_turn_does_not_count(relay, tmp_path):
    env = _failing(relay, tmp_path, {"type": "rate_limit_error"}, folder=_marks(tmp_path, OTHER_RUN, age=600))
    assert _lines(post(env, chat("openclaw/mc-chat", user=USER, stream=True))[2])[-1]["tools"] == "none"


def test_rule_2_text_already_sent_means_may_have_begun(relay, tmp_path):
    env = _failing(relay, tmp_path, {"type": "rate_limit_error"}, folder=_marks(tmp_path), partial="Partly")
    assert _lines(post(env, chat("openclaw/mc-chat", user=USER, stream=True))[2])[-1]["tools"] == "may_have_begun"


@pytest.mark.parametrize("setup", ["no-setting", "no-ready", "missing-folder"])
def test_rule_2_no_way_to_know_is_unknown_never_none(relay, tmp_path, setup):
    folder = {"no-setting": None, "no-ready": _marks(tmp_path, ready=False), "missing-folder": tmp_path / "nope"}[setup]
    env = _failing(relay, tmp_path, {"type": "rate_limit_error"}, folder=folder)
    assert _lines(post(env, chat("openclaw/mc-chat", user=USER, stream=True))[2])[-1]["tools"] == "unknown"


def test_rule_1_a_non_streaming_error_reports_in_headers(relay, tmp_path):
    env = relay(MC_RELAY_TOOL_STARTS=str(_marks(tmp_path)))
    env.gateway.mode, env.gateway.error, env.gateway.error_status = "json_error", {"type": "insufficient_quota", "message": "x"}, 402
    status, headers, _ = post(env, chat("openclaw/mc-chat", user=USER))
    got = {k.lower(): v for k, v in headers.items()}
    assert status == 402 and got["x-mc-plan-signal"] == "plan_exhausted" and got["x-mc-plan-tools"] == "none"
    env.gateway.error, env.gateway.error_status = {"type": "api_error"}, 500
    assert "x-mc-plan-signal" not in {k.lower() for k in post(env, chat("openclaw/mc-chat", user=USER))[1]}


def test_rule_3_a_budget_error_is_marked_as_the_cap(relay, tmp_path):
    env = _failing(relay, tmp_path, {"type": "api_error", "message": "Budget has been exceeded"}, folder=_marks(tmp_path))
    assert _lines(post(env, chat("openclaw/mc-chat", user=USER, stream=True))[2])[-1] == {"t": "error", "class": "upstream", "cap": True, "tools": "none"}


def test_rule_6_the_api_targets_go_on_as_the_maps_model(relay):
    env = relay(MC_RELAY_ROUTES=json.dumps({**ROUTES, "api-openai-strong": "minimoi-gateway/minimoi-openai-strong"}))
    assert post(env, chat("openclaw/mc-chat"), **{"X-MC-Route": "api-openai-strong"})[0] == 200
    assert {k.lower(): v for k, v in env.gateway.seen[-1]["headers"].items()}["x-openclaw-model"] == "minimoi-gateway/minimoi-openai-strong"


def _steps_db(path, steps, *, run=RUN, last_stop="stop"):
    """OpenClaw's record of a tool-using run: one assistant message per step, the last one ending the run."""
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE session_windows (session_id TEXT, session_key TEXT, agent_harness_id TEXT, updated_at INT)")
    db.execute("CREATE TABLE transcript_events (session_id TEXT, seq INT, event_json TEXT, event_zstd BLOB)")
    db.execute("INSERT INTO session_windows VALUES ('s1', ?, 'openclaw', 1)", (KEY,))
    for seq, text in enumerate(steps, 1):
        last = seq == len(steps)
        content = [{"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": text}]
        if not last:
            content.append({"type": "toolCall", "id": f"c{seq}", "name": "read", "arguments": {}})
        message = {"role": "assistant", "provider": "xai", "model": "grok-4.7", "content": content,
                   "stopReason": last_stop if last else "toolUse", "__openclaw": {"runId": run}}
        db.execute("INSERT INTO transcript_events VALUES ('s1', ?, ?, NULL)", (seq * 2, json.dumps({"type": "message", "message": message})))
    db.commit()
    db.close()


def _stream_events(env):
    status, _, text = post(env, chat("openclaw/mc-chat", user=USER) | {"stream": True})
    assert status == 200
    return [json.loads(line) for line in text.splitlines()]


def test_a_tool_using_run_sends_its_final_step_as_the_answer_before_the_finish(relay, tmp_path):
    steps = ["I'll read the file first.", "The answer is 42."]
    _steps_db(tmp_path / "chat.sqlite", steps)
    env = with_db(relay, tmp_path, "stream_steps")
    env.gateway.steps = steps
    events = _stream_events(env)
    kinds = [e["t"] for e in events]
    assert {"t": "answer", "text": "The answer is 42."} in events
    assert kinds.index("answer") == kinds.index("finish") - 1
    assert "".join(e["text"] for e in events if e["t"] == "delta") == "".join(steps)     # the page still sees every step live


@pytest.mark.parametrize("case", ["one_step", "other_run", "ended_on_a_tool", "differs"])
def test_no_answer_line_when_the_record_cannot_prove_it(relay, tmp_path, case):
    steps = ["I'll read the file first.", "The answer is 42."]
    recorded = {"one_step": ["I'll read the file first.The answer is 42."], "differs": ["I'll read it.", "Something else."]}.get(case, steps)
    _steps_db(tmp_path / "chat.sqlite", recorded, run=OTHER_RUN if case == "other_run" else RUN,
              last_stop="toolUse" if case == "ended_on_a_tool" else "stop")
    env = with_db(relay, tmp_path, "stream_steps")
    env.gateway.steps = steps
    events = _stream_events(env)
    assert not [e for e in events if e["t"] == "answer"]
    assert [e["t"] for e in events].count("finish") == 1


# A portal restart loses a turn's stream; the portal settles it only on the runtime's signed end receipt (Dev, 11 Nov).
def _get(env, path, token=CALLER):
    req = urllib.request.Request(env.base + path, headers={"authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def test_the_relay_serves_a_runs_end_receipt_to_the_portal_only(relay, tmp_path):
    proofs = tmp_path / "proofs"
    proofs.mkdir()
    receipt = {"instance": "a" * 32, "run": RUN, "session": "agent:mc-chat:openai-user:x", "agent": "mc-chat",
               "success": True, "model_calls": 2, "tool_calls": 1, "signature": "s"}
    (proofs / f"{RUN}.json").write_text(json.dumps(receipt))
    (proofs / f"{OTHER_RUN}.json").write_text(json.dumps({**receipt, "run": RUN}))     # a file that names another run
    env = relay(MC_RELAY_DEPTH_PROOFS=str(proofs))
    assert _get(env, f"/v1/runs/{RUN}/terminal") == (200, {"receipt": receipt})
    assert _get(env, f"/v1/runs/{OTHER_RUN}/terminal")[0] == 404
    assert _get(env, "/v1/runs/chatcmpl_33333333-3333-4333-8333-333333333333/terminal")[0] == 404
    assert _get(env, "/v1/runs/../terminal")[0] in (401, 403, 404)
    assert _get(env, f"/v1/runs/{RUN}/terminal", token="wrong" * 8)[0] == 401
