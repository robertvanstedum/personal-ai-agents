"""Request, receipt, claim and result: what the record allows next (v0.6 section 7; v0.7 Unit 3).

Delivery is at least once, so every rule here is about *meaning*, never about delivery: a receipt proves a recipient picked an
ID up, not that work started; a claim is the only thing that says one writer owns a resource; a result is the worker's report,
not an independent check. Claims move by compare-and-append: a claim names the next generation of its resource and is refused
while another claim on that resource has not been released as stopped. A clock running out never releases anything (a lease
time is a diagnostic only), because a writer that may still be running is not a writer that has stopped.

``check`` is called by the journal under its lock, before the event is written. ``derive`` rebuilds the same view for the brief.
No model, no network, no clock: ordering is journal order.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.workshop_journal.errors import ClaimConflict, PolicyRefused

LIFECYCLE_KINDS = ("request", "receipt", "claim", "release", "result", "started", "progress", "done", "next", "blocked")
RESULT_OUTCOMES_RETURNED = ("completed", "blocked", "declined", "failed", "uncertain")


@dataclass
class Claim:
    event: dict
    request_id: str
    resource: str
    generation: int
    claimant: str
    stopped_by: dict | None = None            # the release event that closed it, only when it said stopped
    unstopped_releases: list = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.event["event_id"]

    @property
    def active(self) -> bool:
        return self.stopped_by is None


@dataclass
class Flow:
    requests: dict = field(default_factory=dict)        # id -> {"event", "recipients", "receipts": {r: [ev]}, "results": {r: [ev]}}
    claims: dict = field(default_factory=dict)          # id -> Claim
    by_resource: dict = field(default_factory=dict)     # resource -> [claim id] in journal order

    def current_generation(self, resource: str) -> int:
        return max((self.claims[c].generation for c in self.by_resource.get(resource, [])), default=0)

    def effective_claim(self, resource: str) -> Claim | None:
        for cid in reversed(self.by_resource.get(resource, [])):
            if self.claims[cid].active:
                return self.claims[cid]
        return None


def derive(events: list[dict]) -> Flow:
    """The request/claim picture from journal order alone. Rows that break the rules are not trusted to build it."""
    flow = Flow()
    for ev in events:
        if ev.get("v") != 2:
            continue
        kind, p = ev["kind"], ev.get("payload") or {}
        if kind == "request":
            flow.requests[ev["event_id"]] = {"event": ev, "recipients": list(ev.get("recipients") or []), "receipts": {}, "results": {}}
        elif kind == "receipt" and p.get("request_id") in flow.requests:
            flow.requests[p["request_id"]]["receipts"].setdefault(p["recipient"], []).append(ev)
        elif kind == "result" and p.get("request_id") in flow.requests:
            flow.requests[p["request_id"]]["results"].setdefault(p["recipient"], []).append(ev)
        elif kind == "claim" and p.get("request_id") in flow.requests:
            claim = Claim(ev, p["request_id"], p["resource"], p["generation"], p["claimant"])
            flow.claims[ev["event_id"]] = claim
            flow.by_resource.setdefault(p["resource"], []).append(ev["event_id"])
        elif kind == "release" and p.get("claim_id") in flow.claims:
            claim = flow.claims[p["claim_id"]]
            if p.get("generation") == claim.generation and p.get("stopped") is True and claim.stopped_by is None:
                claim.stopped_by = ev
            elif p.get("generation") == claim.generation:
                claim.unstopped_releases.append(ev)
    return flow


def recipient_status(request: dict, recipient: str) -> str:
    """pending (nothing back), received (receipt only), returned (a result from that recipient)."""
    if request["results"].get(recipient):
        return "returned"
    return "received" if request["receipts"].get(recipient) else "pending"


def request_state(request: dict) -> str:
    """open until every required recipient has returned a result."""
    return "closed" if all(recipient_status(request, r) == "returned" for r in request["recipients"]) else "open"


# ── the rules ──────────────────────────────────────────────────────────────────────────────────────────────────────
def _refuse(reason: str):
    raise PolicyRefused(reason)


def check(intent: dict, events: list[dict]) -> None:
    """Raise PolicyRefused or ClaimConflict if the record forbids this event now. Cheap kinds never rebuild the picture."""
    kind, p, actor = intent["kind"], intent["payload"], intent["actor"]
    if kind == "request":
        if not intent["recipients"]:
            _refuse("request_needs_a_recipient")
        if actor in intent["recipients"] or "host" in intent["recipients"]:
            _refuse("request_recipient_not_allowed")
        return
    if kind == "decision":
        record_kind = p["record_event_kind"]
        if record_kind not in ("proposed", "withdrawn") and intent["authority_ref"] is None:
            _refuse("approval_needs_owner_control")              # an approval, rejection or verification cites owner control or a mandate
        if record_kind == "withdrawn":
            mine = {e["event_id"] for e in events if e.get("v") == 2 and e["kind"] == "decision" and e["actor"] == actor
                    and e["payload"]["record_event_kind"] == "proposed"}
            if not p["resolves"] or not set(p["resolves"]) <= mine:
                _refuse("withdraw_own_proposal_only")
        return
    if kind not in ("receipt", "result", "claim", "release", "started", "progress", "done"):
        return
    claim_id = p.get("claim_id")
    needs_flow = kind in ("receipt", "result", "claim", "release") or claim_id or (kind == "done" and p.get("request_id"))
    if not needs_flow and not (kind in ("started", "progress") and p.get("request_id")):
        return
    flow = derive(events)

    def request(rid: str) -> dict:
        req = flow.requests.get(rid)
        if req is None:
            _refuse("unknown_request")
        return req

    if kind in ("started", "progress", "done"):
        if p.get("request_id"):
            request(p["request_id"])
        if claim_id:
            claim = flow.claims.get(claim_id)
            if claim is None:
                _refuse("unknown_claim")
            if claim.claimant != actor:
                _refuse("not_the_claimant")
            if not claim.active:
                _refuse("claim_already_stopped")
        return
    if kind == "receipt":
        req = request(p["request_id"])
        if p["recipient"] not in req["recipients"]:
            _refuse("recipient_not_on_the_request")
        if actor != p["recipient"]:
            _refuse("a_receipt_comes_from_its_recipient")
        return
    if kind == "result":
        req = request(p["request_id"])
        if p["recipient"] not in req["recipients"]:
            _refuse("recipient_not_on_the_request")
        if actor != p["recipient"]:
            _refuse("a_result_comes_from_its_recipient")
        if claim_id:
            claim = flow.claims.get(claim_id)
            if claim is None or claim.request_id != p["request_id"]:
                _refuse("unknown_claim")
            if claim.claimant != actor:
                _refuse("not_the_claimant")
        return
    if kind == "claim":
        req = request(p["request_id"])
        if actor != p["claimant"]:
            _refuse("a_claim_is_made_by_its_claimant")
        if p["claimant"] not in req["recipients"]:
            _refuse("claimant_not_on_the_request")
        if p["generation"] != flow.current_generation(p["resource"]) + 1:
            raise ClaimConflict("not_the_next_generation")
        if flow.effective_claim(p["resource"]) is not None:
            raise ClaimConflict("resource_has_an_effective_claimant")
        return
    if kind == "release":
        claim = flow.claims.get(p["claim_id"])
        if claim is None:
            _refuse("unknown_claim")
        if p["generation"] != claim.generation:
            raise ClaimConflict("not_this_claims_generation")
        if actor != claim.claimant and actor != "robert":
            _refuse("only_the_claimant_or_the_owner_releases")
        if claim.stopped_by is not None:
            _refuse("claim_already_stopped")
