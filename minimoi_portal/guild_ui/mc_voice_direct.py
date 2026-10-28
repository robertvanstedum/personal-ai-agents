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

Durable capture (§6, mc_voice_reveal): bootstrap keeps a start marker on the conversation's floor; every owner and
assistant turn the page posts, and every consult request and receipt, is kept as it happens through
mc_voice_transcripts.retain (one store), with the session id, a stable id, start/end times and partial/interrupted.
Those records are withheld from every view of the conversation until End keeps the end marker, or until the session's
maximum duration has passed without one; meanwhile a dropped session shows mc_voice_reveal.RECOVERY.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid

from flask import Blueprint, current_app, g, jsonify, request

from core.realtime_voice import budget as shared_budget
from core.realtime_voice.bootstrap import create_bootstrap_blueprint
from core.realtime_voice.consult import ConsultLedger, create_consult_blueprint
from core.realtime_voice.controlled_session import SessionRefused
from core.realtime_voice.duration_guard import DurationGuard
from core.realtime_voice.transcripts import MAX_BYTES as TURN_MAX_BYTES, STAMP
from core.realtime_voice.voice_card import load_card

from . import _api_error, cfg, mc_voice_reveal as reveal, owner_api
from .security import check_stop, check_write, json_error
from .stores import NOTE_MAX

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
# The page's transcript posts (§6): bounded per request and per session. Nothing is kept from a refused post.
POST_MAX_BYTES = 64 * 1024
POST_MAX_EVENTS = 20
SESSION_MAX_EVENTS = 600
SESSION_MAX_BYTES = 512 * 1024
CLOCK_SLACK_S = 5
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
    """A voice session is a UUID bound at bootstrap to its owner and to the conversation it was started in, with a
    durable start marker there. Past its maximum duration it is closed. End keeps the end marker (the reveal)."""

    def __init__(self, clock=time.time):
        super().__init__()
        self._meta, self._meta_lock, self.clock = {}, threading.Lock(), clock

    def issue(self, owner):
        from . import api
        session_id, cid = str(uuid.uuid4()), g.mc_voice_direct_conversation
        conv, refusal = api._conversation(cid)
        if refusal is not None:
            raise SessionRefused("Voice conversation unavailable")
        max_minutes = DurationGuard().max_minutes          # what the bootstrap answer tells the page
        started = self.clock()
        marker = reveal.mark_start(api._conv_floor(conv), session_id, principal=owner, conversation_id=conv["id"],
                                   max_minutes=max_minutes,
                                   started=reveal.datetime.fromtimestamp(started, reveal.timezone.utc))
        if marker is None:                                 # no durable start: no session, nothing could be revealed
            raise SessionRefused("Voice session not recorded")
        with self._meta_lock:
            self._meta[session_id] = {"conversation": conv["id"], "started": started, "max_s": max_minutes * 60,
                                      "seen": started, "next": 0, "index": {}, "items": set(), "bytes": 0}
        self.open_session(session_id, owner)
        return session_id

    def conversation_of(self, session_id):
        with self._meta_lock:
            meta = self._meta.get(session_id)
            return meta["conversation"] if meta else None

    def started_at(self, session_id):
        with self._meta_lock:
            meta = self._meta.get(session_id)
            return meta["started"] if meta else None

    def expired(self, session_id):
        with self._meta_lock:
            meta = self._meta.get(session_id)
            return meta is None or self.clock() >= meta["started"] + meta["max_s"]

    def seen(self, session_id):
        with self._meta_lock:
            if session_id in self._meta:
                self._meta[session_id]["seen"] = self.clock()

    def is_open(self, session_id):
        with self._lock:
            state = self._sessions.get(session_id)
            return bool(state and state["open"])

    def live(self, session_id):
        """Open here, within its maximum duration, and heard from lately (the page's heartbeat or a post)."""
        with self._meta_lock:
            meta = self._meta.get(session_id)
            fresh = meta is not None and self.clock() - meta["seen"] <= reveal.LIVE_WINDOW_S
        return fresh and self.is_open(session_id) and not self.expired(session_id)

    def admit(self, session_id, call_id):
        if self.expired(session_id):
            return "closed", None
        status, prior = super().admit(session_id, call_id)
        if status == "new":
            with self._meta_lock:
                meta = self._meta[session_id]
                meta["next"] += 1                           # at most CAP admissions per session: vc-<id>-01 … -12
                meta["index"][call_id] = meta["next"]
            self.seen(session_id)
        return status, prior

    def index_of(self, session_id, call_id):
        with self._meta_lock:
            return self._meta[session_id]["index"][call_id]

    def count(self, session_id, items, size):
        """Admit a post's new turns against the session caps; False (nothing counted) when over."""
        with self._meta_lock:
            meta = self._meta[session_id]
            fresh = set(items) - meta["items"]
            if len(meta["items"]) + len(fresh) > SESSION_MAX_EVENTS or meta["bytes"] + size > SESSION_MAX_BYTES:
                return False
            meta["items"] |= fresh
            meta["bytes"] += size
            return True

    def close_session(self, session_id):
        """End: closed here, and the end marker kept (the reveal). A failed write leaves the reveal to the clock."""
        super().close_session(session_id)
        from . import api
        cid = self.conversation_of(session_id)
        try:
            conv, refusal = api._conversation(cid) if cid else (None, True)
            if refusal is None:
                reveal.mark_end(api._conv_floor(conv), session_id, reason="end")
        except Exception:
            log.exception("mc direct voice: end marker not kept")


def _bound(principal, conv):
    """What mc_voice_transcripts.retain needs, captured in this authenticated request."""
    from . import api
    return {"app": current_app._get_current_object(), "config": cfg(), "principal": principal, "conversation": conv,
            "conversations": api._conversations(), "floor": api._conv_floor(conv)}


def _keep(principal, conv, session_id, role, item, text, *, started, ended, partial=False, interrupted=False):
    from .mc_voice_transcripts import retain
    return retain(_bound(principal, conv), conv["id"], session_id, role, item, 0, text, partial,
                  started=started, ended=ended, interrupted=interrupted)


def _is_note_command(text):
    from . import board_commands, owner_actions
    action = owner_actions.parse(text)
    return (action is not None and action.kind == "note") or board_commands.parse(text) is not None


def _consult(sessions, principal, session_id, call_id, name, owner_text):
    """One bridged call through the typed turn, its request and receipt kept as voice records. Returns the result the
    provider is given (remembered per call_id). A consult that finishes after End still keeps its receipt."""
    from . import api
    cid = sessions.conversation_of(session_id)
    conv, refusal = api._conversation(cid) if cid else (None, True)      # never the legacy thread by default
    if refusal is not None:
        return {"ok": False, "error": "conversation", "spoken": "That conversation is not available now. " + FAILED}
    item = "c" + hashlib.sha256(call_id.encode()).hexdigest()[:32]
    began = reveal.stamp(reveal.now())
    text, bad = api._clean_text(owner_text, NOTE_MAX, "spoken words")
    if bad is None:
        _keep(principal, conv, session_id, "consult", item, f"{name}: {text}", started=began, ended=began)
    result = _typed_turn(sessions, principal, session_id, call_id, name, conv, text, bad)
    try:
        _keep(principal, conv, session_id, "receipt", item, _receipt_line(name, result),
              started=began, ended=reveal.stamp(reveal.now()))
    except Exception:
        log.exception("mc direct voice: consult receipt not kept")
        result = {**result, "receipt_kept": False}
    return result


def _receipt_line(name, result):
    """A compact, factual line for the consult's record: what came back, never more than it says."""
    if name == "save_note":
        head = f"Saved. Operation {result.get('receipt')}." if result.get("saved") else "Not saved."
    elif result.get("answered"):
        head = f"Answered. Reply {result.get('receipt')}."
    else:
        head = f"Not answered ({result.get('error') or 'unavailable'})."
    return f"{head} Spoken: {result.get('spoken') or ''}".strip()[:TURN_MAX_BYTES // 4]


def _typed_turn(sessions, principal, session_id, call_id, name, conv, text, bad):
    from . import api
    from .mc.ledger_gate import Refusal
    from .mc_voice import _speakable
    if bad is not None:
        return {"ok": False, "error": "invalid", "spoken": FAILED}
    if name == "save_note" and not _is_note_command(text):
        return {"ok": False, "error": "not_a_note", "saved": False, "spoken": NOT_A_NOTE}
    # The owner's words kept as his note, as POST /notes keeps a typed one; one id per admitted call (vc-<session>-<n>),
    # so a replay is one note and the reveal can find MC's reply to it.
    note_id = reveal.consult_note_id(session_id, sessions.index_of(session_id, call_id))
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


def _events(body, started_at, clock):
    """The page's transcript events, all validated before any is kept: [(role, item, index, text, partial,
    interrupted, start, end)], or an error word."""
    events = body.get("events") if isinstance(body, dict) else None
    if not isinstance(events, list) or len(events) > POST_MAX_EVENTS:
        return None, "invalid"
    earliest, latest = started_at - CLOCK_SLACK_S, clock() + CLOCK_SLACK_S
    out = []
    for event in events:
        if not isinstance(event, dict) or set(event) - {"role", "item_id", "index", "text", "partial", "interrupted",
                                                        "started_at", "ended_at"}:
            return None, "invalid"
        role, item, index = event.get("role"), event.get("item_id"), event.get("index", 0)
        text, partial, interrupted = event.get("text"), event.get("partial", False), event.get("interrupted", False)
        start, end = event.get("started_at"), event.get("ended_at")
        if (role not in ("user", "assistant") or not isinstance(item, str) or not 1 <= len(item) <= 160
                or not all(c.isascii() and (c.isalnum() or c in "_-") for c in item)
                or type(index) is not int or not 0 <= index < 32
                or not isinstance(text, str) or not text.strip() or len(text.encode()) > TURN_MAX_BYTES
                or type(partial) is not bool or type(interrupted) is not bool or (interrupted and role != "assistant")
                or not all(isinstance(t, str) and STAMP.fullmatch(t) for t in (start, end)) or start > end):
            return None, "invalid"
        times = [reveal.parse_stamp(t) for t in (start, end)]
        if any(t is None or not earliest <= t.timestamp() <= latest for t in times):
            return None, "time"
        out.append((role, item, index, text, partial or interrupted, interrupted, start, end))
    return out, None


def install(app, mount, *, environ, budget=None, clock=time.time):
    """Register MC's direct-path bootstrap, consult and transcript routes on this mount; False (nothing registered)
    when off."""
    from .mc_voice import enabled
    if not enabled(environ) or environ.get(ENV) != "1":
        return False
    config = app.extensions[mount]
    sessions = _Sessions(clock)
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
    boot.register_error_handler(SessionRefused, lambda _exc: json_error(
        "unavailable", "Voice unavailable: the voice session could not be recorded in this conversation.", 503))
    consult = create_consult_blueprint(backend=consult_backend, ledger=sessions,
                                       csrf_ok=lambda: check_stop(config["base_url"]) is None,
                                       owner_id=principal, session_owner=sessions.owner_of)
    consult.before_request(guard)
    transcript = Blueprint("voice_transcript", __name__)

    @transcript.route("/api/realtime-voice/session/<session_id>/transcript", methods=["POST"])
    def keep_transcript(session_id):
        """The page's completed owner and assistant turns, kept as they happen; an empty list is the heartbeat."""
        from . import api
        from .mc_voice_transcripts import retain
        if request.content_length is None or request.content_length > POST_MAX_BYTES:
            return json_error("too_large", "A transcript post is at most 64 KB. Nothing was kept.", 413)
        who, sid = principal(), reveal.session_id(session_id)
        if sid is None or who is None or sessions.owner_of(sid) != who:
            return json_error("not_your_session", "That is not your voice session. Nothing was kept.", 403)
        if not sessions.is_open(sid) or sessions.expired(sid):
            return json_error("session_ended", "That voice session has ended. Nothing was kept.", 409)
        events, bad = _events(request.get_json(silent=True), sessions.started_at(sid), sessions.clock)
        if bad is not None:
            return json_error(bad, "Those transcript events are not valid. Nothing was kept.", 422)
        sessions.seen(sid)
        if not events:
            return jsonify(ok=True, kept=0)
        if not sessions.count(sid, [event[:3] for event in events], sum(len(e[3].encode()) for e in events)):
            return json_error("cap", "This voice session has kept all it may. Nothing more was kept.", 429)
        conv, refusal = api._conversation(sessions.conversation_of(sid))
        if refusal is not None:
            return refusal
        bound, kept = _bound(who, conv), 0
        try:
            for role, item, index, text, partial, interrupted, start, end in events:
                retain(bound, conv["id"], sid, role, item, index, text, partial,
                       started=start, ended=end, interrupted=interrupted)
                kept += 1
        except (SessionRefused, ValueError):
            return json_error("not_kept", "A transcript turn could not be kept.", 409, kept=kept)
        return jsonify(ok=True, kept=kept)

    transcript.before_request(guard)
    transcript_name = mount + "_mc_voice_transcript"
    for bp, name in ((boot, boot_name), (consult, consult_name), (transcript, transcript_name)):
        bp.after_request(no_store)
        bp.register_error_handler(Exception, _api_error)
        app.extensions[name] = config                  # cfg() and owner_api resolve this mount on these routes
        app.register_blueprint(bp, url_prefix=base, name=name)
    config["mc_direct_voice"] = {"bootstrap_url": base + "/api/realtime-voice/bootstrap",
                                 "consult_url": base + "/api/realtime-voice/consult",
                                 "end_url": base + "/api/realtime-voice/session/{session_id}/end",
                                 "transcript_url": base + "/api/realtime-voice/session/{session_id}/transcript",
                                 "heartbeat_seconds": 20, "persona": PERSONA, "locale": LOCALE, "card_release": RELEASE}
    config["mc_direct_voice_live"] = sessions.live      # server-side only: which sessions the reveal treats as live
    return True
