"""Fold immutable events into an occurrence's or an episode's current state (spec v0.3 section 4.6).

Occurrences and episodes are never stored as editable rows. Their state is always this fold over
obligations/events.jsonl, so a restart recomputes exactly what was true, claims included.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---- occurrences ----------------------------------------------------------------------------------------------------

TERMINAL = frozenset({"satisfied", "satisfied_late", "cancelled", "missed"})

# event type -> states it may follow. An occurrence with no events is "open".
_OCC_FROM = {
    "opened": {"none"},
    "nudge_claimed": {"open", "acknowledged", "postponed"},
    "nudged": {"open", "acknowledged", "postponed"},          # needs its own claim, checked separately
    "acknowledged": {"open", "nudged", "postponed"},
    "evidence_linked": {"open", "nudged", "acknowledged", "postponed"},
    "satisfied": {"open", "nudged", "acknowledged", "postponed"},
    "satisfied_late": {"open", "nudged", "acknowledged", "postponed", "missed"},
    "postponed": {"open", "nudged", "acknowledged", "postponed"},
    "cancelled": {"open", "nudged", "acknowledged", "postponed"},
    "missed": {"open", "nudged", "acknowledged", "postponed"},
    "reopened": set(TERMINAL),
}
# event type -> state it leaves (None keeps the state).
_OCC_TO = {
    "opened": "open", "nudge_claimed": None, "nudged": "nudged", "acknowledged": "acknowledged",
    "evidence_linked": None, "satisfied": "satisfied", "satisfied_late": "satisfied_late",
    "postponed": "postponed", "cancelled": "cancelled", "missed": "missed", "reopened": "open",
}


@dataclass
class Occurrence:
    occurrence_id: str
    state: str = "none"
    claims: set = field(default_factory=set)
    nudged_claims: set = field(default_factory=set)
    evidence: list = field(default_factory=list)
    events: int = 0

    @property
    def effective(self) -> str:
        return "open" if self.state == "none" else self.state


def occurrence_refusal(occ: Occurrence, event: dict) -> str | None:
    """None when the event may follow, else a fixed code. Acknowledging never completes."""
    kind = event["type"]
    state = occ.state
    allowed = _OCC_FROM[kind]
    if state not in allowed and not (state == "none" and "open" in allowed):
        return "transition_not_allowed"
    if kind == "nudge_claimed" and occ.claims - occ.nudged_claims:
        return "nudge_already_claimed"           # one open claim at a time: a restart never sends twice
    if kind == "nudge_claimed" and event["claim_id"] in occ.claims:
        return "claim_reused"
    if kind == "nudged" and (event["claim_id"] not in occ.claims or event["claim_id"] in occ.nudged_claims):
        return "nudge_without_claim"
    return None


def apply_occurrence(occ: Occurrence, event: dict) -> None:
    kind = event["type"]
    to = _OCC_TO[kind]
    if to is not None:
        occ.state = to
    elif occ.state == "none":
        occ.state = "open"
    if kind == "nudge_claimed":
        occ.claims.add(event["claim_id"])
    if kind == "nudged":
        occ.nudged_claims.add(event["claim_id"])
    if event.get("evidence_ref"):
        occ.evidence.append(event["evidence_ref"])
    occ.events += 1


# ---- reporting episodes ---------------------------------------------------------------------------------------------

_EP_FROM = {
    "opened": {"none"},
    "ask_claimed": {"open"},
    "asked": {"open"},                       # needs its ask claim
    "escalation_claimed": {"asked"},
    "escalated": {"asked"},                  # needs its escalation claim
    "closed": {"open", "asked", "escalated"},
    "reopened": {"closed"},
    "missed_cycle": {"open", "asked", "escalated"},
    "paused": {"open", "asked", "escalated"},
    "resumed": {"paused"},
}
_EP_TO = {
    "opened": "open", "ask_claimed": None, "asked": "asked", "escalation_claimed": None, "escalated": "escalated",
    "closed": "closed", "reopened": "open", "missed_cycle": None, "paused": "paused", "resumed": "open",
}


@dataclass
class Episode:
    episode_id: str
    state: str = "none"
    ask_claim: str | None = None
    escalation_claim: str | None = None
    missed_cycles: int = 0
    closed_by: dict | None = None
    events: int = 0


def episode_refusal(ep: Episode, event: dict) -> str | None:
    """One ask and one escalation per episode, by construction; a fresh update closes it."""
    kind = event["type"]
    if ep.state not in _EP_FROM[kind]:
        return "transition_not_allowed"
    if kind == "ask_claimed" and ep.ask_claim is not None:
        return "ask_already_claimed"
    if kind == "asked" and (ep.ask_claim is None or event["claim_id"] != ep.ask_claim):
        return "ask_without_claim"
    if kind == "escalation_claimed" and ep.escalation_claim is not None:
        return "escalation_already_claimed"
    if kind == "escalated" and (ep.escalation_claim is None or event["claim_id"] != ep.escalation_claim):
        return "escalation_without_claim"
    return None


def apply_episode(ep: Episode, event: dict) -> None:
    kind = event["type"]
    to = _EP_TO[kind]
    if to is not None:
        ep.state = to
    if kind == "ask_claimed":
        ep.ask_claim = event["claim_id"]
    if kind == "escalation_claimed":
        ep.escalation_claim = event["claim_id"]
    if kind == "missed_cycle":
        ep.missed_cycles += 1
    if kind == "closed":
        ep.closed_by = event["closed_by"]
    if kind == "reopened":
        ep.closed_by = None
    ep.events += 1


# ---- In flight rows ---------------------------------------------------------------------------------------------------
# CoS's working table (the UI note of 1 Nov): one record type, cos.in_flight, one document per row. Each in_flight.*
# command is folded onto the row by the writer, which stores the folded row; the table reads rows as they are.

IN_FLIGHT_KINDS = ("in_flight.open", "in_flight.update", "in_flight.comment", "in_flight.close")


def in_flight_refusal(row: dict | None, kind: str, event: dict) -> str | None:
    """None when the command may follow, else a fixed code: open only a new row, change and close only an open one.
    A closed row still takes comments (its thread stays readable and answerable in Earlier)."""
    if kind == "in_flight.open":
        return "row_exists" if row is not None else None
    if row is None:
        return "no_row"
    if kind in ("in_flight.update", "in_flight.close") and row.get("closed_at") is not None:
        return "row_closed"
    return None


def apply_in_flight(row: dict | None, kind: str, event: dict) -> dict:
    """The row after the command (a new dict; the revision is the writer's). ``at`` is the command's time."""
    at = event["at"]
    if kind == "in_flight.open":
        return {"schema": "minimoi.cos.in_flight/1", "data_scope": event["data_scope"],
                "environment": event["environment"], "item_id": event["item_id"], "kind": event["kind"],
                "title": event["title"], "title_provenance": event["provenance"], "priority": event["priority"],
                "status": event["status"], "with": event["with"], "opened_at": at, "last_moved_at": at,
                "closed_at": None, "outcome": None, "source": event.get("source"), "comments": [],
                "opened_by": event["by"]}
    out = dict(row, comments=list(row["comments"]))
    if kind == "in_flight.update":
        for key in ("title", "priority", "status", "with"):
            if key in event:
                out[key] = event[key]
        if "title" in event:
            out["title_provenance"] = event["provenance"]
        out["last_moved_at"] = at
    elif kind == "in_flight.comment":
        comment = {"at": at, "author": event["author"], "text": event["text"], "provenance": event["provenance"]}
        for key in ("turn_ref", "ref"):
            if key in event:
                comment[key] = event[key]
        out["comments"].append(comment)
        out["last_moved_at"] = at
    elif kind == "in_flight.close":
        out.update(closed_at=at, last_moved_at=at, outcome=event["outcome"],
                   status="done" if event["outcome"] == "done" else out["status"])
        if event.get("note"):
            out["close_note"] = event["note"]
    return out
