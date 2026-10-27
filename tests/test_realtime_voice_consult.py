"""Synthetic consults. No provider, no microphone, no MC."""
from flask import Flask

from core.realtime_voice.consult import ConsultLedger, create_consult_blueprint, stub_backend

OWNER = "42"
SESSION = "sess-1"


def app_for(ledger, sessions=None):
    sessions = sessions if sessions is not None else {SESSION: OWNER}
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(create_consult_blueprint(
        backend=stub_backend,
        ledger=ledger,
        csrf_ok=lambda: True,
        owner_id=lambda: OWNER,
        session_owner=sessions.get,
    ))
    return app


def post(client, **over):
    body = {
        "session_id": SESSION,
        "call_id": "call-1",
        "name": "consult",
        "owner_transcript_item": "What time is it?",
        "arguments": {"question": "the model's paraphrase"},
    }
    body.update(over)
    return client.post("/api/realtime-voice/consult", json=body)


def test_a_consult_uses_the_owners_words_and_a_replay_does_not_run_again():
    calls = []

    def backend(owner_text, arguments):
        calls.append(owner_text)
        return stub_backend(owner_text, arguments)

    ledger = ConsultLedger()
    ledger.open_session(SESSION)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(create_consult_blueprint(
        backend=backend, ledger=ledger, csrf_ok=lambda: True,
        owner_id=lambda: OWNER, session_owner={SESSION: OWNER}.get,
    ))
    client = app.test_client()
    first = post(client)
    second = post(client)
    assert first.status_code == 200
    assert first.get_json()["spoken"] == "Stub answer to: What time is it?"
    assert second.get_json() == first.get_json()
    assert calls == ["What time is it?"]


def test_missing_csrf_forged_call_and_late_call_are_refused():
    ledger = ConsultLedger()
    ledger.open_session(SESSION)
    refused = Flask(__name__)
    refused.config["TESTING"] = True
    refused.register_blueprint(create_consult_blueprint(
        backend=stub_backend, ledger=ledger, csrf_ok=lambda: False,
        owner_id=lambda: OWNER, session_owner={SESSION: OWNER}.get,
    ))
    assert post(refused.test_client()).status_code == 403
    client = app_for(ledger).test_client()
    assert post(client, call_id="not a call").status_code == 400
    ledger.close_session(SESSION)
    late = post(client, call_id="call-2")
    assert late.status_code == 409
    assert late.get_json()["error"] == "session ended"


def test_the_thirteenth_consult_is_the_cap_line_and_a_slow_one_carries_the_holding_line(monkeypatch):
    ledger = ConsultLedger()
    ledger.open_session(SESSION)
    client = app_for(ledger).test_client()
    for n in range(12):
        assert post(client, call_id=f"c{n}").status_code == 200
    capped = post(client, call_id="c12")
    assert capped.status_code == 429
    assert "after we talk" in capped.get_json()["spoken"]

    import core.realtime_voice.consult as consult
    times = iter([0.0, 11.0])
    monkeypatch.setattr(consult.time, "monotonic", lambda: next(times))
    ledger.open_session("sess-slow")
    slow = app_for(ledger, {"sess-slow": OWNER}).test_client()
    held = post(slow, session_id="sess-slow", call_id="slow-1")
    assert held.get_json()["holding_line"] == "Still checking."
