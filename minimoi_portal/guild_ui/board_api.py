"""The Board's and the Media library's JSON API (Guild 1.1 slice 3, spec
§5.1-5.2), part of /api/v1. Every JSON write goes through _write_body
(<= 32 KB, CSRF, same-origin, X-Record-Mode: on_record, an idempotency key);
the upload goes through check_upload (the same checks for multipart, the key
in the Idempotency-Key header, an 8 MB streamed cap). Answers are JSON;
nothing here calls a model or the network.
"""
from __future__ import annotations

from flask import jsonify, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from . import cfg, owner_api, owner_page
from .adapters.contract import now_iso
from .api import _author, _clean_text, _floor, _principal, _source, _store_down, _write_body
from .board import LABELS, PHOTO_CAPTION_MAX
from .media import (MAX_BYTES, OWNER_RE, MediaRejected, media_of, root_problem, sanitize, thumb_key)
from .security import check_upload, json_error
from .stores import FloorStoreUnavailable

WORDS = {
    "done": "Marked done", "undone": "Back on the board", "already_done": "Already done",
    "already_active": "Already on the board", "labelled": "Label saved", "linked": "Link saved",
    "added": "Added to the Board", "moved": "Moved",
    "emptied": "Trash emptied · receipt {receipt}",
    "conflict": "This changed since you opened it. Here is the current state; nothing was changed",
    "in_trash": "That note is in the Trash. Restore it first; nothing was changed",
    "not_found": "Not found. Nothing was changed",
    "gone": "That image was deleted from the library. Nothing was changed",
    "asset_trashed": "That image is in the library Trash: restore it before placing it again. Nothing was changed",
    "idempotency_mismatch": "This form was already used for a different change. Nothing was changed; reload and try again",
    "trashed": "Moved to the library Trash", "restored": "Restored", "already_trashed": "Already in the library Trash",
    "duplicate": "Already in your library: the same image is used", "purged": "Deleted permanently · receipt {receipt}",
    "in_use": "Still used, so nothing was deleted. Remove it where it is used first",
}
HTTP = {"conflict": 409, "in_trash": 409, "asset_trashed": 409, "in_use": 409, "idempotency_mismatch": 409,
        "not_found": 404, "gone": 410}
MEDIA_DOWN = "The media library is unavailable right now. Nothing was changed"


def _int(value, *, positive=True):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if (value > 0 or not positive) else None


def _media_url(asset_id: str, variant: str) -> str:
    return f"{cfg()['url_prefix']}/media/{asset_id}/{variant}"


def _decorate(postits: list[dict] | None, items_by_id: dict | None) -> list[dict] | None:
    """Add photo URLs and what each link points at (title, or unknown when
    the queue can't be read: never a guess)."""
    if postits is None:
        return None
    for p in postits:
        if p.get("asset_id"):
            p["thumb_url"] = _media_url(p["asset_id"], "thumb")
            p["full_url"] = _media_url(p["asset_id"], "full")
        if p.get("item_ref"):
            it = (items_by_id or {}).get(p["item_ref"]) if items_by_id is not None else None
            p["item"] = ({"id": p["item_ref"], "title": it["title"], "status": it["status_label"]} if it
                         else {"id": p["item_ref"], "title": None,
                               "status": "unknown" if items_by_id is None else "not in the queue"})
    return postits


def _queue_index():
    res = cfg()["services"].queue.list_items()
    return {i["id"]: i for i in res.data} if res.ok else None


def _answer(done, *, value_key="postit", **extra):
    outcome = done.outcome
    words = WORDS.get(outcome, "Nothing was changed")
    receipt = done.value.get("receipt_id") if isinstance(done.value, dict) else None
    payload = {"result": outcome, "repeated": done.repeated, value_key: done.value,
               "message": words.format(receipt=receipt or "?"), "observed_at": now_iso(), **extra}
    status = HTTP.get(outcome, 200)
    if status >= 400:
        payload["error"] = outcome
    response = jsonify(payload)
    response.status_code = status
    return response


def _version(body):
    v = body.get("version")
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 1 else None


# ── the Board ───────────────────────────────────────────────────────────────

@owner_api
def board_view():
    res = _floor().board()
    data = res.data if res.ok else {}
    index = _queue_index() if res.ok else None
    for name in ("active", "done", "trash"):
        _decorate(data.get(name), index)
    return jsonify({**_source(res), **({k: data.get(k) for k in ("active", "done", "trash", "order_rev",
                                                                   "trash_rev", "counts")} if res.ok else
                                       {"active": None, "done": None, "trash": None}),
                    "labels": list(LABELS),
                    "message": None if res.ok else "The Board is unavailable — treat as unknown"})


def _postit_change(postit_id, fn):
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    version = _version(body)
    if version is None:
        return json_error("invalid", "Send the note's version (this page was out of date; reload).", 422)
    try:
        done = fn(body, version)
    except ValueError as exc:
        return json_error("invalid", f"That change is not allowed ({exc}). Nothing was changed.", 422)
    except FloorStoreUnavailable as exc:
        return _store_down(exc, {"unavailable": "The Board is unavailable — nothing was changed",
                                 "not_configured": "The Board is not configured on this portal — nothing was changed"})
    if done.value and isinstance(done.value, dict) and "id" in done.value:
        _decorate([done.value], _queue_index() if done.value.get("item_ref") else {})
    return _answer(done)


@owner_api
def postit_done(postit_id: int):
    return _postit_change(postit_id, lambda body, v: _floor().set_done(
        postit_id, True, _author(), expect_version=v, idempotency_key=body["_key"]))


@owner_api
def postit_undone(postit_id: int):
    return _postit_change(postit_id, lambda body, v: _floor().set_done(
        postit_id, False, _author(), expect_version=v, idempotency_key=body["_key"]))


@owner_api
def postit_label(postit_id: int):
    def fn(body, v):
        label = body.get("label")
        if label is not None and label not in LABELS:
            raise ValueError(f"a label is one of {', '.join(LABELS)}, or none")
        return _floor().set_label(postit_id, label, _author(), expect_version=v, idempotency_key=body["_key"])
    return _postit_change(postit_id, fn)


def _check_item_ref(value):
    """(item_ref, refusal). A link names a queue item that exists; linking
    only records its id and never changes it."""
    if value is None:
        return None, None
    ref = _int(value)
    if ref is None or ref >= 10**9:
        return None, json_error("invalid", "A link is a queue item number, or none.", 422)
    res = cfg()["services"].queue.get_item(ref)
    if not res.ok:
        return None, json_error("unavailable", "The queue can't be read, so the link can't be checked. "
                                               "Nothing was changed.", 503)
    if res.data is None:
        return None, json_error("invalid", f"#{ref} is not in the queue. Nothing was changed.", 422)
    return ref, None


@owner_api
def postit_link(postit_id: int):
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    ref, bad = _check_item_ref(body.get("item_ref"))
    if bad is not None:
        return bad
    version = _version(body)
    if version is None:
        return json_error("invalid", "Send the note's version (this page was out of date; reload).", 422)
    try:
        done = _floor().set_link(postit_id, ref, _author(), expect_version=version, idempotency_key=body["_key"])
    except FloorStoreUnavailable as exc:
        return _store_down(exc, {"unavailable": "The Board is unavailable — nothing was changed",
                                 "not_configured": "The Board is not configured on this portal — nothing was changed"})
    if done.value and "id" in done.value:
        _decorate([done.value], _queue_index())
    return _answer(done)


@owner_api
def postit_photo():
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    asset_id = body.get("asset_id")
    if not isinstance(asset_id, str) or len(asset_id) > 64:
        return json_error("invalid", "Name the library image (asset_id).", 422)
    caption = body.get("caption")
    if caption is not None:
        caption, bad = _clean_text(caption, PHOTO_CAPTION_MAX, "caption")
        if bad is not None:
            return bad
    try:
        done = _floor().add_photo(asset_id, _principal(), caption, _author(), idempotency_key=body["_key"])
    except FloorStoreUnavailable as exc:
        return _store_down(exc, {"unavailable": MEDIA_DOWN, "not_configured": MEDIA_DOWN})
    if done.value:
        _decorate([done.value], {})
    return _answer(done)


@owner_api
def postits_reorder():
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    pid, before, after, rev = (_int(body.get("id")), body.get("before_id"), body.get("after_id"),
                               body.get("expect_order_rev"))
    before = _int(before) if before is not None else None
    after = _int(after) if after is not None else None
    if pid is None or (before is None) == (after is None) or not isinstance(rev, int) or isinstance(rev, bool):
        return json_error("invalid", "Send id, exactly one of before_id or after_id, and expect_order_rev.", 422)
    try:
        done = _floor().reorder(pid, before_id=before, after_id=after, expect_order_rev=rev, by=_author(),
                                idempotency_key=body["_key"])
    except ValueError as exc:
        return json_error("invalid", f"That move is not allowed ({exc}). Nothing was changed.", 422)
    except FloorStoreUnavailable as exc:
        return _store_down(exc, {"unavailable": "The Board is unavailable — nothing was changed",
                                 "not_configured": "The Board is not configured on this portal — nothing was changed"})
    return _answer(done, value_key="order")


@owner_api
def trash_empty():
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    if body.get("confirm") != "empty":
        return json_error("invalid", 'Emptying the Trash needs confirm: "empty". Nothing was deleted.', 422)
    rev, items = body.get("trash_rev"), body.get("items")
    ok_items = isinstance(items, list) and len(items) <= 1000 and all(
        isinstance(i, dict) and _int(i.get("id")) and _int(i.get("version")) for i in items)
    if not isinstance(rev, int) or isinstance(rev, bool) or not ok_items:
        return json_error("invalid", "Send trash_rev and the items you saw ({id, version}). Nothing was deleted.", 422)
    try:
        done = _floor().empty_trash(trash_rev=rev, items=items, by=_author(), idempotency_key=body["_key"])
    except FloorStoreUnavailable as exc:
        return _store_down(exc, {"unavailable": "The Board is unavailable — nothing was deleted",
                                 "not_configured": "The Board is not configured on this portal — nothing was deleted"})
    key = "receipt" if done.outcome == "emptied" else "current"
    if key == "current" and isinstance(done.value, dict):
        _decorate(done.value.get("trash"), {})
    return _answer(done, value_key=key)


# ── Take to a Room (Guild 1.1 slice 4, spec §6, §8, §11) ─────────────────────

@owner_api
def note_share(note_id: int):
    """What Take to a Room shares: a stored, on-the-record note, by id only.
    The answer is the note as the server keeps it; the browser then posts it
    to the room through Records, under Records' own login. A request that
    carries any text of its own is refused: nothing from the screen is ever
    shared, and off the record nothing is answered at all (409)."""
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    if any(k in body for k in ("text", "body", "selection")):
        return json_error("invalid", "Only a kept note's id can be taken to a Room, never text. Nothing was shared.",
                          422)
    try:
        note = _floor().note_text(note_id)
    except FloorStoreUnavailable as exc:
        return _store_down(exc, {"unavailable": "Notes are unavailable right now — nothing was shared",
                                 "not_configured": "Notes are not configured on this portal — nothing was shared"})
    if note is None:
        return json_error("not_found", "No kept, on-the-record note with that id. Nothing was shared.", 404)
    note.pop("floor", None)
    return jsonify({"result": "ok", "note": note, "observed_at": now_iso()})


# ── the Media library ───────────────────────────────────────────────────────

def _media():
    m = media_of(cfg()["services"])
    if m is None:
        return None
    return m


def _owner():
    owner = _principal()
    return owner if OWNER_RE.fullmatch(owner or "") else None


def _asset_out(a: dict | None) -> dict | None:
    if a is None:
        return None
    out = {k: v for k, v in a.items() if k not in ("storage_key", "owner")}
    if a.get("state") != "purged" and a.get("kind") != "emoji":
        out["thumb_url"] = _media_url(a["id"], "thumb")
        out["full_url"] = _media_url(a["id"], "full")
    return out


@owner_api
def media_list():
    state = request.args.get("state", "active")
    kind = request.args.get("kind") or None
    if state not in ("active", "trash") or kind not in (None, "photo", "icon", "emoji"):
        return json_error("invalid", "state is active or trash; kind is photo, icon or emoji.", 422)
    m = _media()
    owner = _owner()
    if m is None or owner is None:
        return json_error("unavailable", MEDIA_DOWN, 503)
    res = m.library(owner, state=state, kind=kind)
    data = res.data if res.ok else {}
    return jsonify({**_source(res), "state": state, "kind": kind,
                    "assets": [_asset_out(a) for a in data.get("assets", [])] if res.ok else None,
                    "library_trash_rev": data.get("library_trash_rev"), "counts": data.get("counts"),
                    "files": "ok" if root_problem(m.root) is None else "unavailable",
                    "message": None if res.ok else "The media library is unavailable — treat as unknown"})


UPLOAD_SLACK = 64 * 1024        # multipart headers around the one file part


@owner_api
def media_upload():
    refusal, key = check_upload(cfg()["base_url"])
    if refusal is not None:
        return refusal
    too_big = json_error("too_large", f"The image is larger than {MAX_BYTES // (1024 * 1024)} MB. Nothing was added.", 413)
    if request.content_length is not None and request.content_length > MAX_BYTES + UPLOAD_SLACK:
        return too_big
    request.max_content_length = MAX_BYTES + UPLOAD_SLACK          # also caps a body sent without a length
    try:
        parts = list(request.files.items(multi=True))
        fields = list(request.form.keys())
    except RequestEntityTooLarge:
        return too_big
    if len(parts) != 1 or parts[0][0] != "file" or fields:
        return json_error("invalid", "Send exactly one part, named file. Nothing was added.", 422)
    raw = parts[0][1].stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        return too_big
    m, owner = _media(), _owner()
    if m is None or owner is None:
        return json_error("unavailable", MEDIA_DOWN, 503)
    problem = root_problem(m.root)
    if problem:
        return json_error("unavailable", f"{MEDIA_DOWN} ({problem}).", 503)
    try:
        item = sanitize(raw)
    except MediaRejected as exc:
        return json_error({413: "too_large", 415: "unsupported", 422: "invalid", 503: "busy"}.get(exc.status, "invalid"),
                          f"{exc.message} Nothing was added." if not exc.message.endswith("added.") else exc.message,
                          exc.status)
    try:
        done = m.create(owner, item, idempotency_key=key)
    except FloorStoreUnavailable as exc:
        return _store_down(exc, {"unavailable": MEDIA_DOWN, "not_configured": MEDIA_DOWN})
    except OSError:
        return json_error("unavailable", f"{MEDIA_DOWN} (the file could not be written).", 503)
    return _answer(done, value_key="asset") if done.outcome == "idempotency_mismatch" else jsonify({
        "result": done.outcome, "repeated": done.repeated, "asset": _asset_out(done.value),
        "message": WORDS.get(done.outcome, "Added to your library") if done.outcome != "added" else "Added to your library",
        "observed_at": now_iso()})


def _media_trash(asset_id: str, trashed: bool):
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    version = _version(body)
    if version is None:
        return json_error("invalid", "Send the image's version (this page was out of date; reload).", 422)
    m, owner = _media(), _owner()
    if m is None or owner is None:
        return json_error("unavailable", MEDIA_DOWN, 503)
    try:
        done = m.set_trashed(owner, asset_id, trashed, expect_version=version, idempotency_key=body["_key"])
    except FloorStoreUnavailable as exc:
        return _store_down(exc, {"unavailable": MEDIA_DOWN, "not_configured": MEDIA_DOWN})
    done.value = _asset_out(done.value)
    return _answer(done, value_key="asset")


@owner_api
def media_trash(asset_id: str):
    return _media_trash(asset_id, True)


@owner_api
def media_restore(asset_id: str):
    return _media_trash(asset_id, False)


@owner_api
def media_purge():
    refusal, body = _write_body()
    if refusal is not None:
        return refusal
    if body.get("confirm") != "purge":
        return json_error("invalid", 'A permanent delete needs confirm: "purge". Nothing was deleted.', 422)
    rev, items = body.get("library_trash_rev"), body.get("items")
    ok_items = isinstance(items, list) and 0 < len(items) <= 500 and all(
        isinstance(i, dict) and isinstance(i.get("id"), str) and _int(i.get("version")) for i in items)
    if not isinstance(rev, int) or isinstance(rev, bool) or not ok_items:
        return json_error("invalid", "Send library_trash_rev and the images you saw ({id, version}). "
                                     "Nothing was deleted.", 422)
    m, owner = _media(), _owner()
    if m is None or owner is None:
        return json_error("unavailable", MEDIA_DOWN, 503)
    try:
        done = m.purge(owner, library_trash_rev=rev, items=items, idempotency_key=body["_key"])
    except FloorStoreUnavailable as exc:
        return _store_down(exc, {"unavailable": MEDIA_DOWN, "not_configured": MEDIA_DOWN})
    if done.outcome == "purged" and isinstance(done.value, dict):
        done.value = {k: v for k, v in done.value.items() if k != "collect"}
        return _answer(done, value_key="receipt")
    if isinstance(done.value, dict) and done.value.get("trash"):
        done.value["trash"] = [_asset_out(a) for a in done.value["trash"]]
    return _answer(done, value_key="current")


@owner_api
def media_uses(asset_id: str):
    m, owner = _media(), _owner()
    if m is None or owner is None:
        return json_error("unavailable", MEDIA_DOWN, 503)
    try:
        uses = m.uses(owner, asset_id)
    except FloorStoreUnavailable:
        return json_error("unavailable", MEDIA_DOWN, 503)
    if uses is None:
        return json_error("not_found", "No such image in your library.", 404)
    return jsonify({"asset_id": asset_id, "uses": uses, "observed_at": now_iso()})


# ── serving (a page route, owner-guarded, same origin) ───────────────────────

@owner_page
def media_file(asset_id: str, variant: str):
    """GET <mount>/media/<id>/{thumb,full}: only the owner's own images; a
    purged one answers 410; an image in the library Trash is still served to
    the placements it already has."""
    if variant not in ("thumb", "full"):
        return json_error("not_found", "No such image.", 404)
    m, owner = _media(), _owner()
    if m is None or owner is None:
        return json_error("unavailable", MEDIA_DOWN, 503)
    try:
        asset = m.get(owner, asset_id)
    except FloorStoreUnavailable:
        return json_error("unavailable", MEDIA_DOWN, 503)
    if asset is None or asset["kind"] == "emoji":
        return json_error("not_found", "No such image.", 404)
    if asset["purged_at"]:
        return json_error("gone", "That image was deleted from the library.", 410)
    key = asset["storage_key"] if variant == "full" else thumb_key(asset["storage_key"])
    path = m.root / key
    if not path.is_file():
        return json_error("not_found", "The image file is missing; the reconcile job reports it.", 404)
    return send_file(path, mimetype=asset["mime"] if variant == "full" else "image/webp", max_age=0, etag=True)


RULES = [
    ("/board", "api_board", board_view, ["GET"]),
    ("/postits/<int:postit_id>/done", "api_postit_done", postit_done, ["POST"]),
    ("/postits/<int:postit_id>/undone", "api_postit_undone", postit_undone, ["POST"]),
    ("/postits/<int:postit_id>/label", "api_postit_label", postit_label, ["POST"]),
    ("/postits/<int:postit_id>/link", "api_postit_link", postit_link, ["POST"]),
    ("/postits/photo", "api_postit_photo", postit_photo, ["POST"]),
    ("/postits/reorder", "api_postits_reorder", postits_reorder, ["POST"]),
    ("/postits/trash/empty", "api_trash_empty", trash_empty, ["POST"]),
    ("/notes/<int:note_id>/share", "api_note_share", note_share, ["POST"]),
    ("/media", "api_media", media_list, ["GET"]),
    ("/media", "api_media_upload", media_upload, ["POST"]),
    ("/media/<asset_id>/trash", "api_media_trash", media_trash, ["POST"]),
    ("/media/<asset_id>/restore", "api_media_restore", media_restore, ["POST"]),
    ("/media/purge", "api_media_purge", media_purge, ["POST"]),
    ("/media/<asset_id>/uses", "api_media_uses", media_uses, ["GET"]),
]
