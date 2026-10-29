"""Opt-in managed realtime metadata/transport serves the shared controller;
its authenticated server host is mc_managed_realtime.py. Without that gate,
the existing chained behavior below remains unchanged.

Speaking with Master Craftsman (MC voice, v1): the chained pattern the Chief of Staff's Confer page already uses, on the owner's kept-note path.

Chained, not a talking model: the browser streams the microphone straight to the speech provider for TRANSCRIPTION only (an ephemeral credential minted here, never the
account key); the final transcript becomes an ordinary kept note and an ordinary Master Craftsman turn (``POST /notes`` then ``/mc/turns``, in the page); and MC's own kept
reply text is read aloud by the provider's text-to-speech from this server. So the words that are spoken are exactly the words MC wrote, and no model answers for him.

Off unless ``MINIMOI_MC_VOICE=1``. What is checked up front is only that the shared transport lists the OpenAI provider (a static table): it does NOT prove a key is
configured or accepted. A missing or refused key shows when a session is started, as a bounded 502 with the provider's plain message and never a key; the page then says
"Voice could not start" and typing carries on. Every paid call first reserves a conservative allowance against the shared daily voice budget (core.realtime_voice.budget: $20 per America/Chicago day, durable, atomic, fail-closed; a reserved allowance, not a bill) and is refused with a plain message when the day's allowance is used up. Nothing here changes the provider, the key or the owner's records. It does not keep audio or any provider transcript; the page keeps only what the ordinary note path keeps. On the record only: Private is a bounded gap,
not a policy (see the work record)."""
from __future__ import annotations

import os
import re

from flask import Response, jsonify, request, send_from_directory, stream_with_context

from core.realtime_voice import budget
# MC's reply as plain speech, cut at SPEECH_MAX_CHARS with a spoken pointer to the chat: the direct path's too (Spec 165 §4.2).
from core.realtime_voice.direct_host import SPEECH_MAX_CHARS, speakable as _speakable  # noqa: F401

from . import cfg, feature_enabled, voice_release
from .security import check_write, json_error

ENV = "MINIMOI_MC_VOICE"
_NOTE_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_STATIC_OK = ("realtime-confer-controller.js", "adapters/openai-transcription-webrtc-adapter.js")
_MANAGED_STATIC = ("realtime-voice-controller.js","adapters/openai-webrtc-adapter.js","adapters/xai-websocket-adapter.js")
_DIRECT_STATIC = ("direct-voice-session.js",)          # the direct path's browser session module (Spec 165 §4.3)
_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "core", "realtime_voice", "static")
# A hint to the transcriber about the setting. The enforcement that no provider answer exists is the transcription-only session type, not this wording.
TRANSCRIBE_ONLY_PROMPT = "Transcribe the speaker's words for a conversation with Master Craftsman, the MiniMoi build partner. Keep names and product terms as spoken."
SPEAKING_INSTRUCTIONS = (
    "Speak naturally and clearly as Master Craftsman, an AI build partner. "
    "Use a calm, warm, conversational voice. Do not add, omit, or paraphrase content."
)


def enabled(environ=None) -> bool:
    return voice_release.SHARED_VOICE_READY and (feature_enabled(ENV) if environ is None else environ.get(ENV) == "1")


def csp_connect_extra(environ=None) -> str:
    """What the page's connect-src may also reach while voice is on: the provider's session endpoint, for the browser-to-provider audio session.
    With MC's direct path offering Grok on this mount, also xAI's realtime socket (the browser opens wss://api.x.ai itself)."""
    if not enabled(environ):
        return ""
    try:
        direct = cfg().get("mc_direct_voice") if environ is None else None
    except RuntimeError:                                   # no mount bound (a probe): the OpenAI origin only, as before
        direct = None
    xai = bool(direct) and any(p.get("provider") == "xai" for p in direct.get("providers") or ())
    return " https://api.openai.com" + (" wss://api.x.ai" if xai else "")


def _off():
    return jsonify({"ok": False, "error": "Voice is not switched on here."}), 404


def _providers():
    """The providers the shared transport lists for this mode (a static capability table, not a credential check)."""
    from core.realtime_voice.capabilities import AGENT_CONVERSATION_MODE, providers_for_mode, resolve_agent_conversation_provider
    listed = [p for p in providers_for_mode(AGENT_CONVERSATION_MODE, "en-US") if p.provider == "openai"]      # the shared controller speaks the OpenAI transport only
    default = resolve_agent_conversation_provider("openai", "en-US")
    return AGENT_CONVERSATION_MODE, default, listed


def capabilities():
    if not enabled():
        return _off()
    from core.realtime_voice.capabilities import ProviderUnavailableError
    try:
        mode, default, listed = _providers()
    except ProviderUnavailableError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    direct, records = cfg().get("mc_direct_voice"), cfg().get("mc_direct_voice_records")
    # direct_voice: MC's direct path admitted here (mc_voice_direct.install). direct_records: installed, not admitted (Spec 165
    # §11.1 S1, S3): its record routes' metadata, so the page paints the thread and earlier proposals can be decided, while
    # Voice keeps today's path. Not installed: neither, as before.
    return jsonify({"ok": True, "mode": mode, "default_provider": default.provider, "providers": [p.public_dict() for p in listed], "budget": budget.public_status(), "managed_realtime": cfg().get("mc_managed_realtime"),
                    "direct_voice": direct, **({"direct_records": records} if records and not direct else {})})


def _budget_refusal(exc):
    return jsonify({"ok": False, "error": exc.public, "budget": budget.public_status()}), exc.status


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
    guard = DurationGuard(warning_minutes=int(os.environ.get("VOICE_CONFER_WARNING_MINUTES", 13)), max_minutes=int(os.environ.get("VOICE_CONFER_MAX_MINUTES", 15)))
    try:
        _mode, _default, _listed = _providers()
        # Reserve the provider-bound exposure (the provider's 60-minute maximum at the live-transcription rate) before the provider is contacted. The page's 15-minute timer is a browser timer, not a billing cap, and the browser talks to the provider directly, so there is no early-end rebate.
        with budget.admit(kind="transcription_session", domain="mc", principal=principal, required=True,
                          amount_micro=budget.session_allowance_micro()):
            # The shared chained helper: transcription-only helper and browser turn commits, so the speech provider can never produce an answer of its own.
            credential = openai_realtime.mint_confer_transcription_credential(
                transcription_language="en", user_id_for_safety_identifier=principal, transcription_prompt=TRANSCRIBE_ONLY_PROMPT)
    except budget.VoiceBudgetError as exc:
        return _budget_refusal(exc)
    except ProviderUnavailableError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except openai_realtime.OpenAIRealtimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, **credential, "warning_minutes": guard.warning_minutes, "max_minutes": guard.max_minutes})


def speech(note_request_id):
    """MC's kept reply to one of this owner's kept notes, read aloud. The text comes from the server's own record, never from the page."""
    if not enabled():
        return _off()
    from .api import _conv_floor, _conversation, _principal, _reply_key
    from .stores import FloorStoreUnavailable
    if not _NOTE_ID.fullmatch(note_request_id or ""):
        return json_error("invalid", "That is not a note.", 422)
    conv, refusal = _conversation(request.args.get("c") or None)
    if refusal is not None:
        return refusal
    floor, principal = _conv_floor(conv), _principal()
    try:
        asked, reply = floor.get_note(note_request_id), floor.get_note(_reply_key(note_request_id))
    except FloorStoreUnavailable:
        return json_error("unavailable", "The record is not available right now.", 503)
    if asked is None or asked.get("author_kind") != "owner" or asked.get("who") != principal or reply is None or reply.get("author_kind") == "owner":
        return json_error("not_found", "There is no answer to read for that note.", 404)
    text = _speakable(reply.get("text") or "")
    if not text:
        return json_error("not_found", "There is no answer to read for that note.", 404)
    from core.realtime_voice.providers import openai_speech
    try:
        with budget.admit(kind="tts", domain="mc", principal=principal, required=True, amount_micro=budget.tts_allowance_micro(text)):
            upstream = openai_speech.create_speech_stream(
                text=text, user_id=principal, instructions=SPEAKING_INSTRUCTIONS)
    except budget.VoiceBudgetError as exc:
        return json_error("budget", exc.public, exc.status)
    except openai_speech.OpenAISpeechError:
        return json_error("unavailable", "The voice service could not read the answer just now.", 502)

    @stream_with_context
    def generate():
        try:
            yield from upstream.iter_content(chunk_size=8192)
        finally:
            upstream.close()

    out = Response(generate(), mimetype="audio/mpeg")
    out.headers["Cache-Control"] = "no-store"
    return out


def static_file(name):
    """The shared controller and its transport adapter, served from the one shared copy (never a second copy), only while voice is on.
    The direct path's controller only while it is admitted here; its session module while it is installed, admitted or not
    (Spec 165 §11.1 S1, S3: the thread posts its decisions through the module's jsonPost)."""
    if not enabled():
        return _off()
    direct = bool(cfg().get("mc_direct_voice"))
    installed = direct or bool(cfg().get("mc_direct_voice_records"))
    if (name not in _STATIC_OK and not ((cfg().get("mc_managed_realtime") or direct) and name in _MANAGED_STATIC)
            and not (installed and name in _DIRECT_STATIC)):
        return json_error("not_found", "No such file.", 404)
    return send_from_directory(_STATIC_DIR, name, mimetype="text/javascript")


RULES = [
    ("/mc/voice/capabilities", "api_mc_voice_capabilities", capabilities, ["GET"]),
    ("/mc/voice/bootstrap", "api_mc_voice_bootstrap", bootstrap, ["POST"]),
    ("/mc/voice/speech/<note_request_id>", "api_mc_voice_speech", speech, ["GET"]),
    ("/mc/voice/static/<path:name>", "api_mc_voice_static", static_file, ["GET"]),
]
