"""One consult route for the voice bench and, later, MC.

The backend is swappable. The bench uses a stub. MC will pass its typed turn
service. A consult has no authority the backend does not already have.
"""
import re
import secrets
import threading
import time

from flask import Blueprint, jsonify, request

CAP = 12
HOLD_AFTER_SECONDS = 10
HOLDING_LINE = "Still checking."
CAP_LINE = "I'll check the rest after we talk."


class ConsultLedger:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = {}

    def open_session(self, session_id, owner=None):
        with self._lock:
            self._sessions.setdefault(session_id, {"open": True, "owner": owner, "calls": {}, "results": {}})

    def close_session(self, session_id):
        with self._lock:
            state = self._sessions.setdefault(session_id, {"open": False, "owner": None, "calls": {}, "results": {}})
            state["open"] = False

    def issue(self, owner):
        session_id = "vs-" + secrets.token_hex(8)
        with self._lock:
            self._sessions[session_id] = {"open": True, "owner": owner, "calls": {}, "results": {}}
        return session_id

    def owner_of(self, session_id):
        with self._lock:
            state = self._sessions.get(session_id)
            return state["owner"] if state else None

    def admit(self, session_id, call_id):
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None or not state["open"]:
                return "closed", None
            if call_id in state["results"]:
                return "replay", state["results"][call_id]
            if call_id in state["calls"]:
                return "in_flight", None
            if len(state["calls"]) + len(state["results"]) >= CAP:
                return "capped", None
            state["calls"][call_id] = "in_flight"
            return "new", None

    def remember(self, session_id, call_id, result):
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return
            state["results"][call_id] = result
            state["calls"].pop(call_id, None)


def stub_backend(owner_text, arguments):
    asked = arguments.get("question") if isinstance(arguments, dict) else None
    return {"spoken": f"Stub answer to: {owner_text}", "asked": asked, "receipt": "stub"}


def create_consult_blueprint(*, backend, ledger, csrf_ok, owner_id, session_owner):
    bp = Blueprint("voice_consult", __name__)

    @bp.route("/api/realtime-voice/consult", methods=["POST"])
    def consult():
        if not csrf_ok():
            return jsonify({"ok": False, "error": "csrf"}), 403
        owner = owner_id()
        if owner is None:
            return jsonify({"ok": False, "error": "identity required"}), 401
        body = request.get_json(force=True, silent=True) or {}
        session_id = body.get("session_id")
        call_id = body.get("call_id")
        name = body.get("name")
        owner_text = body.get("owner_transcript_item")
        if not _token(session_id) or not _token(call_id) or name not in {"consult", "save_note"}:
            return jsonify({"ok": False, "error": "invalid consult"}), 400
        if session_owner(session_id) != owner:
            return jsonify({"ok": False, "error": "not your session"}), 403
        if not isinstance(owner_text, str) or not owner_text.strip():
            return jsonify({"ok": False, "error": "owner text required"}), 400
        status, prior = ledger.admit(session_id, call_id)
        if status == "replay":
            return jsonify(prior)
        if status == "in_flight":
            return jsonify({"ok": False, "error": "in flight"}), 409
        if status == "closed":
            return jsonify({"ok": False, "error": "session ended"}), 409
        if status == "capped":
            return jsonify({"ok": False, "error": "cap", "spoken": CAP_LINE}), 429
        started = time.monotonic()
        result = backend(owner_text, body.get("arguments") or {})
        result = {"ok": True, "call_id": call_id, **result}
        if time.monotonic() - started >= HOLD_AFTER_SECONDS:
            result["holding_line"] = HOLDING_LINE
        ledger.remember(session_id, call_id, result)
        return jsonify(result)

    @bp.route("/api/realtime-voice/session/<session_id>/end", methods=["POST"])
    def end(session_id):
        if not csrf_ok():
            return jsonify({"ok": False, "error": "csrf"}), 403
        owner = owner_id()
        if owner is None or session_owner(session_id) != owner:
            return jsonify({"ok": False, "error": "not your session"}), 403
        ledger.close_session(session_id)
        return jsonify({"ok": True, "ended": True})

    return bp


_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,80}")


def _token(value):
    """A session or call id: ASCII letters, digits, '-' and '_' (OpenAI's call ids are call_…), at most 80."""
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None
