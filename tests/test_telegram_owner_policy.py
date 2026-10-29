import asyncio
import importlib
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from core.telegram import owner_policy as policy


def message(sender=42, chat=42, kind="private", bot=False):
    return NS(from_user=NS(id=sender, is_bot=bot), chat=NS(id=chat, type=kind),
              chat_id=chat, text="!ops", reply_text=AsyncMock())


def update(**kwargs):
    return NS(message=message(**kwargs), callback_query=None)


@pytest.mark.parametrize("raw", [None, "", "bad", "-42", 0, -42, True, 42.0, [], "0", "9" * 21])
def test_missing_or_invalid_owner_refuses(raw):
    assert not policy.admits_update(update(), raw)


@pytest.mark.parametrize("kwargs", [{"sender": 43}, {"chat": 43}, {"kind": "group"},
                                      {"kind": "channel"}, {"bot": True}, {"sender": True},
                                      {"sender": "42"}, {"chat": "42"}])
def test_sender_and_private_chat_must_both_match(kwargs):
    assert not policy.admits_update(update(**kwargs), 42)


def test_owner_and_callback_sender_contract():
    assert policy.admits_update(update(), " 42 ")
    query = NS(from_user=NS(id=43, is_bot=False), message=message())
    obj = NS(message=None, callback_query=query)
    assert not policy.admits_update(obj, 42)
    query.from_user.id = 42
    assert policy.admits_update(obj, 42)
    query.message = None
    assert not policy.admits_update(obj, 42)
    assert not policy.admits_update(NS(message=None, callback_query=None), 42)


def test_raw_message_and_callback_require_the_clicking_sender():
    msg = {"chat": {"type": "private", "id": 42}, "from": {"id": 42}}
    assert policy.admits_message(msg, 42)
    query = {"message": msg, "from": {"id": 43}}
    assert not policy.admits_callback(query, 42)
    query["from"]["id"] = 42
    assert policy.admits_callback(query, 42)
    assert not policy.admits_callback({"message": None}, 42)
    assert not policy.admits_message([], 42)


@pytest.fixture
def bots(monkeypatch):
    import keyring
    monkeypatch.setattr(keyring, "get_password", lambda *args: None)
    command = importlib.import_module("core.telegram.telegram_bot")
    system = importlib.import_module("core.telegram.telegram_system_bot")
    feedback = importlib.import_module("core.telegram.telegram_feedback_bot")
    monkeypatch.setattr(command, "ROBERT_CHAT_ID", 42)
    monkeypatch.setattr(system, "get_chat_id", lambda: "42")
    monkeypatch.setattr(feedback, "get_chat_id", lambda: "42")
    return command, system, feedback


@pytest.mark.parametrize("index,name", [(0, "cmd_run"), (0, "cmd_status"), (0, "cmd_briefing"),
    (0, "button_callback"), (0, "handle_text_message"), (0, "handle_voice_polling"),
    (0, "handle_document"), (1, "cmd_start"), (1, "cmd_status"), (1, "handle_message"),
    (2, "button_callback")])
def test_each_public_polling_handler_refuses_before_touching_payload(bots, index, name):
    # No command-specific fields exist: unguarded handlers would either act or fail.
    obj = update(sender=43)
    obj.message.text = object()
    asyncio.run(getattr(bots[index], name)(obj, None))
    obj.message.reply_text.assert_not_called()


@pytest.mark.parametrize("name", ["handle_voice_message", "handle_webhook_callback",
                                 "handle_webhook_text_message", "handle_webhook_command"])
def test_direct_webhook_handlers_refuse_before_payload_access(bots, name):
    getattr(bots[0], name)({}, "unused-token")


def test_system_owner_status_still_checks_services(bots, monkeypatch):
    calls = []
    monkeypatch.setattr(bots[1], "_check_service", lambda name, url: calls.append(name) or f"{name}: up")
    monkeypatch.setattr(bots[1], "_identity", lambda: "synthetic")
    obj = update()
    asyncio.run(bots[1].cmd_status(obj, None))
    assert calls == ["curator", "german", "portal"]
    obj.message.reply_text.assert_awaited_once()


def test_owner_guard_preserves_handler_result():
    calls = []
    @policy.owner_only(lambda: 42)
    async def handler(obj, context):
        calls.append(context)
        return "accepted"
    assert asyncio.run(handler(update(), "context")) == "accepted"
    assert calls == ["context"]


@pytest.mark.parametrize("configured,expected", [("", 0), ("43", 0), ("42", 2)])
def test_actual_system_registration_applies_host_owner_to_voice_and_documents(bots, monkeypatch, configured, expected):
    command, system, _ = bots
    handlers = []
    app = NS(add_handler=handlers.append, run_polling=lambda **kwargs: None)
    builder = NS(token=lambda _: NS(build=lambda: app))
    monkeypatch.setattr(system, "Application", NS(builder=lambda: builder))
    monkeypatch.setattr(system, "_get_token", lambda: "synthetic")
    monkeypatch.setattr(system, "_identity", lambda: "synthetic")
    monkeypatch.setattr(system, "get_chat_id", lambda: configured)
    monkeypatch.setattr(command, "_load_drill_state", lambda: None)
    monkeypatch.setattr(command, "_load_drill_list_state", lambda: None)
    voice, document = AsyncMock(), AsyncMock()
    monkeypatch.setattr(command, "handle_voice_polling", voice)
    monkeypatch.setattr(command, "handle_document", document)
    system.main()
    assert len(handlers) == 5
    for handler in handlers[-2:]:
        asyncio.run(handler.callback(update(), None))
    assert voice.await_count + document.await_count == expected


def test_legacy_webhook_launcher_refuses_missing_secret_before_token_read(bots, monkeypatch):
    command = bots[0]
    monkeypatch.setattr(command, "get_webhook_secret", lambda: "")
    monkeypatch.setattr(command, "get_token", lambda: pytest.fail("must not read token"))
    assert command.run_webhook_mode() == 1


def test_legacy_webhook_launcher_uses_factory_and_expected_handlers(bots, monkeypatch):
    from core.telegram import webhook_app
    command = bots[0]
    monkeypatch.setattr(command, "get_webhook_secret", lambda: "fake-secret")
    monkeypatch.setattr(command, "get_token", lambda: "fake-token")
    captured, calls = {}, []
    def factory(**kwargs):
        captured.update(kwargs)
        return NS(run=lambda **opts: calls.append(opts))
    monkeypatch.setattr(webhook_app, "create_webhook_app", factory)
    monkeypatch.setattr(command, "handle_webhook_command", lambda m, t: calls.append((m, t)))
    command.run_webhook_mode()
    assert captured["resolve_owner"]() == 42
    assert captured["secret"] == "fake-secret"
    captured["command"]({"synthetic": True})
    assert calls == [{"host": "0.0.0.0", "port": 8444, "debug": False}, ({"synthetic": True}, "fake-token")]


@pytest.mark.parametrize("index", [0, 2])
@pytest.mark.parametrize("fail_edit", [False, True])
def test_real_polling_adapters_bind_identity_and_preserve_success(bots, monkeypatch, index, fail_edit):
    from core.telegram.feedback_state import FeedbackTracker
    module = bots[index]
    monkeypatch.setattr(module, "processed_callbacks", FeedbackTracker())
    calls = []
    def writer(*args, **kwargs):
        calls.append((args, kwargs))
        return {"success": True, "outcome": "confirmed", "message": "stored"}
    monkeypatch.setattr(module, "record_feedback", writer)
    msg = message(); msg.message_id = 8; msg.text = "Read article"
    msg.entities = (NS(type="text_link", url="https://example.invalid/article"),)
    msg.reply_markup = {"synthetic": True}
    q = NS(from_user=msg.from_user, message=msg, data="like:3", answer=AsyncMock(), edit_message_text=AsyncMock())
    if fail_edit:
        q.edit_message_text.side_effect = RuntimeError("synthetic edit failure")
    obj = NS(message=None, callback_query=q)
    for _ in range(2): asyncio.run(module.button_callback(obj, None))
    assert len(calls) == 1
    assert "https://example.invalid/article" in repr(calls)
    assert q.edit_message_text.call_args.kwargs["entities"] is msg.entities


def test_real_webhook_callback_deduplicates_even_after_edit_failure(bots, monkeypatch):
    from core.telegram.feedback_state import FeedbackTracker
    module = bots[0]; calls = []
    monkeypatch.setattr(module, "processed_callbacks", FeedbackTracker())
    monkeypatch.setattr(module, "record_feedback", lambda *a: calls.append(a) or
                        {"success": True, "outcome": "confirmed", "message": "stored"})
    monkeypatch.setattr(module, "answer_callback", lambda *a, **kw: None)
    monkeypatch.setattr(module, "edit_message", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError()))
    q = {"id": "fake", "from": {"id": 42}, "data": "like:3", "message": {
        "message_id": 1, "chat": {"id": 42, "type": "private"}, "text": "Read article",
        "entities": [{"type": "text_link", "url": "https://example.invalid/article"}]}}
    module.handle_webhook_callback(q, "fake"); module.handle_webhook_callback(q, "fake")
    assert calls == [("like", 3, {"link": "https://example.invalid/article"})]


def test_command_writer_uses_repository_root(bots, monkeypatch):
    module = bots[0]; calls = []
    monkeypatch.setattr(module, "write_feedback", lambda *a, **kw: calls.append((a, kw)))
    module.record_feedback("like", 3, {"link": "https://example.invalid/article"})
    assert calls[0][1]["root"] == module.PROJECT_ROOT
    assert (module.PROJECT_ROOT / "domains/curator/curator_feedback.py").is_file()
