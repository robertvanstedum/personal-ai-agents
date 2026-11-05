"""Which evidence an operation needs (spec v1.1 Addendum 1, AC01). Documented once, here, with the representative
cases in ``CASES`` and tests in ``tests/partnership/test_evidence_class.py``.

**Full Q01 chain** (source event → interpretation → request → task, every step journalled with its causal parent) for
an operation that does any of:
- changes another seat's obligations;
- writes outside the responding seat's own turn history;
- reserves money, or spends it beyond the request's own metered allowance (a grant is needed);
- produces an external effect (anything leaving MiniMoi: a message, an issue, a file delivered elsewhere).

**Lightweight history** for everything else: ordinary answers and lookups keep D03 history (source, coverage, result)
on the request, and are never promoted to a task just to look important.

Lightweight never bypasses three things:
- authorization (the binding and its scope);
- disclosure controls (what the audience may see);
- Q04 spending evidence. A paid lookup or paid model attempt carries its request id and the spending authority it
  ran under, even with no task.

Scope follows actual effects and spending, not the tool's name or whether it is called a read: a "read" that
reserves money or sends something out is classified by what it does.

**Interpretation to confirm (Codex, spec lead):** AC01 lists "reserve or spend money" among full-chain triggers, and
also says a paid lookup needs no task. This reads the two together as: metered cost inside the request's own allowance
(a paid search, a model attempt) is lightweight plus Q04 attribution; a reservation, or spend that needs a grant, is
full chain. If the lead reads it otherwise, ``classify`` changes in one place.
"""
from __future__ import annotations

from dataclasses import dataclass

FULL = "full_chain"
LIGHT = "lightweight"


@dataclass(frozen=True)
class Operation:
    name: str
    changes_other_seat_obligations: bool = False
    writes_outside_own_history: bool = False
    reserves_money: bool = False
    spend: str = "none"                 # "none" | "metered_within_request" | "needs_grant"
    external_effect: bool = False


@dataclass(frozen=True)
class Classification:
    evidence: str
    needs_spending_attribution: bool
    reasons: tuple[str, ...]


def classify(op: Operation) -> Classification:
    if op.spend not in ("none", "metered_within_request", "needs_grant"):
        raise ValueError("unknown spend kind")
    reasons = []
    if op.changes_other_seat_obligations:
        reasons.append("changes another seat's obligations")
    if op.writes_outside_own_history:
        reasons.append("writes outside the responding seat's own history")
    if op.reserves_money:
        reasons.append("reserves money")
    if op.spend == "needs_grant":
        reasons.append("spends beyond the request's metered allowance")
    if op.external_effect:
        reasons.append("produces an external effect")
    paid = op.spend != "none" or op.reserves_money
    return Classification(FULL if reasons else LIGHT, paid, tuple(reasons))


def check_lightweight_record(record: dict, op: Operation) -> list[str]:
    """What a lightweight record is missing (empty when complete): D03 history always; Q04 attribution when paid."""
    missing = [k for k in ("source", "coverage", "result") if record.get(k) is None]
    if classify(op).needs_spending_attribution:
        missing += [k for k in ("request_id", "spending_authority") if not record.get(k)]
    return missing


# Representative cases (AC01 "include representative paid and unpaid lookup cases").
CASES = {
    "unpaid lookup: read an In flight row": (Operation("cos_actions.get"), LIGHT, False),
    "paid lookup: bounded web search": (Operation("web_search", spend="metered_within_request"), LIGHT, True),
    "paid model attempt answering a question": (Operation("model.answer", spend="metered_within_request"), LIGHT, True),
    "'read' that reserves money": (Operation("vendor.quote_lookup", reserves_money=True), FULL, True),
    "lookup that needs a grant": (Operation("premium.research", spend="needs_grant"), FULL, True),
    "record CoS's own draft in the store": (Operation("cos_actions.open", writes_outside_own_history=True), FULL, False),
    "assign work to MC": (Operation("task.assign", changes_other_seat_obligations=True), FULL, False),
    "file a GitHub issue": (Operation("github.issue_create", external_effect=True), FULL, False),
}
