"""One consult route for the voice bench and, later, MC.

The backend is swappable. The bench uses a stub. MC will pass its typed turn
service. A consult has no authority the backend does not already have.
"""
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
        self._results = {}

    def open_session(self, session_id):
        with self._lock:
            self._sessions.setdefault(session_id, {"open": True, "calls": set()})

    def close_session(self, session_id):
        with self._lock:
            state = self._sessions.setdefault(session_id, {"open": False, "calls": set()})
            state["open"] = False

    def admit(self, session_id, call_id):
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None or not state["open"]:
                return "closed", None
            if call_id in self._results:
                return "replay", self._results[call_id]
            if len(state["calls"]) >= CAP:
                return "capped", None
            state["calls"].add(call_id)
            return "new", None

    def remember(self, call_id, result):
        with self._lock:
            self._results[call_id] = result


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
        if status == "closed":
            return jsonify({"ok": False, "error": "session ended"}), 409
        if status == "capped":
            return jsonify({"ok": False, "error": "cap", "spoken": CAP_LINE}), 429
        started = time.monotonic()
        result = backend(owner_text, body.get("arguments") or {})
        result = {"ok": True, "call_id": call_id, **result}
        if time.monotonic() - started >= HOLD_AFTER_SECONDS:
            result["holding_line"] = HOLDING_LINE
        ledger.remember(call_id, result)
        return jsonify(result)

    return bp


def _token(value):
    return isinstance(value, str) and 1 <= len(value) <= 80 and value.replace("-", "").isalnum()
