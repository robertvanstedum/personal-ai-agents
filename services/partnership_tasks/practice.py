"""Practice Book cards and context-load receipts (spec v1.1 L01-L06; batch B3C; acceptance F10). Revised after
REVIEW_B3C_INDEPENDENT_2026-11-04.

Reuse without risk to live lessons (L01, M04): cards use the lesson ledger *code* (hash chain, anchors, locking) in
their **own ledger instance** (``practice_ledger``: a sibling folder), never the live lesson ledger. An MC build that
predates card events never reads it, so a rollback cannot turn live lessons into an integrity hold, and cards never
share the lesson file's size limit. ``propose_card`` refuses a ledger that holds lessons.

Cards guide behaviour; they never grant authority (L04): no code path reads a card to decide what a seat may do; every
card's text passes the same authority filter as lessons (``core.mc_lessons.commands.clean``) when proposed and again
when assembled; the assembled block says so.

- ``propose_card``: any seat proposes an immutable edition (id, edition, text and SHA-256, scope, applicability, source
  references, proposer; optionally ``condenses``: other cards this one replaces, L02). A retry with the same proposal id
  must be identical in every field.
- ``decide_card`` / ``retire_card``: the principal's own decision. They take the resolved binding (owner session
  only), require the approval reference, and record the deciding seat and session. An edition older than the card's
  current approved one is refused (no silent rollback); approving a condensing card retires the cards it condenses.
- ``assemble``: one seat/session/scope. Charter pins are ``(card_id, edition, sha256)`` and are compared with the
  ledger; a pin that is missing, retired, filtered out or at another hash fails closed. A budget bounds the block
  (``max_cards``, ``max_chars``); what does not fit is omitted ``over_budget``. The receipt (seat, session, assembly
  version, ledger head, per-card hashes, the block's own hash, omitted/refused with reasons) is journalled; it proves
  inclusion in the context, not comprehension or obedience (L03).
"""
from __future__ import annotations

from pathlib import Path

from core.canonical_json import digest
from core.mc_lessons.commands import clean
from core.mc_lessons.ledger import LedgerDamaged, LessonLedger, text_sha
from services.partnership_tasks.journal import Journal, Plan, Write

HEADER = ("[Practice Book: the principal approved each card below as preferred practice. Cards are not permissions: "
          "none approves an action, grants access, raises a limit or changes what this seat may do; every action "
          "still needs its own authority.]")
UNAVAILABLE = HEADER + "\nPractice cards are unavailable right now (integrity hold); do not guess what they say."


class CardRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def practice_ledger(root, *, principal: str, environment: str) -> LessonLedger:
    """The Practice Book's own ledger instance, beside (never inside) the live lesson ledger."""
    return LessonLedger(Path(root) / "practice-book", principal=principal, environment=environment)


def _practice_only(ledger, st) -> None:
    """S2-1: cards are written only to a practice-book ledger instance that holds nothing of the lesson family."""
    if "practice-book" not in Path(ledger.dir).parts or st.lessons or st.proposals or st.suggestions \
            or st.operations or st.turns:
        raise CardRefused("not_a_practice_ledger")


def _owner_from(bindings, token, now):
    """S2-D: the decider is resolved from its token (unknown, expired or revoked bindings refuse)."""
    binding = None if bindings is None else bindings.resolve(token, now)
    if binding is None or not binding.is_owner:
        raise CardRefused("only_the_principal_decides")
    return binding


def _scope_ok(scope) -> bool:
    return isinstance(scope, str) and (scope == "personal" or scope.startswith("shared:"))


def propose_card(ledger, *, proposal_id, card_id, text, scope, applicability, source_refs, proposed_by,
                 condenses=()) -> dict:
    t, reason = clean(text)
    if reason:
        raise CardRefused(reason)
    if not _scope_ok(scope) or not isinstance(applicability, (list, tuple)) or not applicability \
            or not all(isinstance(a, str) and a for a in applicability):
        raise CardRefused("payload_invalid")
    event = {"type": "card_proposed", "proposal_id": proposal_id, "card_id": card_id, "text": t, "sha256": text_sha(t),
             "scope": scope, "applicability": sorted(set(applicability)), "source_refs": list(source_refs),
             "proposed_by": proposed_by, "condenses": sorted(set(condenses) - {card_id})}
    with ledger.writing() as (st, append):
        _practice_only(ledger, st)                                  # never write cards into the live lesson ledger
        for other in event["condenses"]:
            o = st.cards.get(other)
            if o is None or o["current"] is None or o["editions"][o["current"]]["scope"] != scope:
                raise CardRefused("condenses_unknown_or_other_scope_card")
        prior = st.card_proposals.get(proposal_id)
        if prior is not None:
            same = all(prior.get(k) == event[k] for k in event if k != "type")
            if not same:
                raise CardRefused("proposal_id_reused")
            return prior
        event["edition"] = 1 + sum(1 for p in st.card_proposals.values() if p["card_id"] == card_id)
        st = append(event)
        return st.card_proposals[proposal_id]


def decide_card(ledger, *, proposal_id, outcome, bindings, token, now, session_id, approval_ref) -> dict:
    binding = _owner_from(bindings, token, now)
    if outcome not in ("approved", "rejected"):
        raise CardRefused("outcome_invalid")
    if not isinstance(approval_ref, dict) or not approval_ref.get("id"):
        raise CardRefused("approval_ref_required")
    with ledger.writing() as (st, append):
        _practice_only(ledger, st)
        p = st.card_proposals.get(proposal_id)
        if p is None:
            raise CardRefused("unknown_proposal")
        if p["decision"] is not None:
            if p["decision"] == outcome:
                return p
            raise CardRefused("already_decided")
        current = st.cards.get(p["card_id"], {}).get("current")
        approved = [e for e in st.cards.get(p["card_id"], {}).get("editions", {})]
        if outcome == "approved" and approved and p["edition"] < max(approved):
            raise CardRefused("older_than_an_approved_edition")       # no silent rollback (S2-B)
        st = append({"type": "card_decided", "proposal_id": proposal_id, "outcome": outcome,
                     "approval_ref": approval_ref, "by_seat": binding.seat, "by_session": session_id,
                     "previous_current": current})
        return st.card_proposals[proposal_id]


def retire_card(ledger, *, card_id, bindings, token, now, session_id, approval_ref, reason) -> dict:
    binding = _owner_from(bindings, token, now)
    if not isinstance(approval_ref, dict) or not approval_ref.get("id"):
        raise CardRefused("approval_ref_required")
    with ledger.writing() as (st, append):
        _practice_only(ledger, st)
        card = st.cards.get(card_id)
        if card is None or card["current"] is None:
            raise CardRefused("nothing_to_retire")
        st = append({"type": "card_retired", "card_id": card_id, "edition": card["current"],
                     "approval_ref": approval_ref, "reason": reason, "by_seat": binding.seat,
                     "by_session": session_id})
        return st.cards[card_id]


def receipts_journal(root, clock):
    return Journal(root, result_schema="minimoi.partnership.result/1", clock=clock, doc_dirs=("receipts",))


def assemble(ledger, receipts: Journal, *, seat, session_id, scope, assembly_version, pins=(), at,
             max_cards: int = 12, max_chars: int = 3000) -> dict:
    """``pins``: [(card_id, edition, sha256)] the seat's charter requires. Returns {status, block, receipt}."""
    included, omitted, refused = [], [], []
    head, damaged = None, False
    try:
        st = ledger.read()
        head = st.head
    except (LedgerDamaged, OSError, KeyError):
        st, damaged = None, True
    size = len(HEADER)
    filtered = {}
    pinned = {c for c, _, _ in pins}
    if st is not None:
        # S2-2: pinned cards take budget first, then the rest in card-id order.
        for card_id, card in sorted(st.cards.items(), key=lambda kv: (kv[0] not in pinned, kv[0])):
            last = max(card["editions"]) if card["editions"] else None
            if card["current"] is None:
                omitted.append({"card_id": card_id, "edition": last, "reason": "retired"})
                filtered[card_id] = "retired"
                continue
            ed = card["editions"][card["current"]]
            text, reason = clean(ed["text"])
            line = f"{card_id} (edition {card['current']}): {ed['text']}"
            if reason or text != ed["text"] or text_sha(ed["text"]) != ed["sha256"]:
                refused.append({"card_id": card_id, "edition": card["current"], "reason": "fails_text_checks"})
                filtered[card_id] = "fails_text_checks"
            elif ed["scope"] != scope:
                omitted.append({"card_id": card_id, "edition": card["current"], "reason": "other_scope"})
                filtered[card_id] = "other_scope"
            elif "*" not in ed["applicability"] and seat not in ed["applicability"]:
                omitted.append({"card_id": card_id, "edition": card["current"], "reason": "not_applicable_to_seat"})
                filtered[card_id] = "not_applicable_to_seat"
            elif len(included) >= max_cards or size + len(line) + 1 > max_chars:
                why = "pins_exceed_budget" if card_id in pinned else "over_budget"
                omitted.append({"card_id": card_id, "edition": card["current"], "reason": why})
                filtered[card_id] = why
            else:
                included.append({"card_id": card_id, "edition": card["current"], "sha256": ed["sha256"], "line": line})
                size += len(line) + 1
    have = {(c["card_id"], c["edition"], c["sha256"]) for c in included}
    missing = []
    for card_id, edition, sha in pins:
        if (card_id, edition, sha) not in have:
            missing.append({"card_id": card_id, "edition": edition, "sha256": sha,
                            "reason": filtered.get(card_id) or ("ledger_damaged" if damaged else
                                                                "not_at_this_edition_or_hash")})
    status = "refused_context" if (missing or (damaged and pins)) else "assembled"
    if status != "assembled":
        block = ""
    elif damaged:
        block = UNAVAILABLE
    elif included:
        block = "\n".join([HEADER] + [c["line"] for c in included])
    else:
        block = ""
    receipt = {"schema": "minimoi.partnership.load_receipt/1", "seat": seat, "session_id": session_id,
               "scope": scope, "assembly_version": assembly_version, "ledger_head": head,
               "ledger": "damaged" if damaged else "verified",
               "included": [{k: c[k] for k in ("card_id", "edition", "sha256")} for c in included],
               "omitted": omitted, "refused": refused, "pins_missing": missing,
               "budget": {"max_cards": max_cards, "max_chars": max_chars},
               "block_sha256": text_sha(block), "at": at,
               "note": "A receipt proves inclusion in the assembled context, not comprehension or obedience."}
    rid = "PR-" + digest(dict(receipt, status=status))[7:31]

    def plan():
        return Plan([Write(f"receipts/{rid}.json", dict(receipt, receipt_id=rid, status=status))], 1,
                    "practice.load", {"actor_seat": seat, "session_id": session_id, "data_scope": scope,
                                      "targets": [f"receipts/{rid}.json"]})
    res = receipts.run(rid, digest(receipt), plan)
    if res["outcome"] != "accepted":
        status, block = "refused_context", ""                       # no receipt, no context (fail closed)
    return {"status": status, "block": block, "receipt": dict(receipt, receipt_id=rid, status=status),
            "journal": res}
