"""MC voice on the direct path: the shared bootstrap and consult route on a synthetic Guild mount.
Real owner_api, check_write, consult ledger, typed-turn route code and budget (SQLite). The provider mint and the
model call (api.run_mc_turn) are stubs. No provider, no Keychain, no microphone, no network.
"""
import functools
import hashlib
import inspect
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, redirect, request

from core.realtime_voice.budget import VoiceBudget
from minimoi_portal.guild_ui import api, mc_voice_direct as direct
from minimoi_portal.guild_ui.conversations import ConversationNotFound
from minimoi_portal.guild_ui.security import csrf_token
from minimoi_portal.guild_ui.stores import WriteResult

CID = "c-123456abcdef"
BASE = "/guild-next/api/v1/mc/voice/direct/api/realtime-voice"
ON = {"MINIMOI_MC_VOICE": "1", "MINIMOI_MC_VOICE_DIRECT": "1", "MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO": "2000000"}
# mc_managed_realtime.install as of 670d3e09; MANAGED_VOICE_CHANNEL's one definition.
INSTALL_SHA = "b2938e465868316669a21f3db35d8713e7ae9019ffa560453ef99ce2f611bef1"


class Notes:
    floor = "owner-floor"

    def __init__(self):
        self.rows = {}

    def add_note(self, key, text, author, **kw):
        if key in self.rows:
            old = self.rows[key]
            return WriteResult("kept" if old["text"] == text else "idempotency_mismatch", old, True)
        value = {"request_id": key, "id": key, "text": text, "who": author.id, "author_kind": author.kind,
                 "created_at": "2026-10-27T20:00:00+00:00", "context": kw}
        self.rows[key] = value
        return WriteResult("kept", value, False)

    def get_note(self, key):
        return self.rows.get(key)


class Conversations:
    def get(self, cid, principal):
        if cid != CID or principal != "robert":
            raise ConversationNotFound()
        return {"id": cid, "principal": principal, "archived": False, "notes_floor": "owner-floor"}

    def touch(self, cid, principal, first_text=None):
        return self.get(cid, principal)


class Cos:
    """The Chief of Staff's note operation: a receipt unless told to fail."""
    kind = "cos"

    def __init__(self):
        self.calls, self.fail = [], False

    def operation(self, text, *, conversation_id, request_id):
        self.calls.append(text)
        if self.fail:
            return {"state": "failed", "reply": "Not confirmed."}
        return {"state": "ok", "operation": {"type": "note_save", "status": "saved", "operation_id": request_id,
                                       "storage": "cos_platform_memory"}}


def build(monkeypatch, tmp_path, env):
    app = Flask("direct-synthetic")
    app.secret_key = "test-only-session-key"
    notes, cos, turns, mints = Notes(), Cos(), [], []

    def current_user():
        return {"username": "robert", "display_name": "Robert"} if request.headers.get("X-Synthetic-Owner") == "yes" else None

    def owner_guard(fn):
        @functools.wraps(fn)
        def guarded(*a, **k):
            return fn(*a, **k) if current_user() else redirect("/login")
        return guarded

    services = SimpleNamespace(mc=cos, mc_turns=True)
    config = {"owner_guard": owner_guard, "current_user": current_user, "url_prefix": "/guild-next",
              "base_url": "http://localhost", "services": services, "feature_environ": env}
    app.extensions["guild"] = config
    convs = Conversations()
    monkeypatch.setattr(api, "_conversations", lambda: convs)
    monkeypatch.setattr(api, "_conv_floor", lambda conv: notes)
    monkeypatch.setattr(api, "_floor", lambda: notes)

    def typed_turn(actual, **kw):                       # the typed turn's model call, stubbed
        turns.append(kw)
        return {"status": "answered", "reply_note": {"text": "Item **12** is in build.", "request_id": "reply-1"}}, 200
    monkeypatch.setattr(api, "run_mc_turn", typed_turn)
    from minimoi_portal.guild_ui import jobs_api
    monkeypatch.setattr(jobs_api, "job_command", lambda found: None)

    def mint(**kw):
        mints.append(kw)
        return {"provider": "openai", "client_secret": "ek_synthetic", "expires_at": 1, "model": "gpt-realtime-2.1"}
    monkeypatch.setattr("core.realtime_voice.bootstrap.openai_realtime.mint_ephemeral_credential", mint)
    monkeypatch.setattr("core.realtime_voice.bootstrap._rate_limit_state", {})
    tmp_path.mkdir(parents=True, exist_ok=True)
    budget = VoiceBudget("sqlite:///" + str(tmp_path / "budget.db"), cap_usd=lambda: 20)
    budget.ensure_schema()

    @app.get("/csrf")
    def token():
        return jsonify(token=csrf_token())

    installed = direct.install(app, "guild", environ=env, budget=budget)
    client = app.test_client()
    headers = {"X-Synthetic-Owner": "yes", "Origin": "http://localhost", "Content-Type": "application/json",
               "X-CSRF-Token": client.get("/csrf").json["token"], "X-Record-Mode": "on_record"}
    return SimpleNamespace(app=app, client=client, headers=headers, notes=notes, cos=cos, turns=turns, mints=mints,
                           budget=budget, config=config, installed=installed)


@pytest.fixture
def ready(monkeypatch, tmp_path):
    return build(monkeypatch, tmp_path, dict(ON))


def start(r):
    response = r.client.post(BASE + "/bootstrap", json={"persona": "master_craftsman", "provider": "openai",
                                                        "conversation_id": CID}, headers=r.headers)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def call(r, sid, call_id="call-1", name="consult", words="What is stuck on #12?", headers=None, **body):
    return r.client.post(BASE + "/consult", headers=r.headers if headers is None else headers, json={
        "session_id": sid, "call_id": call_id, "name": name, "owner_transcript_item": words,
        "arguments": {"question": "the model's paraphrase"}, **body})


def test_bootstrap_issues_an_owner_session_with_a_sceneless_english_persona_and_two_tools(ready):
    r = ready
    body = start(r)
    assert body["voice_session_id"].startswith("vs-") and body["client_secret"] == "ek_synthetic"
    assert body["voice_notices"] == ["openai voice is unset, using marin", "xai voice is unset, using eve"]
    minted = r.mints[0]
    assert minted["transcription_language"] == "en" and minted["voice"] == "marin"
    assert "Master Craftsman, Robert's colleague in the work" in minted["instructions"]
    assert "SCENARIO FOR THIS SESSION" not in minted["instructions"]
    assert [t["name"] for t in minted["tools"]] == ["consult", "save_note"]
    assert all(set(t) == {"type", "name", "description", "parameters"} for t in minted["tools"])
    assert r.budget.status()["reserved_usd"] == 2.0


def test_bootstrap_refuses_before_the_provider(ready, monkeypatch, tmp_path):
    r = ready
    assert r.client.post(BASE + "/bootstrap", json={"persona": "master_craftsman", "conversation_id": CID}).status_code == 401
    bad = {**r.headers, "X-CSRF-Token": "forged"}
    assert r.client.post(BASE + "/bootstrap", json={"persona": "master_craftsman", "conversation_id": CID}, headers=bad).status_code == 403
    off = {**r.headers, "X-Record-Mode": "off_record"}
    assert r.client.post(BASE + "/bootstrap", json={"persona": "master_craftsman", "conversation_id": CID}, headers=off).status_code == 409
    assert r.client.post(BASE + "/bootstrap", json={"persona": "master_craftsman"}, headers=r.headers).status_code == 422
    assert r.client.post(BASE + "/bootstrap", json={"persona": "frau_berger", "conversation_id": CID}, headers=r.headers).status_code == 400
    assert r.client.post("/guild-next/api/v1/mc/voice/direct/api/realtime-voice/memo/bootstrap", json={},
                         headers=r.headers).status_code == 404
    no_allowance = build(monkeypatch, tmp_path / "b", {k: v for k, v in ON.items() if k != "MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO"})
    refused = no_allowance.client.post(BASE + "/bootstrap", json={"persona": "master_craftsman", "conversation_id": CID},
                                       headers=no_allowance.headers)
    assert refused.status_code == 503 and refused.get_json()["error"] == "unavailable"
    assert not r.mints and not no_allowance.mints


def test_consult_runs_the_typed_turn_once_on_the_owners_words_and_a_replay_does_not_run_it_again(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    first = call(r, sid)
    again = call(r, sid)
    assert first.status_code == 200 and again.get_json() == first.get_json()
    result = first.get_json()
    assert result["ok"] is True and result["answered"] is True
    assert result["spoken"] == "Item 12 is in build." and result["receipt"] == "reply-1"
    assert len(r.turns) == 1
    turn = r.turns[0]
    assert turn["note"]["text"] == "What is stuck on #12?" and turn["note"]["author_kind"] == "owner"
    assert turn["note"]["context"] == {"page": "voice"} and turn["note_id"] == result["note_request_id"]
    assert "host_channel" not in turn and turn["conv"]["id"] == CID
    assert list(r.notes.rows) == [result["note_request_id"]]
    assert "paraphrase" not in str(r.notes.rows)


def test_late_forged_missing_csrf_and_missing_token_calls_are_refused(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    assert call(r, "vs-0000000000000000").status_code == 403                       # forged: not this owner's session
    assert call(r, sid, headers={**r.headers, "X-CSRF-Token": "forged"}).status_code == 403
    no_csrf = {k: v for k, v in r.headers.items() if k != "X-CSRF-Token"}
    assert call(r, sid, headers=no_csrf).status_code == 403
    signed_out = {k: v for k, v in r.headers.items() if k != "X-Synthetic-Owner"}
    assert call(r, sid, headers=signed_out).status_code == 401
    assert call(r, None).status_code == 400                                         # no session token
    assert call(r, sid, words="").status_code == 400
    assert r.client.post(BASE + f"/session/{sid}/end", headers=no_csrf).status_code == 403
    assert r.client.post(BASE + f"/session/{sid}/end", headers=r.headers).get_json() == {"ok": True, "ended": True}
    late = call(r, sid, call_id="call-late")
    assert late.status_code == 409 and late.get_json()["error"] == "session ended"
    assert not r.turns and not r.notes.rows


def test_save_note_is_spoken_as_saved_only_with_a_receipt(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    saved = call(r, sid, call_id="note-1", name="save_note", words="Make a note that the kiln needs a new seal").get_json()
    assert saved["saved"] is True, saved
    assert saved["receipt"] and "not saved" not in saved["spoken"].lower()
    assert saved["spoken"] == "Saved to Chief of Staff notes: the kiln needs a new seal"
    assert call(r, sid, call_id="note-1", name="save_note", words="Make a note that the kiln needs a new seal").get_json() == saved
    assert r.cos.calls == ["/note the kiln needs a new seal"]
    r.cos.fail = True
    failed = call(r, sid, call_id="note-2", name="save_note", words="Make a note that the glaze is ready").get_json()
    assert failed["saved"] is False and failed["receipt"] is None and failed["spoken"] == direct.NOT_SAVED
    plain = call(r, sid, call_id="note-3", name="save_note", words="The kiln is hot today").get_json()
    assert plain["ok"] is False and plain["saved"] is False and plain["spoken"] == direct.NOT_A_NOTE
    assert not r.turns and len(r.cos.calls) == 2


@pytest.mark.parametrize("env", [{}, {"MINIMOI_MC_VOICE": "1"}, {"MINIMOI_MC_VOICE_DIRECT": "1"}])
def test_routes_are_absent_when_the_voice_switch_is_off(monkeypatch, tmp_path, env):
    r = build(monkeypatch, tmp_path, env)
    assert r.installed is False and "mc_direct_voice" not in r.config
    assert r.client.post(BASE + "/bootstrap", json={}, headers=r.headers).status_code == 404
    assert r.client.post(BASE + "/consult", json={}, headers=r.headers).status_code == 404


def test_production_holds_the_direct_path_off():
    from minimoi_portal.guild_mounts import PRODUCTION_HELD_OFF
    assert "MINIMOI_MC_VOICE_DIRECT" in PRODUCTION_HELD_OFF


def test_the_managed_sideband_and_its_channel_are_unchanged(ready):
    from minimoi_portal.guild_ui import mc_managed_realtime
    from minimoi_portal.guild_ui.mc import cos
    assert hashlib.sha256(inspect.getsource(mc_managed_realtime.install).encode()).hexdigest() == INSTALL_SHA
    assert inspect.getsource(cos).count("MANAGED_VOICE_CHANNEL = object()") == 1
    assert "mc_managed_realtime" not in ready.config
    source = inspect.getsource(direct)
    assert "mc.cos" not in source and "mc_managed_realtime" not in source.split('"""', 2)[2]
