import pytest
from core.telegram.webhook_app import create_webhook_app


@pytest.fixture
def hook():
    calls = []
    handlers = {name: lambda data, name=name: calls.append((name, data))
                for name in ("callback", "voice", "command", "text")}
    app = create_webhook_app(secret="synthetic-secret", resolve_owner=lambda: 42,
        start_thread=lambda target, data: calls.append(("thread", data)), **handlers)
    return app.test_client(), calls


def msg(**extra):
    return {"from": {"id": 42}, "chat": {"id": 42, "type": "private"}, **extra}


def post(client, body, secret="synthetic-secret"):
    return client.post("/webhook", json=body, headers={"X-Telegram-Bot-Api-Secret-Token": secret})


@pytest.mark.parametrize("secret", [None, "", " ", 1])
def test_factory_requires_secret_before_construction(secret):
    with pytest.raises(ValueError):
        create_webhook_app(secret=secret, resolve_owner=lambda: 42,
                           callback=None, voice=None, command=None, text=None)


@pytest.mark.parametrize("body", [None, [], "bad", 1])
def test_malformed_json_is_not_dispatched(hook, body):
    client, calls = hook
    assert post(client, body).status_code == 400
    assert calls == []


@pytest.mark.parametrize("secret", ["", "wrong", "é"])
def test_bad_transport_secret_precedes_all_dispatch(hook, secret):
    client, calls = hook
    assert post(client, {"message": msg(text="/run")}, secret).status_code == 403
    assert calls == []


@pytest.mark.parametrize("payload", [{"text": "/run"}, {"text": "hello"}, {"voice": {"file_id": "fake"}}])
def test_foreign_sender_never_dispatches_or_starts_thread(hook, payload):
    client, calls = hook
    body = msg(**payload); body["from"]["id"] = 43
    assert post(client, {"message": body}).status_code == 403
    assert calls == []


def test_callback_checks_clicking_sender(hook):
    client, calls = hook
    query = {"message": msg(), "from": {"id": 43}, "data": "like:1"}
    assert post(client, {"callback_query": query}).status_code == 403
    assert calls == []
    query["from"]["id"] = 42
    assert post(client, {"callback_query": query}).status_code == 200
    assert calls == [("callback", query)]


@pytest.mark.parametrize("payload,kind", [({"text": "/status"}, "command"),
    ({"text": "hello"}, "text"), ({"voice": {"file_id": "fake"}}, "thread")])
def test_owner_dispatch_routes(hook, payload, kind):
    client, calls = hook
    body = msg(**payload)
    assert post(client, {"message": body}).json == {"ok": True}
    assert calls == [(kind, body)]


def test_health_and_unknown_updates(hook):
    client, calls = hook
    assert client.get("/health").json == {"status": "ok", "mode": "webhook"}
    assert post(client, {"edited_message": msg(text="/run")}).json == {"ok": True}
    assert calls == []


def test_dispatch_failure_is_not_exposed():
    def fail(_):
        raise RuntimeError("SECRET fixture should not reach response")
    app = create_webhook_app(secret="fake", resolve_owner=lambda: 42,
                            callback=fail, voice=fail, command=fail, text=fail)
    result = post(app.test_client(), {"message": msg(text="hello")}, "fake")
    assert result.status_code == 500 and result.json == {"error": "Dispatch failed"}


@pytest.mark.parametrize("status", [1, None])
def test_real_cli_dispatch_propagates_webhook_startup_status(monkeypatch, status):
    import argparse
    import ast
    import sys
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / "core/telegram/telegram_bot.py"
    block = ast.parse(source.read_text()).body[-1]
    assert isinstance(block, ast.If)
    monkeypatch.setattr(sys, "argv", ["telegram_bot.py", "--webhook"])
    with pytest.raises(SystemExit) as exc:
        exec(compile(ast.Module(body=[block], type_ignores=[]), str(source), "exec"),
             {"__name__": "__main__", "argparse": argparse, "sys": sys,
              "run_webhook_mode": lambda: status})
    assert exc.value.code == (status or 0)
