"""
core/realtime_voice/providers/openai_realtime.py — server-side ephemeral
client secret minting for OpenAI's Realtime API (WebRTC).

Per the approved architecture (Section 2): browser connects to OpenAI
directly via WebRTC using this ephemeral secret. minimoi's backend never
proxies audio -- its only job is minting the short-lived credential with
the session's instructions baked in server-side.

Endpoint/request/response shape verified 2026-07-24 against
https://developers.openai.com/api/docs/guides/realtime-webrtc.
"""
import requests

from core.get_secret import get_secret

_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
_DEFAULT_MODEL = "gpt-realtime-2.1"
_DEFAULT_TRANSCRIPTION_MODEL = "gpt-live-transcribe"
_REQUEST_TIMEOUT_SECONDS = 10


class OpenAIRealtimeError(Exception):
    pass


def mint_transcription_credential(
    *,
    transcription_language: str,
    user_id_for_safety_identifier: str,
    transcription_prompt: str | None = None,
    transcription_keywords: list[str] | None = None,
) -> dict:
    """Mint a browser-safe credential for a transcription-only session.

    Memo mode deliberately uses ``type=transcription`` rather than a muted
    voice-agent session. Automatic turn detection is disabled so silence does
    not close or commit the memo; the browser explicitly commits when Robert
    taps stop.
    """
    return _mint_transcription_credential(
        transcription_language=transcription_language,
        user_id_for_safety_identifier=user_id_for_safety_identifier,
        turn_detection=None,
        transcription_delay="medium",
        transcription_prompt=transcription_prompt,
        transcription_keywords=transcription_keywords,
    )


def mint_confer_transcription_credential(
    *,
    transcription_language: str,
    user_id_for_safety_identifier: str,
    transcription_prompt: str | None = None,
    transcription_keywords: list[str] | None = None,
) -> dict:
    """Mint a transcription-only session for chained CoS conversation.

    ``gpt-live-transcribe`` currently rejects server turn detection on client
    secrets. The browser adapter therefore detects speech/silence locally and
    explicitly commits each audio turn. The transcription model still only
    transcribes; CoS reasoning remains behind ``call_backend``.
    """
    return _mint_transcription_credential(
        transcription_language=transcription_language,
        user_id_for_safety_identifier=user_id_for_safety_identifier,
        turn_detection=None,
        transcription_delay="low",
        transcription_prompt=transcription_prompt,
        transcription_keywords=transcription_keywords,
    )


def _mint_transcription_credential(
    *,
    transcription_language: str,
    user_id_for_safety_identifier: str,
    turn_detection: dict | None,
    transcription_delay: str,
    transcription_prompt: str | None,
    transcription_keywords: list[str] | None,
) -> dict:
    api_key = get_secret("OPENAI_API_KEY", "openai", "api_key")
    if not api_key:
        raise OpenAIRealtimeError("OpenAI API key not configured")

    transcription = {
        "model": _DEFAULT_TRANSCRIPTION_MODEL,
        "languages": [transcription_language],
        "delay": transcription_delay,
    }
    if transcription_prompt:
        transcription["prompt"] = transcription_prompt
    if transcription_keywords:
        transcription["keywords"] = transcription_keywords

    payload = {
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "transcription": transcription,
                    "turn_detection": turn_detection,
                },
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "OpenAI-Safety-Identifier": _hash_user_id(user_id_for_safety_identifier),
    }

    try:
        resp = requests.post(
            _CLIENT_SECRETS_URL,
            json=payload,
            headers=headers,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise OpenAIRealtimeError(f"OpenAI transcription credential request failed: {e}")

    data = resp.json()
    client_secret = data.get("value")
    if not client_secret:
        raise OpenAIRealtimeError("OpenAI response missing client secret value")

    return {
        "provider": "openai",
        "client_secret": client_secret,
        "expires_at": data.get("expires_at"),
        "model": _DEFAULT_TRANSCRIPTION_MODEL,
        "transport": "webrtc",
    }


def mint_ephemeral_credential(
    *,
    instructions: str,
    voice: str,
    turn_detection: dict,
    transcription_language: str,
    user_id_for_safety_identifier: str,
) -> dict:
    """Requests a short-lived client secret from OpenAI, with the session's
    instructions and turn-detection config already attached server-side --
    the browser never sends or sees raw instructions.

    Returns the minimum connection material the browser adapter needs:
    {"client_secret": ..., "expires_at": ..., "model": ...}. Never returns
    the long-lived OPENAI_API_KEY.
    """
    api_key = get_secret("OPENAI_API_KEY", "openai", "api_key")
    if not api_key:
        raise OpenAIRealtimeError("OpenAI API key not configured")

    payload = {
        "session": {
            "type": "realtime",
            "model": _DEFAULT_MODEL,
            "instructions": instructions,
            "audio": {
                "output": {"voice": voice},
                "input": {
                    "turn_detection": turn_detection,
                    "transcription": {
                        "model": "gpt-4o-transcribe",
                        "language": transcription_language,
                    },
                },
            },
        }
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # Hashed, not the raw user id -- OpenAI's own recommended pattern
        # for the safety-identifier header, avoids sending an internal ID
        # verbatim to a third party.
        "OpenAI-Safety-Identifier": _hash_user_id(user_id_for_safety_identifier),
    }

    try:
        resp = requests.post(
            _CLIENT_SECRETS_URL, json=payload, headers=headers,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise OpenAIRealtimeError(f"OpenAI ephemeral credential request failed: {e}")

    data = resp.json()
    client_secret = data.get("value")
    if not client_secret:
        raise OpenAIRealtimeError("OpenAI response missing client secret value")

    return {
        "provider": "openai",
        "client_secret": client_secret,
        "expires_at": data.get("expires_at"),
        "model": _DEFAULT_MODEL,
    }


def _hash_user_id(user_id: str) -> str:
    import hashlib
    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()
