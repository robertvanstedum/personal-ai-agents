"""Dictation (insertion-only): speech turned into text in a draft box, nothing else.

The shared memo controller streams the microphone straight to the speech provider for TRANSCRIPTION only (an ephemeral credential minted here, never the account key);
the page puts the words into the box the owner is writing in, at the cursor or over a selection, and never sends anything. No agent answers and no model speaks.

Off unless ``MINIMOI_DICTATION=1``. What is checked up front is only that the shared transport lists the OpenAI memo provider (a static table): it does NOT prove a key is
configured or accepted. A missing or refused key shows when a session is started, as a bounded 502 with the provider's plain message and never a key.
Private is a bounded gap, not a policy: the page does not start dictation there. Every credential first reserves a conservative allowance against the shared daily voice budget (core.realtime_voice.budget: $20 per America/Chicago day; a reserved allowance, not a bill) and is refused with a plain message when the day's allowance is used up. Nothing here changes the provider, the key or any record."""
from __future__ import annotations

import os

from flask import jsonify, send_from_directory

from core.realtime_voice import budget

from . import cfg, feature_enabled, voice_release
from .security import check_write, json_error

ENV = "MINIMOI_DICTATION"
_STATIC_OK = ("realtime-memo-controller.js", "adapters/openai-transcription-webrtc-adapter.js")
_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "core", "realtime_voice", "static")
# A hint about the setting. It is a transcription session (no turn detection, no tools, no assistant output): the wording is not what keeps it that way.
PROMPT = "Dictation into a draft message for a team workspace. Transcribe the speaker's words verbatim with natural punctuation; keep names and product terms as spoken."


def enabled(environ=None) -> bool:
    return voice_release.SHARED_VOICE_READY and (feature_enabled(ENV) if environ is None else environ.get(ENV) == "1")


def csp_connect_extra(environ=None) -> str:
    """What the page's connect-src may also reach while dictation is on: the provider's session endpoint, for the browser-to-provider audio session."""
    return " https://api.openai.com" if enabled(environ) else ""


def _off():
    return jsonify({"ok": False, "error": "Dictation is not switched on here."}), 404


def _providers():
    from core.realtime_voice.capabilities import MEMO_MODE, providers_for_mode, resolve_memo_provider
    listed = [p for p in providers_for_mode(MEMO_MODE, "en-US") if p.provider == "openai"]
    default = resolve_memo_provider("openai", "en-US")
    return MEMO_MODE, default, listed


def capabilities():
    if not enabled():
        return _off()
    from core.realtime_voice.capabilities import ProviderUnavailableError
    try:
        mode, default, listed = _providers()
    except ProviderUnavailableError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    return jsonify({"ok": True, "mode": mode, "default_provider": default.provider, "providers": [p.public_dict() for p in listed], "budget": budget.public_status()})


def bootstrap():
    """A short-lived transcription credential for this owner. A write: same-origin, token and on-the-record, like any other write."""
    if not enabled():
        return _off()
    refusal = check_write(cfg()["base_url"])
    if refusal is not None:
        return refusal
    from .api import _principal
    from core.realtime_voice.bootstrap import check_voice_rate_limit
    from core.realtime_voice.capabilities import ProviderUnavailableError
    from core.realtime_voice.duration_guard import DurationGuard
    from core.realtime_voice.providers import openai_realtime
    principal = _principal()
    if not check_voice_rate_limit(principal):
        return jsonify({"ok": False, "error": "rate limited"}), 429
    guard = DurationGuard(warning_minutes=int(os.environ.get("VOICE_MEMO_WARNING_MINUTES", 13)), max_minutes=int(os.environ.get("VOICE_MEMO_MAX_MINUTES", 15)))
    try:
        _providers()
        # The provider-bound exposure (60-minute maximum at the live-transcription rate) is reserved before the provider is contacted. The page's 15-minute timer is a browser timer, not a billing cap; there is no early-end rebate.
        with budget.admit(kind="transcription_session", domain="dictation", principal=principal, required=True,
                          amount_micro=budget.session_allowance_micro()):
            credential = openai_realtime.mint_transcription_credential(
                transcription_language="en", user_id_for_safety_identifier=principal, transcription_prompt=PROMPT)
    except budget.VoiceBudgetError as exc:
        return jsonify({"ok": False, "error": exc.public, "budget": budget.public_status()}), exc.status
    except ProviderUnavailableError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except openai_realtime.OpenAIRealtimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, **credential, "warning_minutes": guard.warning_minutes, "max_minutes": guard.max_minutes})


def static_file(name):
    """The shared memo controller and its transport adapter, from the one shared copy (never a second copy), only while dictation is on."""
    if not enabled():
        return _off()
    if name not in _STATIC_OK:
        return json_error("not_found", "No such file.", 404)
    return send_from_directory(_STATIC_DIR, name, mimetype="text/javascript")


RULES = [
    ("/dictation/capabilities", "api_dictation_capabilities", capabilities, ["GET"]),
    ("/dictation/bootstrap", "api_dictation_bootstrap", bootstrap, ["POST"]),
    ("/dictation/static/<path:name>", "api_dictation_static", static_file, ["GET"]),
]
