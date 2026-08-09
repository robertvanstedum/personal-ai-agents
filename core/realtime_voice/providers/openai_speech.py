"""Server-side OpenAI speech generation for canonical agent replies."""

import hashlib

import requests

from core.get_secret import get_secret


_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
_MODEL = "gpt-4o-mini-tts"
_VOICE = "cedar"
_TIMEOUT_SECONDS = 45


class OpenAISpeechError(RuntimeError):
    pass


def create_speech_stream(*, text: str, user_id: str):
    """Return a streaming provider response for server-owned reply text."""
    api_key = get_secret("OPENAI_API_KEY", "openai", "api_key")
    if not api_key:
        raise OpenAISpeechError("OpenAI API key not configured")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "OpenAI-Safety-Identifier": hashlib.sha256(
            str(user_id).encode("utf-8")
        ).hexdigest(),
    }
    payload = {
        "model": _MODEL,
        "voice": _VOICE,
        "input": text,
        "instructions": (
            "Speak naturally and clearly as an AI Chief of Staff. "
            "Do not add, omit, or paraphrase content."
        ),
        "response_format": "mp3",
    }
    try:
        response = requests.post(
            _SPEECH_URL,
            headers=headers,
            json=payload,
            stream=True,
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise OpenAISpeechError(f"OpenAI speech request failed: {error}")
    return response
