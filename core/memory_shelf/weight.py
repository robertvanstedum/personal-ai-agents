"""Read-time weight and provenance (v0.5 B2 record contract).

Nothing is stored as "how much to trust this". It is derived from facts:

- no event: **deliberation**, shown as deliberation;
- ``approved-*`` outranks ``proposed``; the latest decisive event wins
  (approved / rejected / superseded / withdrawn);
- ``verified`` adds evidence and never approval;
- ``rejected`` and ``superseded`` stay visible and sort under their successor;
- a mandate carries weight only while its own record is approved and not
  withdrawn: ``approved-under-mandate`` for a mandate not in force reads as
  ``proposed`` and says why.
"""
from __future__ import annotations

from typing import Callable

DECISIVE = {"approved-direct", "approved-under-mandate", "rejected", "superseded", "withdrawn"}
RANK = {"approved": 4, "proposed": 3, "deliberation": 2, "rejected": 1, "superseded": 0, "withdrawn": 0}


def _date(event: dict) -> str:
    return event["at"][:10]


def derive(record: dict, mandate_weight: Callable[[str], str] | None = None) -> dict:
    """{weight, line, evidence, successor, mandate_in_force} for one record's front matter."""
    events = sorted(record.get("events") or [], key=lambda e: e["at"])
    chair = record.get("chair", "unknown")
    weight, decisive, successor, note = "deliberation", None, None, None
    verified: list[str] = []
    for event in events:
        kind = event["kind"]
        if kind == "proposed" and decisive is None:
            weight = "proposed"
        elif kind == "verified":
            verified.append(_date(event)[5:])
        elif kind in DECISIVE:
            decisive = event
            if kind == "superseded":
                weight, successor = "superseded", event.get("successor")
            elif kind == "rejected":
                weight = "rejected"
            elif kind == "withdrawn":
                weight = "withdrawn"
            else:
                weight = "approved"
    mandate_ok = None
    if decisive is not None and decisive["kind"] == "approved-under-mandate":
        mandate = str(decisive.get("via", "")).removeprefix("mandate:")
        mandate_ok = bool(mandate_weight) and mandate_weight(mandate) == "approved"
        if not mandate_ok:
            weight, note = "proposed", "its mandate is not in force"
    parts = [f"written by {chair}"]
    if weight == "deliberation":
        parts.append("deliberation (not reviewed)")
    elif weight == "proposed":
        parts.append(note or "proposed, not approved")
    elif weight == "approved":
        how = "direct" if decisive["kind"] == "approved-direct" else f"under mandate {decisive['via'].split(':', 1)[1][-8:].lower()}"
        parts.append(f"approved by Robert ({how}) {_date(decisive)}")
    elif weight == "rejected":
        parts.append(f"rejected {_date(decisive)}")
    elif weight == "superseded":
        parts.append(f"superseded {_date(decisive)} by {str(successor)[-8:].lower()}")
    elif weight == "withdrawn":
        parts.append(f"withdrawn {_date(decisive)}")
    if verified:
        parts.append("verified " + ", ".join(verified))
    return {"weight": weight, "line": " · ".join(parts), "evidence": verified,
            "successor": successor, "mandate_in_force": mandate_ok}


def order(items: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """Order (record_id, derive() info) pairs for display.

    Records sort by weight, then id. A superseded record is placed immediately
    under its successor (rejected and withdrawn ones sort after everything
    active); if the successor is not in the list it falls to the end. Nothing
    is dropped: every record in is a record out.
    """
    ids = {rid for rid, _ in items}
    under: dict[str, list[tuple[str, dict]]] = {}
    top: list[tuple[str, dict]] = []
    for rid, info in sorted(items, key=lambda p: (-RANK[p[1]["weight"]], p[0])):
        parent = info["successor"] if info["weight"] == "superseded" else None
        if parent in ids and parent != rid:
            under.setdefault(parent, []).append((rid, info))
        else:
            top.append((rid, info))
    out: list[tuple[str, dict]] = []

    def place(pair):
        out.append(pair)
        for child in under.get(pair[0], []):
            place(child)
    for pair in top:
        place(pair)
    return out


__all__ = ["derive", "order", "RANK"]
