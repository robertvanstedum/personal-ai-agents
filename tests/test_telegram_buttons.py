"""
Sends a real Telegram message with feedback buttons (Like/Dislike/Save) to
verify inline-keyboard callback wiring end to end. This has a live side
effect — it actually posts to Telegram — so it only runs when a bot token is
available (keychain or TELEGRAM_BOT_TOKEN) and TELEGRAM_CHAT_ID is set.
Skips cleanly otherwise, which is the expected state on CI runners.
"""

import os

import pytest

keyring = pytest.importorskip("keyring", reason="keyring not installed")
import requests


def _get_telegram_token():
    try:
        return keyring.get_password("telegram", "bot_token")
    except Exception:
        return os.environ.get("TELEGRAM_BOT_TOKEN")


@pytest.mark.skipif(not _get_telegram_token(), reason="no Telegram bot token available")
@pytest.mark.skipif(not os.environ.get("TELEGRAM_CHAT_ID"), reason="TELEGRAM_CHAT_ID not set")
def test_send_feedback_buttons():
    token = _get_telegram_token()
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    message = (
        "<b>#3</b> • FISCAL • Fed On The Economy\n\n"
        "<b>Inflation and the Accuracy of Public Debt Forecasts</b>\n\n"
        '<a href="https://www.stlouisfed.org/on-the-economy/2026/feb/inflation-accuracy-public-debt-forecasts">\U0001F517 Read article</a>\n\n'
        "Score: 5.7/10 (mechanical)"
    )
    keyboard = {
        "inline_keyboard": [[
            {"text": "\U0001F44D Like", "callback_data": "like:3"},
            {"text": "\U0001F44E Dislike", "callback_data": "dislike:3"},
            {"text": "\U0001F516 Save", "callback_data": "save:3"},
        ]]
    }

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "reply_markup": keyboard,
            "disable_web_page_preview": True,
        },
        timeout=10,
    )

    assert response.status_code == 200, response.text
