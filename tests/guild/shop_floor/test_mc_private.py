"""A Private question to Master Craftsman (7 Oct 2026). The text goes to the model and the answer comes back; MiniMoi keeps
NOTHING. Each rule below is the one a regression would break: what is refused, what reaches the model, what is never written.
Driven through the real portal with a fake runtime behind the real OpenClaw adapter. No network, no model call."""
from __future__ import annotations

import json
import logging

import pytest

from minimoi_portal.guild_ui.api import PRIVATE_NO_JOB
from minimoi_portal.guild_ui.security import OFF_RECORD_TEXT  # noqa: F401

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored  # noqa: F401  (pytest fixtures)
from test_mc_turns import FakeRuntime, Resp, _openclaw, _turn_on

API = "/guild-next/api/v1"
SESSION = "p" + "a1b2c3d4" * 4


def _private(client, token, text="I want to discuss my wife's job interview", session=SESSION, mode="off_record", **extra):
    return client.post(f"{API}/mc/private", json={"text": text, "session": session, **extra}, headers=write_headers(token, mode=mode))


def _answer(text):
    return lambda body, headers: Resp(200, json.dumps({"id": "chatcmpl_p", "object": "chat.completion", "model": "openclaw/mc-chat",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}), {"X-MC-Correlation-Id": headers.get("X-MC-Correlation-Id")})


@pytest.fixture
def turned(floored):
    runtime = FakeRuntime(_answer("Happy to. What is the role, and what worries you most about the interview?"))
    _turn_on(floored, _openclaw(runtime))
    floored.extra["runtime"] = runtime
    return floored


def _nothing_written(portal):
    db = portal.extra["floor"]
    assert db.rows("floor_messages") == []


def test_a_private_question_is_answered_and_nothing_at_all_is_written(turned, caplog):
    caplog.set_level(logging.INFO)
    client = turned.owner()
    token = turned.csrf(client)
    r = _private(client, token)
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "answered"
    assert body["reply"]["text"].startswith("Happy to.") and "<p>" in body["reply"]["html"]
    assert body["message"] == "Master Craftsman answered · not kept by MiniMoi"
    _nothing_written(turned)                                                             # no note, no reply note
    for forbidden in ("reply_note", "note", "conversation", "history_saved", "job"):
        assert forbidden not in body
    assert "wife" not in caplog.text and "job interview" not in caplog.text             # the text never reaches a log
    assert "chatcmpl_p" not in json.dumps(body)                                          # the runtime's id stays on the server


def test_the_model_gets_the_words_a_private_context_marker_and_a_session_that_is_not_any_conversations(turned):
    client = turned.owner()
    token = turned.csrf(client)
    _private(client, token)
    _private(client, token, text="and the second question")
    _private(client, token, text="a different sitting", session="q" + "z9y8x7w6" * 4)
    one, two, other = turned.extra["runtime"].sent
    assert "I want to discuss my wife's job interview" in one["body"]["messages"][0]["content"]
    assert '"mode": "private"' in one["body"]["messages"][0]["content"]
    assert one["body"]["user"] == two["body"]["user"] != other["body"]["user"]           # one sitting = one backend session
    assert one["body"]["user"].startswith("guild-mc:") and "robert" not in one["body"]["user"]


def test_it_is_only_for_off_the_record_and_the_on_record_door_still_refuses_private(turned):
    client = turned.owner()
    token = turned.csrf(client)
    on = _private(client, token, mode="on_record")
    assert on.status_code == 422 and turned.extra["runtime"].sent == []                  # never a way to ask about kept work unkept
    missing = client.post(f"{API}/mc/private", json={"text": "hi", "session": SESSION}, headers={"X-CSRF-Token": token})
    assert missing.status_code == 422 and turned.extra["runtime"].sent == []
    notes = client.post(f"{API}/notes", json={"request_id": "k" * 16, "text": "x", "record_mode": "off_record"},
                        headers=write_headers(token, mode="off_record"))
    assert notes.status_code == 409 and notes.get_json()["message"] == OFF_RECORD_TEXT   # every other write is still refused
    _nothing_written(turned)


@pytest.mark.parametrize("case", ["no_csrf", "bad_origin", "not_json", "extra_field", "no_session", "short_session", "bad_session_chars", "empty", "too_long", "text_not_string"])
def test_a_malformed_or_unverified_private_question_sends_nothing(turned, case):
    client = turned.owner()
    token = turned.csrf(client)
    headers = write_headers(token, mode="off_record")
    json_body = {"text": "hello", "session": SESSION}
    if case == "no_csrf": headers.pop("X-CSRF-Token")
    if case == "bad_origin": headers["Origin"] = "https://evil.example"
    if case == "extra_field": json_body["conversation_id"] = "c-1"
    if case == "no_session": json_body.pop("session")
    if case == "short_session": json_body["session"] = "abc"
    if case == "bad_session_chars": json_body["session"] = "../../etc/passwd/........"
    if case == "empty": json_body["text"] = "   "
    if case == "too_long": json_body["text"] = "x" * 4001
    if case == "text_not_string": json_body["text"] = ["a"]
    if case == "not_json":
        r = client.post(f"{API}/mc/private", data="text=hi", headers=headers)
    else:
        r = client.post(f"{API}/mc/private", json=json_body, headers=headers)
    assert r.status_code in (403, 415, 422) and turned.extra["runtime"].sent == []
    _nothing_written(turned)


def test_payment_details_are_removed_before_the_text_leaves(turned):
    client = turned.owner()
    token = turned.csrf(client)
    _private(client, token, text="my card is 5555 5555 5555 4444 please remember")
    assert "5555 5555 5555 4444" not in turned.extra["runtime"].sent[0]["body"]["messages"][0]["content"]


def test_a_reply_that_proposes_a_job_is_replaced_by_one_plain_sentence_and_no_job_starts(floored):
    runtime = FakeRuntime(_answer("JOB: Compare the resumes\nI will do it as a background job."))
    _turn_on(floored, _openclaw(runtime))
    client = floored.owner()
    token = floored.csrf(client)
    body = _private(client, token, text="compare two files").get_json()
    assert body["reply"]["text"] == PRIVATE_NO_JOB and "JOB" not in body["reply"]["html"]
    _nothing_written(floored)


def test_a_failed_turn_says_so_and_nothing_is_saved(floored):
    runtime = FakeRuntime(lambda b, h: Resp(500, '{"error":{"message":"LLM request failed: 400 No connected db."}}'))
    _turn_on(floored, _openclaw(runtime))
    client = floored.owner()
    token = floored.csrf(client)
    r = _private(client, token)
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "unavailable" and "reply" not in body
    assert body["message"].endswith("Master Craftsman did not answer. MiniMoi kept nothing.")
    _nothing_written(floored)


def test_with_turns_off_nothing_is_sent(floored):
    runtime = FakeRuntime()
    _turn_on(floored, _openclaw(runtime), turns=False)
    client = floored.owner()
    token = floored.csrf(client)
    r = _private(client, token)
    assert r.status_code == 409 and r.get_json()["error"] == "mc_turns_off" and runtime.sent == []


def test_it_needs_the_owner_and_a_guest_or_a_signed_out_visitor_is_refused(turned):
    token = turned.csrf(turned.owner())
    for who in (turned.client(), turned.guest()):
        r = who.post(f"{API}/mc/private", json={"text": "hi", "session": SESSION}, headers=write_headers(token, mode="off_record"))
        assert r.status_code in (401, 403) and turned.extra["runtime"].sent == []


def test_no_turn_log_and_no_memory_capture_line_is_made_for_a_private_turn(turned, tmp_path, monkeypatch):
    monkeypatch.setenv("MC_TURNS_DIR", str(tmp_path / "turns"))
    client = turned.owner()
    token = turned.csrf(client)
    _private(client, token)
    assert not (tmp_path / "turns").exists()                                             # the memory capture wrote nothing
    log = turned.app.extensions["guild_ui_next"]["services"]
    trace = getattr(log, "turn_log", None)
    assert trace is None or not getattr(trace, "records", [])                            # and the timing trace has no line either


def test_one_private_turn_at_a_time(turned):
    from minimoi_portal.guild_ui import api
    client = turned.owner()
    token = turned.csrf(client)
    api._MC_INFLIGHT.add("robert")
    try:
        r = _private(client, token)
    finally:
        api._MC_INFLIGHT.discard("robert")
    assert r.status_code == 409 and r.get_json()["error"] == "busy" and turned.extra["runtime"].sent == []


def test_the_adapter_keeps_only_status_not_the_private_answer_after_the_request(turned):
    """Codex review, 7 Oct: the shared adapter used to keep the whole TurnResult (reply text included) for the header."""
    client = turned.owner()
    token = turned.csrf(client)
    _private(client, token)
    backend = turned.app.extensions["guild_ui_next"]["services"].mc
    last = backend._last
    assert last is not None and last.status == "answered"
    assert not hasattr(last, "text") and not hasattr(last, "usage") and not hasattr(last, "trace")
    assert "Happy to" not in repr(last) and "interview" not in repr(vars(backend))
    assert backend.health().state == "ready"                                           # the header still says live
