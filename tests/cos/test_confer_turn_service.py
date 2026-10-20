from datetime import datetime, timedelta, timezone
from uuid import uuid4
from core.agent_partner import PARTNER_STYLE

import pytest

from domains.cos import reminders
from domains.cos.claim_truth import NOTICE
from domains.cos.confer_service import (
    ALLOWED_CHANNELS,
    MAX_EXPLICIT_NOTE_CHARS,
    ConferOperationFailed,
    ConferTurnRequest,
    ConferTurnService,
    InvalidConferTurn,
    parse_explicit_note,
    parse_explicit_reminder,
)


def _service(
    calls,
    increments,
    note_writes=None,
    note_outcome=None,
    resets=None,
    reset_outcome=True,
    reminder_writes=None,
    reminder_outcome=None,
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

    def save_reminder(due, text, operation_id):
        if reminder_writes is None:
            raise AssertionError("unexpected reminder write")
        reminder_writes.append((due, text, operation_id))
        if isinstance(reminder_outcome, Exception):
            raise reminder_outcome
        return reminder_outcome or {
            "saved": True,
            "deduplicated": False,
            "reminder": {
                "id": "rem-0123456789ab",
                "due_utc": reminders.format_utc(due),
                "text": text,
            },
        }

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
        save_reminder=save_reminder,
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
            assert context["system_prompt"].startswith("system\n\n" + PARTNER_STYLE)
            assert "only a platform receipt confirms download" in context["system_prompt"]
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
    assert calls[1][1]["system_prompt"].startswith("system\n\n" + PARTNER_STYLE)
    assert "live spoken conversation" not in calls[1][1]["system_prompt"]


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
    ids=["empty-note", "blank-note", "too-long-note"],
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
    assert result.reply == NOTICE          # every sentence was an unsupported claim


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


# ── Natural note grammar (shared by COS, the MC portal and the voice gate) ────

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
def test_existing_note_forms_parse_unchanged(command, expected_note):
    assert parse_explicit_note(command) == expected_note


@pytest.mark.parametrize(
    ("command", "expected_note"),
    [
        ("Make a note that the gate code is 4411", "the gate code is 4411"),
        ("take a note: buy milk", "buy milk"),
        ("make me a note - dentist Tuesday", "dentist Tuesday"),
        ("save a note that X", "X"),
        ("Note to self: renew passport", "renew passport"),
        ("note to self, renew passport", "renew passport"),
        ("Okay, can you jot down a note — pick up the dry cleaning", "pick up the dry cleaning"),
        ("Would you add a note: the build\nis   green", "the build is green"),
    ],
)
def test_natural_note_forms_parse(command, expected_note):
    assert parse_explicit_note(command) == expected_note


@pytest.mark.parametrize(
    "text",
    [
        "noted",
        "Note: the build is green",
        "make a note of that",
        "can you make a note?",
        "I made a note that the build failed",
        "please make a note on the board: buy milk",
        "make a post-it: buy milk",
        "remind me to buy milk at 5pm",
        "what notes do I have?",
        "make a note-taking app for me",
        "note to selfish people: no",
    ],
)
def test_ordinary_chat_is_not_a_note_command(text):
    assert parse_explicit_note(text) is None


@pytest.mark.parametrize("command", ["make a note that", "Note to self:", "take a note:   "])
def test_natural_note_without_text_is_refused(command):
    with pytest.raises(InvalidConferTurn, match="must not be empty"):
        parse_explicit_note(command)


@pytest.mark.parametrize(
    ("command", "expected_note"),
    [
        ("Make a note that the gate code is 4411", "the gate code is 4411"),
        ("Note to self: renew passport", "renew passport"),
    ],
)
def test_natural_note_is_platform_operation_and_bypasses_model(command, expected_note):
    calls = []
    increments = []
    note_writes = []
    request_id = str(uuid4())
    service = _service(calls, increments, note_writes)

    result = service.handle(ConferTurnRequest(
        text=command,
        channel="html_voice",
        request_id=request_id,
    ))

    assert calls == []
    assert increments == [True]
    assert note_writes == [(expected_note, request_id)]
    assert result.reply == "Note saved in COS platform memory."
    assert result.backend_label == "COS Platform"
    assert result.operation == {
        "type": "note_save",
        "status": "saved",
        "operation_id": request_id,
        "storage": "cos_platform_memory",
    }


# ── Explicit reminders ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            "/remind 2026-10-19T14:00Z call the plumber",
            (datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc), "call the plumber"),
        ),
        (
            "/REMIND 2026-10-19T14:00:30Z   call\n  the   plumber",
            (datetime(2026, 10, 19, 14, 0, 30, tzinfo=timezone.utc), "call the plumber"),
        ),
        (
            "/remind\t2026-12-31T23:59Z New Year's Eve.",
            (datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc), "New Year's Eve."),
        ),
    ],
)
def test_explicit_reminder_parses_canonical_command(command, expected):
    assert parse_explicit_reminder(command) == expected


@pytest.mark.parametrize(
    "text",
    ["remind me to call the plumber", "/reminder 2026-10-19T14:00Z x", "/note x", "hello"],
)
def test_non_reminder_text_is_not_a_reminder_command(text):
    assert parse_explicit_reminder(text) is None


@pytest.mark.parametrize(
    ("command", "message"),
    [
        ("/remind", "UTC time"),
        ("/remind tomorrow call the plumber", "UTC time"),
        ("/remind 2026-10-19 14:00 call", "UTC time"),
        ("/remind 2026-10-19T14:00 call", "UTC time"),
        ("/remind 2026-10-19T14:00+01:00 call", "UTC time"),
        ("/remind 2026-13-40T25:00Z call", "UTC time"),
        ("/remind 2026-10-19T14:00Zcall", "UTC time"),
        ("/remind 2026-10-19T14:00Z", "must not be empty"),
        ("/remind 2026-10-19T14:00Z   ", "must not be empty"),
        ("/remind 2026-10-19T14:00Z " + "x" * 501, "500 characters or fewer"),
    ],
)
def test_malformed_reminder_never_calls_backend_or_writer(command, message):
    calls = []
    reminder_writes = []
    service = _service(calls, [], reminder_writes=reminder_writes)

    with pytest.raises(InvalidConferTurn, match=message):
        service.handle(ConferTurnRequest(text=command, channel="html_text"))

    assert calls == []
    assert reminder_writes == []


def test_explicit_reminder_is_platform_operation_with_durable_receipt():
    calls = []
    increments = []
    reminder_writes = []
    request_id = str(uuid4())
    service = _service(calls, increments, reminder_writes=reminder_writes)

    result = service.handle(ConferTurnRequest(
        text="/remind 2026-10-19T14:00Z call the plumber",
        channel="api_text",
        request_id=request_id,
    ))

    assert calls == []
    assert increments == [True]
    assert reminder_writes == [(
        datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc),
        "call the plumber",
        request_id,
    )]
    assert result.reply == (
        "Reminder saved for 2026-10-19T14:00:00Z: call the plumber. Delivery: Telegram."
    )
    assert result.backend_label == "COS Platform"
    assert result.model_label == ""
    assert result.operation == {
        "type": "reminder_set",
        "status": "saved",
        "operation_id": request_id,
        "reminder_id": "rem-0123456789ab",
        "due_utc": "2026-10-19T14:00:00Z",
        "storage": "cos_reminders",
        "delivery": "telegram",
    }
    assert result.public_dict()["operation"] == result.operation


def test_reminder_retry_returns_deduplicated_receipt():
    reminder_writes = []
    service = _service(
        [],
        [],
        reminder_writes=reminder_writes,
        reminder_outcome={
            "saved": True,
            "deduplicated": True,
            "reminder": {"id": "rem-aaaaaaaaaaaa", "due_utc": "2026-10-19T14:00:00Z", "text": "call the plumber"},
        },
    )

    result = service.handle(ConferTurnRequest(
        text="/remind 2026-10-19T14:00Z call the plumber",
        channel="html_text",
        request_id=str(uuid4()),
    ))

    assert result.reply == (
        "Reminder already saved for 2026-10-19T14:00:00Z: call the plumber. Delivery: Telegram."
    )
    assert result.operation["status"] == "deduplicated"
    assert result.operation["reminder_id"] == "rem-aaaaaaaaaaaa"


def test_reminder_without_platform_saver_is_unavailable():
    calls = []
    service = ConferTurnService(
        call_backend=lambda *args: calls.append(args) or "model reply",
        build_context=lambda: {},
        increment_chat=lambda: None,
        backend_metadata=lambda: ("COS Agent A", "test-model"),
    )

    with pytest.raises(ConferOperationFailed, match="COS reminders are unavailable."):
        service.handle(ConferTurnRequest(
            text="/remind 2026-10-19T14:00Z call the plumber",
            channel="html_text",
        ))
    assert calls == []


@pytest.mark.parametrize(
    ("code", "error", "message"),
    [
        ("reminder_due_past", InvalidConferTurn, "in the future and within a year"),
        ("reminder_due_too_far", InvalidConferTurn, "in the future and within a year"),
        ("reminder_limit_reached", InvalidConferTurn, "Too many reminders"),
        ("reminder_idempotency_conflict", ConferOperationFailed, "was not saved"),
        ("reminder_due_naive", ConferOperationFailed, "was not saved"),
    ],
)
def test_store_refusals_map_to_honest_errors(code, error, message):
    increments = []
    service = _service(
        [],
        increments,
        reminder_writes=[],
        reminder_outcome=reminders.ReminderRefused(code),
    )

    with pytest.raises(error, match=message):
        service.handle(ConferTurnRequest(
            text="/remind 2026-10-19T14:00Z call the plumber",
            channel="html_text",
        ))
    assert increments == []


@pytest.mark.parametrize(
    "outcome",
    [
        {"saved": False, "deduplicated": False},
        {"saved": True, "deduplicated": False},
        {"saved": True, "deduplicated": False, "reminder": {"id": "rem-0123456789ab"}},
    ],
    ids=["not-saved", "no-record", "no-due"],
)
def test_reminder_without_stored_record_cannot_return_success(outcome):
    service = _service([], [], reminder_writes=[], reminder_outcome=outcome)

    with pytest.raises(ConferOperationFailed, match="COS reminder was not saved."):
        service.handle(ConferTurnRequest(
            text="/remind 2026-10-19T14:00Z call the plumber",
            channel="api_text",
        ))


def test_invalid_request_id_fails_before_reminder_write():
    reminder_writes = []
    service = _service([], [], reminder_writes=reminder_writes)

    with pytest.raises(InvalidConferTurn, match="valid UUID"):
        service.handle(ConferTurnRequest(
            text="/remind 2026-10-19T14:00Z call the plumber",
            channel="html_text",
            request_id="not-a-uuid",
        ))
    assert reminder_writes == []


def test_reminder_round_trip_through_the_real_store(tmp_path):
    root = tmp_path / "reminders"
    service = ConferTurnService(
        call_backend=lambda *_: pytest.fail("model was called"),
        build_context=lambda: {},
        increment_chat=lambda: None,
        backend_metadata=lambda: ("COS Agent A", "test-model"),
        save_reminder=lambda due, text, operation_id: reminders.add(
            due, text, operation_id, root=root,
        ),
    )
    due = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%MZ")
    request = ConferTurnRequest(
        text=f"/remind {due} call the plumber",
        channel="api_text",
        request_id=str(uuid4()),
    )

    first = service.handle(request)
    retry = service.handle(request)
    past = ConferTurnRequest(text="/remind 2020-01-01T00:00Z too late", channel="api_text")

    assert first.operation["status"] == "saved"
    assert retry.operation["status"] == "deduplicated"
    assert retry.operation["reminder_id"] == first.operation["reminder_id"]
    with pytest.raises(InvalidConferTurn, match="in the future and within a year"):
        service.handle(past)
    stored = reminders._read(root)["reminders"]
    assert [record["id"] for record in stored] == [first.operation["reminder_id"]]
    assert stored[0]["state"] == "pending"


# ── No success claims without a platform receipt ──────────────────────────────

@pytest.mark.parametrize(
    "false_claim",
    [
        "Okay — noted that the deploy is Tuesday.",
        "Got it, noted.",
        "I've made a note of it.",
        "Made a note: dentist Tuesday.",
        "I'll make a note of that for later.",
        "I'll note it down.",
        "Done. I’ve saved that for you.",
        "Reminder set for 9am tomorrow.",
        "Okay, the reminder is set.",
        "I've set a reminder for Monday.",
        "I have set a reminder to call Anna.",
        "Sure thing, I'll remind you at five.",
        "I will remind you tomorrow morning.",
        "I'll ping you when it's time.",
        "Noted. I'll remind you at 5.",
    ],
)
def test_backend_cannot_claim_unverified_note_or_reminder(false_claim):
    service = ConferTurnService(
        call_backend=lambda *_: false_claim,
        build_context=lambda: {},
        increment_chat=lambda: None,
        backend_metadata=lambda: ("COS Agent A", "test-model"),
    )

    result = service.handle(ConferTurnRequest(
        text="Remind me to call Anna at five.",
        channel="html_text",
    ))

    assert result.operation is None
    assert result.reply.endswith(NOTICE) and false_claim not in result.reply
    assert "/note" in result.reply and "/remind" in result.reply


@pytest.mark.parametrize(
    "ordinary_reply",
    [
        "As noted in the log, the deploy finished at 09:12.",
        "Just a reminder that the build is green.",
        "You noted that the deploy failed yesterday.",
        "It should be noted that the cache is cold.",
        "Your reminder settings are unchanged.",
        "I'll notify the Guild when the review lands.",
    ],
)
def test_ordinary_replies_mentioning_notes_or_reminders_are_untouched(ordinary_reply):
    service = ConferTurnService(
        call_backend=lambda *_: ordinary_reply,
        build_context=lambda: {},
        increment_chat=lambda: None,
        backend_metadata=lambda: ("COS Agent A", "test-model"),
    )

    result = service.handle(ConferTurnRequest(text="status?", channel="html_text"))

    assert result.reply == ordinary_reply


# ── 19 Oct hotfix 2: drafting is not a note; receipt truth does not depend on the request ──

@pytest.mark.parametrize("drafting", [
    "Could you please write a note saying call Anna back",
    "please create a note saying pay the gas bill",
    "Write a note saying thank you to the team for the release",
    "Can you write a note that I can send to Anna about the delay?",
    "Create a note that summarizes today's meeting",
    "make a note that's easy to read for the board",
    "Note to self is a great app name",
])
def test_drafting_and_lookalikes_are_not_note_commands(drafting):
    assert parse_explicit_note(drafting) is None


def _reply_for(owner, reply):
    service = ConferTurnService(
        call_backend=lambda *_: reply,
        build_context=lambda: {},
        increment_chat=lambda: None,
        backend_metadata=lambda: ("COS Agent A", "test-model"),
    )
    return service.handle(ConferTurnRequest(text=owner, channel="html_text")).reply


@pytest.mark.parametrize("owner", [
    "Please nudge me tomorrow at 9 to call Anna",                   # wording no grammar knows
    "status?",                                                      # asked for nothing
    "What's the weather?\n\n[Retained voice notes: remind me at 5 to call Anna]",   # context-induced
    "[Draft]\n\nplease handle the plumber",                         # owner's own bracketed paragraph
])
@pytest.mark.parametrize("promise", ["I'll nudge you at 9.", "I'll remind you at 5.", "You'll get a reminder then."])
def test_a_later_action_promise_is_removed_whatever_the_owner_wrote(owner, promise):
    out = _reply_for(owner, f"It's sunny. {promise}")
    assert out == "It's sunny.\n\n" + NOTICE


@pytest.mark.parametrize("owner,reply", [
    ("Remind me what we decided about tomorrow", "We decided to ship on Monday; the review is tomorrow at 9."),
    ("What reminders do I have?", "You have no reminders set right now."),
    ("remind me what we decided", "As I've noted before, we keep the old plist."),
    ("Did the release ship?", "Yes, it shipped in PR 323. I'll note that the backup cron on EC2 still needs the new script copied by hand."),
    ("How does key rotation work?", "I'll remind you that the API key rotates monthly."),
    ("Status?", "We have noted three issues in the log."),
    ("Write a note to Anna about the delay", "I've written a short note for you: Dear Anna, the release moves to Friday."),
    ("make a note that's easy to read", "Here is a short version: ship Monday, review Tuesday."),
])
def test_history_negatives_and_remarks_are_never_changed(owner, reply):
    assert _reply_for(owner, reply) == reply


def test_only_the_claim_sentences_go_and_paragraphs_stay():
    reply = "Yes, it shipped in PR 323. I've set a reminder for 9 tomorrow.\n\n- The cron still needs the script."
    assert _reply_for("status?", reply) == (
        "Yes, it shipped in PR 323.\n\n- The cron still needs the script.\n\n" + NOTICE)
