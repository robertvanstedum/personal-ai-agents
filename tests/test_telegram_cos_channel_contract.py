"""Telegram owner gating and public CoS operation wiring, with synthetic updates."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.telegram import telegram_cos_bot as bot
from domains.cos import channel_api


def update(user=42, text="hello"):
    return SimpleNamespace(effective_user=SimpleNamespace(id=user, is_bot=False),
        effective_chat=SimpleNamespace(id=user, type="private"),
        message=SimpleNamespace(text=text, reply_text=AsyncMock()))


def context():
    return SimpleNamespace(bot_data={bot.OWNER_KEY: 42},
                           bot=SimpleNamespace(send_chat_action=AsyncMock()))


@pytest.mark.parametrize("channel", ["telegram_text", "telegram_voice"])
def test_bot_chat_uses_public_api(monkeypatch, channel):
    chat = Mock(return_value="unchanged reply")
    monkeypatch.setattr(channel_api, "chat", chat)
    assert bot._chat("Grüße", channel=channel) == "unchanged reply"
    chat.assert_called_once_with("Grüße", channel=channel)


def test_owner_status_keeps_exact_output(monkeypatch):
    count = Mock(return_value=17)
    monkeypatch.setattr(channel_api, "memory_character_count", count)
    monkeypatch.setattr(bot, "_identity", lambda: "Synthetic host")
    message = update(text="/status")
    asyncio.run(bot.cmd_status(message, context()))
    count.assert_called_once_with()
    message.message.reply_text.assert_awaited_once_with(
        "<b>CoS status</b> — Synthetic host\nMemory: 17 chars", parse_mode="HTML")


def test_stranger_status_never_reads_domain_memory(monkeypatch):
    count = Mock(side_effect=AssertionError("unauthorized memory read"))
    monkeypatch.setattr(channel_api, "memory_character_count", count)
    message = update(user=99, text="/status")
    asyncio.run(bot.cmd_status(message, context()))
    count.assert_not_called()
    message.message.reply_text.assert_not_called()


def test_owner_text_dispatch_and_reply_are_unchanged(monkeypatch):
    chat = Mock(return_value="saved")
    monkeypatch.setattr(channel_api, "chat", chat)
    message = update(text=" /note@synthetic_bot remember this ")
    asyncio.run(bot.handle_message(message, context()))
    chat.assert_called_once_with("/note remember this", channel="telegram_text")
    message.message.reply_text.assert_awaited_once_with("saved")


def test_transport_no_longer_imports_private_cos_implementation():
    tree = ast.parse(Path(bot.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "domains.cos.chief_of_staff"
