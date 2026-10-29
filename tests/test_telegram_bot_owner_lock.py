"""
tests/test_telegram_bot_owner_lock.py — the system, command and feedback bots answer only Robert.

Every handler in the three bots runs only for the owner's chat. A stranger, a
group, or any update while the owner id is unset gets silence and no side
effect; a button tap is only answered, so the client stops spinning. No
network, token or real Telegram objects: updates are small stand-ins with the
attributes the handlers read, and the owner id is a test value patched into
telegram_bot.ROBERT_CHAT_ID (the one source all three bots read).
"""
import asyncio
import importlib
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.telegram import owner_lock

OWNER = 424242
STRANGER = 777001
GROUP = -100555
WHO = ["owner", "stranger", "group", "unset"]


def run(coro):
    return asyncio.run(coro)


# ── Fixtures ──────────────────────────────────────────────────────────────────
# Modules are resolved per test: another test file re-imports telegram_bot, and
# the system and feedback bots read the owner id from whichever copy is current.

@pytest.fixture
def cmd_bot(monkeypatch):
    mod = importlib.import_module("core.telegram.telegram_bot")
    monkeypatch.setattr(mod, "ROBERT_CHAT_ID", OWNER)
    return mod


@pytest.fixture
def system_bot(cmd_bot):
    return importlib.import_module("core.telegram.telegram_system_bot")


@pytest.fixture
def feedback_bot(cmd_bot):
    return importlib.import_module("core.telegram.telegram_feedback_bot")


def _update(*, chat_id=OWNER, user_id=OWNER, text="hello", voice=None, document=None, callback_data=None):
    chat = SimpleNamespace(id=chat_id, type="private" if chat_id > 0 else "group")
    user = SimpleNamespace(id=user_id, is_bot=False)
    message = SimpleNamespace(text=text, chat_id=chat_id, message_id=55, voice=voice, document=document,
                              reply_markup=None, reply_text=AsyncMock())
    if callback_data is None:
        return SimpleNamespace(effective_chat=chat, effective_user=user, message=message, callback_query=None)
    query = SimpleNamespace(data=callback_data, message=message, from_user=user,
                            answer=AsyncMock(), edit_message_text=AsyncMock())
    return SimpleNamespace(effective_chat=chat, effective_user=user, message=None, callback_query=query)


def _as(who, cmd_bot, monkeypatch, **kw):
    """An update from `who`. 'unset' is the owner's own chat while no owner id is configured."""
    if who == "unset":
        monkeypatch.setattr(cmd_bot, "ROBERT_CHAT_ID", None)
    chat = {"owner": OWNER, "stranger": STRANGER, "group": GROUP, "unset": OWNER}[who]
    user = STRANGER if who == "stranger" else OWNER
    return _update(chat_id=chat, user_id=user, **kw)


def _context():
    return SimpleNamespace(bot_data={}, bot=SimpleNamespace(get_file=AsyncMock(), send_chat_action=AsyncMock()))


def _assert_silent(update, context):
    """Nothing reached the sender: no reply, no bot call; a button tap only answered, empty."""
    message = update.message or update.callback_query.message
    assert message.reply_text.await_count == 0
    assert context.bot.get_file.await_count == 0
    assert context.bot.send_chat_action.await_count == 0
    if update.callback_query is not None:
        update.callback_query.answer.assert_awaited_once_with()
        assert update.callback_query.edit_message_text.await_count == 0


# ── The shared check ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    (OWNER, OWNER), (str(OWNER), OWNER), (f" {OWNER} ", OWNER),
    (None, None), ("", None), ("  ", None), (0, None), ("0", None), (-5, None), ("-100123", None),
    ("abc", None), ("42 42", None), ("4" * 21, None), (True, None), (4.2, None),
])
def test_valid_owner_id_accepts_only_one_positive_id(value, expected):
    assert owner_lock.valid_owner_id(value) == expected


@pytest.mark.parametrize("owner,chat,allowed", [
    (OWNER, OWNER, True),
    (OWNER, STRANGER, False),
    (OWNER, GROUP, False),
    (OWNER, None, False),
    (OWNER, str(OWNER), False),     # Telegram ids are ints; anything else is not the owner
    (None, OWNER, False),
    ("", OWNER, False),
    ("not-a-number", OWNER, False),
])
def test_allows_only_the_owners_chat(owner, chat, allowed):
    assert owner_lock.allows(owner, chat, OWNER, "test") is allowed


def test_refusal_logs_the_senders_numeric_id_only(caplog):
    @owner_lock.owner_only(lambda: OWNER)
    async def handler(update, context):
        raise AssertionError("must not run")

    update = _update(chat_id=STRANGER, user_id=STRANGER, text="my private words")
    with caplog.at_level(logging.WARNING, logger="core.telegram.owner_lock"):
        run(handler(update, _context()))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert str(STRANGER) in text and "handler" in text
    assert "my private words" not in text


def test_unset_owner_is_logged_as_not_configured(caplog):
    @owner_lock.owner_only(lambda: None)
    async def handler(update, context):
        raise AssertionError("must not run")

    with caplog.at_level(logging.WARNING, logger="core.telegram.owner_lock"):
        run(handler(_update(), _context()))
    assert "not configured" in caplog.text


def test_owner_id_that_cannot_be_read_refuses_everyone():
    def broken():
        raise ImportError("no telegram_bot")

    ran = []

    @owner_lock.owner_only(broken)
    async def handler(update, context):
        ran.append(update)

    run(handler(_update(), _context()))
    assert ran == []


def test_button_tap_without_a_chat_is_refused_and_answered():
    @owner_lock.owner_only(lambda: OWNER)
    async def handler(update, context):
        raise AssertionError("must not run")

    query = SimpleNamespace(answer=AsyncMock())
    update = SimpleNamespace(effective_chat=None, effective_user=SimpleNamespace(id=STRANGER),
                             message=None, callback_query=query)
    run(handler(update, _context()))
    query.answer.assert_awaited_once_with()


def test_a_failed_answer_to_a_stranger_does_not_raise():
    @owner_lock.owner_only(lambda: OWNER)
    async def handler(update, context):
        raise AssertionError("must not run")

    update = _update(chat_id=STRANGER, user_id=STRANGER, callback_data="like:3")
    update.callback_query.answer.side_effect = RuntimeError("network down")
    assert run(handler(update, _context())) is None


def test_owner_result_passes_through_unchanged():
    @owner_lock.owner_only(lambda: OWNER)
    async def handler(update, context):
        return "done"

    assert run(handler(_update(), _context())) == "done"
    assert handler.__name__ == "handler" and handler.owner_locked is True


# ── Every registered handler carries the lock ─────────────────────────────────

class _FakeApp:
    def __init__(self):
        self.handlers, self.bot_data = [], {}

    def add_handler(self, handler, group=0):
        self.handlers.append(handler)

    def add_error_handler(self, fn):
        pass

    def run_polling(self, *args, **kwargs):
        pass


class _FakeBuilder:
    def __init__(self, app):
        self.app = app

    def token(self, _):
        return self

    def request(self, _):
        return self

    def get_updates_request(self, _):
        return self

    def build(self):
        return self.app


def _fake_application(monkeypatch, module):
    app = _FakeApp()
    monkeypatch.setattr(module, "Application", SimpleNamespace(builder=lambda: _FakeBuilder(app)))
    return app


def _registered(app):
    assert app.handlers, "the bot registered no handlers"
    unlocked = [h.callback.__name__ for h in app.handlers if not getattr(h.callback, "owner_locked", False)]
    assert unlocked == [], f"handlers without the owner lock: {unlocked}"
    return {h.callback.__name__ for h in app.handlers}


@pytest.fixture
def no_drill_restore(cmd_bot, monkeypatch):
    monkeypatch.setattr(cmd_bot, "_load_drill_state", lambda: None)
    monkeypatch.setattr(cmd_bot, "_load_drill_list_state", lambda: None)


def test_system_bot_registers_only_locked_handlers(system_bot, no_drill_restore, monkeypatch):
    monkeypatch.setattr(system_bot, "_get_token", lambda: "123:TEST")
    app = _fake_application(monkeypatch, system_bot)
    system_bot.main()
    assert _registered(app) == {"cmd_start", "cmd_status", "handle_message",
                                "handle_voice_polling", "handle_document"}


def test_cmd_bot_registers_only_locked_handlers(cmd_bot, no_drill_restore, monkeypatch):
    monkeypatch.setattr(cmd_bot, "get_polling_token", lambda: "123:TEST")
    monkeypatch.setattr(cmd_bot, "requests", SimpleNamespace(
        get=lambda *a, **k: SimpleNamespace(status_code=200), RequestException=Exception))
    app = _fake_application(monkeypatch, cmd_bot)
    cmd_bot.run_bot_mode()
    assert _registered(app) == {"button_callback", "cmd_run", "cmd_status", "cmd_briefing",
                                "handle_text_message", "handle_voice_polling", "handle_document"}


def test_feedback_bot_registers_only_locked_handlers(feedback_bot, monkeypatch):
    monkeypatch.setattr(feedback_bot, "get_telegram_token", lambda: "123:TEST")
    app = _fake_application(monkeypatch, feedback_bot)
    feedback_bot.main()
    assert _registered(app) == {"button_callback"}


# ── System bot (minimoi_system_bot, EC2) ──────────────────────────────────────

@pytest.fixture
def service_checks(system_bot, monkeypatch):
    calls = []
    monkeypatch.setattr(system_bot, "_check_service", lambda name, url: calls.append(name) or f"{name}: up")
    monkeypatch.setattr(system_bot, "_identity", lambda: "test-host")
    return calls


@pytest.mark.parametrize("who", WHO)
def test_system_start_answers_only_the_owner(who, system_bot, cmd_bot, service_checks, monkeypatch):
    update, context = _as(who, cmd_bot, monkeypatch), _context()
    run(system_bot.cmd_start(update, context))
    if who == "owner":
        assert "test-host" in update.message.reply_text.await_args.args[0]
    else:
        _assert_silent(update, context)


@pytest.mark.parametrize("who", WHO)
def test_system_status_checks_services_only_for_the_owner(who, system_bot, cmd_bot, service_checks, monkeypatch):
    update, context = _as(who, cmd_bot, monkeypatch), _context()
    run(system_bot.cmd_status(update, context))
    if who == "owner":
        assert service_checks == ["curator", "german", "portal"]
        update.message.reply_text.assert_awaited_once()
    else:
        assert service_checks == []
        _assert_silent(update, context)


@pytest.mark.parametrize("who", WHO)
def test_system_ops_checks_services_only_for_the_owner(who, system_bot, cmd_bot, service_checks, monkeypatch):
    update, context = _as(who, cmd_bot, monkeypatch, text="!ops"), _context()
    run(system_bot.handle_message(update, context))
    if who == "owner":
        assert service_checks == ["curator", "german", "portal"]
        update.message.reply_text.assert_awaited_once()
    else:
        assert service_checks == []
        _assert_silent(update, context)


@pytest.mark.parametrize("who", WHO)
def test_system_text_reaches_german_routing_only_for_the_owner(who, system_bot, cmd_bot, monkeypatch):
    routed = AsyncMock()
    monkeypatch.setattr(cmd_bot, "handle_text_message", routed)
    update, context = _as(who, cmd_bot, monkeypatch, text="drill sein"), _context()
    run(system_bot.handle_message(update, context))
    if who == "owner":
        routed.assert_awaited_once_with(update, context)
    else:
        assert routed.await_count == 0
        _assert_silent(update, context)


def test_system_bot_logs_a_strangers_id(system_bot, cmd_bot, service_checks, caplog):
    update = _update(chat_id=STRANGER, user_id=STRANGER, text="!ops")
    with caplog.at_level(logging.WARNING, logger="core.telegram.owner_lock"):
        run(system_bot.handle_message(update, _context()))
    assert f"refused non-owner sender {STRANGER}" in caplog.text


# ── Command bot (telegram_bot.py, minimoi_cmd_bot) ────────────────────────────

@pytest.mark.parametrize("who", WHO)
def test_cmd_run_starts_a_curator_run_only_for_the_owner(who, cmd_bot, monkeypatch):
    runs = []

    async def fake_arun(cmd, timeout=120, **kwargs):
        runs.append(cmd)
        return "", "test stop", 1          # a failed run: no briefing is sent

    monkeypatch.setattr(cmd_bot, "_arun", fake_arun)
    update, context = _as(who, cmd_bot, monkeypatch), _context()
    run(cmd_bot.cmd_run(update, context))
    if who == "owner":
        assert runs == [[str(cmd_bot.CURATOR_CRON_SCRIPT)]]
        assert update.message.reply_text.await_count == 2
    else:
        assert runs == []
        _assert_silent(update, context)


@pytest.mark.parametrize("who", WHO)
def test_cmd_status_replies_only_to_the_owner(who, cmd_bot, monkeypatch):
    update, context = _as(who, cmd_bot, monkeypatch), _context()
    run(cmd_bot.cmd_status(update, context))
    if who == "owner":
        update.message.reply_text.assert_awaited_once()
    else:
        _assert_silent(update, context)


@pytest.mark.parametrize("who", WHO)
def test_cmd_briefing_sends_only_for_the_owner(who, cmd_bot, monkeypatch):
    sent = []
    monkeypatch.setattr(cmd_bot, "get_polling_token", lambda: "123:TEST")
    monkeypatch.setattr(cmd_bot, "get_chat_id", lambda: str(OWNER))
    monkeypatch.setattr(cmd_bot, "send_briefing", lambda token, chat_id: sent.append((token, chat_id)))
    update, context = _as(who, cmd_bot, monkeypatch), _context()
    run(cmd_bot.cmd_briefing(update, context))
    if who == "owner":
        assert sent == [("123:TEST", str(OWNER))]
    else:
        assert sent == []
        _assert_silent(update, context)


@pytest.fixture
def recorded_feedback(monkeypatch):
    def install(module):
        calls = []
        monkeypatch.setattr(module, "processed_callbacks", set())
        monkeypatch.setattr(module, "record_feedback", lambda action, rank, *article:
                            calls.append((action, rank)) or {"success": True, "message": "ok"})
        return calls
    return install


@pytest.mark.parametrize("who", WHO)
def test_cmd_button_records_feedback_only_for_the_owner(who, cmd_bot, recorded_feedback, monkeypatch):
    calls = recorded_feedback(cmd_bot)
    update, context = _as(who, cmd_bot, monkeypatch, callback_data="like:3"), _context()
    run(cmd_bot.button_callback(update, context))
    if who == "owner":
        assert calls == [("like", "3")]
        update.callback_query.answer.assert_awaited_once_with("⏳ Recording like...")
        update.callback_query.edit_message_text.assert_awaited_once()
    else:
        assert calls == [] and cmd_bot.processed_callbacks == set()
        _assert_silent(update, context)


@pytest.mark.parametrize("who", WHO)
def test_cmd_text_routes_only_for_the_owner(who, cmd_bot, monkeypatch):
    phrase = AsyncMock()
    monkeypatch.setattr(cmd_bot, "_handle_phrase_command", phrase)
    update, context = _as(who, cmd_bot, monkeypatch, text="!phrase list"), _context()
    run(cmd_bot.handle_text_message(update, context))
    if who == "owner":
        phrase.assert_awaited_once_with(update, "!phrase list")
    else:
        assert phrase.await_count == 0
        _assert_silent(update, context)


@pytest.mark.parametrize("who", WHO)
def test_cmd_voice_is_handled_only_for_the_owner(who, cmd_bot, monkeypatch):
    voice = SimpleNamespace(duration=300, file_id="v1")       # too long: the owner gets the limit reply
    update, context = _as(who, cmd_bot, monkeypatch, voice=voice), _context()
    run(cmd_bot.handle_voice_polling(update, context))
    if who == "owner":
        assert "too long" in update.message.reply_text.await_args.args[0]
    else:
        _assert_silent(update, context)


@pytest.mark.parametrize("who", WHO)
def test_cmd_document_is_handled_only_for_the_owner(who, cmd_bot, monkeypatch):
    doc = SimpleNamespace(file_size=10 ** 6, file_id="d1")    # too large: the owner gets the limit reply
    update, context = _as(who, cmd_bot, monkeypatch, document=doc), _context()
    run(cmd_bot.handle_document(update, context))
    if who == "owner":
        assert "too large" in update.message.reply_text.await_args.args[0]
    else:
        _assert_silent(update, context)


# ── Command bot, webhook mode ─────────────────────────────────────────────────

class _FakeThread:
    started = []

    def __init__(self, target, args=(), daemon=None):
        self.target = target

    def start(self):
        _FakeThread.started.append(self.target.__name__)


@pytest.fixture
def webhook(cmd_bot, monkeypatch):
    import flask
    apps, calls = [], []
    monkeypatch.setattr(flask.Flask, "run", lambda self, *a, **k: apps.append(self))
    monkeypatch.setattr(cmd_bot, "get_token", lambda: "123:TEST")
    monkeypatch.setattr(cmd_bot, "get_webhook_secret", lambda: None)
    monkeypatch.setattr(cmd_bot, "threading", SimpleNamespace(Thread=_FakeThread))
    _FakeThread.started = []
    for name in ("handle_webhook_callback", "handle_webhook_command", "handle_webhook_text_message"):
        monkeypatch.setattr(cmd_bot, name, lambda *a, _n=name: calls.append(_n))

    def handle_voice_message(*args):
        raise AssertionError("the fake thread never runs its target")

    monkeypatch.setattr(cmd_bot, "handle_voice_message", handle_voice_message)
    monkeypatch.setattr(cmd_bot, "answer_callback", lambda token, qid, text, alert=False: calls.append(("answer", qid, text)))
    cmd_bot.run_webhook_mode()
    client = apps[0].test_client()

    def post(update):
        response = client.post("/webhook", json=update)
        assert response.status_code == 200 and response.get_json() == {"ok": True}
        return calls + _FakeThread.started
    return post


def _wh_message(chat_id, user_id, **fields):
    return {"update_id": 1, "message": {"message_id": 9, "chat": {"id": chat_id, "type": "private"},
                                        "from": {"id": user_id, "is_bot": False}, **fields}}


def _wh_callback(chat_id, user_id):
    return {"update_id": 2, "callback_query": {"id": "q1", "data": "like:3", "from": {"id": user_id},
                                               "message": {"message_id": 9, "text": "x",
                                                           "chat": {"id": chat_id, "type": "private"}}}}


@pytest.mark.parametrize("update,expected", [
    (_wh_message(OWNER, OWNER, text="/run"), ["handle_webhook_command"]),
    (_wh_message(OWNER, OWNER, text="hello"), ["handle_webhook_text_message"]),
    (_wh_message(OWNER, OWNER, voice={"file_id": "v1"}), ["handle_voice_message"]),
    (_wh_callback(OWNER, OWNER), ["handle_webhook_callback"]),
    (_wh_message(STRANGER, STRANGER, text="/run"), []),
    (_wh_message(STRANGER, STRANGER, text="hello"), []),
    (_wh_message(STRANGER, STRANGER, voice={"file_id": "v1"}), []),
    (_wh_message(GROUP, OWNER, text="/briefing"), []),
    (_wh_callback(STRANGER, STRANGER), [("answer", "q1", "")]),
    ({"update_id": 3, "edited_message": {"chat": {"id": STRANGER}}}, []),
])
def test_webhook_reaches_handlers_only_for_the_owner(update, expected, webhook):
    assert webhook(update) == expected


def test_webhook_refuses_everyone_when_owner_unset(webhook, cmd_bot, monkeypatch):
    monkeypatch.setattr(cmd_bot, "ROBERT_CHAT_ID", None)
    assert webhook(_wh_message(OWNER, OWNER, text="/run")) == []


# ── Feedback bot (telegram_feedback_bot.py) ───────────────────────────────────

@pytest.mark.parametrize("who", WHO)
def test_feedback_button_records_only_for_the_owner(who, feedback_bot, cmd_bot, recorded_feedback, monkeypatch):
    calls = recorded_feedback(feedback_bot)
    update, context = _as(who, cmd_bot, monkeypatch, callback_data="like:3"), _context()
    run(feedback_bot.button_callback(update, context))
    if who == "owner":
        assert calls == [("like", "3")]
        update.callback_query.answer.assert_awaited_once_with("⏳ Recording like...")
        update.callback_query.edit_message_text.assert_awaited_once()
    else:
        assert calls == [] and feedback_bot.processed_callbacks == set()
        _assert_silent(update, context)
