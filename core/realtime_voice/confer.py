"""Authenticated bootstrap for Confer (CoS) realtime voice.

Every bootstrap logs one outcome line, like Gespräche and Conversas
(``[realtime_voice] domain=cos … outcome=…``), with the size of the
server-built instructions when a session starts. No prompt content is logged.
"""

import os

from flask import Blueprint, jsonify, request

from core.identity import resolve_user_id
from core.realtime_voice.bootstrap import _log_outcome, check_voice_rate_limit, log_session_outcome
from core.realtime_voice.capabilities import (
    AGENT_CONVERSATION_MODE,
    ProviderUnavailableError,
    providers_for_mode,
    resolve_agent_conversation_provider,
)
from core.realtime_voice.duration_guard import DurationGuard
from core.realtime_voice.providers import openai_realtime, xai_voice
from core.realtime_voice.standards import conversation_turn_detection


_DOMAIN = "cos"
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
    session_context=None,
) -> Blueprint:
    """Create the provider-neutral Confer voice bootstrap routes.

    The Realtime model owns low-latency speech and barge-in. Platform-owned
    function tools bridge current facts, durable context, and writes to COS
    Agent A without exposing credentials or unrestricted browser authority.

    session_context: optional ``fn(user_id) -> dict`` whose result the
    bootstrap hands the page as ``session_context`` (CoS: Private mode and its
    epoch at the start of the session). It holds no secret.
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
            _log_outcome(_DOMAIN, None, None, None, "identity_required")
            return jsonify({"ok": False, "error": "identity required"}), 401
        if not check_voice_rate_limit(user_id):
            _log_outcome(_DOMAIN, user_id, None, None, "rate_limited")
            return jsonify({"ok": False, "error": "rate limited"}), 429

        body = request.get_json(silent=True) or {}
        requested_provider = body.get("provider")
        provider = requested_provider if isinstance(requested_provider, str) else None
        instructions = ""
        try:
            capability = resolve_agent_conversation_provider(
                requested_provider, locale
            )
            provider = capability.provider
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
                "completed transcript to Confer and, unless Robert is in "
                "Private mode, to his CoS history; typed Confer remains available. "
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
            _log_outcome(_DOMAIN, user_id, provider, None, "invalid_provider")
            return jsonify({"ok": False, "error": str(error)}), 400
        except ProviderUnavailableError as error:
            _log_outcome(_DOMAIN, user_id, provider, None, "provider_unavailable")
            return jsonify({"ok": False, "error": str(error)}), 503
        except (openai_realtime.OpenAIRealtimeError, xai_voice.XAIVoiceError) as error:
            _log_outcome(_DOMAIN, user_id, provider, None, "provider_error",
                         instructions_chars=len(instructions))
            return jsonify({"ok": False, "error": str(error)}), 502
        except Exception:
            _log_outcome(_DOMAIN, user_id, provider, None, "bootstrap_failed")
            raise

        _log_outcome(_DOMAIN, user_id, provider, credential.get("model"), "started",
                     instructions_chars=len(instructions))
        guard = _duration_guard()
        payload = {
            "ok": True,
            **credential,
            "warning_minutes": guard.warning_minutes,
            "max_minutes": guard.max_minutes,
        }
        if session_context is not None:
            payload["session_context"] = session_context(str(user_id))
        return jsonify(payload)

    @blueprint.route("/api/realtime-voice/confer/outcome", methods=["POST"])
    def outcome():
        return log_session_outcome(_DOMAIN)

    return blueprint
