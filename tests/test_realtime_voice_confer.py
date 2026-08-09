from unittest.mock import Mock, patch

from flask import Flask

from core.realtime_voice.bootstrap import _rate_limit_state
from core.realtime_voice.confer import create_confer_voice_blueprint
from core.realtime_voice.providers import openai_speech
from domains.cos.spoken_reply_store import SpokenReplyStore


def _client():
    _rate_limit_state.clear()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(create_confer_voice_blueprint())
    return app.test_client()


def test_confer_capabilities_require_identity():
    response = _client().get("/api/realtime-voice/confer/capabilities")
    assert response.status_code == 401


def test_confer_capabilities_advertise_only_secure_chained_provider():
    response = _client().get(
        "/api/realtime-voice/confer/capabilities",
        headers={"X-Minimoi-Auth-Id": "42"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["mode"] == "agent_conversation"
    assert data["default_provider"] == "openai"
    assert [provider["provider"] for provider in data["providers"]] == ["openai"]


def test_confer_bootstrap_mints_transcription_only_credential():
    with patch(
        "core.realtime_voice.confer.openai_realtime.mint_confer_transcription_credential",
        return_value={
            "provider": "openai",
            "client_secret": "ephemeral",
            "model": "gpt-live-transcribe",
            "transport": "webrtc",
        },
    ) as mint:
        response = _client().post(
            "/api/realtime-voice/confer/bootstrap",
            json={"provider": "openai"},
            headers={"X-Minimoi-Auth-Id": "42"},
        )
    assert response.status_code == 200
    assert response.get_json()["model"] == "gpt-live-transcribe"
    mint.assert_called_once_with(
        transcription_language="en",
        user_id_for_safety_identifier="42",
    )


def test_confer_bootstrap_rejects_xai_until_proxy_exists():
    response = _client().post(
        "/api/realtime-voice/confer/bootstrap",
        json={"provider": "xai"},
        headers={"X-Minimoi-Auth-Id": "43"},
    )
    assert response.status_code == 503
    assert "WebSocket proxy" in response.get_json()["error"]


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
