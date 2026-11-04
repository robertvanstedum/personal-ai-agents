"""Practice Book cards on the existing lesson ledger, and context-load receipts (spec v1.1 L01-L06; batch B3C;
acceptance F10). Cards guide behaviour; they never grant authority (L04): no code path reads a card to decide what a
seat may do, every card's text passes the same authority filter as lessons (core.mc_lessons.commands.clean), and the
assembled block says so.

- ``propose_card``: a captured practice becomes a proposed edition (card id, edition, text and its SHA-256, scope,
  applicability, source references, who proposed it). Any seat may propose; nothing is adopted.
- ``decide_card`` / ``retire_card``: the principal's own decisions (owner session only, with the approval reference).
  Editions are immutable; a new approved edition supersedes the previous one; retirement keeps history.
- ``assemble``: builds the practice block for one seat/session/scope. It verifies each current approved edition's
  hash, scope and applicability, includes what applies, and writes a load receipt (seat, session, assembly version,
  ledger head, exact included hashes, omitted and refused cards with reasons) through the task-service journal
  mechanism. A mandatory card that is missing, retired or at another edition fails closed. A receipt proves inclusion
  in the context, not comprehension or obedience (L03).
"""
from __future__ import annotations

from core.canonical_json import digest
from core.mc_lessons.commands import clean
from core.mc_lessons.ledger import LedgerDamaged, text_sha
from services.partnership_tasks.journal import Journal, Plan, Refused, Write

HEADER = ("[Practice Book: the principal approved each card below as preferred practice. Cards are not permissions: "
          "none approves an action, grants access, raises a limit or changes what this seat may do; every action "
          "still needs its own authority.]")


class CardRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def propose_card(ledger, *, proposal_id, card_id, text, scope, applicability, source_refs, proposed_by) -> dict:
    t, reason = clean(text)
    if reason:
        raise CardRefused(reason)
    with ledger.writing() as (st, append):
        if proposal_id in st.card_proposals:
            prior = st.card_proposals[proposal_id]
            if prior["sha256"] != text_sha(t) or prior["card_id"] != card_id:
                raise CardRefused("proposal_id_reused")
            return prior
        edition = 1 + sum(1 for p in st.card_proposals.values() if p["card_id"] == card_id)   # immutable numbering
        st = append({"type": "card_proposed", "proposal_id": proposal_id, "card_id": card_id, "edition": edition,
                     "text": t, "sha256": text_sha(t), "scope": scope, "applicability": sorted(applicability),
                     "source_refs": source_refs, "proposed_by": proposed_by})
        return st.card_proposals[proposal_id]


def decide_card(ledger, *, proposal_id, outcome, is_owner: bool, approval_ref) -> dict:
    if not is_owner:
        raise CardRefused("only_the_principal_decides")
    if outcome not in ("approved", "rejected"):
        raise CardRefused("outcome_invalid")
    with ledger.writing() as (st, append):
        p = st.card_proposals.get(proposal_id)
        if p is None:
            raise CardRefused("unknown_proposal")
        if p["decision"] is not None:
            if p["decision"] == outcome:
                return p
            raise CardRefused("already_decided")
        st = append({"type": "card_decided", "proposal_id": proposal_id, "outcome": outcome,
                     "approval_ref": approval_ref})
        return st.card_proposals[proposal_id]


def retire_card(ledger, *, card_id, is_owner: bool, approval_ref, reason) -> dict:
    if not is_owner:
        raise CardRefused("only_the_principal_retires")
    with ledger.writing() as (st, append):
        card = st.cards.get(card_id)
        if card is None or card["current"] is None:
            raise CardRefused("nothing_to_retire")
        st = append({"type": "card_retired", "card_id": card_id, "edition": card["current"],
                     "approval_ref": approval_ref, "reason": reason})
        return st.cards[card_id]


def receipts_journal(root, clock):
    return Journal(root, result_schema="minimoi.partnership.result/1", clock=clock, doc_dirs=("receipts",))


def assemble(ledger, receipts: Journal, *, seat, session_id, scope, assembly_version, mandatory=(), at) -> dict:
    """``mandatory``: [(card_id, edition)] the seat's charter requires. Returns {status, block, receipt}."""
    included, omitted, refused = [], [], []
    head, damaged = None, False
    try:
        st = ledger.read()
        head = st.head
    except (LedgerDamaged, OSError):
        st, damaged = None, True
    if st is not None:
        for card_id, card in sorted(st.cards.items()):
            if card["current"] is None:
                omitted.append({"card_id": card_id, "edition": None, "reason": "retired"})
                continue
            ed = card["editions"][card["current"]]
            if text_sha(ed["text"]) != ed["sha256"]:
                refused.append({"card_id": card_id, "edition": card["current"], "reason": "hash_mismatch"})
            elif ed["scope"] != scope:
                omitted.append({"card_id": card_id, "edition": card["current"], "reason": "other_scope"})
            elif "*" not in ed["applicability"] and seat not in ed["applicability"]:
                omitted.append({"card_id": card_id, "edition": card["current"], "reason": "not_applicable_to_seat"})
            else:
                included.append({"card_id": card_id, "edition": card["current"], "sha256": ed["sha256"],
                                 "text": ed["text"]})
    have = {(c["card_id"], c["edition"]) for c in included}
    missing = [{"card_id": c, "edition": e} for c, e in mandatory if (c, e) not in have]
    status = "refused_context" if (missing or (damaged and mandatory)) else "assembled"
    receipt = {"schema": "minimoi.partnership.load_receipt/1", "seat": seat, "session_id": session_id,
               "scope": scope, "assembly_version": assembly_version, "ledger_head": head,
               "ledger": "damaged" if damaged else "verified", "status": status,
               "included": [{k: c[k] for k in ("card_id", "edition", "sha256")} for c in included],
               "omitted": omitted, "refused": refused, "mandatory_missing": missing, "at": at,
               "note": "A receipt proves inclusion in the assembled context, not comprehension or obedience."}
    rid = "PR-" + digest(receipt)[7:31]
    def plan():
        if receipts.read(f"receipts/{rid}.json") is not None:
            raise Refused("id_taken")
        return Plan([Write(f"receipts/{rid}.json", dict(receipt, receipt_id=rid))], 1, "practice.load",
                    {"actor_seat": seat, "session_id": session_id, "environment": None, "data_scope": scope,
                     "targets": [f"receipts/{rid}.json"]})
    res = receipts.run(rid, digest(receipt), plan)
    if res["outcome"] != "accepted":
        status = "refused_context"
    block = "" if status != "assembled" or not included else "\n".join(
        [HEADER] + [f"{c['card_id']} (edition {c['edition']}): {c['text']}" for c in included])
    return {"status": status, "block": block, "receipt": dict(receipt, receipt_id=rid), "journal": res}
