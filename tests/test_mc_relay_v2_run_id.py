"""Relay v2 forwards OpenClaw's run id once, first, so the portal can bind that run's tool calls to the owner's
turn (MC spec v1.2 §7.1). Only OpenClaw's exact `chatcmpl_<uuid>` shape passes; any other id stays dropped, as in v1.
The real relay runs under node against the v1 test file's fake OpenClaw gateway. Synthetic data only."""
from __future__ import annotations

import json
import uuid

import pytest

import test_mc_relay as v1
from test_mc_relay import HAPPY, chunk, relay, sse, stream_call  # noqa: F401  (relay is a fixture)

V2 = v1.REPO / "docker" / "mc-agent" / "relay_v2.mjs"


@pytest.fixture(autouse=True)
def _run_against_v2(monkeypatch):
    monkeypatch.setattr(v1, "RELAY", V2)


def _with_id(run_id, text=None, finish=None, **extra):
    return chunk(text, finish, **extra) | {"id": run_id}


def _opening(run_id):
    return sse(_with_id(run_id) | {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})


def test_the_run_id_comes_first_and_only_once(relay):
    base, up, _ = relay
    run_id = f"chatcmpl_{uuid.uuid4()}"
    up.script = [(_opening(run_id), 0), (sse(_with_id(run_id, "Hello ")), 0.02), (sse(_with_id(run_id, "Robert.")), 0.02),
                 (sse(_with_id(run_id, None, "stop")), 0.01), (b"data: [DONE]\n\n", 0.01)]
    status, _, events = stream_call(base)
    assert status == 200
    assert events[0] == {"t": "run", "id": run_id}
    assert [e for e in events if e["t"] == "run"] == [{"t": "run", "id": run_id}]
    assert events[1:] == [{"t": "delta", "text": "Hello "}, {"t": "delta", "text": "Robert."},
                          {"t": "finish", "reason": "stop"}]


def test_a_later_different_id_is_never_forwarded(relay):
    base, up, _ = relay
    first, other = f"chatcmpl_{uuid.uuid4()}", f"chatcmpl_{uuid.uuid4()}"
    up.script = [(_opening(first), 0), (sse(_with_id(other, "x")), 0.02), (sse(_with_id(first, None, "stop")), 0.01),
                 (b"data: [DONE]\n\n", 0.01)]
    _, _, events = stream_call(base)
    runs = [e for e in events if e["t"] == "run"]
    assert runs == [{"t": "run", "id": first}]
    assert other not in json.dumps(events)


@pytest.mark.parametrize("bad", ["chatcmpl_up", "chatcmpl_" + "z" * 36, f"chatcmpl_{uuid.uuid4()}x", f"run_{uuid.uuid4()}",
                                 f"CHATCMPL_{uuid.uuid4()}", 12345])
def test_anything_but_openclaws_exact_run_id_shape_is_dropped(relay, bad):
    base, up, _ = relay
    up.script = [(_opening(bad), 0), (sse(_with_id(bad, "Hi")), 0.02), (sse(_with_id(bad, None, "stop")), 0.01),
                 (b"data: [DONE]\n\n", 0.01)]
    _, _, events = stream_call(base)
    assert all(e["t"] != "run" for e in events)
    assert events == [{"t": "delta", "text": "Hi"}, {"t": "finish", "reason": "stop"}]


def test_the_usual_test_stream_is_unchanged(relay):
    base, up, _ = relay
    up.script = HAPPY
    _, _, events = stream_call(base)
    assert events == [{"t": "delta", "text": "Hello "}, {"t": "delta", "text": "wörld"},
                      {"t": "finish", "reason": "stop"},
                      {"t": "usage", "prompt_tokens": 120, "completion_tokens": 7}]
