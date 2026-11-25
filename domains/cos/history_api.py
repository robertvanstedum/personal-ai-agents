"""The page's view of the canonical CoS history: list, reload, start a conversation, and the selection
(spec v0.3 section 4.19; the U6 page is Codex's lane and builds on these).

Served by the CoS backend under /ui/history/, behind the portal's proxy like every other /ui route. Nothing here calls a
model. Lists and reloads are paginated. The selected conversation is held server-side per owner and device, so
tabbing to MC and back reopens the same conversation with its text.
"""
from __future__ import annotations

from typing import Callable

from flask import Blueprint, jsonify, request

from domains.cos.history import History, HistoryRefused

_STATUS = {"conversation_not_found": 404, "identity_required": 401}


def create_blueprint(history: History, *, resolve_owner: Callable, stop_turn=None) -> Blueprint:
    bp = Blueprint("cos_history", __name__)

    def refused(exc: HistoryRefused):
        return jsonify({"error": exc.code}), _STATUS.get(exc.code, 400)

    def unavailable():
        return jsonify({"error": "history_unavailable"}), 503

    @bp.post("/ui/history/conversations/<conversation_id>/turns/<turn_id>/stop")
    def stop(conversation_id, turn_id):
        if resolve_owner(request) is None:
            return refused(HistoryRefused("identity_required"))
        if stop_turn is None:
            return jsonify({"error": "stop_unavailable"}), 503
        try:
            turn = history.stop(conversation_id, turn_id, lambda: stop_turn(turn_id))
        except HistoryRefused as exc:
            return refused(exc)
        except (OSError, RuntimeError):
            return unavailable()
        return jsonify({"turn": turn, "stop_requested": turn.get("failure_code") == "owner_stop"})

    def as_int(name: str, default: int | None) -> int | None:
        value = request.args.get(name, "")
        return int(value) if value.isdigit() else default

    @bp.get("/ui/history/conversations")
    def conversations():
        try:
            return jsonify(history.conversations(limit=as_int("limit", 50), cursor=request.args.get("cursor")))
        except OSError:
            return unavailable()

    @bp.post("/ui/history/conversations")
    def create():
        if resolve_owner(request) is None:
            return refused(HistoryRefused("identity_required"))
        body = request.get_json(silent=True) or {}
        try:
            doc = history.create(str(body.get("title") or ""), private=body.get("private") is True)
            if body.get("select") is True:
                history.select(resolve_owner(request), body.get("device"), doc["conversation_id"])
        except HistoryRefused as exc:
            return refused(exc)
        except OSError:
            return unavailable()
        return jsonify(doc), 201

    @bp.get("/ui/history/conversations/<conversation_id>/turns")
    def turns(conversation_id: str):
        try:
            return jsonify(history.turns(conversation_id, before_seq=as_int("before_seq", None),
                                         limit=as_int("limit", 50)))
        except HistoryRefused as exc:
            return refused(exc)
        except OSError:
            return unavailable()

    @bp.get("/ui/history/selection")
    def selection():
        owner = resolve_owner(request)
        if owner is None:
            return refused(HistoryRefused("identity_required"))
        try:
            cid = history.selected(owner, request.args.get("device"))
            return jsonify({"conversation_id": cid, "conversation": history.conversation(cid),
                            "voice_conversation_id": history.ensure_default()["conversation_id"]})
        except OSError:
            return unavailable()

    @bp.post("/ui/history/selection")
    def select():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(history.select(resolve_owner(request), body.get("device"),
                                          str(body.get("conversation_id") or "")))
        except HistoryRefused as exc:
            return refused(exc)
        except OSError:
            return unavailable()

    @bp.route("/ui/history/partner-settings", methods=["GET", "POST"])
    def partner_settings():
        """The shared ⋯ menu for CoS (core.model_routes.menu), the same shape as MC's: the owner's Voice, kept by MiniMoi
        for every device and chat; the Model follows it. ``conversation`` names whose last answer to describe."""
        from core.model_routes.menu import VoiceUnavailable, catalogue_or_none, choose, menu_view
        from core.partner_settings import SettingsConflict
        from domains.cos import model_route
        owner = resolve_owner(request)
        if owner is None:
            return refused(HistoryRefused("identity_required"))
        store = model_route.settings(history)
        try:
            catalogue = catalogue_or_none()
            if request.method == "GET":
                return jsonify(menu_view(catalogue, "cos", store.get(owner),
                                         model_route.last_turn(history, request.args.get("conversation"))))
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or type(body.get("revision")) is not int:
                return jsonify({"error": "settings_request_invalid"}), 400
            saved = choose(store, owner, catalogue, "cos", body.get("voice"), body["revision"])
            return jsonify({"result": "voice_selected", **menu_view(catalogue, "cos", saved,
                            model_route.last_turn(history, body.get("conversation")))})
        except VoiceUnavailable as exc:
            return jsonify({"error": "voice_unavailable", "message": exc.message}), 409
        except SettingsConflict as exc:
            return jsonify({"error": "settings_changed", "revision": exc.revision,
                            "message": "The voice changed on another device. Reload to see it."}), 409
        except (OSError, ValueError):
            return unavailable()

    @bp.route("/ui/history/preferences", methods=["GET", "POST"])
    def preferences():
        import hashlib
        from core import durable_files as fsio
        owner = resolve_owner(request)
        if owner is None:
            return refused(HistoryRefused("identity_required"))
        body = request.get_json(silent=True) if request.method == "POST" else request.args
        if not isinstance(body, dict) and request.method == "POST":
            return jsonify({"error": "json_object_required"}), 400
        device = str(body.get("device", "browser"))[:128]
        key = hashlib.sha256((str(owner) + ":" + device).encode()).hexdigest()
        path = history.root / "preferences" / (key + ".json")
        try:
            with history._locked():
                value = fsio.read_doc(path) or {"picture": "faded"}   # Review 06: the faint picture is the default
                if request.method == "POST":
                    if body.get("picture") not in ("shown", "faded", "hidden"):
                        return jsonify({"error": "invalid_picture"}), 400
                    value = {"picture": body["picture"]}
                    fsio.write_doc(path, value)
            return jsonify(value)
        except OSError:
            return unavailable()

    return bp
