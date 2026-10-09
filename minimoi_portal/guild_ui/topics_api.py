"""The topic workshop's JSON API (Workshop W1, WORKSHOP_BUILD_SPEC.md section 3), part of /api/v1.

Owner-only, CSRF, same-origin and ``X-Record-Mode: on_record`` on every write (the shared guards); off the record writes are
refused like every other write. Answers are JSON. Nothing here calls a model, a network or a shell, and no endpoint starts a job.
Retrying the same write (same idempotency key) within one portal process returns the first answer instead of creating a second
record; the key is not remembered across a restart (stated limit).
"""
from __future__ import annotations

import collections

from flask import jsonify, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from . import cfg, owner_api
from .adapters.contract import now_iso
from .api import _principal, _write_body
from .media import MAX_BYTES, MediaRejected, sanitize
from .payment_scrub import scrub
from .security import check_upload, json_error
from .topics import (DISPOSITIONS, JOURNAL_KINDS, STAGES, Refused, ItemNotFound, TopicNotFound, TopicStore,
                     TopicStoreUnavailable, topics_of)

DOWN = "The topic workshop is unavailable right now. Nothing was changed."
_SEEN: "collections.OrderedDict[tuple, dict]" = collections.OrderedDict()
_SEEN_MAX = 500


def _store() -> TopicStore:
    return topics_of(cfg()["services"])


def _fail(exc: Exception):
    if isinstance(exc, Refused):
        return json_error(exc.code, exc.message, exc.status)
    if isinstance(exc, TopicNotFound):
        return json_error("not_found", "There is no such topic. Nothing was changed.", 404)
    if isinstance(exc, ItemNotFound):
        return json_error("not_found", "There is no such item. Nothing was changed.", 404)
    return json_error("unavailable", DOWN, 503)


_HANDLED = (Refused, TopicNotFound, ItemNotFound, TopicStoreUnavailable)


def _once(principal: str, key: str, route: str, make):
    """Run ``make`` once per (owner, route, key): a repeat in this process returns the first answer."""
    slot = (principal, route, key)
    if slot in _SEEN:
        return {**_SEEN[slot], "repeated": True}
    out = make()
    _SEEN[slot] = out
    while len(_SEEN) > _SEEN_MAX:
        _SEEN.popitem(last=False)
    return out


def public_item(it: dict) -> dict:
    """What a browser may see of an item: no file names or paths, only the facts it needs."""
    revs = [{k: r.get(k) for k in ("rev", "created", "by", "note", "bytes", "width", "height", "mime")} for r in it.get("revisions", [])]
    out = {k: it.get(k) for k in ("id", "kind", "title", "by", "created", "updated", "archived", "archived_at", "current_rev", "comments_open")}
    out["revisions"] = revs
    if it.get("request"):
        out["request"] = dict(it["request"])
    return out


def _wb():
    return _write_body()


# ── topics ────────────────────────────────────────────────────────────────
@owner_api
def topics_list():
    try:
        rows = _store().list_topics(_principal(), q=request.args.get("q") or None, archived=request.args.get("archived") == "1")
    except _HANDLED as exc:
        return _fail(exc)
    return jsonify({"topics": rows, "observed_at": now_iso()})


@owner_api
def topic_create():
    refusal, body = _wb()
    if refusal is not None:
        return refusal
    principal = _principal()

    def make():
        store = _store()
        topic = store.create_topic(principal, body.get("title"), scrub(str(body.get("summary") or "")))
        try:                                           # each topic has its own Master Craftsman conversation (one session for its life)
            from .conversations import conversations_of
            conv, _ = conversations_of(cfg()["services"]).create(principal, key=body["_key"], scope="topic")      # scoped: it never becomes the Chat page's landing conversation
            conversations_of(cfg()["services"]).rename(conv["id"], principal, topic["title"])
            topic = store.set_conversation(topic["id"], principal, conv["id"])
        except Exception:                              # the topic exists; the page links a conversation with its own guarded POST (/topics/<tid>/conversation)
            pass
        return {"result": "created", "topic": topic, "observed_at": now_iso()}
    try:
        return jsonify(_once(principal, body["_key"], "topic_create", make))
    except _HANDLED as exc:
        return _fail(exc)


def _conversation_of(topic: dict, principal: str):
    """The topic's conversation as the page shows it, or None (read only: nothing is created or repaired here)."""
    if not topic.get("conversation_id"):
        return None
    try:
        from .conversations import conversations_of, public
        return public(conversations_of(cfg()["services"]).get(topic["conversation_id"], principal))
    except Exception:
        return None


@owner_api
def topic_view(tid):
    """Read only. A topic with no linked conversation is reported as such (``conversation_missing``); linking one is the explicit
    POST /topics/<tid>/conversation, so a GET (and so looking while Private) never writes anything."""
    principal = _principal()
    try:
        got = _store().get_topic(tid, principal)
        topic = got["topic"]
        return jsonify({"topic": topic, "items": [public_item(i) for i in got["items"]], "layout": got["layout"],
                        "conversation": _conversation_of(topic, principal), "conversation_missing": not topic.get("conversation_id"),
                        "stages": list(STAGES), "observed_at": now_iso()})
    except _HANDLED as exc:
        return _fail(exc)


@owner_api
def record_view(tid):
    """Read only: the shared Workshop record for this topic (see topic_record). Always answers with a state and a plain sentence;
    a damaged, missing or unlinked record is reported as that, never as an empty list. ``?candidates=<workshop>`` lists the topics
    of a workshop's journal so one can be chosen to link."""
    from . import topic_record
    principal = _principal()
    try:
        _store().get_topic(tid, principal)
        wanted = request.args.get("candidates")
        if wanted:
            return jsonify({"candidates": topic_record.candidates(wanted), "workshop": wanted, "observed_at": now_iso()})
        since = request.args.get("since_seq", type=int)
        return jsonify(topic_record.record(tid, topic_record.get_link(_store(), tid), since_seq=since))
    except _HANDLED as exc:
        return _fail(exc)


@owner_api
def record_link(tid):
    """Link this topic to a topic in a Workshop journal, or unlink it (``{"clear": true}``). A guarded write like every other:
    owner, CSRF, idempotency key, refused off the record. Safe to repeat."""
    from . import topic_record
    refusal, body = _wb()
    if refusal is not None:
        return refusal
    principal = _principal()

    def make():
        store = _store()
        if body.get("clear") is True:
            topic_record.clear_link(store, tid, principal)
            return {"result": "unlinked", "link": None, "observed_at": now_iso()}
        link = topic_record.set_link(store, tid, principal, str(body.get("workshop") or ""), str(body.get("topic") or ""))
        return {"result": "linked", "link": link, "observed_at": now_iso()}
    try:
        return jsonify(_once(principal, body["_key"], f"record_link:{tid}", make))
    except _HANDLED as exc:
        return _fail(exc)


@owner_api
def topic_conversation(tid):
    """Link the topic's own Master Craftsman conversation when it has none (a topic made before one existed, or whose link failed).
    A guarded write: owner, CSRF, idempotency key, refused off the record. Safe to repeat: an existing link is returned unchanged."""
    refusal, body = _wb()
    if refusal is not None:
        return refusal
    principal = _principal()

    def make():
        store = _store()
        topic = store.get_topic(tid, principal)["topic"]
        if not topic.get("conversation_id"):
            from .conversations import conversations_of
            c = conversations_of(cfg()["services"])
            conv, _ = c.create(principal, key=f"topic-{tid}", scope="topic")
            c.rename(conv["id"], principal, topic["title"])
            topic = store.set_conversation(tid, principal, conv["id"])
        return {"result": "linked", "topic": topic, "observed_at": now_iso()}
    try:
        return jsonify(_once(principal, body["_key"], f"topic_conversation:{tid}", make))
    except _HANDLED as exc:
        return _fail(exc)
    except Exception:
        return json_error("unavailable", "The conversation could not be linked. Nothing was changed.", 503)


@owner_api
def topic_update(tid):
    refusal, body = _wb()
    if refusal is not None:
        return refusal
    try:
        t = _store().update_topic(tid, _principal(), title=body.get("title"),
                                  summary=scrub(body["summary"]) if isinstance(body.get("summary"), str) else None,
                                  archived=body.get("archived") if isinstance(body.get("archived"), bool) else None)
    except _HANDLED as exc:
        return _fail(exc)
    return jsonify({"result": "updated", "topic": t, "observed_at": now_iso()})


@owner_api
def layout_save(tid):
    refusal, body = _wb()
    if refusal is not None:
        return refusal
    try:
        lay = _store().set_layout(tid, _principal(), order=body.get("order"), wide=body.get("wide"), last_view=body.get("last_view"))
    except _HANDLED as exc:
        return _fail(exc)
    return jsonify({"result": "saved", "layout": lay, "observed_at": now_iso()})


# ── items ─────────────────────────────────────────────────────────────────
@owner_api
def item_create(tid):
    refusal, body = _wb()
    if refusal is not None:
        return refusal
    principal = _principal()
    kind = body.get("kind")
    if kind == "design":
        return json_error("invalid", "A design is added by uploading its image.", 422)

    def make():
        text = scrub(str(body.get("text") or ""))
        req = body.get("request") if isinstance(body.get("request"), dict) else None
        if req:
            req = {k: scrub(str(v)) for k, v in req.items() if k in ("to", "stage", "included")}
        item = _store().add_item(tid, principal, kind, body.get("title"), text=text, request=req, note=scrub(str(body.get("note") or "")))
        return {"result": "added", "item": public_item(item), "observed_at": now_iso()}
    try:
        return jsonify(_once(principal, body["_key"], f"item_create:{tid}", make))
    except _HANDLED as exc:
        return _fail(exc)


def _image_from_upload():
    """(refusal, sanitized image, title) for a multipart design upload: one part named file, optional title field."""
    refusal, key = check_upload(cfg()["base_url"])
    if refusal is not None:
        return refusal, None, None, None
    too_big = json_error("too_large", f"The image is larger than {MAX_BYTES // (1024 * 1024)} MB. Nothing was added.", 413)
    if request.content_length is not None and request.content_length > MAX_BYTES + 65536:
        return too_big, None, None, None
    request.max_content_length = MAX_BYTES + 65536
    try:
        parts = list(request.files.items(multi=True))
        fields = {k: request.form.get(k) for k in request.form.keys()}
    except RequestEntityTooLarge:
        return too_big, None, None, None
    if len(parts) != 1 or parts[0][0] != "file" or set(fields) - {"title", "note"}:
        return json_error("invalid", "Send one part named file, and optionally title and note. Nothing was added.", 422), None, None, None
    raw = parts[0][1].stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        return too_big, None, None, None
    try:
        img = sanitize(raw)
    except MediaRejected as exc:
        return json_error({413: "too_large", 415: "unsupported", 422: "invalid", 503: "busy"}.get(exc.status, "invalid"), exc.message, exc.status), None, None, None
    return None, img, fields, key


@owner_api
def design_create(tid):
    refusal, img, fields, key = _image_from_upload()
    if refusal is not None:
        return refusal
    principal = _principal()
    title = (fields.get("title") or "").strip() or "Design"

    def make():
        item = _store().add_item(tid, principal, "design", title, image=img, note=scrub(fields.get("note") or ""))
        return {"result": "added", "item": public_item(item), "observed_at": now_iso()}
    try:
        return jsonify(_once(principal, key, f"design_create:{tid}", make))
    except _HANDLED as exc:
        return _fail(exc)


@owner_api
def design_revise(tid, iid):
    refusal, img, fields, key = _image_from_upload()
    if refusal is not None:
        return refusal
    principal = _principal()

    def make():
        item = _store().add_revision(tid, iid, principal, image=img, note=scrub(fields.get("note") or ""))
        return {"result": "revised", "item": public_item(item), "observed_at": now_iso()}
    try:
        return jsonify(_once(principal, key, f"design_revise:{tid}:{iid}", make))
    except _HANDLED as exc:
        return _fail(exc)


@owner_api
def item_view(tid, iid):
    try:
        got = _store().get_item(tid, iid, _principal(), rev=request.args.get("rev") or None)
    except _HANDLED as exc:
        return _fail(exc)
    item = public_item(got)
    item["rev"] = {k: got["rev"].get(k) for k in ("rev", "created", "by", "note", "bytes", "width", "height", "mime")}
    html, blocks = ("", 0)
    if got["kind"] != "design":                         # the same sanitiser as every note; each top-level block is numbered for comments
        from .markdown_render import render_blocks
        html, blocks = render_blocks(got["text"])
    return jsonify({"item": item, "text": got["text"], "html": html, "blocks": blocks, "comments": got["comments"], "observed_at": now_iso()})


@owner_api
def item_revise(tid, iid):
    refusal, body = _wb()
    if refusal is not None:
        return refusal
    principal = _principal()

    def make():
        item = _store().add_revision(tid, iid, principal, text=scrub(str(body.get("text") or "")), note=scrub(str(body.get("note") or "")))
        return {"result": "revised", "item": public_item(item), "observed_at": now_iso()}
    try:
        return jsonify(_once(principal, body["_key"], f"item_revise:{tid}:{iid}", make))
    except _HANDLED as exc:
        return _fail(exc)


@owner_api
def item_archive(tid, iid):
    refusal, body = _wb()
    if refusal is not None:
        return refusal
    away = body.get("archived", True)
    if not isinstance(away, bool):
        return json_error("invalid", "archived must be true or false.", 422)
    try:
        item = _store().set_archived(tid, iid, _principal(), away)
    except _HANDLED as exc:
        return _fail(exc)
    return jsonify({"result": "archived" if away else "restored", "item": public_item(item), "observed_at": now_iso()})


@owner_api
def item_stage(tid, iid):
    refusal, body = _wb()
    if refusal is not None:
        return refusal
    stage = body.get("stage")
    recipient = body.get("recipient")
    if stage is not None and stage not in STAGES:
        return json_error("invalid", "Unknown stage.", 422)
    try:
        item = _store().set_stage(tid, iid, _principal(), stage, recipient=scrub(str(recipient)) if recipient is not None else None)
    except _HANDLED as exc:
        return _fail(exc)
    return jsonify({"result": "stage_set", "item": public_item(item), "observed_at": now_iso()})


@owner_api
def design_image(tid, iid):
    variant = request.args.get("v", "full")
    if variant not in ("full", "thumb"):
        return json_error("not_found", "No such image.", 404)
    try:
        path, mime = _store().design_file(tid, iid, _principal(), request.args.get("rev") or None, thumb=variant == "thumb")
    except _HANDLED as exc:
        return _fail(exc)
    import os
    if not os.path.isfile(path):
        return json_error("not_found", "The image file is missing.", 404)
    return send_file(path, mimetype=mime, max_age=0, etag=True)


# ── comments ──────────────────────────────────────────────────────────────
@owner_api
def comment_add(tid, iid):
    refusal, body = _wb()
    if refusal is not None:
        return refusal
    principal = _principal()
    user = (cfg()["current_user"]() or {})

    def make():
        c = _store().add_comment(tid, iid, principal, scrub(str(body.get("text") or "")), rev=body.get("rev"), anchor=body.get("anchor"),
                                 reply_to=body.get("reply_to"), by={"kind": "owner", "name": user.get("display_name") or principal})
        return {"result": "added", "comment": c, "observed_at": now_iso()}
    try:
        return jsonify(_once(principal, body["_key"], f"comment:{tid}:{iid}", make))
    except _HANDLED as exc:
        return _fail(exc)


@owner_api
def comment_resolve(tid, iid, cid):
    refusal, body = _wb()
    if refusal is not None:
        return refusal
    try:
        c = _store().resolve_comment(tid, iid, _principal(), cid, resolved=body.get("resolved", True) is not False)
    except _HANDLED as exc:
        return _fail(exc)
    return jsonify({"result": "resolved" if c["status"] == "resolved" else "reopened", "comment": c, "observed_at": now_iso()})


@owner_api
def comment_disposition(tid, iid, cid):
    refusal, body = _wb()
    if refusal is not None:
        return refusal
    try:
        c = _store().set_disposition(tid, iid, _principal(), cid, body.get("value"))
    except _HANDLED as exc:
        return _fail(exc)
    return jsonify({"result": "recorded", "comment": c, "observed_at": now_iso()})


# ── journal ───────────────────────────────────────────────────────────────
@owner_api
def journal_list(tid):
    try:
        rows = _store().journal(tid, _principal(), limit=request.args.get("limit", type=int) or 200)
    except _HANDLED as exc:
        return _fail(exc)
    return jsonify({"entries": rows, "kinds": list(JOURNAL_KINDS), "observed_at": now_iso()})


@owner_api
def journal_add(tid):
    """The owner's own entry (decision, note, question…). Agents do not write here: their entries arrive through the inbox
    (section 4 of the spec) and are labelled as claimed, not verified."""
    refusal, body = _wb()
    if refusal is not None:
        return refusal
    principal = _principal()
    user = (cfg()["current_user"]() or {})

    def make():
        e = _store().append_journal(tid, principal, author={"kind": "owner", "name": user.get("display_name") or principal}, via="ui",
                                    kind=body.get("kind", "note"), text=scrub(str(body.get("text") or "")), refs=body.get("refs"), verified=True)
        return {"result": "added", "entry": e, "observed_at": now_iso()}
    try:
        return jsonify(_once(principal, body["_key"], f"journal:{tid}", make))
    except _HANDLED as exc:
        return _fail(exc)


@owner_api
def inbox_status(tid):
    """Read-only: how many contributions wait per agent. Looking never imports anything."""
    try:
        waiting = _store().inbox_waiting(tid, _principal())
    except _HANDLED as exc:
        return _fail(exc)
    return jsonify({"waiting": waiting, "total": sum(waiting.values()), "observed_at": now_iso()})


@owner_api
def inbox_import(tid):
    """Import what agents left in the topic's inbox (an owner action, bounded; see TopicStore.import_inbox). The entries are
    untrusted contributions: stored via the inbox, not verified, and unable to change anything but the record."""
    refusal, body = _wb()
    if refusal is not None:
        return refusal
    try:
        got = _store().import_inbox(tid, _principal(), scrub=scrub)
    except _HANDLED as exc:
        return _fail(exc)
    return jsonify({"result": "imported", **got, "observed_at": now_iso()})


RULES = [
    ("/topics", "api_topics", topics_list, ["GET"]),
    ("/topics/create", "api_topic_create", topic_create, ["POST"]),
    ("/topics/<tid>", "api_topic", topic_view, ["GET"]),
    ("/topics/<tid>/update", "api_topic_update", topic_update, ["POST"]),
    ("/topics/<tid>/conversation", "api_topic_conversation", topic_conversation, ["POST"]),
    ("/topics/<tid>/record", "api_topic_record", record_view, ["GET"]),
    ("/topics/<tid>/record-link", "api_topic_record_link", record_link, ["POST"]),
    ("/topics/<tid>/layout", "api_topic_layout", layout_save, ["POST"]),
    ("/topics/<tid>/items", "api_topic_item_create", item_create, ["POST"]),
    ("/topics/<tid>/items/design", "api_topic_design_create", design_create, ["POST"]),
    ("/topics/<tid>/items/<iid>", "api_topic_item", item_view, ["GET"]),
    ("/topics/<tid>/items/<iid>/revisions", "api_topic_item_revise", item_revise, ["POST"]),
    ("/topics/<tid>/items/<iid>/revisions/design", "api_topic_design_revise", design_revise, ["POST"]),
    ("/topics/<tid>/items/<iid>/archive", "api_topic_item_archive", item_archive, ["POST"]),
    ("/topics/<tid>/items/<iid>/stage", "api_topic_item_stage", item_stage, ["POST"]),
    ("/topics/<tid>/items/<iid>/image", "api_topic_design_image", design_image, ["GET"]),
    ("/topics/<tid>/items/<iid>/comments", "api_topic_comment_add", comment_add, ["POST"]),
    ("/topics/<tid>/items/<iid>/comments/<cid>/resolve", "api_topic_comment_resolve", comment_resolve, ["POST"]),
    ("/topics/<tid>/items/<iid>/comments/<cid>/disposition", "api_topic_comment_disposition", comment_disposition, ["POST"]),
    ("/topics/<tid>/journal", "api_topic_journal", journal_list, ["GET"]),
    ("/topics/<tid>/journal/add", "api_topic_journal_add", journal_add, ["POST"]),
    ("/topics/<tid>/inbox", "api_topic_inbox", inbox_status, ["GET"]),
    ("/topics/<tid>/inbox/import", "api_topic_inbox_import", inbox_import, ["POST"]),
]
