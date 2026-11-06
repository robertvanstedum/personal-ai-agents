"""Issue #35, PR #183's intent on the current code: a failed Telegram send surfaces.

Telegram answers a bad token, unknown chat or blocked bot with a 4xx and requests doesn't raise on its own, so an
unchecked send read as delivered. The scheduled path already has the strict ledger delivery (deliver_briefing); these
cover send_message and send_article, what their callers do with a refusal, and the exit code of the scheduled path.
The network call is mocked; nothing here needs Telegram credentials.
"""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest
import requests

from core.telegram import telegram_bot as tb

TOKEN = "123:secret-token"


def _response(status_code):
    resp = requests.Response()
    resp.status_code = status_code
    resp.url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    return resp


def _posts(*codes):
    return patch.object(tb.requests, "post", side_effect=[_response(c) for c in codes])


def _article():
    return (TOKEN, "chat", 1, "Title", "https://x", "Source", "geo", "5.0")


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_send_message_raises_when_telegram_refuses(status):
    with _posts(status), pytest.raises(tb.TelegramSendFailed) as exc:
        tb.send_message(TOKEN, "chat", "hello")
    assert exc.value.status == status


def test_send_article_raises_when_telegram_refuses():
    with _posts(403), pytest.raises(tb.TelegramSendFailed):
        tb.send_article(*_article())


def test_a_refusal_is_still_a_requests_error_for_callers_that_catch_those():
    with _posts(400), pytest.raises(requests.exceptions.RequestException):
        tb.send_message(TOKEN, "chat", "hello")


def test_the_failure_never_carries_the_bot_token():
    with _posts(401), pytest.raises(tb.TelegramSendFailed) as exc:
        tb.send_message(TOKEN, "chat", "hello")
    assert TOKEN not in str(exc.value) and exc.value.__cause__ is None and exc.value.__suppress_context__


def test_success_returns_normally_and_sends_once():
    with _posts(200) as post:
        tb.send_message(TOKEN, "chat", "hello")
    with _posts(200) as article:
        tb.send_article(*_article())
    assert post.call_count == 1 and article.call_count == 1


def test_a_refusal_is_not_retried_but_a_timeout_still_is():
    with _posts(400) as post, pytest.raises(tb.TelegramSendFailed):
        tb.send_message(TOKEN, "chat", "hello")
    assert post.call_count == 1
    with patch.object(tb.requests, "post", side_effect=[requests.exceptions.Timeout(), _response(200)]) as post:
        tb.send_message(TOKEN, "chat", "hello")
    assert post.call_count == 2


def test_a_briefing_that_is_refused_does_not_report_sent(tmp_path, monkeypatch, capsys):
    briefing = tmp_path / "curator_output.txt"
    briefing.write_text("x")
    monkeypatch.setenv("CURATOR_OUTPUT_PATH", str(briefing))
    monkeypatch.setattr(tb, "parse_curator_output", lambda path: [
        {"num": 1, "title": "T", "url": "u", "source": "s", "category": "c", "score": 1}])
    with _posts(200, 403), pytest.raises(tb.TelegramSendFailed):
        tb.send_briefing(TOKEN, "chat")
    assert "Sent" not in capsys.readouterr().out


@pytest.mark.parametrize("handler", ["cmd_briefing", "cmd_run"])
@pytest.mark.parametrize("failure", [tb.TelegramSendFailed(403), requests.exceptions.ConnectionError(f"/bot{TOKEN}/")])
def test_an_on_demand_briefing_that_fails_says_so_in_the_chat(monkeypatch, capsys, handler, failure):
    monkeypatch.setattr(tb, "ROBERT_CHAT_ID", 42)
    monkeypatch.setattr(tb, "get_polling_token", lambda: TOKEN)
    monkeypatch.setattr(tb, "get_chat_id", lambda: "42")
    monkeypatch.setattr(tb, "_arun", AsyncMock(return_value=("", "", 0)))
    monkeypatch.setattr(tb, "send_briefing", lambda token, chat: (_ for _ in ()).throw(failure))
    message = NS(from_user=NS(id=42, is_bot=False), chat=NS(id=42, type="private"), chat_id=42, reply_text=AsyncMock())
    asyncio.run(getattr(tb, handler)(NS(message=message, callback_query=None), None))
    said = message.reply_text.await_args.args[0]
    assert "not delivered" in said and TOKEN not in said
    out = capsys.readouterr().out
    assert "Briefing not delivered" in out and TOKEN not in out


def test_the_log_line_never_quotes_a_connection_error_url(monkeypatch, capsys):
    monkeypatch.setattr(tb, "send_briefing", lambda token, chat: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError(f"url: /bot{TOKEN}/sendMessage")))
    update = NS(message=NS(reply_text=AsyncMock()))
    asyncio.run(tb._send_briefing_and_say(update, TOKEN, "chat"))
    assert TOKEN not in capsys.readouterr().out


def test_a_voice_note_is_kept_when_telegram_refuses_the_echo(monkeypatch):
    monkeypatch.setattr(tb, "ROBERT_CHAT_ID", 42)
    monkeypatch.setattr(tb, "get_telegram_file_bytes", lambda *a: b"audio")
    monkeypatch.setattr(tb, "transcribe_voice", lambda audio: "a < b")
    monkeypatch.setattr(tb, "classify_voice", lambda text: (None, None))
    kept = []
    monkeypatch.setattr(tb, "log_voice_note", kept.append)
    message = {"chat": {"id": 42, "type": "private"}, "from": {"id": 42, "is_bot": False},
               "voice": {"file_id": "f", "duration": 1}}
    with _posts(400, 400, 200):
        tb.handle_voice_message(message, TOKEN)
    assert kept == ["a < b"]


def test_the_webhook_answers_ok_when_a_reply_is_refused_so_a_run_is_not_redelivered(monkeypatch):
    from core.telegram import webhook_app
    monkeypatch.setattr(tb, "ROBERT_CHAT_ID", 42)
    monkeypatch.setattr(tb, "get_webhook_secret", lambda: "s3cret")
    monkeypatch.setattr(tb, "get_token", lambda: TOKEN)
    captured = {}
    real = webhook_app.create_webhook_app

    def factory(**kwargs):
        captured["app"] = real(**kwargs)
        return NS(run=lambda **opts: None)
    monkeypatch.setattr(webhook_app, "create_webhook_app", factory)
    tb.run_webhook_mode()
    runs = []
    monkeypatch.setattr(tb.subprocess, "run", lambda *a, **k: runs.append(a) or NS(returncode=0, stderr=b""))
    update = {"message": {"text": "/run", "chat": {"id": 42, "type": "private"}, "from": {"id": 42, "is_bot": False}}}
    with _posts(200, 403):                                           # "running" goes out; "complete" is refused
        r = captured["app"].test_client().post("/webhook", json=update,
                                               headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"})
    assert r.status_code == 200 and len(runs) == 1


def test_the_scheduled_send_exits_non_zero_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(tb, "DELIVERY_DIR", tmp_path / "telegram_delivery")
    monkeypatch.setattr(tb, "get_chat_id", lambda: "42")
    with patch("utils.telegram.get_system_token", return_value=""), pytest.raises(SystemExit) as exc:
        tb.run_send_mode()
    assert exc.value.code == 1
    monkeypatch.setattr(tb, "get_chat_id", lambda: "")
    with patch("utils.telegram.get_system_token", return_value="fake"), pytest.raises(SystemExit) as exc:
        tb.run_send_mode()
    assert exc.value.code == 1
