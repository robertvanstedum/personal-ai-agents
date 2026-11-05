"""F2 step 4 (D4): Telegram reaches CoS through cos-scheduler /chat, signed service:telegram with its own key. The
key fixes the channel (Telegram only) and the conversation (the owner session Telegram has always used)."""
import json
import sys

import pytest

from core import portal_assertion as PA
from test_history_wiring import _load

TG_KEY = ("tg-kid", "telegram-bot-secret-0123456789abcdef")


@pytest.fixture
def cos(monkeypatch, tmp_path):
    key = tmp_path / "tg.key"
    key.write_text(f"{TG_KEY[0]}:{TG_KEY[1]}\n")
    monkeypatch.setenv("COS_TELEGRAM_ASSERTION_KEY_FILE", str(key))
    yield _load(monkeypatch, tmp_path, history_on=True)
    sys.modules.pop("domains.cos.chief_of_staff", None)


def post(cos, body, key=TG_KEY, subject="service:telegram"):
    raw = json.dumps(body).encode()
    headers = {"X-Portal-Owner-Assertion": PA.sign(key, audience="cos-owner", subject=subject, method="POST",
                                                   target="/chat", body=raw, env="test"),
               "Content-Type": "application/json", "X-Test-Unsigned": "1"}
    return cos.app.test_client().post("/chat", data=raw, headers=headers)


def test_telegram_turn_in_the_owner_session_is_accepted(cos):
    r = post(cos, {"text": "hello", "channel": "telegram_text", "conversation_id": "owner"})
    assert r.status_code == 200 and len(cos._test_sent) == 1


@pytest.mark.parametrize("body", [
    {"text": "hello", "channel": "api_text", "conversation_id": "owner"},
    {"text": "hello", "channel": "telegram_text", "conversation_id": "guild-abc"},
    {"text": "hello", "channel": "telegram_text"},
    {"text": "hello", "channel": "telegram_voice", "conversation_id": "owner", "request_id": "pick-me"},
])
def test_telegram_cannot_pick_another_channel_or_session(cos, body):
    assert post(cos, body).status_code == 403
    assert cos._test_sent == []


def test_telegram_key_cannot_sign_as_the_owner_or_elsewhere(cos):
    assert post(cos, {"text": "hello", "channel": "telegram_text", "conversation_id": "owner"},
                subject="owner:1").status_code == 403
    raw = b'{"text": "x"}'
    h = {"X-Portal-Owner-Assertion": PA.sign(TG_KEY, audience="cos-owner", subject="service:telegram",
                                              method="POST", target="/ui/send", body=raw, env="test"),
         "Content-Type": "application/json", "X-Test-Unsigned": "1"}
    assert cos.app.test_client().post("/ui/send", data=raw, headers=h).status_code == 403


def test_bot_side_signs_the_exact_bytes(monkeypatch, tmp_path):
    import domains.cos.channel_api as api
    key = tmp_path / "k"; key.write_text(f"{TG_KEY[0]}:{TG_KEY[1]}\n")
    monkeypatch.setenv("COS_TELEGRAM_CHAT_URL", "http://cos-scheduler:8769/chat")
    monkeypatch.setenv("COS_TELEGRAM_SIGNING_KEY_FILE", str(key))
    monkeypatch.setenv("COS_TELEGRAM_SIGNING_ENV", "test")
    sent = {}

    class Resp:
        status_code = 200
        def json(self): return {"reply": "ok"}

    def send(self, prepared, **kw):
        sent["body"], sent["header"] = prepared.body, prepared.headers["X-Portal-Owner-Assertion"]
        return Resp()
    import requests
    monkeypatch.setattr(requests.Session, "send", send)
    assert api.chat("hello", channel="telegram_voice") == "ok"
    keys = {TG_KEY[0]: TG_KEY[1].encode()}
    subject = PA.verify(sent["header"], (TG_KEY[0], TG_KEY[1].encode()), audience="cos-owner", env="test",
                        method="POST", target="/chat", body=sent["body"], spend=lambda *a: True)
    assert subject == "service:telegram" and json.loads(sent["body"]) == {
        "text": "hello", "channel": "telegram_voice", "conversation_id": "owner"}
    monkeypatch.setenv("COS_TELEGRAM_SIGNING_KEY_FILE", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError):
        api.chat("hello", channel="telegram_text")
