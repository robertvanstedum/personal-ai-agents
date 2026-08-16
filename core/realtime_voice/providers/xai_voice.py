"""
core/realtime_voice/providers/xai_voice.py — server-side ephemeral token
minting for xAI's Grok Voice Agent API (WebSocket).

Per the approved architecture (Section 2): browser connects to xAI directly
via WebSocket using this ephemeral token. No mini-moi WebRTC relay for xAI
in this release (their published relay example is explicitly not
production-ready without further hardening).

Endpoint/request/response shape verified 2026-07-24 against
https://docs.x.ai/developers/model-capabilities/audio/ephemeral-tokens and
https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech.

Session configuration (instructions, voice, turn_detection) is sent by the
browser adapter as the first `session.update` message after the WebSocket
opens -- xAI's ephemeral-token endpoint does not accept session config at
mint time the way OpenAI's does. This module still builds and returns that
config from server-side data so the browser never constructs it itself.
"""
import requests

from core.get_secret import get_secret

_CLIENT_SECRETS_URL = "https://api.x.ai/v1/realtime/client_secrets"
_DEFAULT_MODEL = "grok-voice-latest"
_TOKEN_EXPIRES_AFTER_SECONDS = 300
_REQUEST_TIMEOUT_SECONDS = 10


class XAIVoiceError(Exception):
    pass


def mint_ephemeral_credential(
    *,
    instructions: str,
    voice: str,
    turn_detection: dict,
    transcription_language: str,
    tools: list[dict] | None = None,
    tool_choice: str | None = None,
) -> dict:
    """Requests a short-lived token from xAI. Returns the minimum
    connection material the browser adapter needs, plus the session config
    it must send as its first session.update message (built server-side,
    the browser does not construct it) -- the ephemeral token itself never
    exposes the long-lived XAI_API_KEY.
    """
    api_key = get_secret("XAI_API_KEY", "xai", "api_key")
    if not api_key:
        raise XAIVoiceError("xAI API key not configured")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"expires_after": {"seconds": _TOKEN_EXPIRES_AFTER_SECONDS}}

    try:
        resp = requests.post(
            _CLIENT_SECRETS_URL, json=payload, headers=headers,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise XAIVoiceError(f"xAI ephemeral token request failed: {e}")

    data = resp.json()
    token = data.get("token") or data.get("value")
    if not token:
        raise XAIVoiceError("xAI response missing ephemeral token")

    session_config = {
        "voice": voice,
        "instructions": instructions,
        "turn_detection": turn_detection,
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "transcription": {
                    "model": "grok-transcribe",
                    "language_hint": transcription_language,
                },
            },
            "output": {"format": {"type": "audio/pcm", "rate": 24000}},
        },
    }
    if tools:
        session_config["tools"] = tools
        session_config["tool_choice"] = tool_choice or "auto"

    return {
        "provider": "xai",
        "ephemeral_token": token,
        "expires_after_seconds": _TOKEN_EXPIRES_AFTER_SECONDS,
        "model": _DEFAULT_MODEL,
        "session_config": session_config,
    }
