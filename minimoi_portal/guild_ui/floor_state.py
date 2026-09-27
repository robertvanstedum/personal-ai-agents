"""One computation of the Shop floor's state, shared by the pages and the API.

Nothing here calls a model or any paid service: it reads the queue through
the store, the journal's open Checks, and the Operations probe (cached).
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
MC_HEADER = "Master Craftsman is off · notes are not connected yet"
NOT_CONNECTED = {
    "continue": "Continue arrives next: it will be kept on the server, the same on desktop and phone.",
    "postits": "Post-its arrive next: added and removed directly, with a kept bin.",
    "notes": "Notes are not connected yet. Nothing you type is sent or kept.",
}


def light_href(light) -> str:
    kind, _, tile = light["link"].partition(":")
    return url_for(".queue") if kind == "queue" else url_for(".operate", tile=tile)


def item_href(item_id) -> str:
    return url_for(".item", item_id=item_id) if isinstance(item_id, int) else url_for(".queue")


def compute(c: dict) -> dict:
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
    return {
        "observed_at": observed_at,
        "mc_state": MC_STATE,
        "mc_header": MC_HEADER,
        "lights": lights,
        "needs": needs,
        "briefing": opening_briefing(lights, needs, observed_at),
        "queue": {"status": queue_res.status, "source": queue_res.source, "error": queue_res.error,
                  "note": queue_res.note, "observed_at": queue_res.observed_at,
                  "active": len(active) if active is not None else None},
        "continue": {"state": "not_connected", "text": NOT_CONNECTED["continue"]},
        "postits": {"state": "not_connected", "text": NOT_CONNECTED["postits"]},
        "notes": {"state": "not_connected", "text": NOT_CONNECTED["notes"]},
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
