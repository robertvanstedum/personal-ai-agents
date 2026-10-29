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
import contextlib
import os
import time

from flask import Blueprint, jsonify, request

from core.identity import resolve_user_display_name, resolve_user_id
from core.realtime_voice.capabilities import (
    MEMO_MODE,
    ProviderUnavailableError,
    providers_for_mode,
    resolve_memo_provider,
)
from core.realtime_voice import config as rv_config
from core.realtime_voice.duration_guard import DurationGuard
from core.realtime_voice.prompt_builder import build_realtime_instructions
from core.realtime_voice.providers import openai_realtime, xai_voice
from core.realtime_voice.standards import conversation_turn_detection

_RATE_LIMIT_MAX_PER_WINDOW = 10
_RATE_LIMIT_WINDOW_SECONDS = 60

# In-memory, per-process -- valid because both German and Portuguese run as
# single-process Flask dev servers (no gunicorn/multi-worker), confirmed
# during Phase 1 investigation. Revisit if that ever changes.
_rate_limit_state: dict[str, list[float]] = {}

_DEFAULT_VOICE = {"openai": "marin", "xai": "eve"}
_ALLOWED_TOOL_NAMES = frozenset({"consult", "save_note"})


def _allowlisted_tools(tools):
    if not isinstance(tools, list) or not tools:
        return False
    names = []
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) != {"type", "name", "description", "parameters"}:
            return False
        if tool["type"] != "function" or tool["name"] not in _ALLOWED_TOOL_NAMES:
            return False
        names.append(tool["name"])
    return len(names) == len(set(names))
_TRANSCRIPTION_LANGUAGE = {
    "openai": {"de-AT": "de", "pt-BR": "pt", "en-US": "en"},
    "xai": {"de-AT": "de", "pt-BR": "pt-BR", "en-US": "en"},
}
_MEMO_TRANSCRIPTION_LANGUAGE = {"de-AT": "de", "pt-BR": "pt"}
_DEFAULT_MEMO_WARNING_MINUTES = 13
_DEFAULT_MEMO_MAX_MINUTES = 15
_MEMO_TRANSCRIPTION_CONTEXT = {
    "de-AT": {
        "prompt": (
            "German-language personal journal dictation for a writing-practice "
            "surface. The speaker may pause, self-correct, or restart. Produce "
            "verbatim German text with natural punctuation; do not translate, "
            "answer, comment, or add language labels."
        ),
        "keywords": ["mini-moi", "Mein Deutsch", "Schreiben", "Gespräche"],
    },
    "pt-BR": {
        "prompt": (
            "Brazilian Portuguese personal journal dictation for a writing-"
            "practice surface. The speaker may pause, self-correct, or restart. "
            "Produce verbatim Portuguese text with natural punctuation; do not "
            "translate, answer, comment, or add language labels."
        ),
        "keywords": ["mini-moi", "Meu Português", "Escrita", "Conversas"],
    },
}


def check_voice_rate_limit(user_id: str) -> bool:
    now = time.monotonic()
    history = _rate_limit_state.setdefault(str(user_id), [])
    history[:] = [t for t in history if now - t < _RATE_LIMIT_WINDOW_SECONDS]
    if len(history) >= _RATE_LIMIT_MAX_PER_WINDOW:
        return False
    history.append(now)
    return True


def _memo_duration_guard() -> DurationGuard:
    return DurationGuard(
        warning_minutes=int(os.environ.get(
            "VOICE_MEMO_WARNING_MINUTES", _DEFAULT_MEMO_WARNING_MINUTES
        )),
        max_minutes=int(os.environ.get(
            "VOICE_MEMO_MAX_MINUTES", _DEFAULT_MEMO_MAX_MINUTES
        )),
    )


def create_bootstrap_blueprint(*, domain: str, locale: str, get_persona, is_production, local_owner=None, consult_ledger=None,
                               owner=None, admit=None, page_responses=None):
    """
    get_persona: Callable[[str], dict | None] -- domain-owned allow-list.
        Returns {"name", "prompt_txt", "scenes": {key: text}, "voices":
        {"openai": ..., "xai": ...}} for a known persona, else None. This
        callback IS the permitted-personas/scenes allow-list (Section 5) --
        the bootstrap endpoint accepts nothing this callback doesn't return.
        Optional "instructions": the host's own composed session text, used
        instead of the shared language-practice builder.
    is_production: Callable[[], bool] -- gates the dev query-string
        provider override (Section 6/16: never available in production).
    owner: Callable[[], (id, display_name) | None] -- a host that is the portal
        itself (no proxy strips X-Minimoi-* there) names its signed-in owner;
        the proxy headers are then never read. None: German/Portuguese as before.
    admit: Callable[[id], context manager] -- reserves the host's allowance
        around the provider mint (core.realtime_voice.budget); a budget refusal
        answers before the provider is contacted. With admit, consult_ledger.issue
        runs first (nothing is reserved for a session that cannot be recorded) and
        consult_ledger.abandon(session_id), if it has one, runs when the mint does
        not start a session. None: as before.
    page_responses: Callable[[provider], bool] -- only a host on the direct path passes it (Spec 165 §5.1, §7 as amended
        28 Oct 2026: core.realtime_voice.capabilities.model_actions). For a provider it names, the page creates every
        response that may act, one for each committed owner item: the session's turn detection lets it
        (create_response false; the provider's barge-in still on), and the answer says page_responses: true. get_persona
        is then called as get_persona(name, actions=...): True for such a provider, else False, and a persona that still
        carries tools for a provider it does not name is refused (no action tool where the page cannot name the owner).
        None: German/Portuguese as before.
    """
    bp = Blueprint(f"realtime_voice_bootstrap_{domain}", __name__)

    @bp.route("/api/realtime-voice/memo/capabilities", methods=["GET"])
    def memo_capabilities():
        user_id = resolve_user_id(request)
        if user_id is None:
            return jsonify({"ok": False, "error": "identity required"}), 401

        providers = providers_for_mode(MEMO_MODE, locale)
        default = resolve_memo_provider(None, locale)
        return jsonify({
            "ok": True,
            "mode": MEMO_MODE,
            "default_provider": default.provider,
            "providers": [provider.public_dict() for provider in providers],
        })

    @bp.route("/api/realtime-voice/memo/bootstrap", methods=["POST"])
    def memo_bootstrap():
        user_id = resolve_user_id(request)
        if user_id is None:
            return jsonify({"ok": False, "error": "identity required"}), 401

        if not check_voice_rate_limit(user_id):
            _log_outcome(domain, user_id, None, None, "memo_rate_limited")
            return jsonify({"ok": False, "error": "rate limited"}), 429

        body = request.get_json(force=True) or {}
        requested_provider = body.get("provider")
        try:
            capability = resolve_memo_provider(requested_provider, locale)
        except ValueError as error:
            _log_outcome(domain, user_id, requested_provider, None, "memo_invalid_provider")
            return jsonify({"ok": False, "error": str(error)}), 400
        except ProviderUnavailableError as error:
            _log_outcome(domain, user_id, requested_provider, None, "memo_provider_unavailable")
            return jsonify({"ok": False, "error": str(error)}), 503

        try:
            if capability.provider == "openai":
                transcription_context = _MEMO_TRANSCRIPTION_CONTEXT[locale]
                credential = openai_realtime.mint_transcription_credential(
                    transcription_language=_MEMO_TRANSCRIPTION_LANGUAGE[locale],
                    user_id_for_safety_identifier=str(user_id),
                    transcription_prompt=transcription_context["prompt"],
                    transcription_keywords=transcription_context["keywords"],
                )
            else:  # Fail closed if registry and implementation ever drift.
                raise ProviderUnavailableError(
                    f"{capability.provider} memo transport is not implemented"
                )
        except openai_realtime.OpenAIRealtimeError as error:
            _log_outcome(domain, user_id, capability.provider, None, "memo_provider_error")
            return jsonify({"ok": False, "error": str(error)}), 502
        except ProviderUnavailableError as error:
            _log_outcome(domain, user_id, capability.provider, None, "memo_provider_unavailable")
            return jsonify({"ok": False, "error": str(error)}), 503

        _log_outcome(domain, user_id, capability.provider, credential.get("model"), "memo_started")
        guard = _memo_duration_guard()
        return jsonify({
            "ok": True,
            **credential,
            "warning_minutes": guard.warning_minutes,
            "max_minutes": guard.max_minutes,
        })

    @bp.route("/api/realtime-voice/bootstrap", methods=["POST"])
    def bootstrap():
        if owner is not None:
            user_id, learner_name = owner() or (None, None)
        else:
            user_id = resolve_user_id(request)
            if user_id is None and local_owner is not None and request.remote_addr == "127.0.0.1":
                user_id = local_owner
        if user_id is None:
            return jsonify({"ok": False, "error": "identity required"}), 401

        if not check_voice_rate_limit(user_id):
            _log_outcome(domain, user_id, None, None, "rate_limited")
            return jsonify({"ok": False, "error": "rate limited"}), 429

        body = request.get_json(force=True) or {}
        explicit_provider = body.get("provider")
        persona_name = body.get("persona", "")
        scene = body.get("scene", "")
        if owner is None:
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

        paged = page_responses is not None and page_responses(provider) is True
        persona = get_persona(persona_name) if page_responses is None else get_persona(persona_name, actions=paged)
        if persona is None:
            _log_outcome(domain, user_id, provider, None, "unknown_persona")
            return jsonify({"ok": False, "error": "unknown persona"}), 400

        scenes = persona.get("scenes") or {}
        if scenes:
            scene_text = scenes.get(scene)
            if scene_text is None:
                _log_outcome(domain, user_id, provider, None, "unknown_scene")
                return jsonify({"ok": False, "error": "unknown scene"}), 400
        else:
            scene_text = ""
        tools = persona.get("tools") or None
        if tools is not None and (not _allowlisted_tools(tools) or (page_responses is not None and not paged)):
            _log_outcome(domain, user_id, provider, None, "invalid_tools")
            return jsonify({"ok": False, "error": "invalid tools"}), 400

        # Instructions are always built server-side from repository data --
        # any `instructions` field the client sent is discarded, never
        # read. This is the enforcement point for "do not accept arbitrary
        # instructions from the browser."
        # A persona may carry its own server-composed instructions (MC: shared operating rules, his card, bounded
        # continuity; Spec 164 §7), built by the host's get_persona from repository data and the owner's own
        # records. The language-practice text ("the learner", corrections) is then not used. German and Portuguese
        # personas carry none and are built exactly as before.
        composed = persona.get("instructions")
        instructions = composed if isinstance(composed, str) and composed.strip() else build_realtime_instructions(
            locale=locale,
            persona_name=persona["name"],
            persona_txt=persona["prompt_txt"],
            scene_text=scene_text,
            learner_name=learner_name,
        )
        voice = persona.get("voices", {}).get(provider) or _DEFAULT_VOICE[provider]
        turn_detection = _default_turn_detection(provider)
        if paged:                                          # the page creates every response; barge-in still cancels one
            turn_detection = conversation_turn_detection(provider, create_response=False, interrupt_response=True)
        transcription_language = _TRANSCRIPTION_LANGUAGE[provider][locale]

        # A host that reserves an allowance (admit) records its session first (MC: the durable start marker), so an
        # allowance is never spent on a session that could not be recorded; if the provider session then does not
        # start, the ledger's abandon() closes it (MC: an end marker, reason not_started). Without admit, as before.
        early = consult_ledger is not None and admit is not None
        if admit is not None:
            # Imported only for a host that admits (MC): the budget module reads the America/Chicago zone at import,
            # and German and Portuguese ship no tzdata (review B #8).
            from core.realtime_voice.budget import VoiceBudgetError as budget_refused
        else:
            budget_refused = ()                            # an empty tuple: no except clause matches
        voice_session_id = consult_ledger.issue(user_id) if early else None

        def abandon():
            if early and hasattr(consult_ledger, "abandon"):
                consult_ledger.abandon(voice_session_id)

        try:
            with admit(user_id) if admit is not None else contextlib.nullcontext():
                if provider == "openai":
                    credential = openai_realtime.mint_ephemeral_credential(
                        instructions=instructions,
                        voice=voice,
                        turn_detection=turn_detection,
                        transcription_language=transcription_language,
                        user_id_for_safety_identifier=str(user_id),
                        tools=tools,
                    )
                else:
                    credential = xai_voice.mint_ephemeral_credential(
                        instructions=instructions,
                        voice=voice,
                        turn_detection=turn_detection,
                        transcription_language=transcription_language,
                        tools=tools,
                    )
        except budget_refused as e:
            abandon()
            _log_outcome(domain, user_id, provider, None, "budget_refused")
            return jsonify({"ok": False, "error": e.public}), e.status
        except (openai_realtime.OpenAIRealtimeError, xai_voice.XAIVoiceError) as e:
            abandon()
            _log_outcome(domain, user_id, provider, None, "provider_error")
            return jsonify({"ok": False, "error": str(e)}), 502
        except BaseException:
            abandon()
            raise

        _log_outcome(domain, user_id, provider, credential.get("model"), "started")
        guard = DurationGuard()
        if not early:
            voice_session_id = consult_ledger.issue(user_id) if consult_ledger is not None else None
        return jsonify({
            "ok": True,
            "voice_session_id": voice_session_id,
            **credential,
            **({"voice_notices": list(persona["notices"])} if persona.get("notices") else {}),
            **({"page_responses": True} if paged else {}),
            "warning_minutes": guard.warning_minutes,
            "max_minutes": guard.max_minutes,
        })

    return bp


def _default_turn_detection(provider: str) -> dict:
    # One platform conversation standard serves Gespräche, Conversas, and
    # chained agent conversations. Provider adapters keep transport details.
    return conversation_turn_detection(provider)


def _log_outcome(domain, user_id, provider, model, outcome):
    # Deliberately no credentials, no raw audio, no prompt content --
    # only the fields Section 6 asks for.
    print(
        f"[realtime_voice] domain={domain} user_id={user_id} provider={provider} "
        f"model={model} outcome={outcome}",
        flush=True,
    )
