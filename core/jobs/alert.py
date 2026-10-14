"""One Telegram message, from a Mac job, with the same credentials telegram_bot.py uses.

Token and chat id: macOS Keychain (service ``telegram``: ``bot_token`` and ``chat_id``), then the
``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID`` environment. The token is never printed, logged or put in an
error: every failure becomes the word ``not_sent``. Messages carry a job id, a state and fixed codes only;
anything outside a narrow character set is dropped before sending, so a message cannot carry content.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Callable

SENT, NOT_SENT = "sent", "not_sent"
_ALLOWED = re.compile(r"[^A-Za-z0-9 _:.,;=()+\n-]")
MAX_CHARS = 400


def clean(text: str) -> str:
    return _ALLOWED.sub("", str(text))[:MAX_CHARS]


def _keychain(account: str) -> str | None:
    try:
        import keyring
        return keyring.get_password("telegram", account) or None
    except Exception:                                   # noqa: BLE001 - no keyring, locked, not found
        return None


def _post(url: str, payload: dict, timeout: float) -> int:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:      # noqa: S310 - fixed https host
        return response.status


def send(text: str, *, env=None, keychain: Callable[[str], str | None] = _keychain,
         post: Callable[[str, dict, float], int] = _post, timeout: float = 15.0) -> str:
    """``"sent"`` or ``"not_sent"``. Never raises, never echoes the token."""
    env = os.environ if env is None else env
    try:
        token = keychain("bot_token") or env.get("TELEGRAM_BOT_TOKEN")
        chat_id = keychain("chat_id") or env.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return NOT_SENT
        status = post(f"https://api.telegram.org/bot{token}/sendMessage",
                      {"chat_id": chat_id, "text": clean(text), "disable_web_page_preview": True}, timeout)
        return SENT if status == 200 else NOT_SENT
    except Exception:                                   # noqa: BLE001 - the exception text can hold the URL, so say nothing
        return NOT_SENT
