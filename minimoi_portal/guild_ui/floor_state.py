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
from .needs import needs_you

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


def notes_zone(res) -> dict:
    if not res.ok:
        text = UNAVAILABLE["notes"] if res.reason != "not_configured" else f"Notes unavailable — {NOT_CONFIGURED}. Nothing you send is kept."
        return {"state": "unavailable", "reason": res.reason, "text": text, "recent": None, "more": None}
    return {"state": "ok", "text": NOTES_ON, "recent": res.data.get("notes"), "more": res.data.get("notes_more")}


def compute(c: dict, *, notes_limit: int = 0) -> dict:
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
    notes = notes_zone(floor_res)
    return {
        "observed_at": observed_at,
        "mc_state": MC_STATE,
        "mc_header": MC_HEADER if notes["state"] == "ok" else MC_HEADER_NO_NOTES,
        "lights": lights,
        "needs": needs,
        "briefing": opening_briefing(lights, needs, observed_at),
        "queue": {"status": queue_res.status, "source": queue_res.source, "error": queue_res.error,
                  "note": queue_res.note, "observed_at": queue_res.observed_at,
                  "active": len(active) if active is not None else None},
        "continue": continue_zone(floor_res, floor_res.data["continue"] if floor_res.ok else None),
        "postits": postits_zone(floor_res, cap),
        "notes": notes,
        "floor_store": {"status": floor_res.status, "source": floor_res.source, "reason": floor_res.reason,
                        "error": floor_res.error, "observed_at": floor_res.observed_at},
    }


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
