"""Pages of the real Shop floor: floor, bench, queue, item, operate.

Pages are one front end of the API (binding rule B8): they render the same
state the API serves, and their scripts write only through the API.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import render_template, request, url_for

from . import cfg, floor_state, owner_page
from .adapters import ACTIVE, STATUSES, by_recent
from .briefing import LABEL as RULES_LABEL
from .security import OFF_RECORD_TEXT, csrf_token
from .stores import NOTE_MAX, POSTIT_MAX
from .conversations import public

FILING_OFF = "Filing is off until the Record is specified (#235). Nothing is filed."
INVITE_OFF = "Inviting agents needs Rooms; not connected"


def hhmm(iso) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%H:%M UTC")
    except ValueError:
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


def _context(page_id: str, area: str, context_item: str, **extra) -> dict:
    c = cfg()
    conversation, conv_notice = _current_conversation(c)
    state = floor_state.compute(c, notes_limit=NOTES_ON_PAGE, conversation=conversation)
    layout = c["layout"]
    urls = {
        "floor": url_for(".floor"), "build": url_for(".floor"), "bench": url_for(".bench"),
        "queue": url_for(".queue"), "operate": url_for(".operate"), "postits": url_for(".postits"),
        "item": url_for(".item", item_id=987654321).replace("987654321", "__ID__"),
        "api": f"{c['url_prefix']}/api/v1",
        "legacy_build": "/guild/build", "labs": url_for(".labs"), "workshop": url_for(".workshop"),
    }
    lights_by_id = {l["id"]: l for l in state["lights"]}
    phone_numbers = [lights_by_id[n] for n in layout["phone"]["numbers"] if n in lights_by_id]
    page = {
        "base": c["url_prefix"], "urls": urls, "storage_ns": c["storage_ns"],
        "area": area, "page": page_id, "item": context_item,
        "csrf_token": csrf_token(), "mc_state": state["mc_state"],
        "layout": {"version": layout["layout_version"], "bench": layout["bench"]},
        "floor": state, "off_record_text": OFF_RECORD_TEXT, "rules_label": RULES_LABEL,
        "postit_max": POSTIT_MAX, "note_max": NOTE_MAX,
        "conversation": public(conversation) if conversation else None, "conv_notice": conv_notice,
    }
    page.update(extra.pop("page_extra", {}))
    page_open = extra.pop("page_open", False)
    return dict(area=area, page_id=page_id, context_item=context_item, layout=layout, state=state,
                page_open=page_open, postit_max=POSTIT_MAX,
                user=c["current_user"](), page_json=page, urls=urls, phone_numbers=phone_numbers,
                filing_off=FILING_OFF, invite_off=INVITE_OFF, rules_label=RULES_LABEL,
                hhmm=hhmm, age=age, badge=badge, statuses=STATUSES, conversation=conversation,
                conv_notice=conv_notice, **extra)


def _current_conversation(c):
    """(conversation, notice): the one asked for (?c=), else the most recently
    used; the migrated Shop floor thread at first. Never a model call."""
    from .conversations import ConversationNotFound, ConversationStoreUnavailable, conversations_of
    principal = (c["current_user"]() or {}).get("username") or "owner"
    store = conversations_of(c["services"])
    try:
        return store.current(principal, request.args.get("c") or None), None
    except ConversationNotFound:
        try:
            return store.current(principal), "That conversation is not there; showing your latest one."
        except (ConversationNotFound, ConversationStoreUnavailable):
            return None, "Conversations are unavailable right now."
    except ConversationStoreUnavailable:
        return None, "Conversations are unavailable right now; showing the Shop floor thread."


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


@owner_page
def workshop():
    """The focused Workshop (4a: read only; zero model calls): opened from a
    queue item (?item=<id>), or the whole workshop without one."""
    from .workshop_view import view
    item_id = request.args.get("item", type=int)
    ctx = _context("workshop", "Workshop", f"Workshop · #{item_id}" if item_id else "Workshop", page_open=True,
                   page_extra={"workshop_item": item_id})
    return render_template("guild_floor/workshop.html", ws=view(cfg()["services"], item_id), ws_item=item_id, **ctx)


@owner_page
def labs():
    """Planning Studio and Prototype Lab: truthful entry points (not served on dev yet)."""
    ctx = _context("labs", "Labs", "Planning Studio and Prototype Lab")
    return render_template("guild_floor/labs.html", **ctx)


@owner_page
def bench():
    c = cfg()
    services = c["services"]
    ctx = _context("bench", "Build", "workbench", page_open=True)   # the wall: a readable card column on a phone
    panels_cfg = {p["id"]: p for p in ctx["layout"]["bench"]["panels"]}
    queue_res = services.queue.list_items()
    data = {
        "motion": {"res": queue_res,
                   "rows": [i for i in (queue_res.data or []) if i["status_known"] and i["status"] in ACTIVE]},
        "blocked": {"res": queue_res,
                    "rows": [i for i in (queue_res.data or []) if i["status_known"] and i["status"] == "blocked"]},
        "discussions": {"res": services.sessions.list_sessions()},
        "postits": {"board": services.floor.list_postits(), "bin": services.floor.list_bin(limit=20)},
    }
    return render_template("guild_floor/bench.html", panels_cfg=panels_cfg, data=data, **ctx)


@owner_page
def queue():
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
        ctx = _context("item", "Build Queue", f"#{item_id} (not found)")
        return render_template("guild_floor/not_found.html", item_id=item_id, **ctx), 404
    title = res.data["title"] if res.ok else "unknown"
    ctx = _context("item", "Build Queue", f"#{item_id} {title}", page_extra={"item_id": item_id})
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
    ctx = _context("operate", "Operate", "Operate")
    tile_ids = [t["id"] for t in ctx["layout"]["operate"]["tiles"]]
    wanted = request.args.get("tile")
    selected = wanted if wanted in tile_ids else ctx["layout"]["operate"]["default_selected"]
    lights = {l["id"]: l for l in ctx["state"]["lights"]}
    return render_template("guild_floor/operate.html", selected_tile=selected, tile_lights=lights, **ctx)
