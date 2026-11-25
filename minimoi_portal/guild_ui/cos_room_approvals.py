"""Robert's room "yes P-XXXX" for a CoS proposal card, confirmed by the portal (#681; Lane G spec v0.2 change 2).

    POST /internal/mc/v1/rooms/cos-approve     on MC's internal listener, never on the browser portal
    X-CoS-Assertion: <assertion>               audience rooms-approve-from-cos, subject service:cos, spent once

cos-scheduler sends this only for a Guildhall turn that Records admitted with its own observe credential and whose
snapshot names a registry row: an owner approval turn in an owner-private room for a card CoS published there. The
portal is the only place that can act as Robert in the task service (the owner assertion), so it confirms here:

- the interpretation must still be waiting, with exactly the digest stored when the card was published (the card the
  owner saw), never one recomputed now;
- its source must be the verified meeting turn of this same room (store ``cos-meeting-owner``);
- the steps are ``tasks_owner_api.decide_proposal``'s, under fixed ids, so a retry after a crash finishes forward
  and never makes a second task.

The owner identity is configured (``MINIMOI_ROOMS_OWNER_AUTH_ID``), never a browser session.
"""
from __future__ import annotations

import json
import logging
import os
import re

from flask import jsonify, request

from core import portal_assertion as PA

FLAG = "MINIMOI_COS_ROOM_APPROVALS"
PATH = "/internal/mc/v1/rooms/cos-approve"
AUDIENCE = "rooms-approve-from-cos"
SUBJECT = "service:cos"
HEADER = "X-CoS-Assertion"
_IID = re.compile(r"IN-[A-Za-z0-9_-]{4,64}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_CODE = re.compile(r"P-[A-Z0-9]{4}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_log = logging.getLogger("guild_ui.cos_room_approvals")


def _decision(code, state, receipt, status=200):
    return jsonify(code=code, state=state, receipt=receipt[:500]), status


def approve(body, owner):
    """The decision for one approval, as (code, state, receipt). ``body`` is the verified request."""
    from .tasks_owner_api import _client, decide_proposal
    from services.partnership_tasks.client import TaskServiceUnavailable
    room, approval = body["room"], body["approval"]
    code, kind, iid, shown = approval["code"], approval["kind"], approval["source_ref"], approval["payload_digest"]
    client = _client({"auth_id": owner})
    if client is None:
        return code, "refused", f"Proposal {code} cannot be answered right now: task tracking is not connected. Nothing was done."
    try:
        waiting = next((p for p in client.pending() if p["interpretation_id"] == iid), None)
    except TaskServiceUnavailable:
        return code, "refused", f"Proposal {code} cannot be answered right now: the task service did not answer. Nothing was done."
    if waiting is not None:
        source = ((waiting.get("sources") or [{}])[0].get("source") or {})
        if source.get("store") != "cos-meeting-owner" or not str(source.get("id", "")).startswith(f"guildhall:{room}:"):
            return code, "refused", f"Proposal {code} was not made in this meeting, so it cannot be approved here. Nothing was done."
    response = decide_proposal("confirm" if kind == "confirm" else "decline",
                               {"interpretation_id": iid, "proposal_digest": shown}, user={"auth_id": owner})
    data = response.get_json(silent=True) or {}
    if response.status_code == 200 and data.get("state") == "confirmed":
        return code, "confirmed", (f"Confirmed {code}: {data['task_id']} is tracked, led by {str(data['lead']).upper()}. "
                                   "It shows in your task view.")
    if response.status_code == 200 and data.get("state") == "declined":
        return code, "cancelled", f"Declined {code}. Nothing starts."
    if data.get("error") == "unconfirmed":
        return code, "failed", (f"The task service did not confirm every step for {code}. Do not answer again yet; "
                                "the task view finishes it.")
    return code, "refused", data.get("message") or f"Proposal {code} was not confirmed. Nothing was done."


def attach(listener, environ):
    if environ.get(FLAG) != "1":
        return
    owner = (environ.get("MINIMOI_ROOMS_OWNER_AUTH_ID") or "").strip()
    state = environ.get("MINIMOI_MC_ROOMS_STATE_DIR") or ""
    if not owner or not state or not os.path.isabs(state):
        raise ValueError("cos_room_approvals_config_required")

    def endpoint():
        raw = request.get_data(cache=True)
        if request.headers.get("Origin") or request.mimetype != "application/json" or len(raw) > 4096:
            return jsonify(error="invalid_request"), 400
        try:
            subject = PA.verify(request.headers.get(HEADER), PA.load_key(os.environ.get("PORTAL_COS_OWNER_KEY_FILE")),
                                audience=AUDIENCE, env=os.environ.get("PORTAL_COS_OWNER_ENV"), method=request.method,
                                target=PA.canonical_target(request.path, request.query_string), body=raw,
                                spend=PA.NonceFile(os.path.join(state, "cos_room_approve_nonces.sqlite3")))
        except PA.Refused:
            return jsonify(error="unauthorized"), 401
        if subject != SUBJECT:
            return jsonify(error="unauthorized"), 401
        try:
            body = json.loads(raw)
            approval = body["approval"]
            if (set(body) != {"room", "turn_id", "approval"} or not _UUID.fullmatch(str(body["room"]))
                    or not _UUID.fullmatch(str(body["turn_id"])) or approval.get("partner") != "cos"
                    or approval.get("kind") not in ("confirm", "cancel") or not _CODE.fullmatch(str(approval.get("code")))
                    or not _IID.fullmatch(str(approval.get("source_ref")))
                    or not _DIGEST.fullmatch(str(approval.get("payload_digest")))):
                raise ValueError("invalid")
        except (ValueError, KeyError, TypeError, AttributeError):
            return jsonify(error="invalid_request"), 400
        try:
            return _decision(*approve(body, owner))
        except Exception:
            _log.exception("CoS room approval %s could not be completed", body["approval"]["code"])
            return jsonify(error="outcome_uncertain"), 503

    listener.add_url_rule(PATH, "cos_room_approve", endpoint, methods=["POST"])
