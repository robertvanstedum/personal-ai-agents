"""Offline contracts for the actual Telegram article sender.

Load only its two sending functions so collection never imports a runtime,
reads credentials or sends a message. Full bot integration is covered by the
separate delivery-ledger tests. Live acceptance is an explicit operator action.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests


@pytest.fixture
def sender():
    source = Path(__file__).resolve().parents[1] / "core/telegram/telegram_bot.py"
    tree = ast.parse(source.read_text())
    names = {"_article_payload", "send_article"}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == names
    calls = []
    replies = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        reply = replies.pop(0) if replies else None
        if isinstance(reply, Exception):
            raise reply
        return reply

    namespace = {"requests": SimpleNamespace(post=post, exceptions=requests.exceptions)}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
    return SimpleNamespace(send=namespace["send_article"], calls=calls, replies=replies)


def test_send_feedback_buttons_uses_fake_transport(sender, monkeypatch):
    # Ambient settings never select the destination or credentials in this test.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "unused-ambient-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "unused-ambient-chat")
    sender.send("synthetic-token", "synthetic-chat", 3, "Überblick\n第二行",
                "https://example.invalid/article", "Synthetic", "fiscal", "5.7/10")
    assert sender.calls == [(
        "https://api.telegram.org/botsynthetic-token/sendMessage",
        {"json": {
            "chat_id": "synthetic-chat",
            "text": "<b>#3</b> • FISCAL • Synthetic\n\n<b>Überblick\n第二行</b>\n\n"
                    "<a href='https://example.invalid/article'>🔗 Read article</a>\n\nScore: 5.7/10",
            "parse_mode": "HTML", "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": [[
                {"text": "👍 Like", "callback_data": "like:3"},
                {"text": "👎 Dislike", "callback_data": "dislike:3"},
                {"text": "🔖 Save", "callback_data": "save:3"},
            ]]},
        }, "timeout": 30},
    )]


def test_sender_retries_timeouts_with_the_same_payload(sender):
    sender.replies[:] = [requests.exceptions.Timeout(), None]
    sender.send("fake", "fake", 1, "Title", "https://example.invalid", "Source", "other", "?")
    assert len(sender.calls) == 2
    assert sender.calls[0] == sender.calls[1]


def test_sender_surfaces_exhausted_timeouts(sender):
    sender.replies[:] = [requests.exceptions.Timeout() for _ in range(3)]
    with pytest.raises(requests.exceptions.Timeout):
        sender.send("fake", "fake", 1, "Title", "https://example.invalid", "Source", "other", "?")
    assert len(sender.calls) == 3
