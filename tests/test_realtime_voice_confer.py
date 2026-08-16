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


def test_confer_capabilities_advertise_realtime_provider_choices():
    response = _client().get(
        "/api/realtime-voice/confer/capabilities",
        headers={"X-Minimoi-Auth-Id": "42"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["mode"] == "agent_conversation"
    assert data["default_provider"] == "openai"
    assert [provider["provider"] for provider in data["providers"]] == ["openai", "xai"]


def test_confer_bootstrap_mints_openai_realtime_conversation_credential():
    with patch(
        "core.realtime_voice.confer.openai_realtime.mint_ephemeral_credential",
        return_value={
            "provider": "openai",
            "client_secret": "ephemeral",
            "model": "gpt-realtime-2.1",
        },
    ) as mint:
        response = _client().post(
            "/api/realtime-voice/confer/bootstrap",
            json={"provider": "openai"},
            headers={"X-Minimoi-Auth-Id": "42"},
        )
    assert response.status_code == 200
    assert response.get_json()["model"] == "gpt-realtime-2.1"
    kwargs = mint.call_args.kwargs
    assert kwargs["instructions"].startswith("COS voice for 42\n\nSession facts:")
    assert "active voice provider is OpenAI (openai)" in kwargs["instructions"]
    assert "completed transcript to Confer" in kwargs["instructions"]
    assert kwargs["voice"] == "cedar"
    assert kwargs["user_id_for_safety_identifier"] == "42"
    assert kwargs["tool_choice"] == "auto"
    assert [tool["name"] for tool in kwargs["tools"]] == [
        "consult_cos_agent", "save_cos_note",
    ]
    note_tool = kwargs["tools"][1]
    assert "Do not prefix it with 'User asked'" in (
        note_tool["parameters"]["properties"]["note"]["description"]
    )


def test_confer_bootstrap_returns_configured_session_duration(monkeypatch):
    monkeypatch.setenv("VOICE_CONFER_WARNING_MINUTES", "18")
    monkeypatch.setenv("VOICE_CONFER_MAX_MINUTES", "20")
    with patch(
        "core.realtime_voice.confer.openai_realtime.mint_ephemeral_credential",
        return_value={"provider": "openai", "client_secret": "ephemeral"},
    ) as mint:
        response = _client().post(
            "/api/realtime-voice/confer/bootstrap",
            json={"provider": "openai"},
            headers={"X-Minimoi-Auth-Id": "42"},
        )

    assert response.status_code == 200
    assert response.get_json()["max_minutes"] == 20
    assert mint.call_count == 1


def test_confer_bootstrap_mints_xai_realtime_conversation_credential():
    with patch(
        "core.realtime_voice.confer.xai_voice.mint_ephemeral_credential",
        return_value={
            "provider": "xai",
            "ephemeral_token": "ephemeral",
            "model": "grok-voice-latest",
            "session_config": {},
        },
    ) as mint:
        response = _client().post(
            "/api/realtime-voice/confer/bootstrap",
            json={"provider": "xai"},
            headers={"X-Minimoi-Auth-Id": "43"},
        )
    assert response.status_code == 200
    assert response.get_json()["model"] == "grok-voice-latest"
    assert mint.call_args.kwargs["instructions"].startswith(
        "COS voice for 43\n\nSession facts:"
    )
    assert "active voice provider is Grok (xai)" in mint.call_args.kwargs["instructions"]
    assert mint.call_args.kwargs["tool_choice"] == "auto"


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
