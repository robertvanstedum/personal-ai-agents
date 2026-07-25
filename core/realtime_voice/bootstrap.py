"""
core/realtime_voice/bootstrap.py — authenticated, allow-listed realtime
voice session bootstrap endpoint. Shared by German and Portuguese; each
domain registers its own blueprint instance via create_bootstrap_blueprint,
supplying only its own persona lookup and locale (Section 5's "each domain
supplies" list).

Per _working/CLAUDE_CODE_BUILD_SPEC_voice_realtime_2026-07-24.md Section 6:
  - verify signed-in identity + permitted domain
  - accept only allow-listed provider, locale, persona, scene, voice values
  - construct instructions server-side from repository data
  - load the long-lived provider key only on the server (providers/*.py)
  - request a short-lived provider credential
  - return only the minimum connection material required by the adapter
  - rate-limit session starts per user
  - log user_id/domain/provider/model/start time/outcome, never credentials,
    raw audio, or full prompt content
"""
import time

from flask import Blueprint, jsonify, request

from core.identity import resolve_user_display_name, resolve_user_id
from core.realtime_voice import config as rv_config
from core.realtime_voice.duration_guard import DurationGuard
from core.realtime_voice.prompt_builder import build_realtime_instructions
from core.realtime_voice.providers import openai_realtime, xai_voice

_RATE_LIMIT_MAX_PER_WINDOW = 10
_RATE_LIMIT_WINDOW_SECONDS = 60

# In-memory, per-process -- valid because both German and Portuguese run as
# single-process Flask dev servers (no gunicorn/multi-worker), confirmed
# during Phase 1 investigation. Revisit if that ever changes.
_rate_limit_state: dict[str, list[float]] = {}

_DEFAULT_VOICE = {"openai": "marin", "xai": "eve"}
_TRANSCRIPTION_LANGUAGE = {
    "openai": {"de-AT": "de", "pt-BR": "pt"},
    "xai": {"de-AT": "de", "pt-BR": "pt-BR"},
}


def _check_rate_limit(user_id: str) -> bool:
    now = time.monotonic()
    history = _rate_limit_state.setdefault(str(user_id), [])
    history[:] = [t for t in history if now - t < _RATE_LIMIT_WINDOW_SECONDS]
    if len(history) >= _RATE_LIMIT_MAX_PER_WINDOW:
        return False
    history.append(now)
    return True


def create_bootstrap_blueprint(*, domain: str, locale: str, get_persona, is_production):
    """
    get_persona: Callable[[str], dict | None] -- domain-owned allow-list.
        Returns {"name", "prompt_txt", "scenes": {key: text}, "voices":
        {"openai": ..., "xai": ...}} for a known persona, else None. This
        callback IS the permitted-personas/scenes allow-list (Section 5) --
        the bootstrap endpoint accepts nothing this callback doesn't return.
    is_production: Callable[[], bool] -- gates the dev query-string
        provider override (Section 6/16: never available in production).
    """
    bp = Blueprint(f"realtime_voice_bootstrap_{domain}", __name__)

    @bp.route("/api/realtime-voice/bootstrap", methods=["POST"])
    def bootstrap():
        user_id = resolve_user_id(request)
        if user_id is None:
            return jsonify({"ok": False, "error": "identity required"}), 401

        if not _check_rate_limit(user_id):
            _log_outcome(domain, user_id, None, None, "rate_limited")
            return jsonify({"ok": False, "error": "rate limited"}), 429

        body = request.get_json(force=True) or {}
        explicit_provider = body.get("provider")
        persona_name = body.get("persona", "")
        scene = body.get("scene", "")
        learner_name = resolve_user_display_name(request)

        production = is_production()
        dev_override = request.args.get("provider") if not production else None

        try:
            provider = rv_config.resolve_provider(
                explicit=explicit_provider,
                saved_preference=None,
                is_production=production,
                dev_query_override=dev_override,
            )
        except rv_config.InvalidProviderError as e:
            _log_outcome(domain, user_id, explicit_provider, None, "invalid_provider")
            return jsonify({"ok": False, "error": str(e)}), 400

        persona = get_persona(persona_name)
        if persona is None:
            _log_outcome(domain, user_id, provider, None, "unknown_persona")
            return jsonify({"ok": False, "error": "unknown persona"}), 400

        scene_text = persona["scenes"].get(scene)
        if scene_text is None:
            _log_outcome(domain, user_id, provider, None, "unknown_scene")
            return jsonify({"ok": False, "error": "unknown scene"}), 400

        # Instructions are always built server-side from repository data --
        # any `instructions` field the client sent is discarded, never
        # read. This is the enforcement point for "do not accept arbitrary
        # instructions from the browser."
        instructions = build_realtime_instructions(
            locale=locale,
            persona_name=persona["name"],
            persona_txt=persona["prompt_txt"],
            scene_text=scene_text,
            learner_name=learner_name,
        )
        voice = persona.get("voices", {}).get(provider) or _DEFAULT_VOICE[provider]
        turn_detection = _default_turn_detection(provider)
        transcription_language = _TRANSCRIPTION_LANGUAGE[provider][locale]

        try:
            if provider == "openai":
                credential = openai_realtime.mint_ephemeral_credential(
                    instructions=instructions,
                    voice=voice,
                    turn_detection=turn_detection,
                    transcription_language=transcription_language,
                    user_id_for_safety_identifier=str(user_id),
                )
            else:
                credential = xai_voice.mint_ephemeral_credential(
                    instructions=instructions,
                    voice=voice,
                    turn_detection=turn_detection,
                    transcription_language=transcription_language,
                )
        except (openai_realtime.OpenAIRealtimeError, xai_voice.XAIVoiceError) as e:
            _log_outcome(domain, user_id, provider, None, "provider_error")
            return jsonify({"ok": False, "error": str(e)}), 502

        _log_outcome(domain, user_id, provider, credential.get("model"), "started")
        guard = DurationGuard()
        return jsonify({
            "ok": True,
            **credential,
            "warning_minutes": guard.warning_minutes,
            "max_minutes": guard.max_minutes,
        })

    return bp


def _default_turn_detection(provider: str) -> dict:
    if provider == "openai":
        # A predictable silence boundary is easier for a language learner
        # than low-eagerness semantic VAD, which can wait a long time before
        # deciding that a hesitant but complete utterance has ended.
        return {
            "type": "server_vad",
            "prefix_padding_ms": 300,
            "silence_duration_ms": 1200,
        }
    # xAI: server VAD with a longer-than-default silence threshold suitable
    # for a learner (Section 9). xAI's default silence_duration_ms is not
    # documented; this is an explicit, recorded choice, not an assumption
    # that the provider default is already learner-appropriate.
    return {"type": "server_vad", "silence_duration_ms": 1200}


def _log_outcome(domain, user_id, provider, model, outcome):
    # Deliberately no credentials, no raw audio, no prompt content --
    # only the fields Section 6 asks for.
    print(
        f"[realtime_voice] domain={domain} user_id={user_id} provider={provider} "
        f"model={model} outcome={outcome}",
        flush=True,
    )
