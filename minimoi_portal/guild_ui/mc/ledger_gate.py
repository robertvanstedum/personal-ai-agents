"""Every MC dispatch path goes through the durable turn ledger when MINIMOI_MC_TURN_LEDGER=1
(issue #346, P1; mc/turn_ledger.py). Off: nothing here runs and every path behaves as before.

On:
- the turn is recorded and claimed BEFORE any platform action or model call (``gate``);
- the claim's lease is kept alive by a heartbeat until the work really ends, not when the
  browser goes away;
- the end is recorded from what is PROVEN (``Turn.finish_*``): ``not_sent`` only when no model
  request and no platform action was started; after a dispatch, an answer that was kept is
  ``answered``, a timeout or anything ambiguous is ``unknown``, and any other outcome is
  ``failed``. Unknown and failed pause the owner's turns until Continue;
- outcomes are mapped field by field onto the ledger's allowlist: never a whole backend result.

Enable it only where the Continue control exists (the isolated proving stack until the
client UI lands): a pause otherwise has no button.
"""
from __future__ import annotations

import logging
import re

from .turn_ledger import LedgerUnavailable

FLAG = "MINIMOI_MC_TURN_LEDGER"
# Platform receipts' operation.state after a downstream request was sent (owner_actions, Library):
UNCONFIRMED = ("uncertain", "unconfirmed", "unknown")
DOWNSTREAM_FAILED = ("failed", "refused", "error")
_log = logging.getLogger("guild_ui.mc.ledger")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,39}")
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
_KINDS = ("cos", "openclaw", "grok", "platform", "stub")

PAUSED_WORDS = ("Your earlier message's outcome is unknown: it may have run, or may still be running. "
                "Nothing new was sent. Continue when you are ready.")
FAILED_PAUSE_WORDS = "Your earlier message failed after it was sent. Nothing new was sent. Continue when you are ready."
LEDGER_DOWN_WORDS = "Turns cannot be recorded right now, so nothing was sent. Your note is kept."
BUSY_WORDS = "Master Craftsman is still answering your previous note. Nothing new was sent."


def enabled(environ) -> bool:
    return (environ or {}).get(FLAG) == "1"


def _code(value):
    return value if isinstance(value, str) and _CODE.fullmatch(value) else None


def _ident(value):
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def outcome_of(body) -> dict:
    """The allowlisted, content-free outcome of a route's answer body."""
    if not isinstance(body, dict):
        return {}
    out = {}
    if _ident(body.get("turn_id")):
        out["turn_id"] = body["turn_id"]
    if _ident(body.get("backend_turn_id")):
        out["backend_turn_id"] = body["backend_turn_id"]
    if body.get("backend_kind") in _KINDS:
        out["backend_kind"] = body["backend_kind"]
    for key in ("status", "failure_class"):
        if _code(body.get(key)):
            out[key] = body[key]
    if _code(body.get("error")):
        out["reason"] = body["error"]
    note = body.get("reply_note")
    if isinstance(note, dict) and type(note.get("id")) is int:
        out["reply_note_id"] = note["id"]
    if type(body.get("history_saved")) is bool:
        out["history_saved"] = body["history_saved"]
    op = body.get("operation")
    if isinstance(op, dict):
        clean = {}
        if _code(op.get("type")):
            clean["type"] = op["type"]
        for key in ("saved", "repeated"):
            if type(op.get(key)) is bool:
                clean[key] = op[key]
        if type(op.get("id")) is int:
            clean["id"] = op["id"]
        for key in ("reason", "state"):
            if _code(op.get(key)):
                clean[key] = op[key]
        out["operation"] = clean
    return out


class Refusal(Exception):
    """The ledger refused this turn. ``body``/``status`` are the JSON answer; nothing was sent."""

    def __init__(self, code: str, message: str, status: int, **extra):
        super().__init__(code)
        self.body = {"error": code, "message": message, **extra}
        self.status = status


class Turn:
    """One claimed turn: keep its lease alive, mark when work starts, record how it ended."""

    def __init__(self, ledger, admission):
        self.ledger = ledger
        self.claim = admission.claim
        self.entry = admission.entry
        self.started = False          # a model request or platform action has begun
        self.finished = False
        self._hold = ledger.held(self.claim)
        self._hold.__enter__()

    def mark_started(self):
        self.started = True

    def _end(self, state, outcome):
        if self.finished:
            return
        self.finished = True
        try:
            if not self.ledger.finish(self.claim, state, outcome):
                _log.warning("mc ledger %s: finish %s found the claim gone (recorded as unknown elsewhere)",
                             self.claim.ledger_id, state)
        except (LedgerUnavailable, ValueError):
            _log.exception("mc ledger %s: finish %s could not be recorded; the lease will expire to unknown",
                           self.claim.ledger_id, state)
        finally:
            self._hold.__exit__(None, None, None)

    def finish_not_sent(self, body=None):
        """Only for a proven pre-dispatch refusal: no model request and no platform action started."""
        if self.started:
            raise RuntimeError("work started: not_sent is not true")
        self._end("not_sent", outcome_of(body))

    def finish_platform(self, body):
        """A platform action answered; its own receipt says what is proven. A downstream outcome
        that was not confirmed (a CoS note or reminder timeout, an unconfirmed Library filing) is
        unknown, and one that failed or was refused after it was sent is failed; both pause.
        A clarification or a refusal before anything was sent is an answer."""
        self.started = True
        outcome = outcome_of(body)
        op = body.get("operation") if isinstance(body, dict) else None
        state = op.get("state") if isinstance(op, dict) else None
        if state in UNCONFIRMED:
            self._end("unknown", outcome)
        elif state in DOWNSTREAM_FAILED:
            self._end("failed", outcome)
        else:
            self._end("answered", outcome)

    def finish_model(self, body, http_status):
        """The non-streaming model turn's answer, after mark_started() was called at dispatch."""
        if not self.started:
            if isinstance(body, dict) and body.get("status") == "answered":
                self._end("answered", {**outcome_of(body), "reason": "existing_reply"})   # answered from the store
            else:
                self._end("not_sent", outcome_of(body))
            return
        status = body.get("status") if isinstance(body, dict) else None
        if http_status == 200 and status == "answered" and isinstance(body.get("reply_note"), dict):
            self._end("answered", outcome_of(body))
        elif status == "timeout_uncertain":
            self._end("unknown", outcome_of(body))
        else:
            self._end("failed", outcome_of(body))

    def finish_stream(self, status, failure_class, backend_kind, dispatched, stop_acknowledged=None):
        """The streamed turn ended (StreamRun._finish): only now does the claim end."""
        outcome = {"status": _code(status), "failure_class": _code(failure_class),
                   "backend_kind": backend_kind if backend_kind in _KINDS else None}
        outcome = {k: v for k, v in outcome.items() if v is not None}
        if not dispatched and not self.started:
            self._end("not_sent", outcome)
        elif status == "answered":
            self._end("answered", outcome)
        elif status == "stopped" and stop_acknowledged is True:
            self._end("failed", outcome)          # the owner's Stop, acknowledged by the runtime
        elif status in ("error", "unavailable") and failure_class not in (None, "truncated", "upstream"):
            self._end("failed", outcome)
        else:
            self._end("unknown", outcome)          # timeout, cut off, upstream break, unacknowledged stop

    def finish_unknown(self, reason="exception"):
        self._end("unknown", {"reason": reason})


def gate(services, environ, *, principal, conversation_id, note_request_id, request_id, kind="typed",
         attachment_ids=None, topic_refs=None):
    """None when the ledger is off. Otherwise a claimed Turn, or Refusal (nothing recorded or sent)."""
    if not enabled(environ):
        return None
    ledger = getattr(services, "turn_ledger", None)
    if ledger is None:
        raise Refusal("ledger_unavailable", LEDGER_DOWN_WORDS, 503)
    turn_request_id = request_id or ("note-" + note_request_id)
    try:
        admission = ledger.admit(principal=principal, conversation_id=conversation_id,
                                 note_request_id=note_request_id, turn_request_id=turn_request_id, kind=kind,
                                 attachment_ids=list(attachment_ids or []), topic_refs=list(topic_refs or []))
    except LedgerUnavailable:
        raise Refusal("ledger_unavailable", LEDGER_DOWN_WORDS, 503) from None
    except ValueError as exc:
        if "request_mismatch" in str(exc):
            raise Refusal("request_mismatch", "These ids were already used for a different message. "
                          "Nothing was sent.", 409) from None
        raise Refusal("invalid", "This message could not be recorded. Nothing was sent.", 422) from None
    if admission.result == "claimed":
        return Turn(ledger, admission)
    if admission.result == "busy":
        raise Refusal("busy", BUSY_WORDS, 409)
    if admission.result == "paused":
        barrier = admission.barrier or {}
        words = PAUSED_WORDS if barrier.get("reason") == "unknown" else FAILED_PAUSE_WORDS
        raise Refusal("paused", words, 409, ledger_id=barrier.get("ledger_id"), pause_reason=barrier.get("reason"))
    entry = admission.entry or {}
    if admission.result == "running":
        raise Refusal("already_dispatched", "This note was already sent; it is still running. Nothing was sent again.",
                      409, ledger_id=entry.get("id"), state="running")
    # done: never dispatched again; the earlier content-free outcome only.
    raise Refusal("already_done", "This note was already handled; nothing was sent again.", 200,
                  ledger_id=entry.get("id"), state=entry.get("state"), outcome=entry.get("outcome") or {})
