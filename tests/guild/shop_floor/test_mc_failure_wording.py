"""When Master Craftsman gives no answer (Guild 1.1, 6 Oct 2026 Dev findings).

A run stopped by a time limit is NOT "nothing happened": its tools may already have run. The page must keep four
states apart and never say "Nothing was kept" when the owner's own message was kept. No model, no network."""
from __future__ import annotations

import json
import uuid

import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401  (load_portal is a pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from test_mc_streaming import (StreamRuntime, _events, _fresh_dispatch_state, _stream, _wait_idle, nd,  # noqa: F401
                               streaming)
from test_mc_turns import API, _keep  # noqa: F401

from minimoi_portal.guild_ui.mc.streaming import MESSAGES, PARTIAL_CLASSES


@pytest.mark.parametrize("failure", PARTIAL_CLASSES)
def test_a_stopped_or_failed_run_says_the_message_is_kept_and_no_answer_was_saved(failure):
    text = MESSAGES[failure]
    assert "Your message is kept" in text and "Nothing was kept" not in text
    assert "no answer" in text.lower()


@pytest.mark.parametrize("failure", ["idle", "deadline"])
def test_a_relay_cancellation_says_the_relay_cancelled_and_that_commands_may_still_run(failure):
    text = MESSAGES[failure]
    assert "the relay cancelled the run" in text and "may already have" in text and "check before asking again" in text
    assert "a shell command it started may still be running" in text      # a cancelled run is not a killed process
    assert "on this Mac" in text                                           # the honest scope of what a tool run can touch


def test_a_local_timeout_says_the_outcome_is_unknown_and_never_claims_a_cancellation():
    text = MESSAGES["local_timeout"]
    assert "outcome is unknown" in text and "nothing was cancelled or sent again" in text
    assert "may still be working or may have finished" in text and "Your message is kept" in text
    assert "relay cancelled" not in text and "was stopped" not in text


def test_stop_requested_and_stop_confirmed_are_different_messages():
    assert "Stop requested" in MESSAGES["stopped"] and "has not confirmed that" in MESSAGES["stopped"]
    assert MESSAGES["stopped_confirmed"].startswith("Stopped: the relay confirmed it cancelled the run")
    assert "a shell command it started may still be running" in MESSAGES["stopped_confirmed"]
    assert MESSAGES["stopped"] != MESSAGES["stopped_confirmed"]


def test_every_failure_has_its_own_words():
    assert len({MESSAGES[c] for c in PARTIAL_CLASSES}) == len(PARTIAL_CLASSES)
    assert len({MESSAGES["idle"], MESSAGES["deadline"], MESSAGES["local_timeout"], MESSAGES["stopped"], MESSAGES["stopped_confirmed"]}) == 5


def test_the_portal_waits_longer_than_a_relay_configured_for_long_tool_work():
    from minimoi_portal.guild_ui.mc.openclaw import STREAM_LIMITS
    assert STREAM_LIMITS.idle_s == 105.0 and STREAM_LIMITS.deadline_s == 125.0
    assert STREAM_LIMITS.idle_s > 100 and STREAM_LIMITS.deadline_s > 120      # a relay at 100 s idle / 120 s deadline speaks first


def test_a_streamed_idle_stop_reaches_the_page_with_the_honest_wording(streaming):
    streaming.extra["runtime"].script = [nd({"t": "error", "class": "idle"})]
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "Please save this file for me.")
    events = _events(_stream(client, token, note["request_id"]))
    _wait_idle()
    [err] = [e for e in events if e["t"] == "error"]
    assert err["failure_class"] == "idle" and err["message"] == MESSAGES["idle"]
    assert "Nothing was kept" not in json.dumps(events)
    # the owner's own message is still kept, with no answer attached to it
    rows = streaming.extra["floor"].rows("floor_messages")
    assert [r["text"] for r in rows] == ["Please save this file for me."]


class _StopRuntime(StreamRuntime):
    """A relay whose Stop acknowledgement can be made to fail."""
    stop_status = 200

    def post(self, url, data=None, headers=None, stream=False, **kw):
        if url.endswith("/turns/stop"):
            self.posts.append({"url": url, "body": json.loads(data), "headers": dict(headers or {}), "stream": stream})
            self.stopped.set()
            from test_mc_turns import Resp
            return Resp(self.stop_status, '{"stopped":true}')
        return super().post(url, data=data, headers=headers, stream=stream, **kw)


def _stop_flow(streaming, stop_status):
    from test_mc_turns import _openclaw
    runtime = _StopRuntime()
    runtime.stop_status = stop_status
    streaming.extra["services"].mc = _openclaw(runtime)
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "do a long job")
    runtime.script = [nd({"t": "delta", "text": "partial "}), "WAIT"] + [nd({"t": "finish", "reason": "stop"})]
    resp = _stream(client, token, note["request_id"])
    stream = iter(resp.response)
    turn_id = json.loads(next(stream))["turn_id"]
    assert json.loads(next(stream))["t"] == "delta" and runtime.waiting.wait(5)
    stop = client.post(f"{API}/mc/turns/{turn_id}/stop", json={}, headers=write_headers(token))
    rest = [json.loads(line) for line in b"".join(stream).decode().splitlines() if line.strip()]
    _wait_idle()
    final = [e for e in rest if e["t"] == "error"][-1]
    return stop.get_json(), final


def test_a_stop_the_relay_acknowledged_reads_as_confirmed(streaming):
    stop, final = _stop_flow(streaming, 200)
    assert stop["runtime_told"] is True and stop["message"] == MESSAGES["stopped_confirmed"]
    assert final["failure_class"] == "stopped" and final["message"] == MESSAGES["stopped_confirmed"]


def test_a_stop_the_relay_did_not_acknowledge_reads_as_only_requested(streaming):
    stop, final = _stop_flow(streaming, 500)
    assert stop["runtime_told"] is False and stop["message"] == MESSAGES["stopped"]
    assert final["failure_class"] == "stopped" and final["message"] == MESSAGES["stopped"]
    assert "confirmed it cancelled" not in final["message"]


def test_a_local_read_timeout_reaches_the_page_as_unknown_not_as_a_relay_cancellation(streaming):
    class TimeoutResp:
        status_code, text = 200, ""
        headers = {"Content-Type": "application/x-ndjson; charset=utf-8"}

        def iter_content(self, chunk_size=None):
            yield nd({"t": "delta", "text": "working "}).encode()
            raise TimeoutError("Read timed out")

        def close(self):
            pass

    from test_mc_turns import RELAY_TOKEN
    from minimoi_portal.guild_ui.mc.openclaw import OpenClawMasterCraftsman
    runtime = streaming.extra["runtime"]
    streaming.extra["services"].mc = OpenClawMasterCraftsman(
        "http://mc-relay:8790/v1", RELAY_TOKEN, http_get=runtime.get,
        http_post=lambda url, data=None, headers=None, stream=False, **kw: TimeoutResp())
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "a task that goes quiet")
    events = _events(_stream(client, token, note["request_id"]))
    _wait_idle()
    [err] = [e for e in events if e["t"] == "error"]
    assert err["failure_class"] == "local_timeout" and err["message"] == MESSAGES["local_timeout"]
    assert err["partial"] is True and "relay cancelled" not in err["message"]


def test_a_relay_sent_idle_reaches_the_page_as_a_confirmed_cancellation(streaming):
    streaming.extra["runtime"].script = [nd({"t": "error", "class": "idle"})]
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "a task the relay gave up on")
    [err] = [e for e in _events(_stream(client, token, note["request_id"])) if e["t"] == "error"]
    _wait_idle()
    assert err["failure_class"] == "idle" and err["message"] == MESSAGES["idle"]
