"""Derived state: what is in progress, what is next, what needs the owner, and the host's health.

Pure and model free. The reducer reads events in journal order (legacy rows first, then v2 by ``seq``); clock strings never
decide order. Two defects of the first Workshop reducer are fixed here and pinned by tests:

* an agent's ``decision`` no longer closes an owner question. A question closes only when a v2 ``decision`` names it in
  ``payload.resolves``, records an approval kind, and the injected ``resolver`` confirms validated owner control for that exact
  event. With no resolver nothing closes, so a label (``robert``, ``approved``) never resolves anything;
* every open ``needs_you`` is tracked, not just the last one per item.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from core.workshop_journal import schema

Resolver = Callable[[dict], bool]


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def brief(ev: dict) -> dict:
    payload = ev.get("payload") or {}
    recipients = ev.get("recipients") or []
    return {"event_id": ev.get("event_id"), "seq": ev.get("seq"), "at": ev.get("at"), "actor": ev.get("actor"),
            "kind": ev.get("kind"), "item": ev.get("item"), "stage": ev.get("stage"), "text": ev.get("text"),
            "next_actor": ev.get("next_actor") or payload.get("next_actor") or (recipients[0] if recipients else None),
            "refs": ev.get("refs"), "room": ev.get("room")}


def host_view(ev: dict | None) -> dict | None:
    if not ev:
        return None
    health = ev.get("health")
    if health is None and ev.get("v") == 2:
        payload = ev.get("payload") or {}
        health = {k: payload[k] for k in ("component", "status", "observed_at", "reason_code", "counts") if payload.get(k) is not None}
    return {"at": ev.get("at"), "text": ev.get("text"), **(health or {})}


def closed_needs(events: list[dict], resolver: Resolver | None) -> set[str]:
    """IDs of ``needs_you`` events (and their incident IDs) closed by an exact, validated owner resolution."""
    closed: set[str] = set()
    if resolver is None:
        return closed
    for ev in events:
        if ev.get("v") != 2 or ev.get("kind") != "decision":
            continue
        payload = ev.get("payload") or {}
        if payload.get("record_event_kind") not in schema.APPROVAL_KINDS:
            continue
        try:
            valid = resolver(ev) is True
        except Exception:                                 # a resolver that fails resolves nothing
            valid = False
        if valid:
            closed.update(payload.get("resolves") or [])
    return closed


def reduce(workshop_id: str, events: list[dict], *, resolver: Resolver | None = None, now: datetime | None = None) -> dict:
    by_item: dict[str, list[dict]] = {}
    for ev in events:
        by_item.setdefault(ev["item"], []).append(ev)
    closed = closed_needs(events, resolver)
    in_progress, nxt, needs, host = [], [], [], None
    for item, evs in by_item.items():
        last = evs[-1]
        if item.startswith("host:"):
            health = [e for e in evs if e.get("kind") == "health"]
            if health:
                host = health[-1]
            continue
        if last["kind"] == "next" or (last.get("stage") == "design" and not any(e["kind"] == "started" for e in evs)):
            nxt.append(brief(last))
        elif last["kind"] != "done":
            in_progress.append(brief(last))
        for e in evs:
            if e["kind"] != "needs_you":
                continue
            incident = (e.get("payload") or {}).get("incident_id")
            if e.get("event_id") in closed or (incident and incident in closed):
                continue
            needs.append(brief(e))
    items = {item: brief(evs[-1]) for item, evs in by_item.items() if not item.startswith("host:")}
    return {"v": 2, "workshop": workshop_id, "updated_at": _iso(now or datetime.now(timezone.utc)), "events": len(events),
            "in_progress": in_progress, "next": nxt, "needs_you": needs, "items": items, "host": host_view(host),
            "last_event": brief(events[-1]) if events else None}
