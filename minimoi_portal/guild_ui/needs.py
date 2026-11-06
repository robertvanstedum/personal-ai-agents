"""Needs you, computed on read (spec S5). No store in B1.

Check: a Save left uncertain and not yet marked checked (from the journal).
Decide: a blocked queue item, with its blocked reason.
Rework: an item that review or testing sent back (Guild 1.1 slice 2), with its
reason. Both stay until an explicit Save takes the item out of trouble.
Unknown: a queue row whose status or fields cannot be read; it may be
blocked, so it is shown rather than dropped (review F2).
Approve: a task proposal or work card waiting for Robert, from the task service
(contract approval_and_needs v0.1 section 2). ``item_id`` is a string and ``href``
says where to decide it. ``approvals_res`` None means this host has no task
service at all, so nothing can be waiting there.
Three are shown and the total is counted. A failed queue read, an unreadable
journal, or a task service that cannot be read is unknown, never "nothing needs
you" (review F3).
"""
from __future__ import annotations

from .adapters.contract import SourceResult, live_ok, now_iso
from .adapters.queue_reader import JOURNAL_EVIDENCE, by_recent


def _approve_items(approvals_res: SourceResult | None) -> list[dict]:
    if approvals_res is None or not approvals_res.ok:
        return []
    rows = sorted(approvals_res.data or [], key=lambda a: a.get("at") or "", reverse=True)
    items = []
    for a in rows:
        item = {"tag": "Approve", "item_id": a["id"], "op_id": None, "text": a["text"], "at": a.get("at")}
        if a.get("href"):
            item["href"] = a["href"]
        items.append(item)
    return items


def needs_you(queue_res: SourceResult, checks_res, cap: int = 3, approvals_res: SourceResult | None = None) -> dict:
    observed_at = now_iso()
    if not isinstance(checks_res, SourceResult):   # a plain list of Checks, already read
        checks_res = live_ok(list(checks_res or []), JOURNAL_EVIDENCE)
    items = _approve_items(approvals_res)              # first, so the cap never hides a waiting decision
    for c in (checks_res.data or []) if checks_res.ok else []:
        items.append({"tag": "Check", "item_id": c.get("item_id"), "op_id": c.get("op_id"),
                      "text": f"Save not verified — check #{c.get('item_id')}", "at": c.get("at")})
    if not queue_res.ok:
        return {"status": "unknown", "items": items[:cap], "total": None, "observed_at": observed_at,
                "text": "Needs you · unknown — read failed", "error": queue_res.error}
    trouble = [i for i in (queue_res.data or []) if i.get("status_known") and i.get("status") in ("blocked", "rework")]
    trouble.sort(key=by_recent, reverse=True)
    for i in trouble:
        if i["status"] == "blocked":
            reason = i.get("blocked_reason") or i.get("trouble_reason") or "no reason given"
            items.append({"tag": "Decide", "item_id": i["id"], "op_id": None,
                          "text": f"#{i['id']} {i['title']} — blocked: {reason}", "at": i.get("last_transition_at")})
        else:
            reason = i.get("trouble_reason") or "no reason given"
            items.append({"tag": "Rework", "item_id": i["id"], "op_id": None,
                          "text": f"#{i['id']} {i['title']} — rework: {reason}", "at": i.get("last_transition_at")})
    for i in (queue_res.data or []):
        if not i.get("status_known"):
            items.append({"tag": "Unknown", "item_id": i["id"], "op_id": None,
                          "text": f"#{i['id']} {i['title']} — {i.get('status_label')}; check the queue file",
                          "at": i.get("last_transition_at")})
    if not checks_res.ok:
        return {"status": "unknown", "items": items[:cap], "total": None, "observed_at": observed_at,
                "text": "Needs you · unknown — journal unreadable", "error": checks_res.error}
    if approvals_res is not None and not approvals_res.ok:
        return {"status": "unknown", "items": items[:cap], "total": None, "observed_at": observed_at,
                "text": "Needs you · unknown — task service unreadable", "error": approvals_res.error}
    total = len(items)
    text = f"{total} need{'s' if total == 1 else ''} you" if total else "Nothing needs you"
    return {"status": "ok", "items": items[:cap], "total": total, "observed_at": observed_at, "text": text,
            "error": None}
