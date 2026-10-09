"""Pages of the real Shop floor: floor, bench, queue, item, operate.

Pages are one front end of the API (binding rule B8): they render the same
state the API serves, and their scripts write only through the API.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from flask import render_template, request, url_for, Response, redirect

from . import cfg, floor_state, owner_page
from .adapters import ACTIVE, STATUSES, by_recent
from .adapters.queue_reader import TROUBLE
from .briefing import LABEL as RULES_LABEL
from .security import OFF_RECORD_TEXT, csrf_token
from . import doc_reader
from .media import MAX_BYTES
from .stores import NOTE_MAX, POSTIT_MAX
from .conversations import public

FILING_OFF = "Filing is off until the Record is specified (#235). Nothing is filed."
INVITE_OFF = "Inviting agents needs Rooms; not connected"


def hhmm(iso) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%H:%M UTC")
    except (ValueError, OverflowError):                  # OverflowError: a date at the edge of the calendar
        return str(iso)


def age(iso) -> str:
    if not iso:
        return ""
    try:
        moment = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    days = int((datetime.now(timezone.utc) - moment).total_seconds() // 86400)
    return "today" if days <= 0 else f"{days}d"


def badge(res) -> dict:
    """Source mark for a read: live · time | not instrumented, plus unknown/stale."""
    if res.source == "live":
        text = f"live · read {hhmm(res.observed_at)}"
    else:
        text = "not instrumented"
    if res.status == "unknown" and res.source != "not_instrumented":
        text += " · unknown"
    elif res.status == "stale":
        text += " · stale"
    return {"text": text, "source": res.source, "status": res.status, "error": res.error,
            "reason": res.reason, "note": res.note}


NOTES_ON_PAGE = 30


DOCS_URL = "https://github.com/robertvanstedum/personal-ai-agents/tree/main/docs"

# Retired pages kept in reserve (the Workbench "wall", the old Build Queue page, Labs). Guild 1.1 does not serve them:
# with the switch off (the default, and always in production) each old address redirects to the page that replaced it,
# by meaning, and renders nothing retired. The reserve code and its assets are excluded from the release image
# (scripts/release/exclude_reserve_pages.py) and preserved in git (tag workbench-reserve-2026-10-07).
from .reserve import RESERVE_PAGES_VAR, reserve_pages_enabled  # noqa: E402,F401  (the switch lives in reserve.py)


def _moved(endpoint: str):
    """A retired address: a temporary redirect to the supported page that replaced it. Never cached."""
    response = redirect(url_for(endpoint))
    response.headers["Cache-Control"] = "no-store"
    return response


_JS_DIR = os.path.join(os.path.dirname(__file__), "static", "js")


def module_preloads() -> list:
    """Every script module of the Guild UI. The page lists them in its head (<link rel=modulepreload>), so the browser asks for
    all of them at once instead of discovering each level of imports only after the level above has arrived; over a tunnel
    that chain was the 15-25 s a new conversation took to become usable. Read from the folder, so it cannot drift."""
    try:
        names = sorted(n for n in os.listdir(_JS_DIR) if n.endswith(".js"))
    except OSError:
        return []
    if not reserve_pages_enabled():
        names = [n for n in names if n != "bench.js"]
    return [url_for(".asset", filename=f"js/{n}") for n in names]


def _urls(c) -> dict:
    """Every page's links, the landing's included. Build Log and Rooms are
    the later-slice pages until slices 2 and 4 land."""
    return {
        "home": url_for(".home"),
        "floor": url_for(".floor"), "build": url_for(".floor"),
        **({"bench": url_for(".bench")} if reserve_pages_enabled() else {}),
        "queue": url_for(".queue") if reserve_pages_enabled() else url_for(".build_log"),
        "operate": url_for(".operate"), "postits": url_for(".postits"),
        "item": url_for(".item", item_id=987654321).replace("987654321", "__ID__"),
        "api": f"{c['url_prefix']}/api/v1",
        "legacy_build": "/guild/build", "labs": url_for(".labs") if reserve_pages_enabled() else url_for(".experiment"),
        "workshop": url_for(".workshop"), "build_host": url_for(".operate_view", view="build-host"),
        "build_log": url_for(".build_log"), "rooms": url_for(".rooms"),
        "board": url_for(".board"), "media": url_for(".media_library"),
        "improve": url_for(".improve"), "experiment": url_for(".experiment"), "docs": DOCS_URL,
        "item_spec": url_for(".item_spec", item_id=987654321).replace("987654321", "__ID__"),
        "previous": "/guild-previous",
        "media_file": url_for(".media_file", asset_id="__ID__", variant="__V__"),
    }


def _context(page_id: str, area: str, context_item: str, *, conversation_id=None, **extra) -> dict:
    c = cfg()
    scope = None if page_id == "floor" else (
        "operate" if page_id.startswith("operate") else
        {"workshop": "workshop", "board": "board", "postits": "board",
         "improve": "improve", "experiment": "prototype"}.get(page_id, "build"))
    conversation, conv_notice = _current_conversation(c, scope, force_id=conversation_id)
    state = floor_state.compute(c, notes_limit=NOTES_ON_PAGE, conversation=conversation)
    layout = c["layout"]
    urls = _urls(c)
    lights_by_id = {l["id"]: l for l in state["lights"]}
    phone_numbers = [lights_by_id[n] for n in layout["phone"]["numbers"] if n in lights_by_id]
    page = {
        "base": c["url_prefix"], "urls": urls, "storage_ns": c["storage_ns"],
        "area": area, "page": page_id, "item": context_item, "chat_scope": scope,
        "csrf_token": csrf_token(), "mc_state": state["mc_state"],
        "layout": {"version": layout["layout_version"],
                   **({"bench": layout["bench"]} if reserve_pages_enabled() else {})},
        "floor": state, "off_record_text": OFF_RECORD_TEXT, "rules_label": RULES_LABEL,
        "postit_max": POSTIT_MAX, "note_max": NOTE_MAX, "attach": {"max_bytes": MAX_BYTES, "doc_max_bytes": doc_reader.MAX_UPLOAD_BYTES,
                   "doc_ext": doc_reader.ACCEPT, "doc_chars": doc_reader.MAX_STORED_CHARS},
        "conversation": public(conversation) if conversation else None, "conv_notice": conv_notice,
        "jobs": {"enabled": bool(c.get("jobs"))},
    }
    page.update(extra.pop("page_extra", {}))
    page_open = extra.pop("page_open", False)
    return dict(area=area, page_id=page_id, context_item=context_item, layout=layout, state=state,
                page_open=page_open, postit_max=POSTIT_MAX, module_preloads=module_preloads(),
                user=c["current_user"](), page_json=page, urls=urls, phone_numbers=phone_numbers,
                filing_off=FILING_OFF, invite_off=INVITE_OFF, rules_label=RULES_LABEL,
                hhmm=hhmm, age=age, badge=badge, statuses=STATUSES, conversation=conversation,
                conv_notice=conv_notice, **extra)


def _current_conversation(c, scope=None, force_id=None):
    """(conversation, notice): the one asked for (?c=), else the most recently
    used; the migrated Shop floor thread at first. Never a model call."""
    from .conversations import ConversationNotFound, ConversationStoreUnavailable, conversations_of
    principal = (c["current_user"]() or {}).get("username") or "owner"
    store = conversations_of(c["services"])
    try:
        requested = force_id or request.args.get("c") or None
        return (store.get(requested, principal) if requested else
                store.current_for_scope(principal, scope) if scope else store.current(principal)), None
    except ConversationNotFound:
        try:
            return (store.current_for_scope(principal, scope) if scope else store.current(principal)), "That conversation is unavailable; a current conversation is shown."
        except (ConversationNotFound, ConversationStoreUnavailable):
            return None, "Conversations are unavailable right now."
    except ConversationStoreUnavailable:
        return None, "Conversations are unavailable right now; showing the Shop floor thread."


# The Guild home's four doors (Guild 1.1 slice 1, spec §3): the original
# Guild art, labels, kickers, flows and CTAs (templates/guild/guild_landing.html),
# each mapped to where it goes on /guild-next today. The Build door's image is
# the shared Build card's (templates/guild/_build_card.html).
DOORS = (
    {"id": "build", "label": "Build", "href": "floor", "img": None,  # Build opens Master Craftsman chat
     "kicker": "Chat · Board · Build Log", "flow": "design → build", "cta": "Chat →"},
    {"id": "operate", "label": "Operate", "href": "operate", "img": "/static/guild/guild-operate.webp",
     "alt": "Operate — system health watercolor",
     "kicker": "Monitor · Maintain", "flow": "monitor → maintain", "cta": "Status →"},
    {"id": "improve", "label": "Improve", "href": "improve", "img": "/static/guild/guild-improve.webp",
     "alt": "Improve — notebook sketches",
     "kicker": "Review · Analyze", "flow": "review → analyze → improve", "cta": "Explore →"},
    {"id": "experiment", "label": "Prototype Lab", "href": "experiment", "img": "/static/guild/guild-experiment.webp",
     "alt": "Experiment — an alchemist at the bench",
     "kicker": "Ideas · Tinkering · Reference demos", "flow": "try → prove → keep", "cta": "Open →"},
)


@owner_page
def home():
    """/guild-next, /guild-next/ and /guild-next/guild: the Guild home, paired
    with Curator's landing (owner-guarded like every page: a guest or a
    signed-out visitor gets the portal's guard, never the page). Reads no
    store and calls no model."""
    c = cfg()
    urls = _urls(c)
    doors = [dict(d, href=urls[d["href"]]) for d in DOORS]
    return render_template("guild_floor/landing.html", page_id="home", area="Guild", urls=urls,
                           user=c["current_user"](), doors=doors)


@owner_page
def build_log():
    """The Build Log (Guild 1.1 slice 2, spec §4.1): every item in every status
    as one table with saved views, filters, sort, search, ranks and a drawer.
    The page embeds the same answer GET /api/v1/queue?scope=all gives; its
    script writes only through the API (status Save, rank, new item). No
    model call."""
    services = cfg()["services"]
    res = services.queue.list_items()
    checks_res = services.queue.checks()
    ctx = _context("build_log", "Build Log", "Build Log", page_open=True)
    data = {"status": res.status, "items": res.data if res.ok else None, "error": res.error,
            "observed_at": res.observed_at, "rank_digest": getattr(res, "rank_digest", None) if res.ok else None,
            "statuses": list(STATUSES), "trouble_statuses": list(TROUBLE), "create_statuses": ["idea", "design", "backlog"],
            "note": res.note}
    return render_template("guild_floor/build_log.html", res=res, log_data=data, checks=checks_res.data or [],
                           checks_res=checks_res, **ctx)


@owner_page
def rooms():
    """Rooms (Guild 1.1 slice 4, spec §6): group chat first, on Records. The
    page talks to Records only through the dev bridge at /app/records/api/,
    with Records' own login; it never holds a Records credential. When the
    bridge is not installed here, it says so."""
    from flask import current_app
    bridge = current_app.extensions.get("records_bridge") or {"state": "off_not_dev"}
    ctx = _context("rooms", "Rooms", "Rooms", page_open=True)
    rooms_cfg = {"state": bridge.get("state"), "api": "/app/records/api", "login": "/app/records/",
                 "max_bytes": 2_000_000, "take": request.args.get("take", type=int)}
    return render_template("guild_floor/rooms.html", rooms_cfg=rooms_cfg, **ctx)


@owner_page
def floor():
    from .conversations import ConversationStoreUnavailable, conversations_of
    ctx = _context("floor", "Build", "Shop floor")
    view = "archived" if request.args.get("view") == "archived" else "active"
    principal = (cfg()["current_user"]() or {}).get("username") or "owner"
    try:
        store = conversations_of(cfg()["services"])
        rows = store.list(principal, archived=view == "archived")
        conv_list = {"state": "ok", "rows": rows, "unreadable": store.unreadable}
    except ConversationStoreUnavailable:
        conv_list = {"state": "unavailable", "rows": [], "unreadable": 0}
    return render_template("guild_floor/floor.html", floor_cfg=ctx["layout"]["floor"], conv_list=conv_list,
                           conv_view=view, **ctx)


def _build_host(item_id):
    """The old host-diagnostics Workshop (4a: read only; zero model calls), now Operate → Build host: opened from a queue item
    (?item=<id>), or whole without one."""
    from .workshop_view import view
    ctx = _context("operate_build_host", "Operate", f"Build host · #{item_id}" if item_id else "Build host", page_open=True,
                   page_extra={"workshop_item": item_id})
    return render_template("guild_floor/build_host.html", ws=view(cfg()["services"], item_id), ws_item=item_id, **ctx)


@owner_page
def workshop():
    """The topic Workshop (W1): topics, cards, comments and the collaboration journal over the topic's own Master Craftsman
    conversation (Chat | Cards | Split). Old links to the host-diagnostics page (``?item=<queue id>``) go to Operate → Build host.
    The page reads nothing itself: its script reads the topic API, and nothing here calls a model."""
    if request.args.get("item", type=int) is not None or request.args.get("item") == "":
        return redirect(url_for(".operate_view", view="build-host", **({"item": request.args.get("item", type=int)} if request.args.get("item", type=int) else {})), code=302)
    from .topics import ITEM_RE, TOPIC_RE, TopicNotFound, TopicStoreUnavailable, topics_of
    c = cfg()
    principal = (c["current_user"]() or {}).get("username") or "owner"
    store = topics_of(c["services"])
    tid = request.args.get("topic")
    topic, notice = None, None
    try:
        if tid:
            topic = store.get_topic(tid, principal)["topic"] if TOPIC_RE.fullmatch(tid) else None
            if topic is None:
                notice = "That topic was not found; showing your latest."
        if topic is None:
            rows = store.list_topics(principal)
            topic = store._topic(rows[0]["id"], principal) if rows else None
    except TopicNotFound:
        notice = "That topic was not found; showing your latest."
    except TopicStoreUnavailable:
        notice = "The topic workshop is unavailable right now."
    ctx = _context("workshop", "Workshop", f"Workshop · {topic['title']}" if topic else "Workshop", page_open=True,
                   page_extra={"topic_id": topic["id"] if topic else None, "topic_notice": notice,
                               "workshop_view": request.args.get("view") if request.args.get("view") in ("chat", "cards", "split") else None,
                               "workshop_item": request.args.get("item_id") if ITEM_RE.fullmatch(request.args.get("item_id") or "") else None},
                   conversation_id=topic.get("conversation_id") if topic else None)
    return render_template("guild_floor/workshop.html", topic=topic, topic_notice=notice, **ctx)


@owner_page
def labs():
    """Planning Studio and Prototype Lab: truthful entry points (not served on dev yet). Retired: Prototype Lab replaced it."""
    if not reserve_pages_enabled():
        return _moved(".experiment")
    ctx = _context("labs", "Labs", "Planning Studio and Prototype Lab")
    return render_template("guild_floor/labs.html", **ctx)


@owner_page
def bench():
    if not reserve_pages_enabled():            # the Workbench is retained in reserve, not served in Guild 1.1
        return _moved(".board")
    c = cfg()
    services = c["services"]
    ctx = _context("bench", "Build", "workbench", page_open=True)   # the wall: a readable card column on a phone
    panels_cfg = {p["id"]: p for p in ctx["layout"]["bench"]["panels"]}
    queue_res = services.queue.list_items()
    data = {
        "motion": {"res": queue_res,
                   "rows": [i for i in (queue_res.data or []) if i["status_known"] and i["status"] in ACTIVE]},
        "blocked": {"res": queue_res,     # trouble: blocked or rework (Guild 1.1 slice 2)
                    "rows": [i for i in (queue_res.data or []) if i["status_known"] and i["status"] in TROUBLE]},
        "discussions": {"res": services.sessions.list_sessions()},
        "postits": {"board": services.floor.list_postits(), "bin": services.floor.list_bin(limit=20)},
    }
    return render_template("guild_floor/bench.html", panels_cfg=panels_cfg, data=data, **ctx)


@owner_page
def queue():
    if not reserve_pages_enabled():            # the Build Log replaced the Build Queue page
        return _moved(".build_log")
    services = cfg()["services"]
    res = services.queue.list_items()
    ctx = _context("queue", "Build Queue", "Build Queue")
    rows = res.data or []
    items = sorted([i for i in rows if i["status_known"] and i["status"] in ACTIVE], key=by_recent, reverse=True)
    unknown_rows = [i for i in rows if not i["status_known"]]
    checks_res = services.queue.checks()
    return render_template("guild_floor/queue.html", res=res, items=items, unknown_rows=unknown_rows,
                           checks=checks_res.data or [], checks_res=checks_res,
                           focus_id=request.args.get("focus", type=int), **ctx)


@owner_page
def item(item_id: int):
    services = cfg()["services"]
    res = services.queue.get_item(item_id)
    if res.ok and res.data is None:
        ctx = _context("item", "Build Log", f"#{item_id} (not found)", page_open=True)
        return render_template("guild_floor/not_found.html", item_id=item_id, **ctx), 404
    title = res.data["title"] if res.ok else "unknown"
    # page_open: on a phone this page's own content shows (a linked-work link lands here), not a summary
    ctx = _context("item", "Build Log", f"#{item_id} {title}", page_open=True, page_extra={"item_id": item_id})
    checks_res = services.queue.checks()
    checks = [ch for ch in (checks_res.data or []) if ch.get("item_id") == item_id]
    return render_template("guild_floor/item.html", res=res, it=res.data, item_id=item_id,
                           history=services.history.history(item_id), checks=checks, checks_res=checks_res,
                           **ctx)


@owner_page
def postits():
    """Every post-it on the board and the kept bin, with Restore. The same
    page on desktop and phone (review: the phone must reach the bin)."""
    ctx = _context("postits", "Build", "Post-its", page_open=True)
    return render_template("guild_floor/postits.html", bin_res=cfg()["services"].floor.list_bin(),
                           board_res=cfg()["services"].floor.list_postits(), **ctx)


@owner_page
def operate():
    ctx = _context("operate", "Operate", "Operate", page_open=True)     # on a phone its tiles show, not only the chat
    tile_ids = [t["id"] for t in ctx["layout"]["operate"]["tiles"]]
    wanted = request.args.get("tile")
    selected = wanted if wanted in tile_ids else ctx["layout"]["operate"]["default_selected"]
    lights = {l["id"]: l for l in ctx["state"]["lights"]}
    from .scheduled_jobs import overview
    from .memory_capture import overview as memory_overview
    return render_template("guild_floor/operate.html", memory_capture=memory_overview(), scheduled_jobs=overview(), selected_tile=selected, tile_lights=lights, **ctx)


@owner_page
def board():
    """The Board (Guild 1.1 slice 3, spec §5.1, the v4 board look): relaxed
    post-its with Done, labels, links and photos; order by drag, the ‹ ›
    buttons or Alt+Arrow; Trash with Restore and a confirmed Empty. The page
    embeds the same answer GET /api/v1/board gives; its script writes only
    through the API. No review workflow, no model call."""
    from .board import LABELS
    from .board_api import _decorate, _queue_index
    res = cfg()["services"].floor.board()
    data = res.data if res.ok else {}
    index = _queue_index() if res.ok else None
    for name in ("active", "done", "trash"):
        _decorate(data.get(name), index)
    ctx = _context("board", "Board", "Board", page_open=True)
    board_data = {"status": res.status, "reason": res.reason, "labels": list(LABELS),
                  **({k: data.get(k) for k in ("active", "done", "trash", "order_rev", "trash_rev", "counts")}
                     if res.ok else {"active": None, "done": None, "trash": None})}
    return render_template("guild_floor/board.html", res=res, board_data=board_data, **ctx)


@owner_page
def media_library():
    """The shared Media library (spec §5.2): thumbnails, a type filter, Add,
    Trash and Restore, and a permanent delete that says where an image is
    still used. Drawn by js/media.js from GET /api/v1/media."""
    ctx = _context("media", "Media library", "Media library", page_open=True)
    return render_template("guild_floor/media.html", **ctx)


@owner_page
def item_spec(item_id):
    from .references import resolve
    res = cfg()["services"].queue.get_item(item_id)
    reference = (res.data or {}).get("spec_file", "") if res.ok else ""
    return _document(reference, resolve(reference), unavailable=not res.ok)


def _document(reference, path, unavailable=False):
    source = None
    if path:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            pass
    from .markdown_render import render_document
    from .references import ROOT
    from urllib.parse import quote
    github_url = None
    git_date = None
    if source is not None and request.args.get("download") == "md":
        response = Response(source, mimetype="text/markdown")
        response.headers["Content-Disposition"] = "attachment; filename=document.md"
        return response
    if path:
        from .references import published_metadata
        github_url, git_date = published_metadata(path, ROOT)
    def resolve_link(value):
        from urllib.parse import urlsplit, unquote
        from .references import resolve
        target = urlsplit(value)
        if target.scheme or target.netloc or "\\" in value or not path:
            return None
        try:
            candidate = (path.parent / unquote(target.path)).resolve()
            ref = candidate.relative_to(ROOT).as_posix()
        except (ValueError, OSError):
            return None
        if resolve(ref) != candidate:
            return None
        return url_for('.document', ref=ref) + ("#" + quote(target.fragment) if target.fragment else "")
    ctx = _context("docs", "Docs", reference or "Spec missing", page_open=True)
    return render_template("guild_floor/document.html", reference=reference, source=source,
                           rendered=render_document(source, resolve_link) if source is not None else None,
                           github_url=github_url, git_date=git_date,
                           unavailable=unavailable, **ctx), (200 if source is not None else 503 if unavailable else 404)


@owner_page
def document():
    from .references import resolve
    reference = request.args.get("ref", "")
    return _document(reference, resolve(reference))


@owner_page
def docs():
    return redirect(DOCS_URL)


def _reference_page(page_id, area, title, directory, note):
    from .references import catalog, grouped
    rows, error = catalog(directory=directory)
    ctx = _context(page_id, area, title, page_open=True)
    return render_template("guild_floor/references.html", title=title, rows=rows, source=directory,
                           source_error=error, note=note, groups=grouped(rows) if page_id == "docs" else None, **ctx)


def _notebook(page_id, sources):
    from .references import catalog
    sections = []
    for key, title, directory, note in sources:
        rows, error = catalog(directory=directory)
        sections.append(dict(id=key, title=title, source=directory, note=note, rows=rows, error=error))
    return render_template("guild_floor/notebook.html", sections=sections,
                           **_context(page_id, page_id.title(), page_id.title(), page_open=True))


@owner_page
def improve():
    from . import library
    import sqlite3
    from .api import _principal
    db = None
    rows, tags, error = [], [], None
    query, tag = request.args.get("q", "")[:200], request.args.get("tag", "")[:50]
    try:
        db = library.database()
        rows, tags = library.listing(db, _principal(), query, tag)
    except (OSError, sqlite3.Error):
        error = "Library storage is unavailable."
    finally:
        if db is not None: db.close()
    return render_template("guild_floor/library.html", links=rows, tags=tags, query=query, tag=tag,
                           library_error=error, **_context("improve", "Improve", "Technology library", page_open=True))


@owner_page
def experiment():
    """The Prototype Lab gallery: one card per catalog entry. Nothing here checks
    whether a demo is running; a hosted link is only a link."""
    from . import lab_catalog as lab
    return render_template("guild_floor/prototype_lab.html", prototypes=lab.all_prototypes(),
                           **_context("experiment", "Prototype Lab", "Prototype Lab", page_open=True))


@owner_page
def prototype_detail(slug):
    """One prototype: look inside (real screenshots and documents) and Run it
    (its own run book). Reads only the catalog; starts nothing."""
    from . import lab_catalog as lab
    entry = lab.get(slug)
    if entry is None:
        ctx = _context("experiment", "Prototype Lab", "Prototype not found", page_open=True)
        return render_template("guild_floor/prototype_missing.html", **ctx), 404
    return render_template("guild_floor/prototype_detail.html", p=entry, github=lab.GITHUB, blob=lab.blob,
                           tree=lab.tree,
                           **_context("experiment", "Prototype Lab", entry["title"], page_open=True))


@owner_page
def operate_view(view):
    if view in ("runbooks", "maintain"):
        from . import wiki
        import sqlite3
        from .markdown_render import render_document
        db = None
        item, history, rows, error = None, [], [], None
        query = request.args.get("q", "")[:200]
        try:
            db = wiki.database()
            rows = wiki.catalog(db, query)
            if request.args.get("page"):
                item = wiki.read(db, request.args["page"], request.args.get("revision", type=int))
                if item is None:
                    error = "Page or revision not found."
                else:
                    history = [dict(x) for x in db.execute("SELECT revision, author, updated FROM revisions WHERE page=? ORDER BY revision DESC", (item["page"],))]
        except (OSError, sqlite3.Error):
            error = "Wiki storage is unavailable."
        finally:
            if db is not None:
                db.close()
        return render_template("guild_floor/runbooks.html", wiki_rows=rows, wiki_item=item,
                               wiki_history=history, wiki_error=error, wiki_query=query,
                               wiki_html=render_document(item["body"]) if item else "",
                               **_context("operate_runbooks", "Operate", "Runbooks", page_open=True))
    if view in ("agents", "tending"):
        return render_template("guild_floor/operate_agents.html",
                               **_context("operate_agents", "Operate", "Agents", page_open=True))
    if view == "build-host":
        return _build_host(request.args.get("item", type=int))
    if view == "checks":
        checks = cfg()["services"].queue.checks()
        return render_template("guild_floor/operate_checks.html", checks=checks,
                               **_context("operate_checks", "Operate", "Checks", page_open=True))
    return _reference_page("operate_" + (view if view in ("maintain", "tending") else "maintain"),
                           "Operate", "MC tending" if view == "tending" else "Maintain", "docs",
                           "Operational reference documents. Live maintenance requests and autonomous tending history are not connected.")
