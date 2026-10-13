"""Dictation (insertion-only), server side: off unless switched on; a transcription-only credential with no turn detection; owner-only. Stand-ins for the provider; no network, no paid call."""
from __future__ import annotations

import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_mc_streaming import _fresh_dispatch_state, streaming  # noqa: F401
from test_mc_turn_capture import mc_dir, mc_lines  # noqa: F401
from test_mc_turns import API, FakeRuntime, _keep, _openclaw, _turn_on, turned  # noqa: F401

from minimoi_portal.guild_ui import dictation as D


@pytest.fixture
def provider(monkeypatch, tmp_path):
    from core.realtime_voice.bootstrap import _rate_limit_state
    from voice_budget_helpers import install
    install(monkeypatch, tmp_path)                       # MC voice and dictation require the shared voice budget
    from core.realtime_voice.providers import openai_realtime
    _rate_limit_state.clear()
    minted = []

    def mint(**kw):
        minted.append(kw)
        return {"ok": True, "provider": "openai", "client_secret": "ek_test_secret", "expires_at": 1, "model": "stand-in"}
    monkeypatch.setattr(openai_realtime, "mint_transcription_credential", mint)
    return minted


def _on(monkeypatch):
    monkeypatch.setenv(D.ENV, "1")


def test_dictation_is_off_by_default_nothing_in_the_page_and_no_provider_call(turned, provider, monkeypatch):
    monkeypatch.delenv(D.ENV, raising=False)
    client = turned.owner()
    token = turned.csrf(client)
    assert client.get(f"{API}/dictation/capabilities").status_code == 404
    assert client.post(f"{API}/dictation/bootstrap", json={}, headers=write_headers(token)).status_code == 404
    assert client.get(f"{API}/dictation/static/realtime-memo-controller.js").status_code == 404
    assert not provider
    page = client.get("/guild-next/guild/build")
    text = page.get_data(as_text=True)
    assert "data-dictat" not in text and "mc-dictate" not in text and "api.openai.com" not in page.headers["Content-Security-Policy"]


def test_switching_it_on_adds_the_flag_the_composer_button_and_only_the_provider_origin(turned, provider, monkeypatch):
    _on(monkeypatch)
    page = turned.owner().get("/guild-next/guild/build")
    text = page.get_data(as_text=True)
    assert 'data-dictation="true"' in text and 'data-dictate="#mc-input"' in text and "data-dictation-status" in text
    csp = page.headers["Content-Security-Policy"]
    assert "connect-src 'self' https://api.openai.com;" in csp and "script-src 'self';" in csp


def test_the_origin_is_added_once_when_both_voice_features_are_on(turned, provider, monkeypatch):
    _on(monkeypatch)
    monkeypatch.setenv("MINIMOI_MC_VOICE", "1")
    csp = turned.owner().get("/guild-next/guild/build").headers["Content-Security-Policy"]
    assert csp.count("https://api.openai.com") == 1


def test_capabilities_list_the_memo_provider_and_do_not_claim_a_key(turned, provider, monkeypatch):
    _on(monkeypatch)
    body = turned.owner().get(f"{API}/dictation/capabilities").get_json()
    assert body["ok"] and [p["provider"] for p in body["providers"]] == ["openai"] and body["mode"] == "memo"


def test_bootstrap_mints_a_dictation_credential_with_no_turn_detection_and_no_tools(turned, monkeypatch, tmp_path):
    """The actual request the provider receives, not the wording."""
    import requests
    import core.realtime_voice.providers.openai_realtime as rt
    from core.realtime_voice.bootstrap import _rate_limit_state
    _rate_limit_state.clear()
    from voice_budget_helpers import install
    install(monkeypatch, tmp_path)                       # the real request path needs the shared voice budget ledger
    _on(monkeypatch)
    monkeypatch.setattr(rt, "get_secret", lambda *a, **k: "sk-test-not-real")
    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"value": "ek_stand_in", "expires_at": 1}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return Resp()
    monkeypatch.setattr(requests, "post", post)
    client = turned.owner()
    r = client.post(f"{API}/dictation/bootstrap", json={"provider": "openai"}, headers=write_headers(turned.csrf(client)))
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] and body["client_secret"] == "ek_stand_in" and body["max_minutes"] == 15 and "sk-test-not-real" not in str(body)
    session = seen["json"]["session"]
    assert session["type"] == "transcription"                                                   # a transcription-only session: there is no assistant to respond
    assert session["audio"]["input"]["turn_detection"] is None and session["audio"]["input"]["transcription"]["languages"] == ["en"]
    assert "tools" not in session and "tool_choice" not in session and "instructions" not in session
    assert "robert" not in seen["headers"]["OpenAI-Safety-Identifier"]                         # hashed, not the name


def test_bootstrap_is_a_guarded_on_the_record_write_and_rate_limited(turned, provider, monkeypatch):
    _on(monkeypatch)
    client = turned.owner()
    token = turned.csrf(client)
    assert client.post(f"{API}/dictation/bootstrap", json={}).status_code == 403                    # no token
    assert client.post(f"{API}/dictation/bootstrap", json={}, headers=write_headers(token, mode="off_record")).status_code == 409
    assert not provider
    for _ in range(10):
        assert client.post(f"{API}/dictation/bootstrap", json={}, headers=write_headers(token)).status_code == 200
    assert client.post(f"{API}/dictation/bootstrap", json={}, headers=write_headers(token)).status_code == 429


def test_a_missing_key_is_a_bounded_error_with_no_key_in_it(turned, monkeypatch, tmp_path):
    import core.realtime_voice.providers.openai_realtime as rt
    from core.realtime_voice.bootstrap import _rate_limit_state
    _rate_limit_state.clear()
    from voice_budget_helpers import install
    install(monkeypatch, tmp_path)                       # the real request path needs the shared voice budget ledger
    _on(monkeypatch)
    monkeypatch.setattr(rt, "get_secret", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no secrets in tests")))
    client = turned.owner()
    r = client.post(f"{API}/dictation/bootstrap", json={}, headers=write_headers(turned.csrf(client)))
    assert r.status_code == 502 and r.get_json() == {"ok": False, "error": "OpenAI API key not configured"}


def test_a_guest_and_a_signed_out_browser_get_nothing_and_cause_no_provider_call(turned, provider, monkeypatch):
    _on(monkeypatch)
    for who in (turned.guest(), turned.client()):
        for method, path in (("get", "/dictation/capabilities"), ("get", "/dictation/static/realtime-memo-controller.js"), ("post", "/dictation/bootstrap")):
            assert getattr(who, method)(f"{API}{path}").status_code in (401, 403), path
    assert not provider


def test_only_the_shared_memo_controller_and_its_adapter_are_served(turned, provider, monkeypatch):
    _on(monkeypatch)
    client = turned.owner()
    ok = client.get(f"{API}/dictation/static/realtime-memo-controller.js")
    assert ok.status_code == 200 and "javascript" in ok.mimetype and b"RealtimeMemoController" in ok.data
    assert client.get(f"{API}/dictation/static/adapters/openai-transcription-webrtc-adapter.js").status_code == 200
    for bad in ("adapters/xai-websocket-adapter.js", "../confer.py", "realtime-confer-controller.js", "realtime-memo.css"):
        assert client.get(f"{API}/dictation/static/{bad}").status_code == 404
