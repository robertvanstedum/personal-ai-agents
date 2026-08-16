from uuid import uuid4

import pytest

from domains.cos.confer_service import (
    ALLOWED_CHANNELS,
    MAX_EXPLICIT_NOTE_CHARS,
    ConferOperationFailed,
    ConferTurnRequest,
    ConferTurnService,
    InvalidConferTurn,
)


def _service(
    calls,
    increments,
    note_writes=None,
    note_outcome=None,
    resets=None,
    reset_outcome=True,
):
    def call_backend(prompt, context, tool_policy):
        calls.append((prompt, context, tool_policy))
        return f"reply to {prompt}"

    def save_note(note_text, operation_id):
        if note_writes is None:
            raise AssertionError("unexpected note write")
        note_writes.append((note_text, operation_id))
        return note_outcome or {"saved": True, "deduplicated": False}

    def reset_conversation(conversation_id):
        if resets is None:
            raise AssertionError("unexpected session reset")
        resets.append(conversation_id)
        return reset_outcome

    return ConferTurnService(
        call_backend=call_backend,
        build_context=lambda: {
            "recent_memory": "remembered",
            "system_prompt": "system",
        },
        increment_chat=lambda: increments.append(True),
        backend_metadata=lambda: ("Grok (direct API)", "grok-test"),
        save_note=save_note,
        reset_conversation=reset_conversation,
    )


def test_all_channels_use_one_backend_contract_and_result_shape():
    calls = []
    increments = []
    service = _service(calls, increments)

    for channel in sorted(ALLOWED_CHANNELS):
        result = service.handle(ConferTurnRequest(
            text="  hello  ",
            channel=channel,
            conversation_id="owner",
        ))
        assert result.reply == "reply to hello"
        assert result.channel == channel
        assert result.conversation_id == "owner"
        assert result.user_text == "hello"
        assert result.backend_label == "Grok (direct API)"
        assert result.model_label == "grok-test"
        assert result.turn_id
        assert result.created_at.endswith("+00:00")
        assert "user_text" not in result.public_dict()
        assert result.public_dict()["reply"] == "reply to hello"
        assert "runtime_route" not in result.public_dict()

    assert len(calls) == len(ALLOWED_CHANNELS)
    assert len(increments) == len(ALLOWED_CHANNELS)
    for _, context, tool_policy in calls:
        assert context["recent_memory"] == "remembered"
        if context["confer"]["input_channel"] == "html_voice":
            assert context["system_prompt"].startswith("system\n\n")
            assert "one to three short, natural sentences" in context["system_prompt"]
            assert "Do not use Markdown headings or bullet lists" in context["system_prompt"]
        else:
            assert context["system_prompt"] == "system"
        assert context["confer"]["conversation_id"] == "owner"
        assert context["confer"]["input_channel"] in ALLOWED_CHANNELS
        assert context["confer"]["receipt_id"]
        assert tool_policy == {"observation": True, "mutation": False}


def test_voice_concision_instruction_is_not_added_to_typed_turns():
    calls = []
    service = _service(calls, [])

    service.handle(ConferTurnRequest(text="voice", channel="html_voice"))
    service.handle(ConferTurnRequest(text="text", channel="html_text"))

    assert "live spoken conversation" in calls[0][1]["system_prompt"]
    assert calls[1][1]["system_prompt"] == "system"


def test_runtime_route_metadata_reports_the_model_that_served_the_turn():
    def call_backend(prompt, context, tool_policy):
        context["runtime_route"] = {
            "configured_primary": "xai/primary",
            "served_provider": "anthropic",
            "served_model": "anthropic/secondary",
            "fallback_position": 2,
            "fallback_reason": "runtime attempt failed",
        }
        return "fallback reply"

    service = ConferTurnService(
        call_backend=call_backend,
        build_context=lambda: {},
        increment_chat=lambda: None,
        backend_metadata=lambda: ("COS Agent A (OpenClaw)", "xai/primary"),
    )

    result = service.handle(ConferTurnRequest(text="hello", channel="html_text"))

    assert result.model_label == "anthropic/secondary"
    assert result.public_dict()["runtime_route"] == {
        "configured_primary": "xai/primary",
        "served_provider": "anthropic",
        "served_model": "anthropic/secondary",
        "fallback_position": 2,
        "fallback_reason": "runtime attempt failed",
    }


def test_gateway_receipt_overrides_static_runtime_metadata_with_serving_route():
    captured_receipt_ids = []

    def call_backend(prompt, context, tool_policy):
        captured_receipt_ids.append(context["confer"]["receipt_id"])
        return "gateway reply"

    def get_receipt(receipt_id):
        assert receipt_id == captured_receipt_ids[0]
        return {
            "logical_model": "minimoi-cos-agent",
            "served_provider": "anthropic",
            "served_model": "anthropic/claude-sonnet-4-6",
        }

    service = ConferTurnService(
        call_backend=call_backend,
        build_context=lambda: {},
        increment_chat=lambda: None,
        backend_metadata=lambda: ("COS Agent A (OpenClaw)", "gateway-unresolved"),
        get_routing_receipt=get_receipt,
    )

    result = service.handle(ConferTurnRequest(text="hello", channel="html_text"))

    assert result.turn_id == captured_receipt_ids[0]
    assert result.model_label == "anthropic/claude-sonnet-4-6"
    assert result.served_provider == "anthropic"
    assert result.configured_primary == "minimoi-cos-agent"


def test_empty_text_fails_before_backend_or_counter():
    calls = []
    increments = []
    service = _service(calls, increments)

    try:
        service.handle(ConferTurnRequest(text="   ", channel="html_text"))
    except InvalidConferTurn as error:
        assert str(error) == "Nothing to respond to."
    else:
        raise AssertionError("empty Confer turn was accepted")

    assert calls == []
    assert increments == []


def test_unknown_channel_fails_closed():
    service = _service([], [])

    try:
        service.handle(ConferTurnRequest(text="hello", channel="native-agent"))
    except InvalidConferTurn as error:
        assert "Unknown Confer channel" in str(error)
    else:
        raise AssertionError("unknown Confer channel was accepted")


@pytest.mark.parametrize(
    ("command", "expected_note"),
    [
        ("/note Buy café tickets", "Buy café tickets"),
        ("/NOTE\tKeep the tab form", "Keep the tab form"),
        ("Save a note: Review the COS diff", "Review the COS diff"),
        ("save   a note :   Keep   whitespace tidy", "Keep whitespace tidy"),
        ("Okay, save this note: The Bears won today.", "The Bears won today."),
        ("Please save this note: Natural spoken command", "Natural spoken command"),
        ("OK please save a note: Keep this too", "Keep this too"),
    ],
)
def test_explicit_note_is_platform_operation_and_bypasses_model(command, expected_note):
    calls = []
    increments = []
    note_writes = []
    request_id = str(uuid4())
    service = _service(calls, increments, note_writes)

    result = service.handle(ConferTurnRequest(
        text=command,
        channel="html_text",
        conversation_id="owner",
        request_id=request_id,
    ))

    assert calls == []
    assert increments == [True]
    assert note_writes == [(expected_note, request_id)]
    assert result.reply == "Note saved in COS platform memory."
    assert result.backend_label == "COS Platform"
    assert result.model_label == ""
    assert result.operation == {
        "type": "note_save",
        "status": "saved",
        "operation_id": request_id,
        "storage": "cos_platform_memory",
    }
    assert result.public_dict()["operation"] == result.operation


def test_correlated_retry_returns_deduplicated_receipt_without_model_call():
    request_id = str(uuid4())
    note_writes = []
    service = _service(
        [],
        [],
        note_writes,
        note_outcome={"saved": True, "deduplicated": True},
    )

    result = service.handle(ConferTurnRequest(
        text="/note one durable note",
        channel="api_text",
        request_id=request_id,
    ))

    assert note_writes == [("one durable note", request_id)]
    assert result.reply == "Note already saved in COS platform memory."
    assert result.operation["status"] == "deduplicated"


@pytest.mark.parametrize("channel", ["html_text", "html_voice"])
def test_html_channels_share_explicit_note_correlation_contract(channel):
    request_id = str(uuid4())
    note_writes = []
    service = _service([], [], note_writes)

    result = service.handle(ConferTurnRequest(
        text="Save a note: channel parity",
        channel=channel,
        conversation_id="owner",
        request_id=request_id,
    ))

    assert note_writes == [("channel parity", request_id)]
    assert result.channel == channel
    assert result.conversation_id == "owner"
    assert result.operation == {
        "type": "note_save",
        "status": "saved",
        "operation_id": request_id,
        "storage": "cos_platform_memory",
    }


@pytest.mark.parametrize(
    ("command", "message"),
    [
        ("/note", "must not be empty"),
        ("Save a note:   ", "must not be empty"),
        ("/note " + "x" * (MAX_EXPLICIT_NOTE_CHARS + 1), "characters or fewer"),
    ],
)
def test_invalid_explicit_note_never_calls_backend_or_writer(command, message):
    calls = []
    note_writes = []
    service = _service(calls, [], note_writes)

    with pytest.raises(InvalidConferTurn, match=message):
        service.handle(ConferTurnRequest(text=command, channel="html_text"))

    assert calls == []
    assert note_writes == []


def test_invalid_request_id_fails_before_write():
    note_writes = []
    service = _service([], [], note_writes)

    with pytest.raises(InvalidConferTurn, match="valid UUID"):
        service.handle(ConferTurnRequest(
            text="/note safe text",
            channel="html_text",
            request_id="not-a-uuid",
        ))

    assert note_writes == []


@pytest.mark.parametrize(
    "false_claim",
    [
        "Note saved through the platform-owned path.",
        "I've saved your note.",
        "Your note has been successfully recorded.",
        "Noted.\n\nPlatform note recorded: loss of voice conversation.",
        "Platform note recorded: loss of voice conversation.",
    ],
)
def test_backend_cannot_claim_unverified_note_success(false_claim):
    service = ConferTurnService(
        call_backend=lambda *_: false_claim,
        build_context=lambda: {},
        increment_chat=lambda: None,
        backend_metadata=lambda: ("COS Agent A", "test-model"),
    )

    result = service.handle(ConferTurnRequest(
        text="Please take a note.",
        channel="html_voice",
    ))

    assert result.operation is None
    assert result.reply.startswith("No note was saved")
    assert "Save a note:" in result.reply


def test_failed_platform_write_cannot_return_success_receipt():
    service = _service(
        [],
        [],
        [],
        note_outcome={"saved": False, "deduplicated": False},
    )

    with pytest.raises(ConferOperationFailed, match="was not saved"):
        service.handle(ConferTurnRequest(
            text="Save a note: this write must fail",
            channel="api_text",
        ))


def test_similar_non_command_remains_ordinary_chat():
    calls = []
    service = _service(calls, [])

    result = service.handle(ConferTurnRequest(
        text="/notebook is not a note command",
        channel="api_text",
    ))

    assert result.reply == "reply to /notebook is not a note command"
    assert len(calls) == 1
    assert result.operation is None


def test_new_command_resets_mapped_runtime_session_without_model_turn():
    calls = []
    increments = []
    resets = []
    service = _service(calls, increments, resets=resets)

    result = service.handle(ConferTurnRequest(
        text="/new",
        channel="html_text",
        conversation_id="phase3-thread",
    ))

    assert calls == []
    assert resets == ["phase3-thread"]
    assert increments == [True]
    assert result.reply == "New COS Agent A conversation started."
    assert result.model_label == ""
    assert result.operation == {
        "type": "session_reset",
        "status": "reset",
        "storage": "agent_runtime_session",
    }


def test_failed_session_reset_cannot_return_success_receipt():
    service = _service([], [], resets=[], reset_outcome=False)

    with pytest.raises(ConferOperationFailed, match="was not reset"):
        service.handle(ConferTurnRequest(text="/new", channel="api_text"))
