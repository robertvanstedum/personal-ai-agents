"""The Shop floor's JSON API, version 1 (spec §5, binding rule B8).

Base: <mount>/api/v1. Every answer is JSON; guard refusals are 401/403 JSON
(never a redirect); every write needs the CSRF token and the record mode
(security.py). A native app can use this API as it is.
"""
from __future__ import annotations

import re
import time

from flask import jsonify, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from . import cfg, floor_state, owner_api
from .adapters import STATUSES, normalize
from .adapters.contract import now_iso
from .markdown_render import with_html
from .mc.turn_log import turn_log_of
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


# ── conversations (Guild 1.1 slice 2): file first, one owner, no model calls ──

CONV_WORDS = {
    "unavailable": "Conversations are unavailable right now; nothing was changed.",
    "not_found": "There is no such conversation. Nothing was changed.",
}


def _conversations():
    from .conversations import conversations_of
    return conversations_of(cfg()["services"])


def _conversation(cid):
    """(conversation, refusal). A request that names no conversation is the
    Shop floor thread, as before conversations existed (older clients, the
    old thread); the page always names its conversation."""
    from .conversations import LEGACY_ID, ConversationNotFound, ConversationStoreUnavailable
    try:
        return _conversations().get(cid or LEGACY_ID, _principal()), None
    except ConversationNotFound:
        return None, json_error("not_found", CONV_WORDS["not_found"], 404)
    except ConversationStoreUnavailable:
        if not cid or cid == LEGACY_ID:
            # The conversation files are unreadable: the Shop floor thread still
            # works on the floor's own notes (nothing to bump, nothing to title).
            return {"id": LEGACY_ID, "legacy": True, "notes_floor": _floor().floor, "unfiled": True}, None
        return None, json_error("unavailable", CONV_WORDS["unavailable"], 503)


def _conv_floor(conv):
    """The floor store for a conversation's notes."""
    base = _floor()
    nf = (conv or {}).get("notes_floor")
    return base if not nf or nf == base.floor else base.for_floor(nf)


def _touch(conv, first_text=None):
    """Bump a conversation after a kept note or reply; never fails the write."""
    from .conversations import ConversationNotFound, ConversationStoreUnavailable
    if conv.get("unfiled"):
        return conv
    try:
        return _conversations().touch(conv["id"], _principal(), first_text=first_text)
    except (ConversationNotFound, ConversationStoreUnavailable):
        return conv


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
    conv, refusal = _conversation(request.args.get("conversation"))
    if refusal is not None:
        return refusal
    res = _conv_floor(conv).list_notes(before=before, limit=max(1, min(limit or 50, 200)))
    data = res.data if res.ok else {}
    if res.ok:
        turn_log_of(cfg()["services"]).annotate(data.get("notes"))
        with_html(data.get("notes"))
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
    conv, refusal = _conversation(body.get("conversation_id"))
    if refusal is not None:
        return refusal
    context = body.get("context") if isinstance(body.get("context"), dict) else {}
    item_ref = context.get("item_ref")
    item_ref = item_ref if isinstance(item_ref, int) and not isinstance(item_ref, bool) and 0 < item_ref < 10**9 else None
    try:
        done = _conv_floor(conv).add_note(request_id, text, _author(), area=_short(context.get("area")),
                                          item_ref=item_ref, page=_short(context.get("page"), 40))
    except FloorStoreUnavailable as exc:
        return _store_down(exc, NOTE_WORDS)
    if done.outcome == "idempotency_mismatch":
        return _mismatch()
    with_html([done.value] if done.value else None)
    from .conversations import public
    conv = _touch(conv, first_text=text) if not done.repeated else conv
    return jsonify({"result": "kept", "repeated": done.repeated, "note": done.value, "message": NOTE_WORDS["kept"],
                    "conversation": public(conv), "observed_at": now_iso()})


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
    "already": "Master Craftsman already answered this note · shown from the record; nothing was sent again.",
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
    conv, refusal = _conversation(body.get("conversation_id"))
    if refusal is not None:
        return refusal
    floor = _conv_floor(conv)
    try:
        note = floor.get_note(note_id)
    except FloorStoreUnavailable as exc:
        return json_error("unavailable", MC_WORDS["notes_down"], 503,
                          reason="not_configured" if isinstance(exc, FloorStoreNotConfigured) else "unavailable")
    if note is None:
        return json_error("not_found", MC_WORDS["no_note"], 404)
    if note.get("author_kind") != "owner" or note.get("who") != principal:
        return json_error("not_allowed", MC_WORDS["not_yours"], 403)
    answer, status = run_mc_turn(services, conversations=_conversations(), floor=floor, conv=conv,
                                 principal=principal, note=note, note_id=note_id)
    return jsonify(answer), status


@owner_api
def conversations_list():
    from .conversations import ConversationStoreUnavailable, public
    view = request.args.get("view", "active")
    if view not in ("active", "archived"):
        return json_error("invalid", "view is active or archived.", 422)
    try:
        rows = _conversations().list(_principal(), archived=view == "archived")
    except ConversationStoreUnavailable:
        return json_error("unavailable", CONV_WORDS["unavailable"], 503)
    return jsonify({"view": view, "conversations": [public(c) for c in rows], "observed_at": now_iso()})


def _conv_write(action):
    """Every conversation write: owner (route guard), CSRF, record mode, JSON,
    an idempotency key; then one file change. Never a model call."""
    from .conversations import ConversationNotFound, ConversationStoreUnavailable, public
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    try:
        conv, status = action(_conversations(), body)
    except ConversationNotFound:
        return json_error("not_found", CONV_WORDS["not_found"], 404)
    except ConversationStoreUnavailable:
        return json_error("unavailable", CONV_WORDS["unavailable"], 503)
    if not isinstance(conv, dict):       # a refusal (a JSON error response) from the action
        return conv
    return jsonify({"result": status, "conversation": public(conv), "observed_at": now_iso()})


@owner_api
def conversation_create():
    def action(store, body):
        work_item = None
        ref = body.get("about_item")
        if ref is not None:
            if not isinstance(ref, int) or isinstance(ref, bool) or not 0 < ref < 10**9:
                return json_error("invalid", "about_item is a queue item number.", 422), None
            res = cfg()["services"].queue.get_item(ref) if hasattr(cfg()["services"].queue, "get_item") else None
            item = res.data if res is not None and getattr(res, "ok", False) else None
            label = f"#{ref} {item.get('title')}" if isinstance(item, dict) and item.get("title") else f"#{ref}"
            work_item = {"kind": "item", "ref": str(ref), "label": label[:120],
                         "href": url_for(".item", item_id=ref)}
        conv, repeated = store.create(_principal(), key=body["_key"], work_item=work_item)
        return conv, ("repeated" if repeated else "created")
    return _conv_write(action)


@owner_api
def conversation_rename(cid):
    from .conversations import clean_title

    def action(store, body):
        title = clean_title(body.get("title"))
        if title is None:
            return json_error("invalid", "A conversation needs a title (1 to 80 characters).", 422), None
        return store.rename(cid, _principal(), title), "renamed"
    return _conv_write(action)


def _flag(cid, change, status):
    def action(store, body):
        return change(store), status
    return _conv_write(action)


@owner_api
def conversation_pin(cid):
    return _flag(cid, lambda st: st.set_pinned(cid, _principal(), True), "pinned")


@owner_api
def conversation_unpin(cid):
    return _flag(cid, lambda st: st.set_pinned(cid, _principal(), False), "unpinned")


@owner_api
def conversation_archive(cid):
    """Remove from list: archive (a view action; nothing is erased)."""
    return _flag(cid, lambda st: st.set_archived(cid, _principal(), True), "archived")


@owner_api
def conversation_restore(cid):
    return _flag(cid, lambda st: st.set_archived(cid, _principal(), False), "restored")


def run_mc_turn(services, *, conversations, floor, conv, principal, note, note_id) -> tuple[dict, int]:
    """One Master Craftsman turn for a kept owner note, after the route's
    guards (write guard, turn gate, the note is this owner's): one reply per
    note, MC's state must allow turns, one turn in flight per owner, the note
    scrubbed again, only an answered turn kept. Returns (answer, HTTP status).
    Used by the /mc/turns route and by the operator-only cost probe
    (mc/cost_probe.py), which reaches it only through docker exec."""
    import hashlib
    import uuid

    from .mc import NotAnAnswer, TurnRequest, keep_reply

    # One reply per note (#251 review F1): a note that already has a kept
    # reply is answered from the store, and Master Craftsman is not called
    # again (from stage C every call is paid).
    reply_key = "mc-" + hashlib.sha256(note_id.encode("utf-8")).hexdigest()[:40]
    try:
        existing = floor.get_note(reply_key)
    except FloorStoreUnavailable as exc:
        return {"error": "unavailable", "message": MC_WORDS["notes_down"],
                "reason": "not_configured" if isinstance(exc, FloorStoreNotConfigured) else "unavailable"}, 503
    if existing is not None:
        shown = floor_state.mc_view(services, notes_ok=True)
        turn_log_of(services).annotate([existing])
        with_html([existing])
        return {"turn_id": None, "status": "answered", "repeated": True,
                "backend_kind": "stub" if existing.get("who") == "master_craftsman_stub" else None,
                "failure_class": None, "reply_note": existing, "observed_at": now_iso(),
                "mc_state": shown["state"], "mc_header": shown["header"],
                "message": MC_WORDS["already"]}, 200
    shown = floor_state.mc_view(services, notes_ok=True)
    if not shown["turns"]:
        return {"error": "mc_unavailable", "message": f"{shown['header']}. Your note is kept; nothing was sent to Master Craftsman.",
                "mc_state": shown["state"], "reason": shown["reason"]}, 503
    with _MC_LOCK:
        if principal in _MC_INFLIGHT:
            return {"error": "busy", "message": MC_WORDS["busy"], "mc_state": shown["state"]}, 409
        _MC_INFLIGHT.add(principal)
    turn_id = uuid.uuid4().hex
    try:
        from .conversations import session_conversation_id
        ctx = note.get("context") or {}
        # Each conversation is its own session in the backend (the adapter maps
        # the id: OpenClaw hashes it into its `user` field). Only this note is
        # sent: other conversations never reach the model's context.
        req = TurnRequest(conversation_id=session_conversation_id(conv, principal), text=scrub(note["text"]),
                          note_request_id=note_id, correlation_id=turn_id,
                          context={"about": ctx.get("area"), "item_ref": ctx.get("item_ref"), "page": ctx.get("page")})
        _mc_log().info("mc turn %s start backend=%s", turn_id, services.mc.kind)
        started = time.monotonic()
        result = services.mc.turn(req)
        duration_ms = int((time.monotonic() - started) * 1000)
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
        try:
            kept = keep_reply(floor, result, request_id=reply_key, area=ctx.get("area"),
                              item_ref=ctx.get("item_ref"), page=ctx.get("page"))
            outcome = getattr(kept, "outcome", None)
        except (FloorStoreUnavailable, NotAnAnswer):
            kept, outcome = None, "store_failed"
        if outcome != "kept" or kept is None or not getattr(kept, "value", None):
            # Anything but a clean keep (a store failure, an idempotency
            # mismatch, a missing row) is "not kept", said as such.
            _mc_log().warning("mc turn %s answered but not kept (%s)", turn_id, outcome)
            shown = floor_state.mc_view(services, notes_ok=True)
            return {**answer, "status": "error", "failure_class": "not_kept", "message": MC_WORDS["not_kept"],
                    "mc_state": shown["state"], "mc_header": shown["header"]}, 200
        answer["reply_note"] = kept.value
        _touch_in(conversations, conv, principal)
    turns = turn_log_of(services)
    turns.record(turn_id=turn_id, status=result.status, backend_kind=result.backend_kind, duration_ms=duration_ms,
                 failure_class=result.failure_class,
                 reply_request_id=reply_key if answer["reply_note"] else None)
    if answer["reply_note"]:
        turns.annotate([answer["reply_note"]])
        with_html([answer["reply_note"]])
    shown = floor_state.mc_view(services, notes_ok=True)
    answer.update({"mc_state": shown["state"], "mc_header": shown["header"],
                   "message": "Master Craftsman answered · kept on the record" if result.status == "answered"
                   else f"{shown['header']}. Your note is kept; Master Craftsman did not answer."})
    return answer, 200


def _touch_in(conversations, conv, principal):
    from .conversations import ConversationNotFound, ConversationStoreUnavailable
    if conv.get("unfiled"):
        return conv
    try:
        return conversations.touch(conv["id"], principal)
    except (ConversationNotFound, ConversationStoreUnavailable):
        return conv


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
    ("/conversations", "api_conversations", conversations_list, ["GET"]),
    ("/conversations", "api_conversation_create", conversation_create, ["POST"]),
    ("/conversations/<cid>/rename", "api_conversation_rename", conversation_rename, ["POST"]),
    ("/conversations/<cid>/pin", "api_conversation_pin", conversation_pin, ["POST"]),
    ("/conversations/<cid>/unpin", "api_conversation_unpin", conversation_unpin, ["POST"]),
    ("/conversations/<cid>/archive", "api_conversation_archive", conversation_archive, ["POST"]),
    ("/conversations/<cid>/restore", "api_conversation_restore", conversation_restore, ["POST"]),
    ("/", "api_root", not_found, ALL_METHODS),
    ("/<path:rest>", "api_not_found", not_found, ALL_METHODS),
]
