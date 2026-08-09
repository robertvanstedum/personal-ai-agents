from domains.cos.confer_service import (
    ALLOWED_CHANNELS,
    ConferTurnRequest,
    ConferTurnService,
    InvalidConferTurn,
)


def _service(calls, increments):
    def call_backend(prompt, context, tool_policy):
        calls.append((prompt, context, tool_policy))
        return f"reply to {prompt}"

    return ConferTurnService(
        call_backend=call_backend,
        build_context=lambda: {
            "recent_memory": "remembered",
            "system_prompt": "system",
        },
        increment_chat=lambda: increments.append(True),
        backend_metadata=lambda: ("Grok (direct API)", "grok-test"),
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

    assert len(calls) == len(ALLOWED_CHANNELS)
    assert len(increments) == len(ALLOWED_CHANNELS)
    for _, context, tool_policy in calls:
        assert context["recent_memory"] == "remembered"
        assert context["system_prompt"] == "system"
        assert context["confer"]["conversation_id"] == "owner"
        assert context["confer"]["input_channel"] in ALLOWED_CHANNELS
        assert tool_policy == {"observation": True, "mutation": False}


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
