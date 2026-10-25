"""Relay v2 (docker/mc-agent/relay_v2.mjs) must keep every behaviour of relay v1: this runs the whole v1 test file
against the v2 source. Where v2 deliberately differs, the v1 test is replaced below with the v2 expectation."""
from __future__ import annotations

import pytest

import test_mc_relay as v1
from test_mc_relay import *  # noqa: F401,F403  (every v1 test and fixture, collected here against v2)

V2 = v1.REPO / "docker" / "mc-agent" / "relay_v2.mjs"


@pytest.fixture(autouse=True)
def _run_against_v2(monkeypatch):
    monkeypatch.setattr(v1, "RELAY", V2)


def test_silent_tool_work_before_headers_completes_without_redispatch(relay):
    base, up, _ = relay
    up.header_delay = 0.6  # inside the scaled one-second silence allowance
    up.script = HAPPY
    status, _, events = stream_call(base)
    assert status == 200
    assert any(e.get("reason") == "stop" for e in events)
    assert len(up.seen) == 1


def test_received_request_then_silence_times_out_without_retry(relay):
    base, up, _ = relay
    up.header_delay = 1.6  # receipt is recorded before headers; this does not simulate a Board commit
    up.script = HAPPY
    status, _, body = stream_call(base)
    assert status == 504 and "relay_timeout" in body
    time.sleep(0.15)
    assert len(up.seen) == 1


def test_stop_before_headers_does_not_redispatch(relay):
    base, up, _ = relay
    up.header_delay = 0.8
    up.script = HAPPY
    results = []
    t = threading.Thread(target=lambda: results.append(stream_call(base, corr="a" * 32)))
    t.start()
    until = time.monotonic() + 2
    while not up.seen and time.monotonic() < until:
        time.sleep(0.01)
    assert up.seen
    assert call(base, "/v1/turns/stop", "POST", {"correlation_id": "a" * 32})[0] == 200
    t.join(3)
    assert not t.is_alive()
    assert results[0][0] == 409 and "relay_stopped" in results[0][2]
    assert len(up.seen) == 1
