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
