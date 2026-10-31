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
