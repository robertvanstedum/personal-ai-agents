"""The relay forwards a run's tool steps (issue #545): each tool's name and timing from OpenClaw's own record, never its
arguments, its result or any other run's calls."""
from __future__ import annotations

import json
import shutil
import sqlite3

import pytest

from test_mc_relay_v2 import chat
from test_mc_relay_v2_routes import KEY, OTHER_RUN, RUN, USER, post, relay, with_db  # noqa: F401  (relay: pytest fixture)

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")

SECRET = "ghp_SECRETVALUE1234567890"


def _record(path, *, other_run_call=False):
    """A tool-using run: a GitHub read (ok), a command (failed, its result holding a secret), then the answer."""
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE session_windows (session_id TEXT, session_key TEXT, agent_harness_id TEXT, updated_at INT)")
    db.execute("CREATE TABLE transcript_events (session_id TEXT, seq INT, event_json TEXT, event_zstd BLOB)")
    db.execute("INSERT INTO session_windows VALUES ('s1', ?, 'openclaw', 1)", (KEY,))
    rows = []
    if other_run_call:                     # an earlier turn's call: never this run's step
        rows.append({"role": "assistant", "content": [{"type": "toolCall", "id": "old", "name": "write", "arguments": {}}],
                     "stopReason": "toolUse", "__openclaw": {"runId": OTHER_RUN}, "timestamp": 1})
        rows.append({"role": "toolResult", "toolCallId": "old", "toolName": "write", "content": [], "timestamp": 2})
    rows += [
        {"role": "assistant", "timestamp": 1_000, "stopReason": "toolUse", "__openclaw": {"runId": RUN},
         "content": [{"type": "text", "text": "Checking GitHub now."},
                     {"type": "toolCall", "id": "c1", "name": "minimoi_github_issues", "arguments": {"number": 545, "token": SECRET}},
                     {"type": "toolCall", "id": "c2", "name": "exec", "arguments": {"command": f"echo {SECRET}"}},
                     {"type": "toolCall", "id": "c3", "name": "bad name; rm -rf", "arguments": {}}]},
        {"role": "toolResult", "toolCallId": "c1", "toolName": "minimoi_github_issues", "timestamp": 1_800,
         "content": [{"type": "text", "text": f"issue body {SECRET}"}]},
        {"role": "toolResult", "toolCallId": "c2", "toolName": "exec", "timestamp": "1970-01-01T00:00:02.500Z", "isError": True,
         "content": [{"type": "text", "text": SECRET}]},
        {"role": "assistant", "timestamp": 3_000, "stopReason": "stop", "__openclaw": {"runId": RUN},
         "content": [{"type": "text", "text": "All green."}]},
    ]
    for seq, message in enumerate(rows, 1):
        db.execute("INSERT INTO transcript_events VALUES ('s1', ?, ?, NULL)", (seq, json.dumps({"type": "message", "message": message})))
    db.commit()
    db.close()


def _stream(env):
    status, _, text = post(env, chat("openclaw/mc-chat", user=USER) | {"stream": True})
    assert status == 200
    return text, [json.loads(line) for line in text.splitlines()]


def test_the_runs_steps_go_out_as_names_and_timing_before_the_finish(relay, tmp_path):
    _record(tmp_path / "chat.sqlite", other_run_call=True)
    env = with_db(relay, tmp_path, "stream_steps")
    env.gateway.steps = ["Checking GitHub now.", "All green."]
    text, events = _stream(env)
    steps = [e for e in events if e["t"] == "step"]
    assert steps == [
        {"t": "step", "n": 1, "name": "minimoi_github_issues", "phase": "start"},
        {"t": "step", "n": 1, "name": "minimoi_github_issues", "phase": "end", "ok": True, "ms": 800},
        {"t": "step", "n": 2, "name": "exec", "phase": "start"},
        {"t": "step", "n": 2, "name": "exec", "phase": "end", "ok": False, "ms": 1500},
    ]
    kinds = [e["t"] for e in events]
    assert max(i for i, k in enumerate(kinds) if k == "step") < kinds.index("finish")
    assert SECRET not in text and "545" not in text                 # never an argument or a result
    assert "write" not in text and "rm -rf" not in text             # never another run's call, never a name that is not a word


def test_no_record_no_steps_and_the_stream_is_unchanged(relay, tmp_path):
    env = relay(MC_RELAY_CHAT_DB=str(tmp_path / "missing.sqlite"))
    env.gateway.run_id, env.gateway.mode = RUN, "stream_steps"
    _, events = _stream(env)
    assert not [e for e in events if e["t"] == "step"] and "finish" in [e["t"] for e in events]


def test_without_a_record_configured_the_relay_sends_no_steps(relay):
    env = relay()
    env.gateway.run_id, env.gateway.mode = RUN, "stream_steps"
    _, events = _stream(env)
    assert not [e for e in events if e["t"] == "step"]
