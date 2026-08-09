"""
tests/test_telegram_send_failure_issue35.py — issue #35.

The curator cron reported "Briefing complete" on days the Telegram send
actually failed: send_message()/send_article() never checked the HTTP
response (Telegram returns 4xx/5xx on a bad token or unknown chat without
requests raising on its own), and run_send_mode() did a bare `return` when
the token/chat_id were missing, so the process always exited 0. These tests
mock the network call and pin the fixed behavior — a failed send must raise,
and a missing credential must exit non-zero — without depending on live
Telegram credentials.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.telegram import telegram_bot


def _response(status_code):
    resp = requests.Response()
    resp.status_code = status_code
    return resp


class TestSendMessageChecksResponse:
    def test_raises_on_telegram_error_response(self):
        bad_response = _response(400)
        with patch.object(telegram_bot.requests, "post", return_value=bad_response):
            with pytest.raises(requests.exceptions.HTTPError):
                telegram_bot.send_message("fake-token", "fake-chat", "hello")

    def test_returns_normally_on_success(self):
        ok_response = _response(200)
        with patch.object(telegram_bot.requests, "post", return_value=ok_response):
            telegram_bot.send_message("fake-token", "fake-chat", "hello")


class TestSendArticleChecksResponse:
    def test_raises_on_telegram_error_response(self):
        bad_response = _response(403)
        with patch.object(telegram_bot.requests, "post", return_value=bad_response):
            with pytest.raises(requests.exceptions.HTTPError):
                telegram_bot.send_article(
                    "fake-token", "fake-chat", 1, "Title", "https://x", "Source", "geo", "5.0"
                )


class TestRunSendModeExitsNonZeroWithoutCredentials:
    def test_missing_token_exits_nonzero(self):
        with patch("utils.telegram.get_system_token", return_value=None), \
             patch.object(telegram_bot, "get_chat_id", return_value="8379221702"):
            with pytest.raises(SystemExit) as exc_info:
                telegram_bot.run_send_mode()
            assert exc_info.value.code != 0

    def test_missing_chat_id_exits_nonzero(self):
        with patch("utils.telegram.get_system_token", return_value="fake-token"), \
             patch.object(telegram_bot, "get_chat_id", return_value=""):
            with pytest.raises(SystemExit) as exc_info:
                telegram_bot.run_send_mode()
            assert exc_info.value.code != 0
