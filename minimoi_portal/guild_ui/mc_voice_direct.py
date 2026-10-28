"""MC voice on the direct path (Spec 164 v0.3 §3, §9): the shared bootstrap and the shared consult route on this mount.

The provider speaks directly, as in German. A consult is MC's typed turn (api._ledger_turn + api._answer_turn, what
POST /mc/turns runs after its prelude) on the owner's own latest transcript item, kept first as his note labelled
voice, exactly as POST /notes keeps a typed one. The model's arguments are never the turn text. No authority a typed
note lacks: same owner, CSRF, on-the-record, conversation, turn ledger and receipts. A note is spoken as saved only on
its platform receipt. Not the managed sideband: mc_managed_realtime and MANAGED_VOICE_CHANNEL are not used here.

Off unless the voice switch is on (mc_voice.enabled) AND MINIMOI_MC_VOICE_DIRECT=1, which production holds off
(guild_mounts.PRODUCTION_HELD_OFF): production already runs with MINIMOI_MC_VOICE=1. Off: no route is registered.
A session reserves MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO against the shared daily voice budget before the provider
is contacted; unset, voice is unavailable and says so. Sessions live in this process (the portal is one process).
"""
from __future__ import annotations

import hashlib
import logging
import os

from flask import current_app, g, request

from core.realtime_voice import budget as shared_budget
from core.realtime_voice.bootstrap import create_bootstrap_blueprint
from core.realtime_voice.consult import ConsultLedger, create_consult_blueprint
from core.realtime_voice.voice_card import load_card

from . import _api_error, cfg, owner_api
from .security import check_stop, check_write, json_error

log = logging.getLogger(__name__)
ENV = "MINIMOI_MC_VOICE_DIRECT"
ALLOWANCE_ENV = "MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO"
RELEASE = "0.1.18"                 # MC's card; read at each bootstrap, so a session keeps the card it started with
PERSONA = "master_craftsman"
LOCALE = "en-US"
PREFIX = "/api/v1/mc/voice/direct"
NOT_SAVED = "The note is not saved."
NOT_A_NOTE = "Nothing was saved. Say 'make a note that' and the note, and I'll save it."
FAILED = "That did not go through. Nothing is claimed."
# Only type, name, description and parameters (bootstrap._allowlisted_tools). The descriptions are the model's rules.
TOOLS = [
    {"type": "function", "name": "consult",
     "description": ("Ask Master Craftsman's own turn for current facts, project state, files, jobs or any work. "
                     "The server acts on Robert's own last words, not on these arguments. Speak the result's 'spoken' "
                     "text without adding claims. If 'ok' is false, say it did not go through."),
     "parameters": {"type": "object", "properties": {"question": {"type": "string"}}}},
    {"type": "function", "name": "save_note",
     "description": ("Save a note when Robert says 'make a note that …' or 'save a note: …'. The server saves his own "
                     "words, not these arguments. Say it is saved only when the result has 'saved' true."),
     "parameters": {"type": "object", "properties": {"note": {"type": "string"}}}},
]


def _persona(name):
    if name != PERSONA:
        return None
    try:
        card = load_card(RELEASE)
    except (OSError, ValueError):                  # missing or unreadable release: unknown persona, voice unavailable
        log.exception("mc direct voice: card %s unavailable", RELEASE)
        return None
    return {"name": "Master Craftsman", "prompt_txt": card["card"], "scenes": {}, "voices": card["voices"],
            "tools": TOOLS, "notices": card["notices"]}


def _allowance(environ):
    raw = environ.get(ALLOWANCE_ENV, "")
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= 20_000_000:
        return None
    return int(raw)


class _Sessions(ConsultLedger):
    """A voice session is bound at bootstrap to its owner and to the conversation it was started in."""

    def __init__(self):
        super().__init__()
        self._where = {}

    def issue(self, owner):
        session_id = super().issue(owner)
        self._where[session_id] = g.mc_voice_direct_conversation
        return session_id

    def conversation_of(self, session_id):
        return self._where.get(session_id)


def _is_note_command(text):
    from . import board_commands, owner_actions
    action = owner_actions.parse(text)
    return (action is not None and action.kind == "note") or board_commands.parse(text) is not None


def _consult(sessions, principal, session_id, call_id, name, owner_text):
    """One bridged call through the typed turn. Returns the result the provider is given (remembered per call_id)."""
    from . import api
    from .mc.ledger_gate import Refusal
    from .mc_voice import _speakable
    from .stores import NOTE_MAX
    cid = sessions.conversation_of(session_id)
    conv, refusal = api._conversation(cid) if cid else (None, True)      # never the legacy thread by default
    if refusal is not None:
        return {"ok": False, "error": "conversation", "spoken": "That conversation is not available now. " + FAILED}
    text, bad = api._clean_text(owner_text, NOTE_MAX, "spoken words")
    if bad is not None:
        return {"ok": False, "error": "invalid", "spoken": FAILED}
    if name == "save_note" and not _is_note_command(text):
        return {"ok": False, "error": "not_a_note", "saved": False, "spoken": NOT_A_NOTE}
    # The owner's words kept as his note, as POST /notes keeps a typed one; one key per call, so a replay is one note.
    note_id = "voice-" + hashlib.sha256("\0".join((principal, session_id, call_id)).encode()).hexdigest()[:40]
    floor = api._conv_floor(conv)
    kept = floor.add_note(note_id, text, api._author(), page="voice")
    if kept.outcome != "kept" or not kept.value:
        return {"ok": False, "error": "not_kept", "spoken": FAILED}
    if not kept.repeated:
        conv = api._touch(conv, first_text=text)
    services = cfg()["services"]
    found = (services, principal, conv, floor, kept.value, note_id, note_id, [])
    g.topic_context = None
    g.mc_kept_owner_scope = (principal, conv["id"], note_id)
    try:
        turn = api._ledger_turn(found, kind="voice")
    except Refusal as refused:
        return {"ok": False, "error": refused.body.get("error"), "spoken": refused.body.get("message") or FAILED}
    response = current_app.make_response(api._answer_turn(found, turn))
    answer = response.get_json(silent=True) if response.is_json else None
    answer = answer if isinstance(answer, dict) else {}
    answered = response.status_code == 200 and answer.get("status") == "answered"
    reply = answer.get("reply_note") if isinstance(answer.get("reply_note"), dict) else {}
    said = reply.get("text") if answered and isinstance(reply.get("text"), str) else answer.get("message")
    result = {"note_request_id": note_id, "answered": answered}
    if name == "save_note":
        operation = answer.get("operation") if isinstance(answer.get("operation"), dict) else {}
        receipt = operation.get("operation_id") or operation.get("id")
        saved = answered and operation.get("saved") is True and bool(receipt)
        result.update(saved=saved, receipt=receipt if saved else None,
                      spoken=_speakable(said or "") if saved else NOT_SAVED)
        if not saved:
            result["ok"] = False
        return result
    result.update(receipt=reply.get("request_id") if answered else None,
                  spoken=_speakable(said or "") if isinstance(said, str) and said.strip() else FAILED)
    if not answered:
        result["ok"] = False
    return result


def install(app, mount, *, environ, budget=None):
    """Register MC's direct-path bootstrap and consult routes on this mount; False (nothing registered) when off."""
    from .mc_voice import enabled
    if not enabled(environ) or environ.get(ENV) != "1":
        return False
    config = app.extensions[mount]
    sessions = _Sessions()
    base = config["url_prefix"] + PREFIX
    boot_name, consult_name = mount + "_mc_voice_bootstrap", mount + "_mc_voice_consult"

    def principal():
        user = config["current_user"]() or {}
        name = user.get("username")
        return name if isinstance(name, str) and name else None

    def owner():
        who = principal()
        if who is None:
            return None
        shown = (config["current_user"]() or {}).get("display_name") or who
        shown = " ".join("".join(ch for ch in str(shown) if ch.isalnum() or ch in " -'’").split())[:80]
        return who, shown or who

    def admit(user_id):
        return shared_budget.admit(kind="realtime_direct_session", domain="mc", principal=str(user_id), required=True,
                                   amount_micro=_allowance(environ), budget=budget)

    @owner_api
    def verified_owner():
        return "", 204

    def guard():
        """The owner, then the Guild write guard (Stop's for End, so End works off the record), then MC turns on."""
        verdict = verified_owner()
        if verdict.status_code != 204:
            return verdict
        end = request.path.endswith("/end")
        refusal = (check_stop if end else check_write)(config["base_url"])
        if refusal is not None:
            return refusal
        if not end and not config["services"].mc_turns:
            return json_error("mc_turns_off", "Voice unavailable: Master Craftsman's turns are off here.", 409)
        return None

    def bootstrap_guard():
        if request.endpoint != boot_name + ".bootstrap":              # the shared blueprint's memo routes are not MC's
            return json_error("not_found", "No such route.", 404)
        refused = guard()
        if refused is not None:
            return refused
        if _allowance(environ) is None:
            return json_error("unavailable", "Voice unavailable: no realtime voice allowance is set here.", 503)
        from . import api
        from .conversations import ID_RE
        body = request.get_json(silent=True)
        cid = body.get("conversation_id") if isinstance(body, dict) else None
        if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
            return json_error("invalid", "Voice unavailable: name the conversation to talk in.", 422)
        conv, refusal = api._conversation(cid)
        if refusal is not None:
            return refusal
        g.mc_voice_direct_conversation = conv["id"]
        return None

    def no_store(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    def consult_backend(owner_text, _arguments):
        body = request.get_json(silent=True) or {}          # the route has validated these
        who, sid, call_id, name = principal(), body["session_id"], body["call_id"], body["name"]
        try:
            return _consult(sessions, who, sid, call_id, name, owner_text)
        except Exception:
            log.exception("mc direct voice: consult failed")
            return {"ok": False, "error": "unavailable", "spoken": FAILED}

    boot = create_bootstrap_blueprint(domain="mc", locale=LOCALE, get_persona=_persona,
                                      is_production=lambda: os.environ.get("MINIMOI_ROLE") != "development",
                                      consult_ledger=sessions, owner=owner, admit=admit)
    boot.before_request(bootstrap_guard)
    consult = create_consult_blueprint(backend=consult_backend, ledger=sessions,
                                       csrf_ok=lambda: check_stop(config["base_url"]) is None,
                                       owner_id=principal, session_owner=sessions.owner_of)
    consult.before_request(guard)
    for bp, name in ((boot, boot_name), (consult, consult_name)):
        bp.after_request(no_store)
        bp.register_error_handler(Exception, _api_error)
        app.extensions[name] = config                  # cfg() and owner_api resolve this mount on these routes
        app.register_blueprint(bp, url_prefix=base, name=name)
    config["mc_direct_voice"] = {"bootstrap_url": base + "/api/realtime-voice/bootstrap",
                                 "consult_url": base + "/api/realtime-voice/consult",
                                 "end_url": base + "/api/realtime-voice/session/{session_id}/end",
                                 "persona": PERSONA, "locale": LOCALE, "card_release": RELEASE}
    return True
