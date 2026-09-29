"""The focused Workshop screen's data (4a: read only, model free).

Everything comes from files and the queue: the workshop's state.json (synced
from the Mac by scripts/workshop/workshop.py), the Build Queue, and the usage
store. Nothing here calls a model, and nothing launches: the launcher is 4b.

Honest states: a missing, unreadable or stale host reading is "unknown", and
its runs are "unknown", never "nothing running".
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from ..workshop.record import STALE_AFTER, load_state, now, parse
from .adapters import ACTIVE, by_recent
from .needs import needs_you

APPROVED = ("spec_ready", "in_build")


def workshop_dir() -> str | None:
    return os.environ.get("MINIMOI_WORKSHOPS_DIR") or None


def workshop_id() -> str:
    return os.environ.get("MINIMOI_WORKSHOP_ID") or "mac"


def _age(value) -> str | None:
    d = parse(value) if value else None
    if d is None:
        return None
    mins = int((now() - d).total_seconds() // 60)
    return "just now" if mins < 1 else f"{mins} min ago" if mins < 90 else f"{mins // 60} h ago"


def admission(state: dict | None, status: str) -> dict:
    """ok | tight | blocked | unknown, with the reason and the reading's age."""
    host = (state or {}).get("host") or {}
    if status == "missing":
        return {"verdict": "unknown", "reasons": ["no workshop record synced yet (workshop.py observe, then sync)"],
                "observed_at": None, "age": None}
    if status == "unreadable":
        return {"verdict": "unknown", "reasons": ["the workshop record could not be read"], "observed_at": None, "age": None}
    if not host:
        return {"verdict": "unknown", "reasons": ["no host reading yet (workshop.py observe)"], "observed_at": None, "age": None}
    seen = host.get("observed_at") or host.get("at")
    if status == "stale":
        return {"verdict": "unknown",
                "reasons": [f"the last host reading is {_age(seen)} old (over {int(STALE_AFTER.total_seconds() // 60)} min): "
                            f"it said {host.get('verdict', 'unknown')}"],
                "observed_at": seen, "age": _age(seen)}
    return {"verdict": host.get("verdict") or "unknown", "reasons": host.get("reasons") or [],
            "observed_at": seen, "age": _age(seen)}


def observed_runs(state: dict | None, status: str) -> dict:
    host = (state or {}).get("host") or {}
    if status != "ok" or not host or not host.get("clients_known"):
        return {"known": False, "runs": [], "text": "Running agents unknown (no fresh host reading)"}
    runs = host.get("runs") or []
    text = "No agent client running" if not runs else f"{len(runs)} agent client{'s' if len(runs) != 1 else ''} running"
    return {"known": True, "runs": runs, "text": text}


def usage_this_month(folder: str | None) -> dict:
    """Gateway spend this month by actor, from the usage store; None when unknown."""
    if not folder or not os.path.isdir(folder):
        return {"known": False}
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    path = os.path.join(folder, f"usage-{month}.jsonl")
    spent: dict[str, float] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("emitter") == "gateway" and isinstance(r.get("cost_usd"), (int, float)):
                    spent[r.get("actor") or "unknown"] = spent.get(r.get("actor") or "unknown", 0.0) + r["cost_usd"]
    except FileNotFoundError:
        return {"known": True, "month": month, "by_actor": {}}
    except OSError:
        return {"known": False}
    return {"known": True, "month": month, "by_actor": {k: round(v, 4) for k, v in sorted(spent.items())}}


RECOVERY = {
    "blocked": ["Let a running agent client finish, or close one you are not using, then observe again.",
                "Free disk on the Mac (for example docker builder prune -f, then colima ssh -- sudo fstrim -av)."],
    "tight": ["A new run can start but would share the Mac: prefer one at a time."],
    "unknown": ["On the Mac: scripts/workshop/workshop.py observe, then sync. A reading older than 15 minutes counts as unknown."],
    "ok": [],
}


def view(services, item_id: int | None, *, usage_dir: str | None = None) -> dict:
    state, status = load_state(workshop_dir(), workshop_id())
    adm = admission(state, status)
    queue_res = services.queue.list_items()
    checks = services.queue.checks()
    items = queue_res.data or []
    item = next((i for i in items if i.get("id") == item_id), None) if item_id else None
    key = f"queue:{item_id}" if item_id else None
    item_event = ((state or {}).get("items") or {}).get(key) if key else None
    needs = needs_you(queue_res, checks, cap=100)
    item_needs = [n for n in needs.get("items", []) if item_id and n.get("item_id") == item_id]
    ws_needs = [n for n in (state or {}).get("needs_you", []) if not key or n.get("item") == key]
    queued = sorted([i for i in items if i.get("status_known") and i.get("status") == "spec_ready" and i.get("id") != item_id],
                    key=by_recent, reverse=True)[:5]
    return {
        "workshop": workshop_id(), "record_status": status, "state": state,
        "admission": adm, "runs": observed_runs(state, status),
        "last_event": item_event or (state or {}).get("last_event"),
        "last_event_scope": "this item" if item_event else "the workshop",
        "next_actor": (item_event or {}).get("next_actor"),
        "item": item, "item_res": queue_res, "approved": bool(item and item.get("status") in APPROVED),
        "needs": {"status": needs.get("status"), "text": needs.get("text"), "items": item_needs, "workshop": ws_needs},
        "queued": queued, "queued_next": (state or {}).get("next", [])[:5],
        "budget": usage_this_month(usage_dir or os.environ.get("MINIMOI_USAGE_DIR")),
        "recovery": RECOVERY.get(adm["verdict"], RECOVERY["unknown"]),
        "active_statuses": ACTIVE,
    }


def api_view(v: dict) -> dict:
    """What the browser's refresh reads (no queue rows, no raw state)."""
    return {"workshop": v["workshop"], "record_status": v["record_status"], "admission": v["admission"],
            "runs": v["runs"], "last_event": v["last_event"], "next_actor": v["next_actor"],
            "needs": {"status": v["needs"]["status"], "count": len(v["needs"]["items"]) + len(v["needs"]["workshop"])},
            "budget": v["budget"]}


__all__ = ["view", "api_view", "admission", "observed_runs", "usage_this_month", "workshop_dir", "workshop_id"]
