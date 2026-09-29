"""One computation of the Shop floor's state, shared by the pages and the API.

Nothing here calls a model or any paid service: it reads the queue through
the store, the journal's open Checks, the Operations probe (cached), and the
floor store (post-its, Continue, and on pages the latest notes) on one
database connection.
"""
from __future__ import annotations

import hashlib
import json

from flask import url_for

from .adapters.contract import now_iso
from .briefing import opening_briefing
from .lights import derive_lights
from .mc import OffBackend
from .mc import view as mc_view_of
from .needs import needs_you

# Master Craftsman's state comes from the backend switch (mc/backend.py); these
# are the "off" texts, kept for readers of B1.
MC_STATE = "off"
MC_HEADER = "Master Craftsman is off · your messages are kept as notes"
MC_HEADER_NO_NOTES = "Master Craftsman is off · notes unavailable, nothing you send is kept"
CONTINUE_NONE = "Nothing to continue — open a queue item"
UNAVAILABLE = {
    "continue": "Continue unavailable — treat as unknown",
    "postits": "Post-its unavailable — add and remove are paused",
    "notes": "Conversation unavailable — treat as unknown. Nothing you send is kept.",
}
NOT_CONFIGURED = "the floor database is not configured on this portal"
NOTES_ON = "On the record, your messages are kept as notes. Master Craftsman does not reply."


def light_href(light) -> str:
    kind, _, tile = light["link"].partition(":")
    return url_for(".queue") if kind == "queue" else url_for(".operate", tile=tile)


def item_href(item_id) -> str:
    return url_for(".item", item_id=item_id) if isinstance(item_id, int) else url_for(".queue")


def principal_of(c: dict) -> str:
    user = c["current_user"]() or {}
    return user.get("username") or "owner"


def continue_zone(res, target=None) -> dict:
    """Continue as shown: the server's target, "nothing yet", or unavailable."""
    if not res.ok:
        text = UNAVAILABLE["continue"] if res.reason != "not_configured" else f"Continue unavailable — {NOT_CONFIGURED}"
        return {"state": "unavailable", "reason": res.reason, "text": text, "target": None,
                "observed_at": res.observed_at}
    if target is None:
        return {"state": "ok", "text": CONTINUE_NONE, "target": None, "observed_at": res.observed_at}
    target = dict(target)
    target["href"] = item_href(int(target["ref"])) if target.get("kind") == "item" and str(target.get("ref", "")).isdigit() \
        else url_for(".queue")
    return {"state": "ok", "text": target["label"], "target": target, "observed_at": res.observed_at}


def postits_zone(res, cap: int) -> dict:
    if not res.ok:
        text = UNAVAILABLE["postits"] if res.reason != "not_configured" else f"Post-its unavailable — {NOT_CONFIGURED}"
        return {"state": "unavailable", "reason": res.reason, "text": text, "shown": None, "active_total": None,
                "more": None, "bin_total": None, "cap": cap, "observed_at": res.observed_at}
    shown = res.data["postits"][:cap]
    total = res.data["active_total"]
    text = "No post-its on the board" if not total else f"{total} on the board"
    return {"state": "ok", "text": text, "shown": shown, "active_total": total, "more": max(0, total - len(shown)),
            "bin_total": res.data["bin_total"], "cap": cap, "observed_at": res.observed_at}


def notes_zone(res, notes_text: str = NOTES_ON) -> dict:
    if not res.ok:
        text = UNAVAILABLE["notes"] if res.reason != "not_configured" else f"Notes unavailable — {NOT_CONFIGURED}. Nothing you send is kept."
        return {"state": "unavailable", "reason": res.reason, "text": text, "recent": None, "more": None}
    return {"state": "ok", "text": notes_text, "recent": res.data.get("notes"), "more": res.data.get("notes_more")}


def mc_view(services, *, notes_ok: bool) -> dict:
    """Master Craftsman's state for this request: the switch's backend and its
    cached health (60 s). Never a model call: health reads readiness only."""
    cached = getattr(services, "mc_health", None)
    health = cached.get() if cached is not None else OffBackend().health()
    return mc_view_of(health, notes_ok=notes_ok, turns_on=bool(getattr(services, "mc_turns", False)))


class _NotesRead:
    """A conversation's notes read, shaped like the floor summary for notes_zone."""

    def __init__(self, res):
        self.ok, self.reason = res.ok, getattr(res, "reason", None)
        self.data = {"notes": res.data.get("notes"), "notes_more": res.data.get("more")} if res.ok else None


def conversation_focus(continue_zone_: dict, conversation: dict | None = None) -> dict:
    """What the current conversation is about (the Shop floor's context rail).

    Slice 1 (no conversation records yet): the item Robert last opened from
    the wall or the queue (Continue). Slice 2 binds a work item to each
    conversation: pass that conversation's record and its ``work_item`` wins.
    """
    if conversation and conversation.get("work_item"):
        item = conversation["work_item"]
        return {"source": "conversation", "state": "ok", "target": item, "text": item.get("label", "")}
    return {"source": "continue", "state": continue_zone_["state"], "target": continue_zone_.get("target"),
            "text": continue_zone_["text"]}


def chat_blockers(mc: dict, cost_level: str | None = None) -> list[dict]:
    """What blocks chat, shown as one short line above the composer: MC down
    (turns on, runtime unavailable) and the cost level at act or stop. The
    cost level has no source yet (usage levels come later), so it is None."""
    out = []
    if mc.get("turns") and mc.get("state") == "unavailable":
        out.append({"kind": "mc_down", "text": mc.get("header") or "Master Craftsman is unavailable"})
    if cost_level in ("act", "stop"):
        out.append({"kind": "cost", "text": f"Cost level: {cost_level}"})
    return out


def compute(c: dict, *, notes_limit: int = 0, conversation: dict | None = None) -> dict:
    services, layout = c["services"], c["layout"]
    observed_at = now_iso()
    queue_res = services.queue.list_items()
    checks = services.queue.checks()
    lights = derive_lights(layout["floor"]["lights"], services, queue_res)
    for light in lights:
        light["href"] = light_href(light)
    needs = needs_you(queue_res, checks, cap=layout["floor"]["max_reminders"])
    for n in needs["items"]:
        n["href"] = item_href(n["item_id"])
    active = [i for i in (queue_res.data or []) if i.get("status_known") and i.get("status") in ("spec_ready", "in_build")] \
        if queue_res.ok else None
    cap = layout["floor"].get("postit_cap", 4)
    floor_res = services.floor.summary(principal_of(c), rail_cap=cap, notes_limit=notes_limit)
    mc = mc_view(services, notes_ok=floor_res.ok)
    notes_res = floor_res
    if notes_limit and conversation and conversation.get("notes_floor") not in (None, services.floor.floor):
        # A conversation's own notes (slice 2) live under its own floor key.
        conv_res = services.floor.for_floor(conversation["notes_floor"]).list_notes(limit=notes_limit)
        notes_res = _NotesRead(conv_res)
    notes = notes_zone(notes_res, mc["notes_text"])
    if notes.get("recent"):
        from .mc.turn_log import turn_log_of
        turn_log_of(services).annotate(notes["recent"])
        from .markdown_render import with_html
        with_html(notes["recent"])
    out = {
        "observed_at": observed_at,
        "mc_state": mc["state"],
        "mc_header": mc["header"],
        "mc": {"state": mc["state"], "reason": mc["reason"], "turns": mc["turns"], "observed_at": mc["observed_at"]},
        "lights": lights,
        "needs": needs,
        "briefing": opening_briefing(lights, needs, observed_at),
        "queue": {"status": queue_res.status, "source": queue_res.source, "error": queue_res.error,
                  "note": queue_res.note, "observed_at": queue_res.observed_at,
                  "active": len(active) if active is not None else None},
        "continue": continue_zone(floor_res, floor_res.data["continue"] if floor_res.ok else None),
        "postits": postits_zone(floor_res, cap),
        "cost_level": None,              # no source yet; the strip shows cost only when it is not good
        "notes": notes,
        "floor_store": {"status": floor_res.status, "source": floor_res.source, "reason": floor_res.reason,
                        "error": floor_res.error, "observed_at": floor_res.observed_at},
    }
    out["focus"] = conversation_focus(out["continue"], conversation)
    out["blockers"] = chat_blockers({**out["mc"], "header": out["mc_header"]}, out["cost_level"])
    return out


def _strip_times(value):
    if isinstance(value, dict):
        return {k: _strip_times(v) for k, v in value.items() if not k.endswith("observed_at")}
    if isinstance(value, list):
        return [_strip_times(v) for v in value]
    return value


def etag(state: dict) -> str:
    """Covers every source's status and value, so a 304 never hides a failure
    (review N4); only observation times are left out."""
    stable = dict(state)
    stable["briefing"] = {k: v for k, v in state["briefing"].items() if k != "text"}
    canonical = json.dumps(_strip_times(stable), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
