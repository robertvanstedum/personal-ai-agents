"""MC voice, v1 (server side): off unless switched on; a transcription-only credential; MC's own kept reply read aloud. Stand-ins for the provider; no network, no paid call."""
from __future__ import annotations

import re

import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_mc_streaming import _fresh_dispatch_state, streaming  # noqa: F401
from test_mc_turn_capture import mc_dir, mc_lines  # noqa: F401
from test_mc_turns import API, FakeRuntime, _keep, _openclaw, _turn_on, turned  # noqa: F401
from test_mc_upload_reading import _ask, _new, _note

from minimoi_portal.guild_ui import mc_voice as V


class Calls:
    def __init__(self):
        self.minted, self.spoken = [], []


@pytest.fixture
def provider(monkeypatch, tmp_path):
    from core.realtime_voice.bootstrap import _rate_limit_state
    from voice_budget_helpers import install
    install(monkeypatch, tmp_path)                       # MC voice and dictation require the shared voice budget
    from core.realtime_voice.providers import openai_realtime, openai_speech
    _rate_limit_state.clear()
    calls = Calls()

    def mint(**kw):
        calls.minted.append(kw)
        return {"ok": True, "provider": "openai", "client_secret": "ek_test_secret", "expires_at": 1, "model": "stand-in"}

    class Upstream:
        def iter_content(self, chunk_size=0):
            yield b"ID3-stand-in-audio"

        def close(self):
            pass

    def speak(*, text, user_id, instructions):
        assert instructions == V.SPEAKING_INSTRUCTIONS
        calls.spoken.append((text, user_id))
        return Upstream()

    monkeypatch.setattr(openai_realtime, "mint_confer_transcription_credential", mint)
    monkeypatch.setattr(openai_speech, "create_speech_stream", speak)
    return calls


def _on(monkeypatch):
    monkeypatch.setenv(V.ENV, "1")


def _page(client, cid=None):
    return client.get(f"/guild-next/guild/build" + (f"?c={cid}" if cid else ""))


# ── off by default ──
def test_voice_is_off_by_default_and_calls_nothing(turned, provider, monkeypatch):
    monkeypatch.delenv(V.ENV, raising=False)
    client = turned.owner()
    token = turned.csrf(client)
    assert client.get(f"{API}/mc/voice/capabilities").status_code == 404
    assert client.post(f"{API}/mc/voice/bootstrap", json={}, headers=write_headers(token)).status_code == 404
    assert client.get(f"{API}/mc/voice/speech/some-note-id-1").status_code == 404
    assert client.get(f"{API}/mc/voice/static/realtime-confer-controller.js").status_code == 404
    assert not provider.minted and not provider.spoken
    html = _page(client)
    text = html.get_data(as_text=True)
    assert "data-mc-voice" not in text and "mc-voice" not in text and "api.openai.com" not in html.headers["Content-Security-Policy"]     # off means nothing on the page


def test_switching_it_on_shows_the_flag_and_opens_connect_src_only_to_the_provider(turned, provider, monkeypatch):
    _on(monkeypatch)
    resp = _page(turned.owner())
    assert 'data-mc-voice="true"' in resp.get_data(as_text=True)
    csp = resp.headers["Content-Security-Policy"]
    assert "connect-src 'self' https://api.openai.com;" in csp and "script-src 'self';" in csp and "default-src 'self'" in csp and "media-src 'self' blob:" in csp


def test_capabilities_list_only_the_provider_the_shared_controller_speaks(turned, provider, monkeypatch):
    _on(monkeypatch)
    body = turned.owner().get(f"{API}/mc/voice/capabilities").get_json()
    assert body["ok"] and [p["provider"] for p in body["providers"]] == ["openai"] and body["default_provider"] == "openai"


# ── the transcription credential ──
def test_bootstrap_mints_a_transcription_only_credential_for_this_owner(turned, provider, monkeypatch):
    _on(monkeypatch)
    client = turned.owner()
    token = turned.csrf(client)
    r = client.post(f"{API}/mc/voice/bootstrap", json={"provider": "openai"}, headers=write_headers(token))
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] and body["client_secret"] == "ek_test_secret" and body["warning_minutes"] == 13 and body["max_minutes"] == 15
    [call] = provider.minted
    assert call["transcription_prompt"] == V.TRANSCRIBE_ONLY_PROMPT and call["transcription_language"] == "en" and call["user_id_for_safety_identifier"] == "robert"
    assert "tools" not in call and "instructions" not in call
    assert "OPENAI_API_KEY" not in str(body)


def test_bootstrap_is_a_guarded_on_the_record_write(turned, provider, monkeypatch):
    _on(monkeypatch)
    client = turned.owner()
    token = turned.csrf(client)
    assert client.post(f"{API}/mc/voice/bootstrap", json={}).status_code == 403                                   # no token
    off = client.post(f"{API}/mc/voice/bootstrap", json={}, headers=write_headers(token, mode="off_record"))
    assert off.status_code == 409                                                                                  # Private: refused before any provider call
    assert not provider.minted


def test_bootstrap_is_rate_limited_and_reports_provider_trouble_plainly(turned, provider, monkeypatch):
    from core.realtime_voice.providers import openai_realtime
    _on(monkeypatch)
    client = turned.owner()
    token = turned.csrf(client)
    for _ in range(10):
        assert client.post(f"{API}/mc/voice/bootstrap", json={}, headers=write_headers(token)).status_code == 200
    assert client.post(f"{API}/mc/voice/bootstrap", json={}, headers=write_headers(token)).status_code == 429

    from core.realtime_voice.bootstrap import _rate_limit_state
    _rate_limit_state.clear()

    def fail(**kw):
        raise openai_realtime.OpenAIRealtimeError("provider said no")
    monkeypatch.setattr(openai_realtime, "mint_confer_transcription_credential", fail)
    r = client.post(f"{API}/mc/voice/bootstrap", json={}, headers=write_headers(token))
    assert r.status_code == 502 and r.get_json()["error"] == "provider said no"


# ── MC's own kept reply, read aloud ──
def _answered(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    note = _note(client, token, cid, "What is stuck on #12?")
    body = _ask(client, token, note["request_id"], cid).get_json()
    assert body["status"] == "answered"
    return client, cid, note, body["reply_note"]["text"]


def test_the_reply_that_is_read_aloud_is_exactly_the_kept_reply_from_the_servers_record(turned, provider, monkeypatch):
    _on(monkeypatch)
    client, cid, note, reply = _answered(turned)
    r = client.get(f"{API}/mc/voice/speech/{note['request_id']}?c={cid}")
    assert r.status_code == 200 and r.mimetype == "audio/mpeg" and r.headers["Cache-Control"] == "no-store" and r.data == b"ID3-stand-in-audio"
    [(spoken, user)] = provider.spoken
    assert spoken == V._speakable(reply) == "Item 12 is in build." and user == "robert"


def test_there_is_nothing_to_read_for_an_unanswered_unknown_or_foreign_note(turned, provider, monkeypatch):
    _on(monkeypatch)
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    unanswered = _note(client, token, cid, "Nobody asked MC yet")
    assert client.get(f"{API}/mc/voice/speech/{unanswered['request_id']}?c={cid}").status_code == 404
    assert client.get(f"{API}/mc/voice/speech/not-a-note-0001?c={cid}").status_code == 404
    assert client.get(f"{API}/mc/voice/speech/bad!id?c={cid}").status_code == 422
    assert not provider.spoken


def test_a_reply_is_spoken_as_plain_words_and_a_long_one_is_cut_with_a_pointer_to_the_chat():
    assert V._speakable("**Item 12** is [in build](http://x). `ok`\n\n```\ncode\n```\nDone.") == "Item 12 is in build. ok. (a code block, shown in the chat) Done."
    long = ("Sentence one is here. " * 400)
    out = V._speakable(long)
    assert len(out) <= V.SPEECH_MAX_CHARS + 40 and out.endswith("The rest is in the chat.")


# ── the shared controller and nothing else ──
def test_only_the_shared_controller_and_its_one_adapter_are_served_and_only_while_on(turned, provider, monkeypatch):
    _on(monkeypatch)
    client = turned.owner()
    ok = client.get(f"{API}/mc/voice/static/realtime-confer-controller.js")
    assert ok.status_code == 200 and "javascript" in ok.mimetype and b"ConferVoiceController" in ok.data
    assert client.get(f"{API}/mc/voice/static/adapters/openai-transcription-webrtc-adapter.js").status_code == 200
    for bad in ("adapters/xai-websocket-adapter.js", "../confer.py", "..%2Fconfer.py", "realtime-memo-controller.js"):
        assert client.get(f"{API}/mc/voice/static/{bad}").status_code == 404


# ── who may use it, and what a missing key looks like ──
def test_a_guest_and_a_signed_out_browser_get_nothing_and_cause_no_provider_call(turned, provider, monkeypatch):
    _on(monkeypatch)
    owner = turned.owner()
    cid = _new(owner, turned.csrf(owner))
    note = _note(owner, turned.csrf(owner), cid, "A question")
    _ask(owner, turned.csrf(owner), note["request_id"], cid)
    for who in (turned.guest(), turned.client()):                          # a guest (family tier) and a signed-out browser
        for method, path in (("get", "/mc/voice/capabilities"), ("get", f"/mc/voice/speech/{note['request_id']}?c={cid}"),
                             ("get", "/mc/voice/static/realtime-confer-controller.js"), ("post", "/mc/voice/bootstrap")):
            r = getattr(who, method)(f"{API}{path}")
            assert r.status_code in (401, 403), (path, r.status_code)
    assert not provider.minted and not provider.spoken


def test_another_conversation_or_a_note_that_is_not_yours_is_never_read_aloud(turned, provider, monkeypatch):
    _on(monkeypatch)
    client, cid, note, _reply = _answered(turned)
    other = _new(client, turned.csrf(client))                                # a second conversation of the same owner
    assert client.get(f"{API}/mc/voice/speech/{note['request_id']}?c={other}").status_code == 404
    assert client.get(f"{API}/mc/voice/speech/{note['request_id']}?c=c-000000000000").status_code in (404, 422)
    assert provider.spoken == []                                             # none of the wrong-conversation attempts reached the voice service
    assert client.get(f"{API}/mc/voice/speech/{note['request_id']}?c={cid}").status_code == 200       # the right conversation still works
    assert len(provider.spoken) == 1


def test_a_missing_key_is_a_bounded_error_with_no_key_in_it(turned, monkeypatch, tmp_path):
    import core.get_secret as secrets_module
    from core.realtime_voice.bootstrap import _rate_limit_state
    _rate_limit_state.clear()
    from voice_budget_helpers import install
    install(monkeypatch, tmp_path)                       # the real request path needs the shared voice budget ledger
    _on(monkeypatch)
    monkeypatch.setattr(secrets_module, "get_secret", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no secrets in tests")))
    import core.realtime_voice.providers.openai_realtime as rt
    monkeypatch.setattr(rt, "get_secret", secrets_module.get_secret)
    client = turned.owner()
    r = client.post(f"{API}/mc/voice/bootstrap", json={}, headers=write_headers(turned.csrf(client)))
    assert r.status_code == 502 and r.get_json() == {"ok": False, "error": "OpenAI API key not configured"}


def test_the_request_sent_to_the_provider_forbids_any_provider_response(turned, monkeypatch, tmp_path):
    """Not the wording: the actual session configuration the provider receives. create_response and interrupt_response are False, there are no tools and no assistant voice job."""
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
    r = client.post(f"{API}/mc/voice/bootstrap", json={"provider": "openai"}, headers=write_headers(turned.csrf(client)))
    body = r.get_json()
    assert r.status_code == 200 and body["client_secret"] == "ek_stand_in" and "sk-test-not-real" not in str(body)
    session = seen["json"]["session"]
    assert session["type"]=="transcription"
    assert session["audio"]["input"]["turn_detection"] is None
    assert "tools" not in session and "tool_choice" not in session and "instructions" not in session
    assert session["audio"]["input"]["transcription"]["model"]=="gpt-live-transcribe"
    assert session["audio"]["input"]["transcription"]["languages"]==["en"]
    assert seen["headers"]["OpenAI-Safety-Identifier"] and "robert" not in seen["headers"]["OpenAI-Safety-Identifier"]       # hashed, not the name
