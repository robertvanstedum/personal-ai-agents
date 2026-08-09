from unittest.mock import Mock, patch

from core.realtime_voice.providers import openai_realtime, xai_voice


def _response(payload):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def test_openai_ephemeral_session_requests_input_transcription():
    with (
        patch("core.realtime_voice.providers.openai_realtime.get_secret", return_value="secret"),
        patch(
            "core.realtime_voice.providers.openai_realtime.requests.post",
            return_value=_response({"value": "ephemeral", "expires_at": 123}),
        ) as post,
    ):
        openai_realtime.mint_ephemeral_credential(
            instructions="instructions",
            voice="cedar",
            turn_detection={"type": "server_vad"},
            transcription_language="de",
            user_id_for_safety_identifier="3",
        )

    transcription = post.call_args.kwargs["json"]["session"]["audio"]["input"]["transcription"]
    assert transcription == {"model": "gpt-4o-transcribe", "language": "de"}


def test_openai_memo_uses_transcription_only_session_without_turn_detection():
    with (
        patch("core.realtime_voice.providers.openai_realtime.get_secret", return_value="secret"),
        patch(
            "core.realtime_voice.providers.openai_realtime.requests.post",
            return_value=_response({"value": "ephemeral", "expires_at": 123}),
        ) as post,
    ):
        result = openai_realtime.mint_transcription_credential(
            transcription_language="pt",
            user_id_for_safety_identifier="42",
        )

    session = post.call_args.kwargs["json"]["session"]
    assert session["type"] == "transcription"
    assert session["audio"]["input"]["turn_detection"] is None
    assert session["audio"]["input"]["transcription"] == {
        "model": "gpt-live-transcribe",
        "languages": ["pt"],
        "delay": "low",
    }
    assert result == {
        "provider": "openai",
        "client_secret": "ephemeral",
        "expires_at": 123,
        "model": "gpt-live-transcribe",
        "transport": "webrtc",
    }


def test_openai_confer_uses_transcription_only_session_with_server_vad():
    with (
        patch("core.realtime_voice.providers.openai_realtime.get_secret", return_value="secret"),
        patch(
            "core.realtime_voice.providers.openai_realtime.requests.post",
            return_value=_response({"value": "ephemeral", "expires_at": 123}),
        ) as post,
    ):
        openai_realtime.mint_confer_transcription_credential(
            transcription_language="en",
            user_id_for_safety_identifier="42",
        )

    session = post.call_args.kwargs["json"]["session"]
    assert session["type"] == "transcription"
    assert session["audio"]["input"]["turn_detection"] == {
        "type": "server_vad",
        "threshold": 0.5,
        "prefix_padding_ms": 300,
        "silence_duration_ms": 700,
    }


def test_xai_session_config_requests_input_transcription():
    with (
        patch("core.realtime_voice.providers.xai_voice.get_secret", return_value="secret"),
        patch(
            "core.realtime_voice.providers.xai_voice.requests.post",
            return_value=_response({"token": "ephemeral"}),
        ),
    ):
        result = xai_voice.mint_ephemeral_credential(
            instructions="instructions",
            voice="rex",
            turn_detection={"type": "server_vad"},
            transcription_language="pt-BR",
        )

    transcription = result["session_config"]["audio"]["input"]["transcription"]
    assert transcription == {"model": "grok-transcribe", "language_hint": "pt-BR"}
