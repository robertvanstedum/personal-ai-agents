"""The Shop floor's JSON API, version 1 (spec §5, binding rule B8).

Base: <mount>/api/v1. Every answer is JSON; guard refusals are 401/403 JSON
(never a redirect); every write needs the CSRF token and the record mode
(security.py). A native app can use this API as it is.
"""
from __future__ import annotations

import re

from flask import jsonify, request

from . import cfg, floor_state, owner_api
from .adapters import STATUSES, normalize
from .adapters.contract import now_iso
from .security import OFF_RECORD_TEXT, check_write, csrf_token, json_error

API_VERSION = 1
ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
_HEX64 = re.compile(r"[0-9a-f]{64}")
_OP_ID = re.compile(r"[0-9a-f]{32}")
_IDEMPOTENCY = re.compile(r"[A-Za-z0-9_-]{8,64}")

# One plain sentence per result (spec §6.4). Only ids and receipts are filled in.
# Result codes are the queue store's; "failed" and "idempotency_mismatch" come
# from its fix for review S1-S4.
SAVE_WORDS = {
    "saved": "Saved · verified · receipt {receipt}",
    "conflict": "#{item} changed since you opened it. Here is the current state; Save again if you still want it",
    "busy": "Another save is in progress. Nothing was changed. Try again in a moment",
    "uncertain": "Save not verified — check #{item}.",
    "unavailable": "The queue can't be read, so nothing was saved",
    "refused": "Save is off on this portal: the live queue folder is not attached. Nothing was saved",
    "not_found": "#{item} is not in the queue. Nothing was saved",
    "invalid": "That change is not allowed. Nothing was saved",
    "failed": "The queue could not be written, so nothing was saved. The live queue is unchanged",
    "idempotency_mismatch": "This form was already used to save a different change. Nothing was saved; reload the page and try again",
    "not_listening": OFF_RECORD_TEXT,
}
SAVE_HTTP = {"saved": 200, "conflict": 409, "busy": 503, "uncertain": 500, "unavailable": 503,
             "refused": 503, "not_found": 404, "invalid": 422, "failed": 500, "idempotency_mismatch": 409}


def save_message(result: str, item_id, receipt=None, audit=None) -> str:
    text = SAVE_WORDS.get(result, "Nothing was saved").format(item=item_id, receipt=receipt or "?")
    if result == "saved" and audit == "failed":
        text += " · history not recorded"
    return text


def _principal() -> str:
    user = cfg()["current_user"]() or {}
    return user.get("username") or "owner"


@owner_api
def session_view():
    c = cfg()
    user = c["current_user"]() or {}
    return jsonify({
        "api_version": API_VERSION,
        "user": {"username": user.get("username"), "display_name": user.get("display_name"),
                 "tier": user.get("tier")},
        "csrf_token": csrf_token(),
        "mc_state": floor_state.MC_STATE,
        "record_modes": ["on_record", "off_record"],
        "server_time": now_iso(),
        "base": f"{c['url_prefix']}/api/v1",
    })


@owner_api
def floor_view():
    state = floor_state.compute(cfg())
    tag = floor_state.etag(state)
    # Only a real tag match is "not modified"; "*" never is (review F11).
    if tag in request.if_none_match.as_set(include_weak=True):
        response = jsonify({})
        response.status_code = 304
        response.set_etag(tag)
        return response
    response = jsonify(state)
    response.set_etag(tag)
    return response


@owner_api
def queue_view():
    services = cfg()["services"]
    res = services.queue.list_items()
    checks_res = services.queue.checks()
    return jsonify({**res.meta(), "items": res.data if res.ok else None,
                    "checks": checks_res.data if checks_res.ok else None, "checks_source": checks_res.meta(),
                    "statuses": list(STATUSES)})


@owner_api
def item_view(item_id: int):
    res = cfg()["services"].queue.get_item(item_id)
    if res.ok and res.data is None:
        return json_error("not_found", f"#{item_id} is not in the queue.", 404)
    return jsonify({**res.meta(), "item": res.data if res.ok else None})


@owner_api
def history_view(item_id: int):
    res = cfg()["services"].history.history(item_id)
    return jsonify({**res.meta(), "history": res.data if res.ok else None})


@owner_api
def save_status(item_id: int):
    c = cfg()
    refusal = check_write(c["base_url"])
    if refusal is not None:
        return refusal
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return json_error("invalid", "The request body must be a JSON object.", 422)
    to = body.get("to")
    note = body.get("note")
    expect = body.get("expect_item_digest")
    key = body.get("idempotency_key")
    if to not in STATUSES:
        return json_error("invalid", save_message("invalid", item_id), 422, result="invalid")
    if note is not None and (not isinstance(note, str) or len(note) > 500):
        return json_error("invalid", "The note must be text of at most 500 characters.", 422, result="invalid")
    if not isinstance(expect, str) or not _HEX64.fullmatch(expect):
        return json_error("invalid", "This page was out of date; reload and try again", 422, result="invalid")
    # Required (review F9): the page sends one key per opened form or change, so a
    # repeated click or a retry replays the first receipt instead of writing twice.
    if not isinstance(key, str) or not _IDEMPOTENCY.fullmatch(key):
        return json_error("invalid", "Every Save needs an idempotency key (8 to 64 letters, digits, - or _). "
                          "Nothing was saved.", 422, result="invalid")
    note = (note or "").strip() or None
    services = c["services"]

    def audit(_item, old, new):
        if services.audit is None:
            return "skipped"
        return services.audit(item_id, old, new, note)

    result = services.store.save_status(
        item_id, to, expect_item_digest=expect, note=note, principal=_principal(),
        via="guild-next", idempotency_key=key, audit=audit)
    current = result.current_item
    payload = {
        "result": result.result,
        "verified": bool(result.verified),
        "receipt_id": result.receipt_id,
        "audit": result.audit if result.ok else None,
        "repeated": bool(result.repeated),
        "item": normalize(current) if isinstance(current, dict) and isinstance(current.get("id"), int) else None,
        "item_digest": result.current_item_digest,
        "observed_at": now_iso(),
        "message": save_message(result.result, item_id, result.receipt_id, result.audit),
    }
    status = SAVE_HTTP.get(result.result, 500)
    if status >= 400:
        payload["error"] = "unavailable" if result.result == "refused" else result.result
    if result.result == "busy":
        payload["retry_after_s"] = 5
    response = jsonify(payload)
    response.status_code = status
    if result.result == "busy":
        response.headers["Retry-After"] = "5"
    return response


@owner_api
def mark_checked(op_id: str):
    c = cfg()
    refusal = check_write(c["base_url"])
    if refusal is not None:
        return refusal
    if not _OP_ID.fullmatch(op_id or ""):
        return json_error("not_found", "No open Check with that id.", 404)
    outcome = c["services"].store.mark_checked(op_id, _principal())
    if outcome is True or outcome == "checked":
        return jsonify({"result": "checked", "op_id": op_id, "message": "Marked checked",
                        "observed_at": now_iso()})
    if outcome in (False, "invalid"):
        return json_error("not_found", "No open Check with that id.", 404)
    if outcome == "busy":
        return json_error("busy", SAVE_WORDS["busy"], 503, retry_after_s=5)
    if outcome == "refused":
        return json_error("unavailable", "The live queue folder is not attached. Nothing was changed.", 503)
    return json_error("failed", "The journal could not be written. Nothing was changed.", 500)


@owner_api
def not_found(rest: str = ""):
    return json_error("not_found", "No such Guild API resource.", 404)


RULES = [
    ("/session", "api_session", session_view, ["GET"]),
    ("/floor", "api_floor", floor_view, ["GET"]),
    ("/queue", "api_queue", queue_view, ["GET"]),
    ("/queue/items/<int:item_id>", "api_item", item_view, ["GET"]),
    ("/queue/items/<int:item_id>/history", "api_history", history_view, ["GET"]),
    ("/queue/items/<int:item_id>/status", "api_save_status", save_status, ["POST"]),
    ("/queue/journal/<op_id>/checked", "api_mark_checked", mark_checked, ["POST"]),
    ("/", "api_root", not_found, ALL_METHODS),
    ("/<path:rest>", "api_not_found", not_found, ALL_METHODS),
]
