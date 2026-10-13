"""The shared voice budget on the MC voice and dictation routes: reserved before the provider is contacted, refused plainly at the cap, fail-closed, no rebate.
Stand-ins for the provider; a real SQLite ledger; no network, no paid call."""
from __future__ import annotations

import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_mc_streaming import _fresh_dispatch_state, streaming  # noqa: F401
from test_mc_turn_capture import mc_dir, mc_lines  # noqa: F401
from test_mc_turns import API, FakeRuntime, _keep, _openclaw, _turn_on, turned  # noqa: F401
from test_mc_voice import _answered, _on as _mc_on, provider as mc_provider  # noqa: F401
from test_dictation import _on as _dict_on, provider as dict_provider  # noqa: F401

from core.realtime_voice import budget as B
from voice_budget_helpers import install  # noqa: F401


def _fill(ledger, usd):
    ledger.reserve(kind="tts", domain="cos", principal="someone", amount_micro=B.micro(usd))


def _post(client, token, path):
    return client.post(f"{API}{path}", json={}, headers=write_headers(token))


# ── MC voice ──
def test_a_session_reserves_its_full_allowed_length_before_the_provider_is_contacted(turned, mc_provider, monkeypatch):
    _mc_on(monkeypatch)
    ledger = B.default_budget()
    client = turned.owner()
    token = turned.csrf(client)
    r = _post(client, token, "/mc/voice/bootstrap")
    assert r.status_code == 200 and len(mc_provider.minted) == 1
    assert ledger.status()["reserved_usd"] == 1.02                                   # the provider's 60-minute maximum at the live-transcription rate, not the page's 15-minute timer
    caps = client.get(f"{API}/mc/voice/capabilities").get_json()["budget"]
    assert caps["ok"] and caps["cap_usd"] == 20.0 and caps["reserved_usd"] == 1.02 and caps["remaining_usd"] == 18.98
    assert caps["timezone"] == "America/Chicago" and "not a bill" in caps["label"] and "not a guaranteed spending limit" in caps["label"]


def test_at_the_cap_mc_voice_and_its_reading_are_refused_plainly_without_a_provider_call(turned, mc_provider, monkeypatch):
    _mc_on(monkeypatch)
    ledger = B.default_budget()
    client, cid, note, _reply = _answered(turned)
    token = turned.csrf(client)
    _fill(ledger, 19.995)
    r = _post(client, token, "/mc/voice/bootstrap")
    body = r.get_json()
    assert r.status_code == 429 and not body["ok"] and "used up" in body["error"] and "not a bill" in body["error"] and "midnight Chicago time" in body["error"]
    assert body["budget"]["remaining_usd"] == 0.005
    s = client.get(f"{API}/mc/voice/speech/{note['request_id']}?c={cid}")
    assert s.status_code == 429 and "used up" in s.get_data(as_text=True) and "not a bill" in s.get_data(as_text=True)
    assert not mc_provider.minted and not mc_provider.spoken
    assert ledger.status()["reserved_usd"] == 19.995                                  # refused asks reserve nothing


def test_reading_aloud_reserves_by_the_length_of_the_server_side_text(turned, mc_provider, monkeypatch):
    _mc_on(monkeypatch)
    ledger = B.default_budget()
    client, cid, note, reply = _answered(turned)
    assert client.get(f"{API}/mc/voice/speech/{note['request_id']}?c={cid}").status_code == 200
    [(spoken, _user)] = mc_provider.spoken
    assert ledger.status()["reserved_usd"] == B.tts_allowance_micro(spoken) / B.MICRO


def test_an_unreadable_ledger_refuses_voice_and_nothing_reaches_the_provider(turned, mc_provider, monkeypatch, tmp_path):
    _mc_on(monkeypatch)
    monkeypatch.setenv(B.ENV_DSN, "sqlite:////nonexistent-dir-for-voice-budget/x.db")
    monkeypatch.setattr(B, "_default", None)
    client, cid, note, _reply = _answered(turned)
    token = turned.csrf(client)
    r = _post(client, token, "/mc/voice/bootstrap")
    assert r.status_code == 503 and "nothing was sent to the speech provider" in r.get_json()["error"]
    assert client.get(f"{API}/mc/voice/speech/{note['request_id']}?c={cid}").status_code == 503
    assert not mc_provider.minted and not mc_provider.spoken
    caps = client.get(f"{API}/mc/voice/capabilities").get_json()
    assert caps["ok"] and caps["budget"]["ok"] is False                              # the page still loads; it simply cannot start voice


def test_the_provider_refusing_us_gives_the_allowance_back_but_a_timeout_does_not_and_nothing_retries(turned, mc_provider, monkeypatch):
    from core.realtime_voice.bootstrap import _rate_limit_state
    from core.realtime_voice.providers import openai_realtime
    _mc_on(monkeypatch)
    ledger = B.default_budget()
    client = turned.owner()
    token = turned.csrf(client)
    attempts = []

    def refuse(**kw):
        attempts.append(1)
        raise openai_realtime.OpenAIRealtimeError("provider refused", 401)

    def hang(**kw):
        attempts.append(1)
        raise openai_realtime.OpenAIRealtimeError("timed out", None)
    monkeypatch.setattr(openai_realtime, "mint_confer_transcription_credential", refuse)
    r = _post(client, token, "/mc/voice/bootstrap")
    assert r.status_code == 502 and attempts == [1] and ledger.status()["reserved_usd"] == 0.0
    _rate_limit_state.clear()
    monkeypatch.setattr(openai_realtime, "mint_confer_transcription_credential", hang)
    r = _post(client, token, "/mc/voice/bootstrap")
    assert r.status_code == 502 and attempts == [1, 1] and ledger.status()["reserved_usd"] == 1.02


def test_the_existing_guards_run_before_any_reservation(turned, mc_provider, monkeypatch):
    _mc_on(monkeypatch)
    ledger = B.default_budget()
    client = turned.owner()
    token = turned.csrf(client)
    assert client.post(f"{API}/mc/voice/bootstrap", json={}).status_code == 403                                   # no token
    assert client.post(f"{API}/mc/voice/bootstrap", json={}, headers=write_headers(token, mode="off_record")).status_code == 409
    guest = turned.guest()
    assert guest.post(f"{API}/mc/voice/bootstrap", json={}, headers=write_headers(token)).status_code in (401, 403, 404)       # not the owner: refused before any reservation
    assert turned.client().post(f"{API}/mc/voice/bootstrap", json={}).status_code in (401, 403, 302)
    assert ledger.status()["reserved_usd"] == 0.0 and not mc_provider.minted


def test_off_means_off_the_ledger_is_never_touched(turned, mc_provider, monkeypatch, tmp_path):
    monkeypatch.delenv("MINIMOI_MC_VOICE", raising=False)
    monkeypatch.setenv(B.ENV_DSN, f"sqlite:///{tmp_path / 'never-created.db'}")
    monkeypatch.setattr(B, "_default", None)
    client = turned.owner()
    token = turned.csrf(client)
    assert client.get(f"{API}/mc/voice/capabilities").status_code == 404
    assert _post(client, token, "/mc/voice/bootstrap").status_code == 404
    assert not (tmp_path / "never-created.db").exists()


# ── dictation ──
def test_dictation_reserves_a_session_and_is_refused_at_the_cap(turned, dict_provider, monkeypatch):
    _dict_on(monkeypatch)
    ledger = B.default_budget()
    client = turned.owner()
    token = turned.csrf(client)
    assert _post(client, token, "/dictation/bootstrap").status_code == 200 and len(dict_provider) == 1
    assert ledger.status()["reserved_usd"] == 1.02
    _fill(ledger, 18.50)
    r = _post(client, token, "/dictation/bootstrap")
    assert r.status_code == 429 and "used up" in r.get_json()["error"] and len(dict_provider) == 1
    assert client.get(f"{API}/dictation/capabilities").get_json()["budget"]["remaining_usd"] == 0.48


def test_dictation_fails_closed_and_keeps_its_guards(turned, dict_provider, monkeypatch):
    _dict_on(monkeypatch)
    client = turned.owner()
    token = turned.csrf(client)
    assert client.post(f"{API}/dictation/bootstrap", json={}).status_code == 403
    assert B.default_budget().status()["reserved_usd"] == 0.0
    monkeypatch.setenv(B.ENV_DSN, "sqlite:////nonexistent-dir-for-voice-budget/x.db")
    monkeypatch.setattr(B, "_default", None)
    r = _post(client, token, "/dictation/bootstrap")
    assert r.status_code == 503 and not dict_provider


def test_mc_voice_and_dictation_draw_on_one_shared_allowance(turned, mc_provider, dict_provider, monkeypatch):
    _mc_on(monkeypatch)
    _dict_on(monkeypatch)
    ledger = B.default_budget()
    client = turned.owner()
    token = turned.csrf(client)
    assert _post(client, token, "/mc/voice/bootstrap").status_code == 200
    assert _post(client, token, "/dictation/bootstrap").status_code == 200
    assert ledger.status()["reserved_usd"] == 2.04
