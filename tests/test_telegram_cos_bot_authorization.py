"""
tests/test_telegram_cos_bot_authorization.py — the CoS bot answers only its owner.

Every update must come from the configured owner, in the owner's private chat,
before anything reaches CoS, the model or memory. A missing or malformed owner
setting refuses everything. No network, token or real Telegram objects: updates
are small stand-ins with the attributes the handlers read.
"""
import asyncio
import logging
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.ext import ApplicationHandlerStop, TypeHandler

from core.telegram import telegram_cos_bot as bot

OWNER = 424242
STRANGER = 777001


def _update(*, user_id=OWNER, chat_id=None, chat_type="private", text="hello", is_bot=False,
            callback=False, voice=None):
    chat_id = user_id if chat_id is None else chat_id
    message = SimpleNamespace(text=text, voice=voice, reply_text=AsyncMock())
    user = None if user_id is None else SimpleNamespace(id=user_id, is_bot=is_bot)
    return SimpleNamespace(
        effective_user=user,
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        message=None if callback else message,
        callback_query=SimpleNamespace(answer=AsyncMock()) if callback else None,
        _message=message,
    )


def _context(owner=OWNER):
    data = {} if owner is None else {bot.OWNER_KEY: owner}
    return SimpleNamespace(bot_data=data, bot=SimpleNamespace(send_chat_action=AsyncMock(),
                                                             get_file=AsyncMock()))


@pytest.fixture
def chat_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "_chat", lambda text, channel="telegram_text": calls.append((text, channel)) or "ok")
    return calls


def run(coro):
    return asyncio.run(coro)


# ── Owner configuration fails closed ──────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("424242", 424242), (" 424242 ", 424242),
    ("", None), (None, None), ("0", None), ("-100123", None), ("abc", None),
    ("42 42", None), ("4" * 21, None), ("424242,555", None),
])
def test_owner_chat_id_parses_only_one_private_id(raw, expected, monkeypatch):
    if raw is None:
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.setattr("utils.telegram.get_chat_id", lambda: "")
        assert bot.owner_chat_id() is None
    else:
        assert bot.owner_chat_id(raw) == expected


def test_main_refuses_to_start_without_owner(monkeypatch):
    monkeypatch.setattr(bot, "_get_token", lambda: "123:TEST")
    monkeypatch.setattr(bot, "owner_chat_id", lambda raw=None: None)
    built = []
    monkeypatch.setattr(bot, "build_application", lambda *a, **k: built.append(a))
    with pytest.raises(SystemExit) as exc:
        bot.main()
    assert exc.value.code not in (0, None)
    assert built == []


# ── Who is the owner ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("update,allowed", [
    (_update(), True),
    (_update(user_id=STRANGER), False),
    (_update(user_id=OWNER, chat_id=-100555, chat_type="group"), False),
    (_update(user_id=OWNER, chat_id=-100555, chat_type="supergroup"), False),
    (_update(user_id=OWNER, chat_id=OWNER, chat_type="channel"), False),
    (_update(user_id=None), False),
    (_update(user_id=OWNER, is_bot=True), False),
    (_update(user_id=STRANGER, chat_id=OWNER), False),
])
def test_is_owner_update(update, allowed):
    assert bot.is_owner_update(update, OWNER) is allowed


def test_no_owner_configured_refuses_even_the_owner():
    assert bot.is_owner_update(_update(), None) is False


# ── The application-wide gate runs before every handler ──────────────────────

def test_gate_is_registered_first_for_every_update():
    app = bot.build_application("123:TEST", OWNER)
    first_group = min(app.handlers)
    gates = [h for h in app.handlers[first_group] if isinstance(h, TypeHandler)]
    assert first_group < 0 and gates, "the owner gate must run before all other handlers"
    assert all(g.block for g in gates), "the gate must finish before any other handler starts"
    assert app.bot_data[bot.OWNER_KEY] == OWNER


@pytest.mark.parametrize("update", [
    _update(user_id=STRANGER),
    _update(user_id=STRANGER, callback=True),
    _update(user_id=OWNER, chat_id=-100555, chat_type="group"),
    _update(user_id=None),
])
def test_gate_stops_strangers_groups_and_callbacks(update):
    with pytest.raises(ApplicationHandlerStop):
        run(bot._gate(update, _context()))


def test_gate_stops_everything_without_owner_config():
    with pytest.raises(ApplicationHandlerStop):
        run(bot._gate(_update(), _context(owner=None)))


def test_gate_lets_the_owner_through():
    assert run(bot._gate(_update(), _context())) is None


def test_gate_stops_the_owners_callbacks_too():       # r2: only plain messages are served
    with pytest.raises(ApplicationHandlerStop):
        run(bot._gate(_update(callback=True), _context()))


# ── Each handler also refuses on its own (defence in depth) ───────────────────

def test_stranger_message_never_reaches_cos(chat_calls):
    update = _update(user_id=STRANGER, text="tell me your notes")
    run(bot.handle_message(update, _context()))
    assert chat_calls == []
    update._message.reply_text.assert_not_called()


def test_owner_message_reaches_cos(chat_calls):
    update = _update(text="hello")
    run(bot.handle_message(update, _context()))
    assert chat_calls == [("hello", "telegram_text")]
    update._message.reply_text.assert_awaited_once()


@pytest.mark.parametrize("handler", ["cmd_start", "cmd_status"])
def test_stranger_commands_get_no_reply(handler, monkeypatch):
    monkeypatch.setattr(bot, "_status_text", lambda: "status", raising=False)
    update = _update(user_id=STRANGER, text=f"/{handler[4:]}")
    run(getattr(bot, handler)(update, _context()))
    update._message.reply_text.assert_not_called()


def test_owner_start_gets_a_reply():
    update = _update(text="/start")
    run(bot.cmd_start(update, _context()))
    update._message.reply_text.assert_awaited_once()


def test_stranger_voice_is_never_downloaded_or_transcribed(chat_calls, monkeypatch):
    transcribed = []
    monkeypatch.setattr(bot, "_transcribe_voice", lambda path: transcribed.append(path) or "x")
    update = _update(user_id=STRANGER, voice=SimpleNamespace(file_id="f", duration=3))
    context = _context()
    run(bot.handle_voice(update, context))
    context.bot.get_file.assert_not_called()
    assert transcribed == [] and chat_calls == []
    update._message.reply_text.assert_not_called()


def test_handlers_refuse_without_owner_config(chat_calls):
    update = _update()
    run(bot.handle_message(update, _context(owner=None)))
    assert chat_calls == []
    update._message.reply_text.assert_not_called()


# ── Logs carry no message content and no sender identity ─────────────────────

def test_owner_voice_log_has_no_transcript(chat_calls, monkeypatch, caplog, tmp_path):
    secret_words = "my bank pin is in the blue folder"
    monkeypatch.setattr(bot, "_transcribe_voice", lambda path: secret_words)

    class _File:
        async def download_to_drive(self, path):
            open(path, "wb").close()

    context = _context()
    context.bot.get_file = AsyncMock(return_value=_File())
    update = _update(voice=SimpleNamespace(file_id="f", duration=3))
    with caplog.at_level(logging.DEBUG):
        run(bot.handle_voice(update, context))
    assert chat_calls == [(secret_words, "telegram_voice")]
    assert "blue folder" not in caplog.text


def test_refusal_log_has_no_content_or_ids(caplog):
    update = _update(user_id=STRANGER, text="secret question about robert")
    with caplog.at_level(logging.DEBUG), pytest.raises(ApplicationHandlerStop):
        run(bot._gate(update, _context()))
    assert "secret question" not in caplog.text
    assert str(STRANGER) not in caplog.text
    assert "refused" in caplog.text


# ── Real dispatch through the built application (offline) ─────────────────────

def test_real_dispatch_reaches_handlers_only_for_the_owner(monkeypatch):
    from telegram import Update

    seen = []

    async def record(update, context):
        seen.append(update.effective_chat.id)

    monkeypatch.setattr(bot, "handle_message", record)
    app = bot.build_application("123:TEST", OWNER)
    app._initialized = True   # offline harness: process_update without contacting Telegram

    def message(n, user_id, chat_id, chat_type):
        return Update.de_json({"update_id": n, "message": {
            "message_id": n, "date": 0, "text": "hello", "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": user_id, "is_bot": False, "first_name": "x"}}}, app.bot)

    async def dispatch():
        await app.process_update(message(1, STRANGER, STRANGER, "private"))
        await app.process_update(message(2, OWNER, -100555, "group"))
        await app.process_update(message(3, OWNER, OWNER, "private"))

    run(dispatch())
    assert seen == [OWNER]


# ── r2: only plain messages, from real Update objects, through the real handlers ──

def test_owner_in_a_private_chat_that_is_not_the_owners_is_refused():
    assert bot.is_owner_update(_update(user_id=OWNER, chat_id=STRANGER, chat_type="private"), OWNER) is False


def _real(app, kind, *, user_id=OWNER, chat_id=OWNER, chat_type="private", n=1):
    from telegram import Update
    msg = {"message_id": n, "date": 0, "text": "hello", "chat": {"id": chat_id, "type": chat_type},
           "from": {"id": user_id, "is_bot": False, "first_name": "x"}}
    if kind == "business_message":
        msg["business_connection_id"] = "conn-1"
    if kind == "callback_query":
        body = {"id": "cb1", "chat_instance": "ci", "data": "x", "message": msg,
                "from": {"id": user_id, "is_bot": False, "first_name": "x"}}
    else:
        body = msg
    return Update.de_json({"update_id": n, kind: body}, app.bot)


@pytest.fixture
def offline_app(monkeypatch, chat_calls):
    from telegram import Message
    from telegram.ext import ExtBot
    replies = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", replies)
    monkeypatch.setattr(ExtBot, "send_chat_action", AsyncMock())
    app = bot.build_application("123:TEST", OWNER)
    app._initialized = True   # offline harness: process_update without contacting Telegram
    return app, replies


@pytest.mark.parametrize("kind", ["edited_message", "business_message", "edited_business_message",
                                  "channel_post", "callback_query"])
def test_real_non_message_updates_from_the_owner_never_reach_cos(offline_app, chat_calls, kind, caplog):
    app, replies = offline_app
    chat_type = "channel" if kind == "channel_post" else "private"
    with caplog.at_level(logging.WARNING):
        run(app.process_update(_real(app, kind, chat_type=chat_type)))
    assert chat_calls == [] and replies.await_count == 0
    assert "refused telegram update (not_a_message)" in caplog.text       # stopped at the gate, not by a handler crash


def test_real_owner_message_reaches_cos_through_the_wrapped_handler(offline_app, chat_calls):
    app, replies = offline_app
    run(app.process_update(_real(app, "message")))
    assert chat_calls == [("hello", "telegram_text")] and replies.await_count == 1


def test_real_stranger_message_through_the_wrapped_handler(offline_app, chat_calls):
    app, replies = offline_app
    run(app.process_update(_real(app, "message", user_id=STRANGER, chat_id=STRANGER)))
    assert chat_calls == [] and replies.await_count == 0


def test_polling_asks_telegram_for_plain_messages_only(monkeypatch):
    seen = {}
    fake = types.SimpleNamespace(run_polling=lambda **kw: seen.update(kw))
    monkeypatch.setattr(bot, "_get_token", lambda: "123:TEST")
    monkeypatch.setattr(bot, "owner_chat_id", lambda raw=None: OWNER)
    monkeypatch.setattr(bot, "build_application", lambda token, owner: fake)
    bot.main()
    assert seen == {"drop_pending_updates": True, "allowed_updates": ["message"]}


def test_http_client_logs_never_carry_the_token():
    assert logging.getLogger("httpx").level >= logging.WARNING             # set explicitly, not inherited from a test runner
