"""Server-side OpenAI speech generation for canonical agent replies."""

import hashlib

import requests

from core.get_secret import get_secret


_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
_MODEL = "gpt-4o-mini-tts"
_VOICE = "cedar"
_TIMEOUT_SECONDS = 45
DEFAULT_INSTRUCTIONS = (
    "Speak naturally and clearly as an AI Chief of Staff. "
    "Do not add, omit, or paraphrase content."
)


class OpenAISpeechError(RuntimeError):
    """``status_code`` is the provider's HTTP status when it answered (None for a timeout or dropped connection); the voice budget releases an allowance only for a 4xx."""

    def __init__(self, message: str = "", status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _require_api_key() -> str:
    """Keep credential lookup failures inside the speech-provider contract."""
    try:
        api_key = get_secret("OPENAI_API_KEY", "openai", "api_key")
    except RuntimeError as error:
        raise OpenAISpeechError("OpenAI API key not configured") from error
    if not api_key:
        raise OpenAISpeechError("OpenAI API key not configured")
    return api_key


def create_speech_stream(*, text: str, user_id: str, instructions: str = DEFAULT_INSTRUCTIONS):
    """Read server-owned reply text; the host may supply its own speaking style.

    Instructions are host configuration, never a browser/model-supplied field.
    Existing Chief of Staff callers keep the same default, model and voice.
    """
    api_key = _require_api_key()

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
        "instructions": instructions,
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
        raise OpenAISpeechError(f"OpenAI speech request failed: {error}",
                                getattr(getattr(error, "response", None), "status_code", None))
    return response
