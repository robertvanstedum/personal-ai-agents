"""The Shop floor's JSON API, version 1 (spec §5, binding rule B8).

Base: <mount>/api/v1. Every answer is JSON; guard refusals are 401/403 JSON
(never a redirect); every write needs the CSRF token and the record mode
(security.py). A native app can use this API as it is.
"""
from __future__ import annotations

import os
import re
import time

from flask import current_app, g, jsonify, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from . import cfg, floor_state, owner_api
from .adapters import ACTIVE, STATUSES, normalize
from domains.guild.queue_store import CREATE_STATUSES, valid_rank, TITLE_MAX, TROUBLE
from .adapters.contract import now_iso
from .markdown_render import with_html
from .mc.turn_capture import capturer
from .mc.turn_log import turn_log_of
from .payment_scrub import scrub
from .security import OFF_RECORD_TEXT, check_private, check_stop, check_upload, check_write, csrf_token, json_error
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
    """The queue. ``?scope=all`` (Guild 1.1 slice 2, the Build Log) answers every
    status; ``?scope=active`` only Spec Ready and In Build (plus unknown rows,
    which are never dropped). Without ``scope`` the answer is exactly what it
    was before slice 2, for compatibility (it already listed every row). Every
    answer carries ``rank_digest``, the collection's unchanged-check for a rank
    change (spec §4.3)."""
    services = cfg()["services"]
    scope = request.args.get("scope", "default")
    if scope not in ("default", "active", "all"):
        return json_error("invalid", "scope is active or all.", 422)
    res = services.queue.list_items()
    checks_res = services.queue.checks()
    items = None
    if res.ok:
        items = res.data
        if scope == "active":
            items = [i for i in res.data if not i["status_known"] or i["status"] in ACTIVE]
    return jsonify({**res.meta(), "items": items, "scope": scope,
                    "total": len(res.data) if res.ok else None,
                    "rank_digest": getattr(res, "rank_digest", None) if res.ok else None,
                    "checks": checks_res.data if checks_res.ok else None, "checks_source": checks_res.meta(),
                    "statuses": list(STATUSES), "trouble_statuses": list(TROUBLE),
                    "create_statuses": list(CREATE_STATUSES)})


@owner_api
def item_view(item_id: int):
    res = cfg()["services"].queue.get_item(item_id)
    if res.ok and res.data is None:
        return json_error("not_found", f"#{item_id} is not in the queue.", 404)
    return jsonify({**res.meta(), "item": res.data if res.ok else None})


@owner_api
def journal_view(item_id: int):
    """The item's history from the file journal (spec §4.4): from, to,
    principal, via, at and receipt, oldest first. Unknown when unreadable."""
    res = cfg()["services"].queue.journal(item_id)
    return jsonify({**res.meta(), "journal": res.data if res.ok else None})


RANK_WORDS = {
    "saved": "Rank saved · verified · receipt {receipt}",
    "conflict": "The ranking changed since you opened it. Here is the current ranking; set the rank again if you still want it",
}


def _store_answer(result, message: str, **extra):
    """A queue store result as JSON, with the usual HTTP codes and Retry-After."""
    payload = {"result": result.result, "verified": bool(result.verified), "receipt_id": result.receipt_id,
               "repeated": bool(result.repeated), "observed_at": now_iso(), "message": message, **extra}
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


def _current_ranking():
    res = cfg()["services"].queue.list_items()
    if not res.ok:
        return None, None
    pairs = sorted([i["id"], i["owner_rank"]] for i in res.data if i.get("owner_rank"))
    return pairs, getattr(res, "rank_digest", None)


@owner_api
def set_rank(item_id: int):
    """Owner rank group, or none (spec §4.3): one guarded write behind the rank
    digest; a conflict answers the current ranking. No model call."""
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    rank = body.get("rank")
    expect = body.get("expect_rank_digest")
    if rank is not None and not valid_rank(rank):
        return json_error("invalid", "Use a positive whole number or clear the rank. Nothing was changed.", 422, result="invalid")
    if not isinstance(expect, str) or not _HEX64.fullmatch(expect):
        return json_error("invalid", "This page was out of date; reload and try again", 422, result="invalid")
    result = cfg()["services"].store.set_rank(item_id, rank, expect_rank_digest=expect, principal=_principal(),
                                             via="guild-next", idempotency_key=body["_key"])
    ranking, digest = (result.ranking, result.rank_digest) if result.ranking is not None else _current_ranking()
    words = RANK_WORDS.get(result.result) or save_message(result.result, item_id, result.receipt_id)
    return _store_answer(result, words.format(receipt=result.receipt_id or "?"), item_id=item_id, rank=rank,
                         ranking=ranking, rank_digest=digest, changed=result.changed)


@owner_api
def create_item():
    """A new item through the queue store (spec §4.5): idea, design or backlog;
    the next id under the lock; journaled. Never the legacy DB route."""
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    title, bad = _clean_text(body.get("spec_title"), TITLE_MAX, "title")
    if bad is not None:
        return bad
    status = body.get("status", "idea")
    if status not in CREATE_STATUSES:
        return json_error("invalid", "A new item starts as idea, design or backlog. Nothing was added.", 422,
                          result="invalid")
    result = cfg()["services"].store.create_item(title, status, principal=_principal(), via="guild-next",
                                                 idempotency_key=body["_key"])
    item = result.current_item
    new_id = result.item_id if result.ok else None
    if result.ok and result.repeated:
        found = cfg()["services"].queue.get_item(new_id)
        item_out = found.data if found.ok else None
    else:
        item_out = normalize(item) if isinstance(item, dict) and isinstance(item.get("id"), int) else None
    message = (f"#{new_id} added · verified · receipt {result.receipt_id}" if result.ok
               else save_message(result.result, "new item", result.receipt_id))
    return _store_answer(result, message, item=item_out, item_id=new_id)


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
    if to in TROUBLE and not note:
        return json_error("invalid", "Blocked and Rework need a reason. Nothing was saved.", 422, result="invalid")
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
    body = request.get_json(silent=True)
    if not cid and isinstance(body, dict) and body.get("chat_scope"):
        return None, json_error("unavailable", "This area conversation is unavailable. Reload to retry.", 503)
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


def _skeleton(text: str) -> str:
    """Letters and digits only, lower case, single spaces: what a selection on
    the rendered page and the stored Markdown have in common."""
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text.lower()).split())


PIN_MIN_CHARS = 3


def _pinned_from(selection: str, note_text: str) -> bool:
    """The pinned text is part of the note: its skeleton appears in the note's,
    and it has at least PIN_MIN_CHARS letters or digits (or is the whole note),
    so a stray letter or two cannot pass as a quote."""
    part, whole = _skeleton(selection), _skeleton(note_text)
    if not part or part not in whole:
        return False
    return len(part.replace(" ", "")) >= PIN_MIN_CHARS or part == whole


PIN_REFUSED = ("Not pinned: the pinned text must come from one kept, on-the-record message. Select text inside "
               "a single message and try again. Nothing was changed.")


@owner_api
def postit_add():
    """A post-it: text (<= 280), optionally a label and a linked queue item.
    Pinned from Chat (Guild 1.1 slice 3, the slice 1 review's carry-forward),
    it names ``source_note_id``: the server checks that the note is a stored,
    on-the-record note on this floor or one of its conversations, and that
    the pinned text is part of it; otherwise nothing is kept."""
    from .board import LABELS
    from .board_api import _check_item_ref
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    text, bad = _clean_text(body.get("text"), POSTIT_MAX, "post-it")
    if bad is not None:
        return bad
    label = body.get("label")
    if label is not None and label not in LABELS:
        return json_error("invalid", f"A label is one of {', '.join(LABELS)}, or none.", 422)
    item_ref, bad = _check_item_ref(body.get("item_ref"))
    if bad is not None:
        return bad
    source = body.get("source_note_id")
    try:
        if source is not None:
            if isinstance(source, bool) or not isinstance(source, int) or source <= 0:
                return json_error("invalid", PIN_REFUSED, 422)
            note = _floor().note_text(source)
            if note is None or not _pinned_from(text, note["text"]):
                return json_error("not_from_note", PIN_REFUSED, 422)
        done = _floor().add_postit(text, _author(), idempotency_key=body["_key"], label=label, item_ref=item_ref)
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


def _mc_acquire(principal) -> bool:
    with _MC_LOCK:
        if principal in _MC_INFLIGHT:
            return False
        _MC_INFLIGHT.add(principal)
        return True


def _mc_release(principal) -> None:
    with _MC_LOCK:
        _MC_INFLIGHT.discard(principal)


def _mc_prelude():
    """The guards both turn endpoints share. Returns (refusal, None) or
    (None, (services, principal, conv, floor, note, note_id, request_id, attachment_ids))."""
    if request.content_length is not None and request.content_length > MAX_BODY:
        return _too_large(), None
    refusal = check_write(cfg()["base_url"])
    if refusal is not None:
        return refusal, None
    services = cfg()["services"]
    if not services.mc_turns:
        state = floor_state.mc_view(services, notes_ok=True)["state"]
        return json_error("mc_turns_off", MC_WORDS["off"], 409, mc_state=state), None
    body = request.get_json(silent=True)
    note_id = body.get("note_request_id") if isinstance(body, dict) else None
    if not isinstance(note_id, str) or not _IDEMPOTENCY.fullmatch(note_id):
        return json_error("invalid", "Name the kept note to send (note_request_id). Nothing was sent to Master Craftsman.", 422), None
    request_id = body.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or not _IDEMPOTENCY.fullmatch(request_id)):
        return json_error("invalid", "request_id is an idempotency key. Nothing was sent to Master Craftsman.", 422), None
    principal = _principal()
    conv, refusal = _conversation(body.get("conversation_id"))
    if refusal is not None:
        return refusal, None
    floor = _conv_floor(conv)
    try:
        note = floor.get_note(note_id)
    except FloorStoreUnavailable as exc:
        return json_error("unavailable", MC_WORDS["notes_down"], 503,
                          reason="not_configured" if isinstance(exc, FloorStoreNotConfigured) else "unavailable"), None
    if note is None:
        return json_error("not_found", MC_WORDS["no_note"], 404), None
    if note.get("author_kind") != "owner" or note.get("who") != principal:
        return json_error("not_allowed", MC_WORDS["not_yours"], 403), None
    from .turn_files import FilesRefused, clean_ids
    from .topic_context import clean_refs
    try:
        attachment_ids = clean_ids(body.get("attachment_ids"))
        g.topic_context = clean_refs(body.get("topic_context"))      # items the owner chose for THIS turn ("Ask Master Craftsman about this")
    except FilesRefused as exc:
        return json_error(exc.code, exc.message, exc.status), None
    return None, (services, principal, conv, floor, note, note_id, request_id, attachment_ids)


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
    turn id and outcome only, never text or tokens. A ``request_id`` already
    dispatched (on this endpoint or the stream) is never sent again."""
    refusal, found = _mc_prelude()
    if refusal is not None:
        return refusal
    services, principal, conv, floor, note, note_id, request_id, attachment_ids = found
    from .jobs_api import job_command
    command = job_command(found)                      # "run this as a job" / "stop": the server decides, no model is asked
    if command is not None:
        return command
    answer, status = run_mc_turn(services, conversations=_conversations(), floor=floor, conv=conv,
                                 principal=principal, note=note, note_id=note_id, request_id=request_id,
                                 attachment_ids=attachment_ids)
    return jsonify(answer), status


PRIVATE_TEXT_MAX = 4000
_PRIVATE_SESSION = re.compile(r"[A-Za-z0-9_-]{16,64}")
PRIVATE_NO_JOB = ("That needs a background job, and jobs are not available in Private. "
                  "Ask it in a normal conversation, where the result can be kept.")


@owner_api
def mc_private():
    """A Private question to Master Craftsman (7 Oct 2026, Robert: "there is no point writing if I don't get a response; I
    need to live with it going to a model"). The text goes to the model and the answer comes back to this page, and MiniMoi
    keeps NOTHING: no note, no reply note, no conversation, no turn log, no memory capture, no files, no job. The page holds
    the thread and forgets it. Memory inside one sitting is the page's random ``session`` id: the backend maps it to its own
    session (the agent runtime keeps that record on this Mac, which the wording says). A reply that proposes a job is
    replaced by one plain sentence; a Private question never starts work. One turn in flight per owner, like any turn."""
    import uuid

    from .mc import TurnRequest

    if request.content_length is not None and request.content_length > MAX_BODY:
        return _too_large()
    refusal = check_private(cfg()["base_url"])
    if refusal is not None:
        return refusal
    services = cfg()["services"]
    if not services.mc_turns:
        state = floor_state.mc_view(services, notes_ok=True)["state"]
        return json_error("mc_turns_off", MC_WORDS["off"], 409, mc_state=state)
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - {"text", "session"}:
        return json_error("invalid", "A Private question is {text, session}. Nothing was sent.", 422)
    session = body.get("session")
    if not isinstance(session, str) or not _PRIVATE_SESSION.fullmatch(session):
        return json_error("invalid", "A Private question needs its sitting id (16 to 64 letters, digits, - or _). Nothing was sent.", 422)
    text, refused = _clean_text(body.get("text"), PRIVATE_TEXT_MAX, "message")
    if refused is not None:
        return refused
    shown = floor_state.mc_view(services, notes_ok=True)
    if not shown["turns"]:
        return json_error("mc_unavailable", f"{shown['header']}. Nothing was sent to Master Craftsman.", 503,
                          mc_state=shown["state"], reason=shown["reason"])
    principal = _principal()
    if not _mc_acquire(principal):
        return json_error("busy", MC_WORDS["busy"], 409, mc_state=shown["state"])
    turn_id = uuid.uuid4().hex
    try:
        req = TurnRequest(conversation_id=f"private:{principal}:{session}", text=text, note_request_id=f"private-{turn_id}",
                          correlation_id=turn_id, context={"mode": "private"})
        _mc_log().info("mc private turn %s start backend=%s", turn_id, services.mc.kind)    # no text, ever
        result = services.mc.turn(req)
        _mc_log().info("mc private turn %s end status=%s class=%s", turn_id, result.status, result.failure_class)
    finally:
        _mc_release(principal)
        if services.mc_health is not None:
            services.mc_health.invalidate()
    shown = floor_state.mc_view(services, notes_ok=True)
    answer = {"turn_id": turn_id, "status": result.status, "failure_class": result.failure_class,
              "observed_at": now_iso(), "mc_state": shown["state"], "mc_header": shown["header"]}
    if result.status == "answered":
        reply = (result.text or "").strip()
        if reply.split("\n", 1)[0].startswith("JOB:"):
            reply = PRIVATE_NO_JOB
        shown_reply = {"text": reply}
        with_html([shown_reply])
        answer.update({"reply": shown_reply, "message": "Master Craftsman answered · not kept by MiniMoi"})
    else:
        answer["message"] = f"{shown['header']}. Master Craftsman did not answer. MiniMoi kept nothing."
    return jsonify(answer), 200


def _reply_key(note_id: str) -> str:
    import hashlib
    return "mc-" + hashlib.sha256(note_id.encode("utf-8")).hexdigest()[:40]


def _repeated_answer(services, existing) -> dict:
    shown = floor_state.mc_view(services, notes_ok=True)
    turn_log_of(services).annotate([existing])
    with_html([existing])
    return {"turn_id": None, "status": "answered", "repeated": True,
            "backend_kind": "stub" if existing.get("who") == "master_craftsman_stub" else None,
            "failure_class": None, "reply_note": existing, "observed_at": now_iso(),
            "mc_state": shown["state"], "mc_header": shown["header"], "message": MC_WORDS["already"]}


@owner_api
def mc_turn_stream():
    """The streamed Master Craftsman turn (streaming spec v0.2 §3, v0.3):
    the same guards as /mc/turns, then these pre-dispatch refusals, each a
    normal JSON answer the browser may fall back on or show: the switch
    (``stream_off``) and the backend (``stream_unsupported``); then
    ``open_mc_stream``. NDJSON: ``ack`` first, and only then the worker
    dispatches (mc/streaming.py)."""
    refusal, found = _mc_prelude()
    if refusal is not None:
        return refusal
    services, principal, conv, floor, note, note_id, request_id, attachment_ids = found
    from .jobs_api import job_command
    command = job_command(found)                      # spoken job commands are answered here, streaming or not
    if command is not None:
        return command
    if not getattr(services, "mc_stream", False):
        return json_error("stream_off", "Streaming is not switched on on this portal. Nothing was sent.", 409)
    if not getattr(services.mc, "supports_streaming", False):
        return json_error("stream_unsupported", "This Master Craftsman backend does not stream. Nothing was sent.", 409)
    if not request_id:
        return json_error("invalid", "A streamed turn needs a request_id. Nothing was sent to Master Craftsman.", 422)
    opened = open_mc_stream(services, conversations=_conversations(), floor=floor, conv=conv, principal=principal,
                            note=note, note_id=note_id, request_id=request_id, attachment_ids=attachment_ids)
    if opened[0] == "refused":
        _, body, status = opened
        return jsonify(body), status
    _, run, body = opened
    response = current_app.response_class(body, mimetype="application/x-ndjson")
    response.headers["Content-Type"] = "application/x-ndjson; charset=utf-8"
    response.headers["X-Accel-Buffering"] = "no"
    response.call_on_close(run.abandon_if_not_started)
    return response


def open_mc_stream(services, *, conversations, floor, conv, principal, note, note_id, request_id, observe=None,
                   capture: bool = True, attachment_ids=None):
    """Everything /mc/turns/stream does after its request guards, with no
    request context: an existing reply answered from the record; an earlier
    dispatch of the same ``request_id`` (``already_dispatched``); Master
    Craftsman's state; the backend's own pre-dispatch checks; one turn in
    flight. Returns ("refused", body, status) or ("stream", run, events):
    ``events`` is the NDJSON generator the browser reads (ack first; the
    worker dispatches only after it). ``observe`` wraps the backend's raw
    events (the operator-only cost probe counts finish and usage with it).
    ``capture`` is the turn capture (mc/turn_capture.py: one ``mc_turn`` line
    for an answered, live turn); the operator probes turn it off.
    Used by the route and by mc/stream_probe.py."""
    import threading
    import uuid

    from .conversations import session_conversation_id
    from .markdown_render import render_markdown
    from .mc import TurnRequest
    from .mc.stream import StreamRefused
    from .mc.stream_usage import portal_folder as portal_usage_folder
    from .mc.streaming import DISPATCHED, StreamContext, StreamRun, browser_events

    reply_key = _reply_key(note_id)
    try:
        existing = floor.get_note(reply_key)
    except FloorStoreUnavailable as exc:
        return "refused", {"error": "unavailable", "message": MC_WORDS["notes_down"],
                           "reason": "not_configured" if isinstance(exc, FloorStoreNotConfigured) else "unavailable"}, 503
    if existing is not None:
        return "refused", _repeated_answer(services, existing), 200
    shown = floor_state.mc_view(services, notes_ok=True)
    if not shown["turns"]:
        return "refused", {"error": "mc_unavailable", "mc_state": shown["state"], "reason": shown["reason"],
                           "message": f"{shown['header']}. Your note is kept; nothing was sent to Master Craftsman."}, 503
    from .turn_files import FilesRefused
    try:
        files = _turn_files(conversations, conv, principal, attachment_ids)
    except FilesRefused as exc:
        return "refused", {"error": exc.code, "message": exc.message}, exc.status
    turn_id = uuid.uuid4().hex
    ctx = note.get("context") or {}
    sent_text = scrub(note["text"])
    turn_req = TurnRequest(conversation_id=session_conversation_id(conv, principal),
                           text=_with_files(sent_text, files),
                           note_request_id=note_id, correlation_id=turn_id,
                           context={"about": ctx.get("area"), "item_ref": ctx.get("item_ref"), "page": ctx.get("page")})
    cancel = threading.Event()
    try:
        events = services.mc.stream_turn(turn_req, cancel)
    except StreamRefused as exc:
        return "refused", {"error": "mc_unavailable", "reason": exc.failure_class,
                           "message": f"{exc.message or 'Master Craftsman cannot take this turn'}. Nothing was sent."}, \
            413 if exc.failure_class == "request_too_large" else 503
    if observe is not None:
        events = observe(events)
    if not _mc_acquire(principal):
        return "refused", {"error": "busy", "message": MC_WORDS["busy"], "mc_state": shown["state"]}, 409
    earlier = DISPATCHED.claim(request_id, principal, turn_id)
    if earlier is not None:
        _mc_release(principal)
        return "refused", DISPATCHED.refusal(earlier, principal), 409
    turns = turn_log_of(services)

    def annotate(reply):
        turns.annotate([reply])
        with_html([reply])

    from .jobs_api import make_hook
    run = StreamRun(StreamContext(
        turn_id=turn_id, principal=principal, backend=services.mc, events=events, floor=floor, reply_key=reply_key,
        job_hook=make_hook(services, conv, principal, note, note_id, attachment_ids),
        context={"area": ctx.get("area"), "item_ref": ctx.get("item_ref"), "page": ctx.get("page")},
        release=lambda: _mc_release(principal), request_id=request_id, turn_log=turns, mc_health=services.mc_health,
        header=lambda: floor_state.mc_view(services, notes_ok=True),
        touch=lambda: _touch_in(conversations, conv, principal), annotate=annotate,
        usage_folder=portal_usage_folder(), cancel=cancel, files=_files_view(files),
        on_dispatch=_files_recorder(conversations, conv, principal, note_id, files),
        capture=(capturer(turn_id=turn_id, conversation_id=conv.get("id"), note=note, note_id=note_id,
                          user_text=sent_text, backend_kind=getattr(services.mc, "kind", None)) if capture else None)))
    return "stream", run, browser_events(run, render=render_markdown.__wrapped__)


@owner_api
def mc_turn_stop(turn_id):
    """The owner's Stop for a streamed turn: the write guard's JSON, origin
    and token checks, but not its record-mode check (streaming spec v0.3 N1),
    so Stop works while off the record. Ends MiniMoi's side at once and asks
    the relay to abort Master Craftsman's call."""
    from .mc.streaming import MESSAGES, RUNS
    refusal = check_stop(cfg()["base_url"])
    if refusal is not None:
        return refusal
    run = RUNS.get(turn_id) if re.fullmatch(r"[0-9a-f]{32}", turn_id or "") else None
    if run is None or run.ctx.principal != _principal():
        return json_error("not_running", "No such Master Craftsman turn is running. Nothing was changed.", 404)
    reached = run.stop()
    return jsonify({"result": "stopping", "runtime_told": reached,
                    "message": MESSAGES["stopped_confirmed"] if reached else MESSAGES["stopped"],
                    "observed_at": now_iso()})


@owner_api
def conversations_list():
    from .conversations import ConversationStoreUnavailable, public
    view = request.args.get("view", "active")
    if view not in ("active", "archived"):
        return json_error("invalid", "view is active or archived.", 422)
    try:
        store = _conversations()
        rows = store.list(_principal(), archived=view == "archived")
    except ConversationStoreUnavailable:
        return json_error("unavailable", CONV_WORDS["unavailable"], 503)
    return jsonify({"view": view, "conversations": [public(c) for c in rows], "unreadable": store.unreadable,
                    "observed_at": now_iso()})


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
        from .conversations import CHAT_SCOPES
        scope = body.get("scope")
        if scope is not None and (not isinstance(scope, str) or scope not in CHAT_SCOPES):
            return json_error("invalid", "Unknown conversation area.", 422), None
        conv, repeated = store.create(_principal(), key=body["_key"], work_item=work_item, scope=scope)
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


@owner_api
def conversation_work_item(cid):
    """Attach or replace the linked work: a number (looked up in the Build Log,
    else shown as a GitHub issue whose title is not loaded) or a link into this
    repository on GitHub. Nothing is fetched; nothing is inferred."""
    from . import linked_work

    def action(store, body):
        try:
            kind, value, fragment = linked_work.parse(body.get("ref"))
        except linked_work.LinkRefused as exc:
            return json_error("invalid", f"{exc} Nothing was changed.", 422), None
        queue_item = href = None
        if kind == "number":
            queue = cfg()["services"].queue
            res = queue.get_item(int(value)) if hasattr(queue, "get_item") else None
            data = res.data if res is not None and getattr(res, "ok", False) else None
            if isinstance(data, dict):
                queue_item, href = data, url_for(".item", item_id=int(value))
        item = linked_work.work_item(kind, value, fragment, queue_item=queue_item, item_href=href)
        return store.set_work_item(cid, _principal(), item), "linked"
    return _conv_write(action)


@owner_api
def conversation_work_item_clear(cid):
    """Remove the linked work from this one conversation. Its messages stay."""
    def action(store, body):
        return store.set_work_item(cid, _principal(), None), "cleared"
    return _conv_write(action)


@owner_api
def conversation_attach(cid):
    """Keep an uploaded image with this conversation. The Media library owns the
    bytes (owner only, sanitised, served only to the owner); this stores only
    the pointer. Master Craftsman does not receive it: nothing here reaches a model."""
    from .board_api import _media, _owner
    from .conversations import (ASSET_ID_RE, ConversationNotFound, ConversationStoreUnavailable,
                                TooManyAttachments, clean_file_name, public)
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    asset_id = body.get("asset_id")
    if not isinstance(asset_id, str) or not ASSET_ID_RE.fullmatch(asset_id):
        return json_error("invalid", "asset_id is the id of an uploaded image. Nothing was changed.", 422)
    m, owner = _media(), _owner()
    down = "Image storage is unavailable right now. Nothing was changed."
    if m is None or owner is None:
        return json_error("unavailable", down, 503)
    try:
        asset = m.get(owner, asset_id)
    except FloorStoreUnavailable:
        return json_error("unavailable", down, 503)
    if asset is None or asset.get("kind") == "emoji" or asset.get("purged_at"):
        return json_error("not_found", "That image is not in your library. Nothing was changed.", 404)
    def release():
        return _release_hold(m, owner, asset_id, cid)

    try:
        conv, held = _conversations().attach_held(cid, _principal(), asset_id, clean_file_name(body.get("name")),
                                                  lambda: m.attach(owner, asset_id, cid), release)
    except ConversationNotFound:
        return json_error("not_found", CONV_WORDS["not_found"], 404)
    except ConversationStoreUnavailable:
        return json_error("unavailable", CONV_WORDS["unavailable"], 503)
    except FloorStoreUnavailable:
        return json_error("unavailable", down, 503)
    except TooManyAttachments as exc:
        return json_error("too_many", f"A conversation keeps at most {exc.args[0]} files. Nothing was changed.", 422)
    if held.outcome not in ("attached", "already"):
        words = {"not_found": "That image is not in your library.", "gone": "That image was deleted from the library.",
                 "asset_trashed": "That image is in the library Trash. Restore it first."}
        return json_error(held.outcome if held.outcome in ("not_found", "gone") else "asset_trashed",
                          f"{words.get(held.outcome, 'It could not be kept.')} Nothing was changed.",
                          404 if held.outcome == "not_found" else 410 if held.outcome == "gone" else 409)
    return jsonify({"result": "attached", "conversation": public(conv), "observed_at": now_iso()})


def _release_hold(m, owner, asset_id, cid) -> bool:
    """True only when the Media hold is really released. Missing storage or a failed write is False."""
    if m is None or owner is None:
        return False
    try:
        return m.detach(owner, asset_id, cid).outcome in ("detached", "not_found")   # an image that is gone holds nothing
    except FloorStoreUnavailable:
        return False


@owner_api
def conversation_detach(cid):
    """Remove an image from this conversation's files. The image stays in the Media
    library; only this conversation's hold on it is released."""
    from .board_api import _media, _owner
    from .conversations import ASSET_ID_RE, ConversationNotFound, ConversationStoreUnavailable, public
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    asset_id = body.get("asset_id")
    if not isinstance(asset_id, str) or not ASSET_ID_RE.fullmatch(asset_id):
        return json_error("invalid", "asset_id is the id of an attached image. Nothing was changed.", 422)
    m, owner = _media(), _owner()
    try:
        conv, released = _conversations().detach_held(cid, _principal(), asset_id,
                                                      lambda: _release_hold(m, owner, asset_id, cid))
    except ConversationNotFound:
        return json_error("not_found", CONV_WORDS["not_found"], 404)
    except ConversationStoreUnavailable:
        return json_error("unavailable", CONV_WORDS["unavailable"], 503)
    return jsonify({"result": "detached" if released else "release_pending", "released": released,
                    "message": ("Removed from this conversation. The image is still in your library." if released else
                                "Removed from this conversation, but its hold in the library was not released yet. "
                                "Retry to release it; until then the image cannot be deleted from the library."),
                    "conversation": public(conv), "observed_at": now_iso()})


@owner_api
def conversation_retry_release(cid):
    """Finish a removal whose library hold was not released. It can only release a pending removal: if the image
    was attached again meanwhile, nothing is removed and the page is told the current state."""
    from .board_api import _media, _owner
    from .conversations import ASSET_ID_RE, ConversationNotFound, ConversationStoreUnavailable, public
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    asset_id = body.get("asset_id")
    if not isinstance(asset_id, str) or not ASSET_ID_RE.fullmatch(asset_id):
        return json_error("invalid", "asset_id is the id of a file waiting to be released. Nothing was changed.", 422)
    m, owner = _media(), _owner()
    try:
        conv, outcome = _conversations().retry_release(cid, _principal(), asset_id,
                                                       lambda: _release_hold(m, owner, asset_id, cid))
    except ConversationNotFound:
        return json_error("not_found", CONV_WORDS["not_found"], 404)
    except ConversationStoreUnavailable:
        return json_error("unavailable", CONV_WORDS["unavailable"], 503)
    words = {"released": "Released. The image is still in your library.",
             "pending": "Still not released. Try again in a moment; until then the image cannot be deleted from the library.",
             "current": "That file is attached again, so nothing was removed.",
             "nothing_pending": "Nothing is waiting to be released for that file."}
    return jsonify({"result": outcome, "released": outcome in ("released", "nothing_pending"), "message": words[outcome],
                    "conversation": public(conv), "observed_at": now_iso()})


def run_mc_turn(services, *, conversations, floor, conv, principal, note, note_id,
                request_id: str | None = None, capture: bool = True, attachment_ids=None) -> tuple[dict, int]:
    """One Master Craftsman turn for a kept owner note, after the route's
    guards (write guard, turn gate, the note is this owner's): one reply per
    note, MC's state must allow turns, one turn in flight per owner, the note
    scrubbed again, only an answered turn kept. Returns (answer, HTTP status).
    An answered, live turn that is kept also appends one ``mc_turn`` line when
    ``MC_TURNS_DIR`` is set (mc/turn_capture.py; ``capture=False`` for the
    operator probes); ``history_saved`` in the answer says whether it was
    written (absent when nothing was meant to be). A capture failure never
    changes the answer, the status or the kept notes.
    Used by the /mc/turns route and by the operator-only cost probe
    (mc/cost_probe.py), which reaches it only through docker exec."""
    import hashlib
    import uuid

    from .mc import NotAnAnswer, TurnRequest, keep_reply

    # One reply per note (#251 review F1): a note that already has a kept
    # reply is answered from the store, and Master Craftsman is not called
    # again (from stage C every call is paid).
    from .mc.streaming import DISPATCHED
    reply_key = _reply_key(note_id)
    try:
        existing = floor.get_note(reply_key)
    except FloorStoreUnavailable as exc:
        return {"error": "unavailable", "message": MC_WORDS["notes_down"],
                "reason": "not_configured" if isinstance(exc, FloorStoreNotConfigured) else "unavailable"}, 503
    if existing is not None:
        return _repeated_answer(services, existing), 200
    shown = floor_state.mc_view(services, notes_ok=True)
    if not shown["turns"]:
        return {"error": "mc_unavailable", "message": f"{shown['header']}. Your note is kept; nothing was sent to Master Craftsman.",
                "mc_state": shown["state"], "reason": shown["reason"]}, 503
    from .turn_files import FilesRefused
    try:
        files = _turn_files(conversations, conv, principal, attachment_ids)
    except FilesRefused as exc:
        return {"error": exc.code, "message": exc.message}, exc.status
    if not _mc_acquire(principal):
        return {"error": "busy", "message": MC_WORDS["busy"], "mc_state": shown["state"]}, 409
    turn_id = uuid.uuid4().hex
    if request_id:
        earlier = DISPATCHED.claim(request_id, principal, turn_id)
        if earlier is not None:
            _mc_release(principal)
            return DISPATCHED.refusal(earlier, principal), 409
    try:
        from .conversations import session_conversation_id
        ctx = note.get("context") or {}
        # Each conversation is its own session in the backend (the adapter maps
        # the id: OpenClaw hashes it into its `user` field). Only this note is
        # sent: other conversations never reach the model's context.
        sent_text = scrub(note["text"])
        req = TurnRequest(conversation_id=session_conversation_id(conv, principal), text=_with_files(sent_text, files),
                          note_request_id=note_id, correlation_id=turn_id,
                          context={"about": ctx.get("area"), "item_ref": ctx.get("item_ref"), "page": ctx.get("page")})
        _mc_log().info("mc turn %s start backend=%s files=%d", turn_id, services.mc.kind, len(files.report) if files else 0)
        _files_recorder(conversations, conv, principal, note_id, files)()
        started = time.monotonic()
        result = services.mc.turn(req)
        duration_ms = int((time.monotonic() - started) * 1000)
        job_started = None
        if result.status == "answered":
            from .jobs_api import make_hook
            hook = make_hook(services, conv, principal, note, note_id, attachment_ids)
            try:
                decision = hook.decide(result.text, clean=True) if hook is not None else None
            except Exception:
                _mc_log().exception("mc turn %s: the job proposal could not be handled", turn_id)
                decision = None
            if decision is not None:
                from .mc import TurnResult
                result = TurnResult("answered", result.backend_kind, text=decision.text, usage=result.usage, trace=result.trace)
                job_started = decision.job
        trace = result.trace or {}
        _mc_log().info("mc turn %s end status=%s class=%s echo=%s response=%s", turn_id, result.status,
                       result.failure_class, trace.get("correlation_echo") == turn_id, bool(trace.get("response_id")))
    finally:
        _mc_release(principal)
        if services.mc_health is not None:
            services.mc_health.invalidate()
    answer = {"turn_id": turn_id, "status": result.status, "backend_kind": result.backend_kind,
              "failure_class": result.failure_class, "reply_note": None, "observed_at": now_iso()}
    if files is not None and files.report:
        answer["files"] = _files_view(files)
    if job_started is not None:
        answer["job"] = job_started
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
            refused = {**answer, "status": "error", "failure_class": "not_kept", "message": MC_WORDS["not_kept"],
                       "mc_state": shown["state"], "mc_header": shown["header"]}
            DISPATCHED.finish(request_id, refused)
            return refused, 200
        answer["reply_note"] = kept.value
        _touch_in(conversations, conv, principal)
        history_saved = None
        if capture:
            try:
                history_saved = capturer(turn_id=turn_id, conversation_id=conv.get("id"), note=note, note_id=note_id,
                                         user_text=sent_text, backend_kind=result.backend_kind)(kept.value)
            except Exception:
                history_saved = False                 # R6: never changes the answer
        if history_saved is not None:
            answer["history_saved"] = history_saved
    turns = turn_log_of(services)
    turns.record(turn_id=turn_id, status=result.status, backend_kind=result.backend_kind, duration_ms=duration_ms,
                 failure_class=result.failure_class,
                 reply_request_id=reply_key if answer["reply_note"] else None,
                 history_saved=answer.get("history_saved"))
    if answer["reply_note"]:
        turns.annotate([answer["reply_note"]])
        with_html([answer["reply_note"]])
    shown = floor_state.mc_view(services, notes_ok=True)
    answer.update({"mc_state": shown["state"], "mc_header": shown["header"],
                   "message": "Master Craftsman answered · kept on the record" if result.status == "answered"
                   else f"{shown['header']}. Your note is kept; Master Craftsman did not answer."})
    DISPATCHED.finish(request_id, answer)
    return answer, 200


def _turn_files(conversations, conv, principal, ids):
    """The Built files for one turn (turn_files.build), or None when none were chosen. Raises FilesRefused before
    anything is sent: an id not in this conversation, or too many documents."""
    try:
        refs = getattr(g, "topic_context", None) or []
    except RuntimeError:                                  # no request (the operator cost probe): nothing was chosen
        refs = []
    if not ids and not refs:
        return None
    from .conversations import ConversationNotFound, ConversationStoreUnavailable, DocumentGone
    from .turn_files import Built, build

    def load(doc_id):
        try:
            return conversations.document_text(conv["id"], principal, doc_id)
        except (DocumentGone, ConversationNotFound, ConversationStoreUnavailable):
            raise LookupError(doc_id) from None

    built = build(conv, ids, load=load, scrub=scrub) if ids else Built()
    if refs:                                           # the owner's chosen topic items ride the same data block and report
        from .topic_context import build as build_context
        from .topics import topics_of
        block, report = build_context(refs, store=topics_of(cfg()["services"]), principal=principal, scrub=scrub)
        built = Built("\n\n".join(x for x in (built.block, block) if x), [*built.report, *report])
    return built


def _with_files(text: str, files) -> str:
    """The owner's words, then (only when files were chosen) the data block. The stored note and the capture keep the words."""
    return f"{text}\n\n{files.block}" if files is not None and files.block else text


def _files_view(files):
    from .turn_files import summary
    return summary(files.report) if files is not None and files.report else None


def _files_recorder(conversations, conv, principal, note_id, files):
    """A callable that saves the per-turn file report, called right before the request is sent (not earlier, so a
    refused or abandoned turn never records files as sent). A save failure is logged and never stops the turn."""
    def record():
        if files is None or not files.report or conv.get("unfiled"):
            return
        from .conversations import ConversationNotFound, ConversationStoreUnavailable
        from .turn_files import summary
        try:
            conversations.record_turn_files(conv["id"], principal, note_id, summary(files.report))
        except (ConversationNotFound, ConversationStoreUnavailable):
            _mc_log().warning("mc turn files report for note not saved")
    return record


@owner_api
def conversation_document_upload(cid):
    """Keep a document's TEXT with this conversation so Master Craftsman can read it with a message. The file is
    read in a bounded child process; only the text is kept (not the original). Images are not read here. Off the
    record nothing is read (the upload guard). Nothing here calls a model."""
    import hashlib

    from .conversations import (ConversationNotFound, ConversationStoreUnavailable, TooManyDocuments, clean_file_name,
                                public)
    from .doc_reader import MAX_UPLOAD_BYTES, Unreadable, extract_safely, refusal_for
    refusal, _key = check_upload(cfg()["base_url"])
    if refusal is not None:
        return refusal
    slack = 64 * 1024
    too_big = json_error("too_large", f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. Nothing was added.", 413)
    if request.content_length is not None and request.content_length > MAX_UPLOAD_BYTES + slack:
        return too_big
    request.max_content_length = MAX_UPLOAD_BYTES + slack
    try:
        parts = list(request.files.items(multi=True))
        fields = list(request.form.keys())
    except RequestEntityTooLarge:
        return too_big
    if len(parts) != 1 or parts[0][0] != "file" or fields:
        return json_error("invalid", "Send exactly one part, named file. Nothing was added.", 422)
    name = clean_file_name(parts[0][1].filename or "file")
    blocked = refusal_for(name)
    if blocked:
        return json_error(blocked[0], f"{blocked[1]} Nothing was added.", 415)
    raw = parts[0][1].stream.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        return too_big
    digest = hashlib.sha256(raw).hexdigest()
    store, principal = _conversations(), _principal()
    try:
        conv = store.get(cid, principal)
    except ConversationNotFound:
        return json_error("not_found", CONV_WORDS["not_found"], 404)
    except ConversationStoreUnavailable:
        return json_error("unavailable", CONV_WORDS["unavailable"], 503)
    for d in conv.get("documents") or []:
        if d.get("sha256") == digest[:12] and d.get("name") == name:
            return jsonify({"result": "already", "document": {"id": d["id"], "name": d["name"]},
                            "conversation": public(conv), "observed_at": now_iso()})
    try:
        got = extract_safely(raw, name)
    except Unreadable as exc:
        status = {"too_large": 413, "unsupported": 415, "image": 415, "unavailable": 503}.get(exc.code, 422)
        return json_error(exc.code, f"{exc.message} Nothing was added.", status)
    try:
        conv, entry = store.add_document(cid, principal, {**got, "name": name, "bytes": len(raw), "sha256": digest},
                                         got["text"], original=raw)
    except ConversationNotFound:
        return json_error("not_found", CONV_WORDS["not_found"], 404)
    except ConversationStoreUnavailable:
        return json_error("unavailable", CONV_WORDS["unavailable"], 503)
    except TooManyDocuments as exc:
        return json_error("too_many", f"A conversation keeps at most {exc.args[0]} documents. Remove one first. Nothing was added.", 422)
    words = (f"Read {entry['chars']:,} characters" + (f" of {entry['chars_total']:,}" if entry["truncated"] else "")
             + (f" · {entry['pages_read']} of {entry['pages_total']} pages" if entry.get("pages_total") else "")
             + (" · original kept" if entry.get("has_original") else
                " · original not kept (storage full)" if entry.get("original_skipped") else ""))
    return jsonify({"result": "added", "document": {k: entry.get(k) for k in ("id", "name", "kind", "chars", "chars_total",
                                                                           "pages_read", "pages_total", "truncated",
                                                                           "has_original", "original_skipped")},
                    "message": words, "conversation": public(conv), "observed_at": now_iso()})


@owner_api
def conversation_document_original(cid, doc_id):
    """The owner's own original file, as a download. Owner and conversation are checked; the id is resolved on the
    server, never taken as a path; the answer is always an attachment with a generic type and nosniff, so an HTML or
    SVG original is never rendered in the portal's origin."""
    from urllib.parse import quote

    from .conversations import ConversationNotFound, ConversationStoreUnavailable, DocumentGone
    try:
        entry, data = _conversations().read_original(cid, _principal(), doc_id)
    except ConversationNotFound:
        return json_error("not_found", CONV_WORDS["not_found"], 404)
    except DocumentGone:
        return json_error("not_found", "No original was kept for that file. Nothing was changed.", 404)
    except ConversationStoreUnavailable:
        return json_error("unavailable", CONV_WORDS["unavailable"], 503)
    safe = re.sub(r"[^\w .()+,\-]", "_", entry.get("name") or "file")[:120] or "file"
    response = current_app.response_class(data, mimetype="application/octet-stream")
    response.headers["Content-Disposition"] = f"attachment; filename=\"{safe.encode('ascii', 'replace').decode()}\"; filename*=UTF-8''{quote(safe)}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@owner_api
def conversation_document_remove(cid):
    """Take a document out of this conversation and delete its saved text and its original (copies already made
    elsewhere, such as a job's inbox or a hand-off folder, are not deleted)."""
    from .conversations import DOC_ID_RE, ConversationNotFound, ConversationStoreUnavailable, DocumentGone, public
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    doc_id = body.get("document_id")
    if not isinstance(doc_id, str) or not DOC_ID_RE.fullmatch(doc_id):
        return json_error("invalid", "document_id is the id of a kept document. Nothing was changed.", 422)
    try:
        conv, removed = _conversations().remove_document(cid, _principal(), doc_id)
    except ConversationNotFound:
        return json_error("not_found", CONV_WORDS["not_found"], 404)
    except (DocumentGone, ConversationStoreUnavailable):
        return json_error("unavailable", CONV_WORDS["unavailable"], 503)
    return jsonify({"result": "removed" if removed else "nothing_to_remove", "conversation": public(conv),
                    "message": ("Removed. Its saved text and its original were deleted; a copy already made elsewhere, such as in a "
                                "job or a hand-off folder, is not." if removed else "That document was not there."),
                    "observed_at": now_iso()})


def _touch_in(conversations, conv, principal):
    from .conversations import ConversationNotFound, ConversationStoreUnavailable
    if conv.get("unfiled"):
        return conv
    try:
        return conversations.touch(conv["id"], principal)
    except (ConversationNotFound, ConversationStoreUnavailable):
        return conv


@owner_api
def workshop_view_api():
    """The Workshop's status refresh: files and the queue only, never a model."""
    from .workshop_view import api_view, view
    return jsonify({**api_view(view(cfg()["services"], request.args.get("item", type=int))), "observed_at": now_iso()})


from .board_api import RULES as _BOARD_RULES  # noqa: E402  (it imports helpers from this module)

RULES = [
    ("/session", "api_session", session_view, ["GET"]),
    ("/floor", "api_floor", floor_view, ["GET"]),
    ("/queue", "api_queue", queue_view, ["GET"]),
    ("/queue/items/<int:item_id>", "api_item", item_view, ["GET"]),
    ("/queue/items/<int:item_id>/history", "api_history", history_view, ["GET"]),
    ("/queue/items/<int:item_id>/status", "api_save_status", save_status, ["POST"]),
    # Guild 1.1 slice 2 (spec §4.3-4.5): rank, the file journal, and a new item.
    ("/queue/items", "api_create_item", create_item, ["POST"]),
    ("/queue/items/<int:item_id>/rank", "api_set_rank", set_rank, ["POST"]),
    ("/queue/items/<int:item_id>/journal", "api_item_journal", journal_view, ["GET"]),
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
    ("/mc/private", "api_mc_private", mc_private, ["POST"]),
    ("/mc/turns/stream", "api_mc_turn_stream", mc_turn_stream, ["POST"]),
    ("/mc/turns/<turn_id>/stop", "api_mc_turn_stop", mc_turn_stop, ["POST"]),
    ("/conversations", "api_conversations", conversations_list, ["GET"]),
    ("/workshop", "api_workshop", workshop_view_api, ["GET"]),
    ("/conversations", "api_conversation_create", conversation_create, ["POST"]),
    ("/conversations/<cid>/rename", "api_conversation_rename", conversation_rename, ["POST"]),
    ("/conversations/<cid>/pin", "api_conversation_pin", conversation_pin, ["POST"]),
    ("/conversations/<cid>/unpin", "api_conversation_unpin", conversation_unpin, ["POST"]),
    ("/conversations/<cid>/archive", "api_conversation_archive", conversation_archive, ["POST"]),
    ("/conversations/<cid>/restore", "api_conversation_restore", conversation_restore, ["POST"]),
    ("/conversations/<cid>/work-item", "api_conversation_work_item", conversation_work_item, ["POST"]),
    ("/conversations/<cid>/work-item/clear", "api_conversation_work_item_clear", conversation_work_item_clear, ["POST"]),
    ("/conversations/<cid>/attachments", "api_conversation_attach", conversation_attach, ["POST"]),
    ("/conversations/<cid>/attachments/remove", "api_conversation_detach", conversation_detach, ["POST"]),
    ("/conversations/<cid>/attachments/retry", "api_conversation_retry_release", conversation_retry_release, ["POST"]),
    ("/conversations/<cid>/documents", "api_conversation_document_add", conversation_document_upload, ["POST"]),
    ("/conversations/<cid>/documents/remove", "api_conversation_document_remove", conversation_document_remove, ["POST"]),
    ("/conversations/<cid>/documents/<doc_id>/original", "api_conversation_document_original", conversation_document_original, ["GET"]),
    # Guild 1.1 slice 3: the Board and the Media library (board_api.py).
    *_BOARD_RULES,
    ("/", "api_root", not_found, ALL_METHODS),
    ("/<path:rest>", "api_not_found", not_found, ALL_METHODS),
]

# Runbooks shares the same owner, CSRF and record-mode guards.
from .wiki import save_page as wiki_save_page
RULES.append(("/wiki/pages", "wiki_save_page", wiki_save_page, ["POST"]))

from .library import save_link as library_save_link
RULES.append(("/library/links", "library_save_link", library_save_link, ["POST"]))

# Master Craftsman jobs (overnight build, step 3): owner-only, off unless MINIMOI_GUILD_JOBS is on.
from .jobs_api import RULES as _JOB_RULES
RULES.extend((rule, endpoint, owner_api(view), methods) for rule, endpoint, view, methods in _JOB_RULES)

# The topic workshop (Workshop W1): file-first topics, items, comments and the collaboration journal.
from .topics_api import RULES as _TOPIC_RULES
RULES.extend(_TOPIC_RULES)
