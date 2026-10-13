from unittest.mock import Mock, patch

import pytest
from flask import Flask

from core.realtime_voice.bootstrap import _rate_limit_state
from core.realtime_voice.confer import create_confer_voice_blueprint
from core.realtime_voice.providers import openai_speech
from domains.cos.spoken_reply_store import SpokenReplyStore


def _client():
    _rate_limit_state.clear()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(create_confer_voice_blueprint(
        build_voice_instructions=lambda user_id: f"COS voice for {user_id}",
    ))
    return app.test_client()


def test_confer_capabilities_require_identity():
    response = _client().get("/api/realtime-voice/confer/capabilities")
    assert response.status_code == 401


@pytest.fixture(autouse=True)
def voice_ledger(monkeypatch,tmp_path):
    from core.realtime_voice import budget
    monkeypatch.setenv(budget.ENV_DSN,f"sqlite:///{tmp_path / 'voice.db'}")
    monkeypatch.setenv("MINIMOI_LIMITS_DIR",str(tmp_path / 'limits'))
    monkeypatch.setattr(budget,"_default",None)
    ledger=budget.default_budget()
    ledger.ensure_schema()
    return ledger


def test_confer_capabilities_offer_transcription_for_the_selected_agent():
    response=_client().get("/api/realtime-voice/confer/capabilities",headers={"X-Minimoi-Auth-Id":"42"})
    assert response.status_code==200
    data=response.get_json()
    assert data["mode"]=="agent_conversation" and data["default_provider"]=="openai"
    assert [p["provider"] for p in data["providers"]]==["openai"]
    assert data["budget"]["cap_usd"]==20.0


def test_confer_bootstrap_transcribes_instead_of_minting_a_second_assistant(voice_ledger):
    with patch("core.realtime_voice.confer.openai_realtime.mint_confer_transcription_credential",return_value={"provider":"openai","client_secret":"ephemeral","model":"gpt-live-transcribe"}) as mint:
        response=_client().post("/api/realtime-voice/confer/bootstrap",json={"provider":"openai"},headers={"X-Minimoi-Auth-Id":"42"})
    assert response.status_code==200 and response.get_json()["model"]=="gpt-live-transcribe"
    assert mint.call_args.kwargs["user_id_for_safety_identifier"]=="42"
    assert mint.call_args.kwargs["transcription_language"]=="en"
    assert voice_ledger.status()["reserved_usd"]==1.02


def test_confer_bootstrap_returns_configured_session_duration(monkeypatch):
    monkeypatch.setenv("VOICE_CONFER_WARNING_MINUTES","18")
    monkeypatch.setenv("VOICE_CONFER_MAX_MINUTES","20")
    with patch("core.realtime_voice.confer.openai_realtime.mint_confer_transcription_credential",return_value={"provider":"openai","client_secret":"ephemeral"}) as mint:
        response=_client().post("/api/realtime-voice/confer/bootstrap",json={"provider":"openai"},headers={"X-Minimoi-Auth-Id":"42"})
    assert response.status_code==200 and response.get_json()["max_minutes"]==20 and mint.call_count==1


def test_confer_refuses_a_separate_speech_assistant_before_provider_contact():
    with patch("core.realtime_voice.confer.openai_realtime.mint_confer_transcription_credential") as mint:
        response=_client().post("/api/realtime-voice/confer/bootstrap",json={"provider":"xai"},headers={"X-Minimoi-Auth-Id":"42"})
    assert response.status_code==400 and not mint.called


def test_confer_requires_the_shared_allowance_and_refuses_before_provider(voice_ledger):
    from core.realtime_voice import budget
    voice_ledger.reserve(kind="tts",domain="mc",principal="owner",amount_micro=budget.micro(19.0))
    with patch("core.realtime_voice.confer.openai_realtime.mint_confer_transcription_credential") as mint:
        response=_client().post("/api/realtime-voice/confer/bootstrap",json={"provider":"openai"},headers={"X-Minimoi-Auth-Id":"42"})
    assert response.status_code==429 and "used up" in response.get_json()["error"] and not mint.called


def test_confer_refuses_an_unavailable_ledger_before_provider(monkeypatch,tmp_path):
    from core.realtime_voice import budget
    monkeypatch.setenv(budget.ENV_DSN,f"sqlite:///{tmp_path / 'not_initialized.db'}")
    monkeypatch.setattr(budget,"_default",None)
    with patch("core.realtime_voice.confer.openai_realtime.mint_confer_transcription_credential") as mint:
        response=_client().post("/api/realtime-voice/confer/bootstrap",json={"provider":"openai"},headers={"X-Minimoi-Auth-Id":"42"})
    assert response.status_code==503 and not mint.called


def test_spoken_reply_store_is_user_scoped_and_bounded(monkeypatch):
    clock = iter([10.0, 10.0, 10.0, 10.0, 14.0])
    monkeypatch.setattr("domains.cos.spoken_reply_store.time.monotonic", lambda: next(clock))
    store = SpokenReplyStore(ttl_seconds=3, max_entries=1)
    store.put("turn-1", text="first", provider="openai", user_id="7")
    assert store.get("turn-1", user_id="8") is None
    store.put("turn-2", text="second", provider="openai", user_id="7")
    assert store.get("turn-1", user_id="7") is None
    assert store.get("turn-2", user_id="7") is None


def test_openai_speech_stream_uses_exact_server_owned_reply():
    provider_response = Mock()
    with (
        patch.object(openai_speech, "get_secret", return_value="secret"),
        patch.object(openai_speech.requests, "post", return_value=provider_response) as post,
    ):
        result = openai_speech.create_speech_stream(
            text="Canonical CoS reply.",
            user_id="42",
        )
    assert result is provider_response
    assert post.call_args.kwargs["json"]["input"] == "Canonical CoS reply."
    assert post.call_args.kwargs["json"]["model"] == "gpt-4o-mini-tts"
    assert post.call_args.kwargs["stream"] is True


def test_openai_speech_translates_secret_store_failure():
    with patch.object(
        openai_speech,
        "get_secret",
        side_effect=RuntimeError("secret backend unavailable"),
    ):
        with pytest.raises(
            openai_speech.OpenAISpeechError,
            match="OpenAI API key not configured",
        ):
            openai_speech.create_speech_stream(text="Reply.", user_id="42")
