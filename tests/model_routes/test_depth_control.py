import json

import pytest

from core.model_routes.depth_control import ControlBuffer, DepthRequest, InvalidDepthControl, MARKER, instruction

NONCE = "a" * 32


def control(**changes):
    return MARKER + json.dumps({"kind": "request_depth", "nonce": NONCE, "reason": "Compare the risks", **changes})


def test_terminal_signal_can_arrive_one_character_at_a_time_without_leaking():
    buffer = ControlBuffer(NONCE)
    for char in control():
        assert buffer.feed(char) == ""
    assert buffer.finish(completed=True) == DepthRequest(NONCE, "Compare the risks")


def test_normal_answer_streams_without_waiting_for_completion():
    buffer = ControlBuffer(NONCE)
    assert buffer.feed("The answer") == "The answer"
    assert buffer.feed(" is four.") == " is four."
    assert buffer.finish(completed=True) is None


def test_signal_after_answer_started_is_never_a_continuation():
    buffer = ControlBuffer(NONCE)
    assert buffer.feed("Quoted response:\n") == "Quoted response:\n"
    assert buffer.feed(control()) == control()
    assert buffer.finish(completed=True) is None


@pytest.mark.parametrize("payload", [
    control(nonce="b" * 32), control(kind="call_tool"), control(route="api-openai-strong"),
    control(reason=""), control(reason="a" * 241), control(reason="x\ny"),
    control() + "\nHere is my answer.", control()[:-1],
    MARKER + '{"kind":"request_depth","nonce":"' + NONCE + '","reason":"a","reason":"b"}',
])
def test_malformed_control_never_authorizes_another_attempt(payload):
    buffer = ControlBuffer(NONCE)
    assert buffer.feed(payload) == ""
    with pytest.raises(InvalidDepthControl):
        buffer.finish(completed=True)


def test_failed_or_stopped_runtime_cannot_complete_control():
    buffer = ControlBuffer(NONCE)
    buffer.feed(control())
    with pytest.raises(InvalidDepthControl):
        buffer.finish(completed=False)


def test_control_size_is_bounded_in_bytes():
    buffer = ControlBuffer(NONCE)
    with pytest.raises(InvalidDepthControl):
        buffer.feed(MARKER + "ü" * 600)


def test_oversized_valid_json_permanently_poisons_control():
    buffer = ControlBuffer(NONCE)
    with pytest.raises(InvalidDepthControl):
        buffer.feed(control() + " " * 1024)
    with pytest.raises(InvalidDepthControl):
        buffer.finish(completed=True)


def test_terminal_control_cannot_be_consumed_twice():
    buffer = ControlBuffer(NONCE)
    buffer.feed(control())
    buffer.finish(completed=True)
    with pytest.raises(InvalidDepthControl):
        buffer.finish(completed=True)
    with pytest.raises(InvalidDepthControl):
        buffer.feed("late text")


def test_instruction_contains_only_host_nonce_not_route_authority():
    prompt = instruction(NONCE)
    assert NONCE in prompt and MARKER in prompt
    assert "api-xai" not in prompt
    with pytest.raises(ValueError):
        instruction("bad nonce")
