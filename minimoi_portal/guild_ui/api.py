"""The Shop floor's JSON API, version 1 (spec §5, binding rule B8).

Base: <mount>/api/v1. Every answer is JSON; guard refusals are 401/403 JSON
(never a redirect); every write needs the CSRF token and the record mode
(security.py). A native app can use this API as it is.
"""
from __future__ import annotations

import re

from flask import jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from . import cfg, floor_state, owner_api
from .adapters import STATUSES, normalize
from .adapters.contract import now_iso
from .payment_scrub import scrub
from .security import OFF_RECORD_TEXT, check_write, csrf_token, json_error
from .stores import (GUILD_PLATFORM, NOTE_MAX, POSTIT_MAX, Author, FloorStoreNotConfigured,
                     FloorStoreUnavailable)

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


def _author() -> Author:
    """The signed-in owner as a writer. The API never lets a caller write as
    anyone else: Master Craftsman's post-its come through its own tool (B3)."""
    user = cfg()["current_user"]() or {}
    username = user.get("username") or "owner"
    return Author(username, "owner", user.get("display_name") or username)


@owner_api
def session_view():
    c = cfg()
    user = c["current_user"]() or {}
    return jsonify({
        "api_version": API_VERSION,
        "user": {"username": user.get("username"), "display_name": user.get("display_name"),
                 "tier": user.get("tier")},
        "csrf_token": csrf_token(),
        "mc_state": floor_state.mc_view(c["services"], notes_ok=True)["state"],
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
    if result.result == "saved" and not result.repeated:
        payload["notes_line"] = _platform_line(payload["message"], result.receipt_id, item_id)
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


def _platform_line(text: str, receipt_id: str | None, item_id: int) -> str:
    """The Save receipt as a platform line in the thread (review N3): written
    after the receipt exists, and its failure is only reported."""
    floor = cfg()["services"].floor
    if floor is None or not floor.configured() or not receipt_id:
        return "skipped"
    try:
        done = floor.add_note(f"receipt-{receipt_id}", text, GUILD_PLATFORM, area="Build Queue", item_ref=item_id,
                              page="save")
    except FloorStoreUnavailable:
        return "failed"
    return "ok" if done.outcome == "kept" else "failed"


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


# ── Notes, post-its and Continue (deliverable (c)) ──────────────────────────
NOTE_WORDS = {
    "kept": "Kept as a note",
    "unavailable": "Not saved — notes unavailable",
    "not_configured": "Not saved — notes are not configured on this portal",
}
POSTIT_WORDS = {
    "added": "Post-it added",
    "binned": "Post-it moved to the bin",
    "already_binned": "That post-it was already in the bin",
    "restored": "Post-it restored from the bin",
    "already_active": "That post-it is already on the board",
    "unavailable": "Post-its unavailable — nothing was changed",
    "not_configured": "Post-its are not configured on this portal — nothing was changed",
}


def _store_down(exc: FloorStoreUnavailable, words: dict):
    code = "not_configured" if isinstance(exc, FloorStoreNotConfigured) else "unavailable"
    return json_error("unavailable", words[code], 503, reason=code)


def _source(res) -> dict:
    """A floor-store read's meta, with "unavailable" spelled out for front ends."""
    meta = res.meta()
    meta["available"] = res.ok
    return meta


def _floor():
    return cfg()["services"].floor


# The largest body a notes, post-its or Continue write may carry (review B1c #1):
# a 2,000-character note is at most ~12 KB of JSON even with every character
# escaped, so 32 KB is ample, and nothing larger is ever read or scrubbed.
MAX_BODY = 32 * 1024


def _too_large():
    return json_error("too_large", f"This request is larger than {MAX_BODY // 1024} KB. Nothing was changed.", 413)


def _write_body():
    """(refusal, body): body size, CSRF, record mode, a JSON object, and its
    idempotency key (one per opened form or change, not per click)."""
    if request.content_length is not None and request.content_length > MAX_BODY:
        return _too_large(), None
    request.max_content_length = MAX_BODY      # also bounds a body sent without a length
    try:
        if request.content_length is None and len(request.get_data(cache=True)) >= MAX_BODY:
            return _too_large(), None          # read stopped at the limit: the body was longer
        refusal = check_write(cfg()["base_url"])
        if refusal is not None:
            return refusal, None
        body = request.get_json(silent=True)
    except RequestEntityTooLarge:
        return _too_large(), None
    if not isinstance(body, dict):
        return json_error("invalid", "The request body must be a JSON object.", 422), None
    key = body.get("idempotency_key", body.get("request_id"))
    if not isinstance(key, str) or not _IDEMPOTENCY.fullmatch(key):
        return json_error("invalid", "Every write needs an idempotency key (8 to 64 letters, digits, - or _).",
                          422), None
    if key.startswith(("receipt-", "mc-")):
        return json_error("invalid", "Keys starting with receipt- or mc- are the platform's own.", 422), None
    if "request_id" in body and "idempotency_key" in body and body["request_id"] != body["idempotency_key"]:
        return json_error("invalid", "request_id and idempotency_key disagree.", 422), None
    body["_key"] = key
    return None, body


MISMATCH = "This form was already used for a different change. Nothing was changed; reload and try again"


def _mismatch():
    return json_error("idempotency_mismatch", MISMATCH, 409, result="idempotency_mismatch")


def _clean_text(value, limit: int, what: str):
    """Refuse over-long text before the scrub ever sees it, then check the
    scrubbed text again: removing payment details can make it longer, and
    that is the writer's problem to fix (422), never a store outage."""
    if not isinstance(value, str) or not value.strip():
        return None, json_error("invalid", f"The {what} is empty.", 422)
    if len(value) > limit:
        return None, json_error("invalid", f"The {what} is longer than {limit} characters.", 422)
    clean = scrub(value.strip())
    if len(clean) > limit:
        return None, json_error(
            "invalid", f"The {what} is too long after removing payment details ({len(clean)} characters, "
                       f"at most {limit}). Nothing was kept; shorten it and send it again.", 422)
    return clean, None


def _short(value, limit: int = 60):
    """A short context field: cut to its limit before the scrub, and again after."""
    if not isinstance(value, str) or not value.strip():
        return None
    return scrub(value.strip()[:limit])[:limit]


@owner_api
def notes_list():
    before = request.args.get("before", type=int)
    limit = request.args.get("limit", default=50, type=int)
    res = _floor().list_notes(before=before, limit=max(1, min(limit or 50, 200)))
    data = res.data if res.ok else {}
    return jsonify({**_source(res), "notes": data.get("notes") if res.ok else None,
                    "more": data.get("more") if res.ok else None,
                    "message": None if res.ok else floor_state.notes_zone(res)["text"]})


@owner_api
def notes_add():
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    request_id = body["_key"]   # a note's request id is its idempotency key
    text, bad = _clean_text(body.get("text"), NOTE_MAX, "note")
    if bad is not None:
        return bad
    context = body.get("context") if isinstance(body.get("context"), dict) else {}
    item_ref = context.get("item_ref")
    item_ref = item_ref if isinstance(item_ref, int) and not isinstance(item_ref, bool) and 0 < item_ref < 10**9 else None
    try:
        done = _floor().add_note(request_id, text, _author(), area=_short(context.get("area")),
                                 item_ref=item_ref, page=_short(context.get("page"), 40))
    except FloorStoreUnavailable as exc:
        return _store_down(exc, NOTE_WORDS)
    if done.outcome == "idempotency_mismatch":
        return _mismatch()
    return jsonify({"result": "kept", "repeated": done.repeated, "note": done.value, "message": NOTE_WORDS["kept"],
                    "observed_at": now_iso()})


@owner_api
def postits_list():
    res = _floor().list_postits()
    cap = cfg()["layout"]["floor"].get("postit_cap", 4)
    return jsonify({**_source(res), "postits": res.data["postits"] if res.ok else None,
                    "bin_total": res.data["bin_total"] if res.ok else None, "cap": cap,
                    "message": None if res.ok else floor_state.postits_zone(res, cap)["text"]})


@owner_api
def postits_bin_list():
    limit = request.args.get("limit", default=100, type=int)
    res = _floor().list_bin(limit=max(1, min(limit or 100, 500)))
    return jsonify({**_source(res), "bin": res.data["bin"] if res.ok else None,
                    "total": res.data["total"] if res.ok else None,
                    "message": None if res.ok else "Bin unavailable — treat as unknown"})


def _postit_answer(done):
    if done.outcome == "idempotency_mismatch":
        return _mismatch()
    if done.outcome == "not_found":
        return json_error("not_found", "No such post-it on this floor. Nothing was changed.", 404)
    return jsonify({"result": done.outcome, "repeated": done.repeated, "postit": done.value,
                    "message": POSTIT_WORDS[done.outcome], "observed_at": now_iso()})


@owner_api
def postit_add():
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    text, bad = _clean_text(body.get("text"), POSTIT_MAX, "post-it")
    if bad is not None:
        return bad
    try:
        done = _floor().add_postit(text, _author(), idempotency_key=body["_key"])
    except FloorStoreUnavailable as exc:
        return _store_down(exc, POSTIT_WORDS)
    return _postit_answer(done)


def _postit_move(postit_id: int, action: str):
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    store = _floor()
    try:
        done = (store.bin_postit if action == "bin" else store.restore_postit)(
            postit_id, _author(), idempotency_key=body["_key"])
    except FloorStoreUnavailable as exc:
        return _store_down(exc, POSTIT_WORDS)
    return _postit_answer(done)


@owner_api
def postit_bin(postit_id: int):
    return _postit_move(postit_id, "bin")


@owner_api
def postit_restore(postit_id: int):
    return _postit_move(postit_id, "restore")


@owner_api
def continue_get():
    res = _floor().get_continue(_principal())
    zone = floor_state.continue_zone(res, res.data if res.ok else None)
    return jsonify({**_source(res), "continue": zone["target"], "text": zone["text"], "state": zone["state"]})


@owner_api
def continue_put():
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    ref = body.get("ref")
    if body.get("kind") != "item" or not isinstance(ref, int) or isinstance(ref, bool) or not 0 < ref < 10**9:
        return json_error("invalid", "Continue takes a queue item: {\"kind\": \"item\", \"ref\": <id>}.", 422)
    item = cfg()["services"].queue.get_item(ref)
    if not item.ok:
        return json_error("unavailable", "The queue can't be read, so Continue was not changed.", 503)
    if item.data is None:
        return json_error("not_found", f"#{ref} is not in the queue. Continue was not changed.", 404)
    label = f"#{ref} {item.data.get('title') or ''}".strip()[:200]
    try:
        done = _floor().set_continue(_principal(), kind="item", ref=str(ref), label=label,
                                     idempotency_key=body["_key"])
    except FloorStoreUnavailable as exc:
        return _store_down(exc, {"unavailable": "Continue unavailable — nothing was changed",
                                 "not_configured": "Continue is not configured on this portal — nothing was changed"})
    if done.outcome == "idempotency_mismatch":
        return _mismatch()
    zone = floor_state.continue_zone(item, done.value)
    return jsonify({"result": "set", "repeated": done.repeated, "continue": zone["target"], "text": zone["text"],
                    "state": "ok", "message": f"Continue: {zone['text']}", "observed_at": now_iso()})


@owner_api
def not_found(rest: str = ""):
    return json_error("not_found", "No such Guild API resource.", 404)


MC_WORDS = {
    "off": "Master Craftsman turns are not switched on on this portal. Nothing was sent to Master Craftsman.",
    "no_note": "There is no kept note with that id on this floor. Nothing was sent to Master Craftsman.",
    "not_yours": "That note is not yours to send. Nothing was sent to Master Craftsman.",
    "notes_down": "Notes are unavailable, so nothing was sent to Master Craftsman.",
    "busy": "Master Craftsman is still answering your previous note. Nothing new was sent.",
    "not_kept": "Master Craftsman answered, but the answer could not be kept, so it is not shown. Treat it as unknown.",
}
_MC_INFLIGHT: set = set()
_MC_LOCK = __import__("threading").Lock()


def _mc_log():
    import logging
    return logging.getLogger("guild_ui.mc")


@owner_api
def mc_turn():
    """Send one kept, on-the-record note to Master Craftsman, server side
    (separate-container plan stage B; MC spec v0.9 §4 relay).

    Order: the write guard (JSON, same origin, CSRF, on the record: an
    off-the-record request is refused 409 before anything else); the
    environment's turn gate; the note must be kept on this floor by this
    owner; Master Craftsman's state must allow turns; one turn in flight per
    owner. The note's text is scrubbed again before it leaves. Only an
    answered turn is kept, with the author its backend decides (a stub reply
    is the stub's, never Master Craftsman's). The answer to the browser is
    allow-listed: never a token, URL, trace or runtime id. Logs carry the
    turn id and outcome only, never text or tokens."""
    import hashlib
    import uuid
    from datetime import datetime, timezone

    from .mc import NotAnAnswer, TurnRequest, keep_reply

    if request.content_length is not None and request.content_length > MAX_BODY:
        return _too_large()
    refusal = check_write(cfg()["base_url"])
    if refusal is not None:
        return refusal
    services = cfg()["services"]
    if not services.mc_turns:
        state = floor_state.mc_view(services, notes_ok=True)["state"]
        return json_error("mc_turns_off", MC_WORDS["off"], 409, mc_state=state)
    body = request.get_json(silent=True)
    note_id = body.get("note_request_id") if isinstance(body, dict) else None
    if not isinstance(note_id, str) or not _IDEMPOTENCY.fullmatch(note_id):
        return json_error("invalid", "Name the kept note to send (note_request_id). Nothing was sent to Master Craftsman.", 422)
    principal = _principal()
    try:
        note = _floor().get_note(note_id)
    except FloorStoreUnavailable as exc:
        return json_error("unavailable", MC_WORDS["notes_down"], 503,
                          reason="not_configured" if isinstance(exc, FloorStoreNotConfigured) else "unavailable")
    if note is None:
        return json_error("not_found", MC_WORDS["no_note"], 404)
    if note.get("author_kind") != "owner" or note.get("who") != principal:
        return json_error("not_allowed", MC_WORDS["not_yours"], 403)
    shown = floor_state.mc_view(services, notes_ok=True)
    if not shown["turns"]:
        return json_error("mc_unavailable", f"{shown['header']}. Your note is kept; nothing was sent to Master Craftsman.",
                          503, mc_state=shown["state"], reason=shown["reason"])
    with _MC_LOCK:
        if principal in _MC_INFLIGHT:
            return json_error("busy", MC_WORDS["busy"], 409, mc_state=shown["state"])
        _MC_INFLIGHT.add(principal)
    turn_id = uuid.uuid4().hex
    try:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ctx = note.get("context") or {}
        req = TurnRequest(conversation_id=f"{services.floor.floor}:{principal}:{day}", text=scrub(note["text"]),
                          note_request_id=note_id, correlation_id=turn_id,
                          context={"about": ctx.get("area"), "item_ref": ctx.get("item_ref"), "page": ctx.get("page")})
        _mc_log().info("mc turn %s start backend=%s", turn_id, services.mc.kind)
        result = services.mc.turn(req)
        trace = result.trace or {}
        _mc_log().info("mc turn %s end status=%s class=%s echo=%s response=%s", turn_id, result.status,
                       result.failure_class, trace.get("correlation_echo") == turn_id, bool(trace.get("response_id")))
    finally:
        with _MC_LOCK:
            _MC_INFLIGHT.discard(principal)
        if services.mc_health is not None:
            services.mc_health.invalidate()
    answer = {"turn_id": turn_id, "status": result.status, "backend_kind": result.backend_kind,
              "failure_class": result.failure_class, "reply_note": None, "observed_at": now_iso()}
    if result.status == "answered":
        reply_key = "mc-" + hashlib.sha256(note_id.encode("utf-8")).hexdigest()[:40]
        try:
            kept = keep_reply(_floor(), result, request_id=reply_key, area=ctx.get("area"),
                              item_ref=ctx.get("item_ref"), page=ctx.get("page"))
        except (FloorStoreUnavailable, NotAnAnswer):
            _mc_log().warning("mc turn %s answered but not kept", turn_id)
            shown = floor_state.mc_view(services, notes_ok=True)
            return jsonify({**answer, "status": "error", "failure_class": "not_kept", "message": MC_WORDS["not_kept"],
                            "mc_state": shown["state"], "mc_header": shown["header"]}), 200
        answer["reply_note"] = kept.value
    shown = floor_state.mc_view(services, notes_ok=True)
    answer.update({"mc_state": shown["state"], "mc_header": shown["header"],
                   "message": "Master Craftsman answered · kept on the record" if result.status == "answered"
                   else f"{shown['header']}. Your note is kept; Master Craftsman did not answer."})
    return jsonify(answer)


RULES = [
    ("/session", "api_session", session_view, ["GET"]),
    ("/floor", "api_floor", floor_view, ["GET"]),
    ("/queue", "api_queue", queue_view, ["GET"]),
    ("/queue/items/<int:item_id>", "api_item", item_view, ["GET"]),
    ("/queue/items/<int:item_id>/history", "api_history", history_view, ["GET"]),
    ("/queue/items/<int:item_id>/status", "api_save_status", save_status, ["POST"]),
    ("/queue/journal/<op_id>/checked", "api_mark_checked", mark_checked, ["POST"]),
    ("/notes", "api_notes", notes_list, ["GET"]),
    ("/notes", "api_notes_add", notes_add, ["POST"]),
    ("/postits", "api_postits", postits_list, ["GET"]),
    ("/postits", "api_postit_add", postit_add, ["POST"]),
    ("/postits/bin", "api_postits_bin", postits_bin_list, ["GET"]),
    ("/postits/<int:postit_id>/bin", "api_postit_bin", postit_bin, ["POST"]),
    ("/postits/<int:postit_id>/restore", "api_postit_restore", postit_restore, ["POST"]),
    ("/continue", "api_continue", continue_get, ["GET"]),
    ("/continue", "api_continue_put", continue_put, ["PUT"]),
    ("/mc/turns", "api_mc_turn", mc_turn, ["POST"]),
    ("/", "api_root", not_found, ALL_METHODS),
    ("/<path:rest>", "api_not_found", not_found, ALL_METHODS),
]
