"""Needs you, computed on read (spec S5). No store in B1.

Check: a Save left uncertain and not yet marked checked (from the journal).
Decide: a blocked queue item, with its blocked reason.
Three are shown and the total is counted. A failed queue read is unknown,
never "nothing needs you".
"""
from __future__ import annotations

from .adapters.contract import SourceResult, now_iso


def needs_you(queue_res: SourceResult, checks: list[dict], cap: int = 3) -> dict:
    observed_at = now_iso()
    items = []
    for c in checks:
        items.append({"tag": "Check", "item_id": c.get("item_id"), "op_id": c.get("op_id"),
                      "text": f"Save not verified — check #{c.get('item_id')}", "at": c.get("at")})
    if not queue_res.ok:
        return {"status": "unknown", "items": items[:cap], "total": None, "observed_at": observed_at,
                "text": "Needs you · unknown — read failed", "error": queue_res.error}
    blocked = [i for i in (queue_res.data or []) if i.get("status_known") and i.get("status") == "blocked"]
    blocked.sort(key=lambda i: i.get("last_transition_at") or "", reverse=True)
    for i in blocked:
        reason = i.get("blocked_reason") or "no reason given"
        items.append({"tag": "Decide", "item_id": i["id"], "op_id": None,
                      "text": f"#{i['id']} {i['title']} — blocked: {reason}", "at": i.get("last_transition_at")})
    total = len(items)
    text = f"{total} need{'s' if total == 1 else ''} you" if total else "Nothing needs you"
    return {"status": "ok", "items": items[:cap], "total": total, "observed_at": observed_at, "text": text,
            "error": None}
