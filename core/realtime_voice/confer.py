"""Authenticated bootstrap for chained agent-conversation voice mode."""

import os

from flask import Blueprint, jsonify, request

from core.identity import resolve_user_id
from core.realtime_voice.bootstrap import check_voice_rate_limit
from core.realtime_voice.capabilities import (
    AGENT_CONVERSATION_MODE,
    ProviderUnavailableError,
    providers_for_mode,
    resolve_agent_conversation_provider,
)
from core.realtime_voice.duration_guard import DurationGuard
from core.realtime_voice.providers import openai_realtime


_DEFAULT_WARNING_MINUTES = 13
_DEFAULT_MAX_MINUTES = 15
_CONFER_TRANSCRIPTION_PROMPT = (
    "English-language personal Chief of Staff conversation. Transcribe the "
    "speaker verbatim with natural punctuation; preserve product and domain "
    "names and do not answer, summarize, or add language labels."
)
_CONFER_TRANSCRIPTION_KEYWORDS = [
    "mini-moi", "Curator", "Guild", "Mein Deutsch", "Meu Português",
    "Chief of Staff", "CoS", "OpenClaw", "Grok",
]


def _duration_guard() -> DurationGuard:
    return DurationGuard(
        warning_minutes=int(os.environ.get(
            "VOICE_CONFER_WARNING_MINUTES", _DEFAULT_WARNING_MINUTES
        )),
        max_minutes=int(os.environ.get(
            "VOICE_CONFER_MAX_MINUTES", _DEFAULT_MAX_MINUTES
        )),
    )


def create_confer_voice_blueprint(*, locale: str = "en-US") -> Blueprint:
    """Create the provider-neutral Confer voice bootstrap routes.

    Agent reasoning is intentionally absent. These routes only select an
    available speech provider and mint a short-lived transcription credential.
    """
    blueprint = Blueprint("realtime_voice_confer", __name__)

    @blueprint.route("/api/realtime-voice/confer/capabilities", methods=["GET"])
    def capabilities():
        user_id = resolve_user_id(request)
        if user_id is None:
            return jsonify({"ok": False, "error": "identity required"}), 401

        providers = providers_for_mode(AGENT_CONVERSATION_MODE, locale)
        default = resolve_agent_conversation_provider(None, locale)
        return jsonify({
            "ok": True,
            "mode": AGENT_CONVERSATION_MODE,
            "default_provider": default.provider,
            "providers": [provider.public_dict() for provider in providers],
        })

    @blueprint.route("/api/realtime-voice/confer/bootstrap", methods=["POST"])
    def bootstrap():
        user_id = resolve_user_id(request)
        if user_id is None:
            return jsonify({"ok": False, "error": "identity required"}), 401
        if not check_voice_rate_limit(user_id):
            return jsonify({"ok": False, "error": "rate limited"}), 429

        body = request.get_json(silent=True) or {}
        requested_provider = body.get("provider")
        try:
            capability = resolve_agent_conversation_provider(
                requested_provider, locale
            )
            if capability.provider != "openai":
                raise ProviderUnavailableError(
                    f"{capability.provider} Confer transport is not implemented"
                )
            credential = openai_realtime.mint_confer_transcription_credential(
                transcription_language="en",
                user_id_for_safety_identifier=str(user_id),
                transcription_prompt=_CONFER_TRANSCRIPTION_PROMPT,
                transcription_keywords=_CONFER_TRANSCRIPTION_KEYWORDS,
            )
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        except ProviderUnavailableError as error:
            return jsonify({"ok": False, "error": str(error)}), 503
        except openai_realtime.OpenAIRealtimeError as error:
            return jsonify({"ok": False, "error": str(error)}), 502

        guard = _duration_guard()
        return jsonify({
            "ok": True,
            **credential,
            "warning_minutes": guard.warning_minutes,
            "max_minutes": guard.max_minutes,
        })

    return blueprint
