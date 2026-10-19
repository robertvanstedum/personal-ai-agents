"""Reminders are off unless the mount switches them on (MINIMOI_MC_OWNER_REMINDERS). A reminder is delivered by
Telegram to the owner's phone, so no test or Dev portal may send one by default. Off, a reminder command is recognised
and refused honestly: no operation id, no Chief of Staff call, no model call, and (with the turn ledger on) no pause.
Notes are unaffected. Covers the shared seam (owner_actions.execute) and both callers: the typed route and the
managed-voice dispatch. No network, no model, no database."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import Flask

from minimoi_portal.guild_ui import owner_actions as oa
from minimoi_portal.guild_ui.mc import ledger_gate

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from test_owner_actions import run
from test_mc_turn_ledger_routes import FakeCos as RouteCos, FakeLedger, _keep, _ledger_on, owner, turned  # noqa: F401
from test_mc_managed_realtime import CosOperations, ready, start  # noqa: F401

API = "/guild-next/api/v1"
REMINDER = "Remind me in 20 minutes to stretch"


# ── the seam ─────────────────────────────────────────────────────────────────────────────────────

def test_off_by_default_outside_any_mount():
    assert oa.reminders_enabled() is False


def test_off_a_reminder_is_refused_and_nothing_reaches_the_chief_of_staff():
    out, cos, floor = run(REMINDER)
    assert out == {"reply": oa.REMINDERS_OFF,
                   "operation": {"type": "reminder_set", "saved": False, "reason": "reminders_off"}}
    assert cos.calls == [] and floor.rows == {}
    assert "nothing was scheduled" in out["reply"] and "Telegram" not in out["reply"]


@pytest.mark.parametrize("text", ["remind me to call mom", "/remind 2026-10-19T14:00Z call the plumber",
                                  "set a reminder for tomorrow morning to water the plants"])
def test_off_every_reminder_form_is_refused_before_parsing_its_time(text):
    out, cos, _ = run(text)
    assert out["operation"]["reason"] == "reminders_off" and cos.calls == []


def test_off_notes_and_ordinary_chat_are_unchanged():
    out, cos, _ = run("Make a note that the gate code is 4411")
    assert out["operation"]["type"] == "cos_note" and out["operation"]["saved"] is True and len(cos.calls) == 1
    out, cos, _ = run("What did we decide about the roof?")
    assert out is None and cos.calls == []


def test_on_a_reminder_reaches_the_chief_of_staff(monkeypatch):
    monkeypatch.setattr(oa, "reminders_enabled", lambda: True)
    out, cos, _ = run(REMINDER)
    assert out["operation"]["type"] == "reminder_set" and out["operation"]["saved"] is True and len(cos.calls) == 1


def test_the_switch_is_read_from_the_mount_not_the_process(monkeypatch):
    from minimoi_portal.guild_ui import _worker_config
    monkeypatch.setenv(oa.REMINDERS_FLAG, "1")                      # a stray process flag never turns it on
    app = Flask("reminders-guard")
    with app.app_context():
        assert oa.reminders_enabled() is False
        with _worker_config(app, {"feature_environ": {oa.REMINDERS_FLAG: "1"}}):
            assert oa.reminders_enabled() is True
        with _worker_config(app, {"feature_environ": {oa.REMINDERS_FLAG: "0"}}):
            assert oa.reminders_enabled() is False


def test_a_broken_configuration_reads_as_off(monkeypatch):
    import minimoi_portal.guild_ui as gui

    def broken(_name):
        raise KeyError("no mount")
    monkeypatch.setattr(gui, "feature_enabled", broken)
    assert oa.reminders_enabled() is False


# ── the typed route ──────────────────────────────────────────────────────────────────────────────

def _cos(portal):
    services = portal.app.extensions["guild_ui_next"]["services"]
    services.mc = RouteCos({"state": "ok"})
    return services.mc


def _env(portal, **flags):
    ext = portal.app.extensions["guild_ui_next"]
    ext["feature_environ"] = {**(ext.get("feature_environ") or {}), **flags}


@pytest.mark.parametrize("path", ["/mc/turns", "/mc/turns/stream"])
def test_typed_reminder_off_is_answered_by_the_platform_with_nothing_sent(turned, path):
    cos = _cos(turned)
    client, token = owner(turned)
    note = _keep(client, token, REMINDER)
    r = client.post(f"{API}{path}", json={"note_request_id": note["request_id"], "request_id": "req-" + note["request_id"][-12:],
                                          "record_mode": "on_record"}, headers=write_headers(token))
    body = r.get_json()
    assert r.status_code == 200 and body["backend_kind"] == "platform" and body["message"] == oa.REMINDERS_OFF
    assert body["operation"] == {"type": "reminder_set", "saved": False, "reason": "reminders_off"}
    assert cos.calls == [] and turned.extra["runtime"].sent == []          # no Chief of Staff call, no model call


def test_typed_reminder_off_does_not_pause_the_ledger(turned):
    cos = _cos(turned)
    ledger = FakeLedger()
    _ledger_on(turned, ledger)
    client, token = owner(turned)
    note = _keep(client, token, REMINDER)
    r = client.post(f"{API}/mc/turns", json={"note_request_id": note["request_id"], "request_id": "req-" + note["request_id"][-12:],
                                             "record_mode": "on_record"}, headers=write_headers(token))
    assert r.get_json()["message"] == oa.REMINDERS_OFF and cos.calls == []
    assert ledger.finished[0][0] == "answered"
    assert ledger.finished[0][1]["operation"] == {"type": "reminder_set", "saved": False, "reason": "reminders_off"}


def test_typed_reminder_on_reaches_the_chief_of_staff(turned):
    cos = _cos(turned)
    _env(turned, **{oa.REMINDERS_FLAG: "1"})
    client, token = owner(turned)
    note = _keep(client, token, REMINDER)
    client.post(f"{API}/mc/turns", json={"note_request_id": note["request_id"], "request_id": "req-" + note["request_id"][-12:],
                                         "record_mode": "on_record"}, headers=write_headers(token))
    assert len(cos.calls) == 1 and cos.calls[0].startswith("/remind ")


# ── the managed-voice dispatch ───────────────────────────────────────────────────────────────────

def test_spoken_reminder_off_is_refused_with_nothing_sent(ready):
    r = ready
    cos = CosOperations()
    r.config["services"].mc.operation = cos.operation
    s, _ = start(r)
    with ThreadPoolExecutor(max_workers=1) as worker:
        out = worker.submit(s.dispatch, "consult_cos_agent", "remind me in 20 minutes to stretch", "c-123456abcdef", "op-guard-1").result()
    assert out["reply"] == oa.REMINDERS_OFF and out["operation"]["reason"] == "reminders_off"
    assert cos.calls == [] and not r.calls                                  # no Chief of Staff call, no model consult


def test_spoken_transcript_reminder_off_sends_nothing(ready):
    r = ready
    cos = CosOperations()
    r.config["services"].mc.operation = cos.operation
    s, _ = start(r)
    s.observe({"type": "input_audio_buffer.committed", "item_id": "guard-spoken-1"})
    s.observe({"type": "conversation.item.input_audio_transcription.completed", "item_id": "guard-spoken-1",
               "transcript": "Remind me in 20 minutes to check the oven",
               "usage": {"type": "tokens", "input_tokens": 1, "output_tokens": 1,
                         "input_token_details": {"text_tokens": 0, "audio_tokens": 1}}})
    s.worker.submit(lambda: None).result(timeout=2)
    assert cos.calls == [] and not r.calls


def test_spoken_reminder_on_reaches_the_chief_of_staff(ready):
    r = ready
    r.env[oa.REMINDERS_FLAG] = "1"
    cos = CosOperations()
    r.config["services"].mc.operation = cos.operation
    s, _ = start(r)
    with ThreadPoolExecutor(max_workers=1) as worker:
        out = worker.submit(s.dispatch, "consult_cos_agent", "remind me in 20 minutes to stretch", "c-123456abcdef", "op-guard-2").result()
    assert out["operation"]["type"] == "reminder_set" and out["operation"]["saved"] is True and len(cos.calls) == 1


def test_ledger_classifies_the_refusal_as_an_answer():
    ledger = FakeLedger()
    turn = ledger_gate.Turn(ledger, ledger.admit(principal="p", conversation_id="c", note_request_id="n",
                                                 turn_request_id="t", kind="typed"))
    turn.finish_platform({"status": "answered", "backend_kind": "platform",
                          "operation": {"type": "reminder_set", "saved": False, "reason": "reminders_off"}})
    assert ledger.finished[0][0] == "answered"
