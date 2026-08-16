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
from core.realtime_voice.providers import openai_realtime, xai_voice
from core.realtime_voice.standards import conversation_turn_detection


_DEFAULT_WARNING_MINUTES = 13
_DEFAULT_MAX_MINUTES = 15
_COS_REALTIME_TOOLS = [
    {
        "type": "function",
        "name": "consult_cos_agent",
        "description": (
            "Consult COS Agent A for current facts, web research, MinimoI "
            "state, stored context, or substantive Chief of Staff judgment."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "The complete request for COS Agent A.",
                },
            },
            "required": ["request"],
        },
    },
    {
        "type": "function",
        "name": "save_cos_note",
        "description": "Save an explicit note through the platform-owned path.",
        "parameters": {
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "description": (
                        "The note content itself, preserving Robert's meaning. "
                        "Do not prefix it with 'User asked', 'Robert asked', "
                        "or other third-person narration unless he explicitly "
                        "dictated that wording."
                    ),
                },
            },
            "required": ["note"],
        },
    },
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


def create_confer_voice_blueprint(
    *,
    locale: str = "en-US",
    build_voice_instructions=None,
) -> Blueprint:
    """Create the provider-neutral Confer voice bootstrap routes.

    The Realtime model owns low-latency speech and barge-in. Platform-owned
    function tools bridge current facts, durable context, and writes to COS
    Agent A without exposing credentials or unrestricted browser authority.
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
            if build_voice_instructions is None:
                raise ProviderUnavailableError(
                    "COS realtime voice instructions are unavailable"
                )
            instructions = (
                f"{build_voice_instructions(str(user_id)).rstrip()}\n\n"
                f"Session facts: the active voice provider is "
                f"{capability.label} ({capability.provider}). When Robert asks, "
                "state that exact provider plainly. Stopping voice ends the "
                "microphone and realtime-provider session, then adds the "
                "completed transcript to Confer; typed Confer remains available. "
                "Typed and voice inputs may coexist in the page, but they are "
                "separate live channels, so recommend using one at a time to "
                "avoid overlapping replies."
            )
            if capability.provider == "openai":
                credential = openai_realtime.mint_ephemeral_credential(
                    instructions=instructions,
                    voice="cedar",
                    turn_detection=conversation_turn_detection("openai"),
                    transcription_language="en",
                    user_id_for_safety_identifier=str(user_id),
                    tools=_COS_REALTIME_TOOLS,
                    tool_choice="auto",
                )
            elif capability.provider == "xai":
                credential = xai_voice.mint_ephemeral_credential(
                    instructions=instructions,
                    voice="eve",
                    turn_detection=conversation_turn_detection("xai"),
                    transcription_language="en-US",
                    tools=_COS_REALTIME_TOOLS,
                    tool_choice="auto",
                )
            else:
                raise ProviderUnavailableError(
                    f"{capability.provider} Confer transport is not implemented"
                )
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        except ProviderUnavailableError as error:
            return jsonify({"ok": False, "error": str(error)}), 503
        except (openai_realtime.OpenAIRealtimeError, xai_voice.XAIVoiceError) as error:
            return jsonify({"ok": False, "error": str(error)}), 502

        guard = _duration_guard()
        return jsonify({
            "ok": True,
            **credential,
            "warning_minutes": guard.warning_minutes,
            "max_minutes": guard.max_minutes,
        })

    return blueprint
