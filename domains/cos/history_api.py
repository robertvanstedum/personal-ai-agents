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


def create_blueprint(history: History, *, resolve_owner: Callable) -> Blueprint:
    bp = Blueprint("cos_history", __name__)

    def refused(exc: HistoryRefused):
        return jsonify({"error": exc.code}), _STATUS.get(exc.code, 400)

    def unavailable():
        return jsonify({"error": "history_unavailable"}), 503

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
            return jsonify({"conversation_id": cid, "conversation": history.conversation(cid)})
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

    return bp
