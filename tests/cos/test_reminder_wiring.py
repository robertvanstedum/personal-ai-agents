"""COS wiring for explicit reminders: the /chat receipt, Loop R, Telegram receipts.

The CoS app is imported with its scheduler stubbed and Telegram polling off;
credentials and ``requests.post`` are fakes, so nothing leaves the process.
"""

from datetime import datetime, timedelta, timezone
import json
import sys
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError, ProtocolError

from domains.cos import reminders


TOKEN = "SYNTHETIC-TOKEN-must-never-appear"


@pytest.fixture
def cos(monkeypatch, tmp_path):
    for name in (
        "apscheduler",
        "apscheduler.schedulers",
        "apscheduler.schedulers.background",
        "apscheduler.triggers",
        "apscheduler.triggers.cron",
        "apscheduler.triggers.interval",
    ):
        monkeypatch.setitem(sys.modules, name, MagicMock())
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
    monkeypatch.setenv("DISABLE_TELEGRAM_POLL", "1")
    monkeypatch.setenv("COS_BACKEND_TYPE", "grok")
    monkeypatch.setenv("MINIMOI_LIMITS_DIR", str(tmp_path / "limits"))
    monkeypatch.setenv("COS_REMINDERS_DIR", str(tmp_path / "reminders"))
    monkeypatch.delenv("MINIMOI_COS_MANAGED_REALTIME", raising=False)
    sys.modules.pop("domains.cos.chief_of_staff", None)
    import domains.cos.chief_of_staff as module

    def no_network(*args, **kwargs):
        raise AssertionError("unexpected network call")

    monkeypatch.setattr(module.requests, "post", no_network)
    monkeypatch.setattr(module.requests, "get", no_network)
    module.reminder_root = tmp_path / "reminders"
    yield module
    sys.modules.pop("domains.cos.chief_of_staff", None)


def _future_due(hours=24):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%MZ")


class _Response:
    def __init__(self, status_code=200, body=None, raw=None):
        self.status_code = status_code
        self._body = body
        self._raw = raw

    def json(self):
        if self._raw is not None:
            return json.loads(self._raw)
        return self._body


def test_scheduler_registers_loop_r_every_minute(cos):
    scheduler = cos.BackgroundScheduler.return_value
    jobs = {call.kwargs.get("id"): call for call in scheduler.add_job.call_args_list}

    loop_r = jobs["loop_r"]
    assert loop_r.args[1] == "interval"
    assert loop_r.kwargs == {
        "minutes": 1,
        "id": "loop_r",
        "misfire_grace_time": 60,
        "max_instances": 1,
        "coalesce": True,
    }
    # The registered job runs the reminder due check through the /loops wrapper.
    cos._deliver_due_reminders = lambda: {"idle": True}
    loop_r.args[0]()
    assert cos._loop_state["loop_r"]["last_result"] == {"idle": True}
    assert cos._loop_state["loop_r"]["error"] is None


def test_chat_route_returns_the_reminder_receipt_without_a_model(cos, monkeypatch):
    monkeypatch.setattr(cos, "call_backend", lambda *a: pytest.fail("model was called"))
    request_id = str(uuid4())
    due = _future_due()

    response = cos.app.test_client().post("/chat", json={
        "text": f"/remind {due} call the plumber",
        "request_id": request_id,
    })

    assert response.status_code == 200
    body = response.get_json()
    operation = body["operation"]
    assert body["backend_label"] == "COS Platform" and body["model_label"] == ""
    assert operation["type"] == "reminder_set" and operation["status"] == "saved"
    assert operation["operation_id"] == request_id
    assert operation["due_utc"] == due[:-1] + ":00Z"
    assert operation["storage"] == "cos_reminders" and operation["delivery"] == "telegram"
    [record] = reminders._read(cos.reminder_root)["reminders"]
    assert record["id"] == operation["reminder_id"] and record["state"] == "pending"


def test_chat_route_refuses_a_past_reminder_with_400(cos):
    response = cos.app.test_client().post("/chat", json={
        "text": "/remind 2020-01-01T00:00Z too late",
    })

    assert response.status_code == 400
    assert "in the future" in response.get_json()["reply"]


def test_idle_loop_never_looks_up_credentials(cos, monkeypatch):
    import utils.telegram

    monkeypatch.setattr(utils.telegram, "get_cos_token", lambda: pytest.fail("credentials read"))

    assert cos._deliver_due_reminders() == {"idle": True}


def test_missing_credentials_leave_due_reminders_untouched(cos, monkeypatch):
    import utils.telegram

    reminders.add(
        datetime.now(timezone.utc) + timedelta(seconds=30), "soon", str(uuid4()),
        root=cos.reminder_root,
    )
    monkeypatch.setattr(reminders, "_now", lambda now: datetime.now(timezone.utc) + timedelta(minutes=1))
    monkeypatch.setattr(utils.telegram, "get_cos_token", lambda: "")
    monkeypatch.setattr(utils.telegram, "get_chat_id", lambda: "")

    assert cos._deliver_due_reminders() == {"credentials_unavailable": True}
    [record] = reminders._read(cos.reminder_root)["reminders"]
    assert record["state"] == "pending" and record["attempts"] == 0


def test_due_reminder_is_delivered_through_plain_text_receipt_send(cos, monkeypatch):
    import utils.telegram

    reminders.add(
        datetime.now(timezone.utc) + timedelta(seconds=30), "call the plumber", str(uuid4()),
        root=cos.reminder_root,
    )
    monkeypatch.setattr(reminders, "_now", lambda now: datetime.now(timezone.utc) + timedelta(minutes=1))
    monkeypatch.setattr(utils.telegram, "get_cos_token", lambda: TOKEN)
    monkeypatch.setattr(utils.telegram, "get_chat_id", lambda: "12345")
    posts = []

    def fake_post(url, json=None, timeout=None, allow_redirects=None):
        assert allow_redirects is False
        posts.append((url, json, timeout))
        return _Response(body={"ok": True, "result": {"message_id": 314}})

    monkeypatch.setattr(cos.requests, "post", fake_post)

    summary = cos._deliver_due_reminders()

    assert summary["delivered"] == 1
    [(url, payload, timeout)] = posts
    assert url.endswith("/sendMessage") and timeout == 15
    assert payload == {
        "chat_id": "12345",
        "text": "Reminder: call the plumber",
        "disable_web_page_preview": True,
    }
    [record] = reminders._read(cos.reminder_root)["reminders"]
    assert record["state"] == "delivered" and record["telegram_message_id"] == 314
    assert TOKEN not in json.dumps(summary) and TOKEN not in json.dumps(record)


def _raise(exc):
    def post(*args, **kwargs):
        raise exc
    return post


def _refused_connection():
    reason = NewConnectionError(None, "Connection refused")
    return requests.ConnectionError(MaxRetryError(None, f"/bot{TOKEN}/sendMessage", reason))


@pytest.mark.parametrize(
    ("post", "error", "code"),
    [
        (_raise(requests.ReadTimeout(f"read timed out for /bot{TOKEN}")), reminders.DeliveryUncertain, "telegram_timeout"),
        (_raise(requests.ConnectTimeout(f"connect timed out for /bot{TOKEN}")), reminders.DeliveryFailed, "telegram_connect_timeout"),
        (_raise(_refused_connection()), reminders.DeliveryFailed, "telegram_unreachable"),
        (_raise(requests.ConnectionError(ProtocolError("Connection aborted."))), reminders.DeliveryUncertain, "telegram_connection_lost"),
        (lambda *a, **k: _Response(400, {"ok": False, "description": "chat not found"}), reminders.DeliveryFailed, "telegram_http_400"),
        (lambda *a, **k: _Response(502, None, raw="Bad Gateway"), reminders.DeliveryUncertain, "telegram_unreadable_response"),
        (lambda *a, **k: _Response(200, {"ok": False}), reminders.DeliveryUncertain, "telegram_not_ok"),
        (lambda *a, **k: _Response(200, None, raw="<html>"), reminders.DeliveryUncertain, "telegram_unreadable_response"),
        (lambda *a, **k: _Response(200, {"ok": True, "result": {}}), reminders.DeliveryUncertain, "telegram_missing_message_id"),
    ],
    ids=[
        "read-timeout", "connect-timeout", "refused", "aborted", "http-400",
        "http-502", "ok-false", "unreadable-200", "no-message-id",
    ],
)
def test_receipt_send_classifies_every_outcome_without_the_token(cos, monkeypatch, post, error, code):
    monkeypatch.setattr(cos.requests, "post", post)

    with pytest.raises(error) as raised:
        cos._tg_send_receipt(TOKEN, "12345", "Reminder: x")

    assert raised.value.code == code
    assert TOKEN not in str(raised.value)
    # Nothing token-bearing is chained onto the raised error.
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None or raised.value.__suppress_context__


def test_receipt_send_returns_the_message_id(cos, monkeypatch):
    monkeypatch.setattr(
        cos.requests,
        "post",
        lambda *a, **k: _Response(200, {"ok": True, "result": {"message_id": 99}}),
    )

    assert cos._tg_send_receipt(TOKEN, "12345", "Reminder: x") == {"ok": True, "message_id": 99}
