"""B3C: Practice Book on the existing lesson ledger, with context-load receipts (spec v1.1 L01-L06; acceptance F10).
SYNTHETIC: seed templates are proposals only; no private incident association or live adoption is made."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.mc_lessons.ledger import LessonLedger
from services.partnership_tasks.practice import (CardRefused, assemble, decide_card, propose_card, receipts_journal,
                                                 retire_card)

SEEDS = json.loads((Path(__file__).parent / "fixtures" / "practice_seeds.json").read_text("utf-8"))["seeds"]
APPROVAL = {"store": "records", "id": "evt-syn-owner-approval", "edition": "1", "digest": None}
AT = "2026-11-05T09:00:00+00:00"


@pytest.fixture
def book(tmp_path):
    ledger = LessonLedger(tmp_path / "lessons", principal="principal", environment="dev")
    receipts = receipts_journal(tmp_path / "receipts", clock=lambda: AT)
    return ledger, receipts


def propose(ledger, card_id, text, n=1, applicability=("*",), scope="personal"):
    return propose_card(ledger, proposal_id=f"prop-{card_id}-{n}", card_id=card_id, text=text, scope=scope,
                        applicability=list(applicability), source_refs=[{"store": "records", "id": "evt-incident-syn"}],
                        proposed_by="cos")


def load(ledger, receipts, seat="mc", mandatory=(), scope="personal"):
    return assemble(ledger, receipts, seat=seat, session_id="sess-syn-1", scope=scope, assembly_version="asm-1",
                    mandatory=mandatory, at=AT)


def test_seed_templates_are_proposals_and_two_collide_with_the_existing_authority_filter(book):
    ledger, _ = book
    refused = []
    for seed in SEEDS:
        try:
            propose(ledger, seed["card_id"], seed["text"])
        except CardRefused as exc:
            refused.append((seed["card_id"], exc.code))
    # Recorded for Codex: the spec's wording mentions spending and approvals, which the lesson filter refuses.
    assert refused == [("PB-bounded-investigation", "authority"), ("PB-approval-fatigue", "authority")]
    st = ledger.read()
    assert len(st.card_proposals) == 5 and st.cards == {}                    # proposed, nothing adopted


def test_f10_proposed_approved_superseded_retired_with_exact_load_receipts(book):
    ledger, receipts = book
    p1 = propose(ledger, "PB-return-window", SEEDS[0]["text"])
    with pytest.raises(CardRefused, match="only_the_principal_decides"):
        decide_card(ledger, proposal_id=p1["proposal_id"], outcome="approved", is_owner=False, approval_ref=APPROVAL)
    decide_card(ledger, proposal_id=p1["proposal_id"], outcome="approved", is_owner=True, approval_ref=APPROVAL)
    first = load(ledger, receipts)
    assert first["status"] == "assembled"
    assert first["receipt"]["included"] == [{"card_id": "PB-return-window", "edition": 1,
                                             "sha256": p1["sha256"]}]
    assert "not permissions" in first["block"] and SEEDS[0]["text"] in first["block"]
    p2 = propose(ledger, "PB-return-window", "State the expected return so the principal can correct it early.", n=2)
    decide_card(ledger, proposal_id=p2["proposal_id"], outcome="approved", is_owner=True, approval_ref=APPROVAL)
    second = load(ledger, receipts)
    assert second["receipt"]["included"][0]["edition"] == 2                  # a new edition needs a new receipt
    assert first["receipt"]["receipt_id"] != second["receipt"]["receipt_id"]
    retire_card(ledger, card_id="PB-return-window", is_owner=True, approval_ref=APPROVAL, reason="Superseded by charter.")
    third = load(ledger, receipts)
    assert third["receipt"]["included"] == [] and third["receipt"]["omitted"][0]["reason"] == "retired"
    history = ledger.read().cards["PB-return-window"]["history"]
    assert [h["event"] for h in history] == ["approved", "approved", "retired"]   # nothing erased


def test_f10_a_mandatory_card_at_another_edition_fails_closed(book):
    ledger, receipts = book
    p = propose(ledger, "PB-receipt-honesty", SEEDS[1]["text"])
    decide_card(ledger, proposal_id=p["proposal_id"], outcome="approved", is_owner=True, approval_ref=APPROVAL)
    out = load(ledger, receipts, mandatory=[("PB-receipt-honesty", 2)])
    assert out["status"] == "refused_context" and out["block"] == ""
    assert out["receipt"]["mandatory_missing"] == [{"card_id": "PB-receipt-honesty", "edition": 2}]
    assert load(ledger, receipts, mandatory=[("PB-receipt-honesty", 1)])["status"] == "assembled"


def test_f10_applicability_and_scope_are_checked_and_omissions_are_receipted(book):
    ledger, receipts = book
    p = propose(ledger, "PB-worker-results", SEEDS[5]["text"], applicability=("mc",))
    decide_card(ledger, proposal_id=p["proposal_id"], outcome="approved", is_owner=True, approval_ref=APPROVAL)
    assert load(ledger, receipts, seat="mc")["receipt"]["included"][0]["card_id"] == "PB-worker-results"
    cos = load(ledger, receipts, seat="cos")
    assert cos["receipt"]["omitted"] == [{"card_id": "PB-worker-results", "edition": 1,
                                          "reason": "not_applicable_to_seat"}]
    other = load(ledger, receipts, seat="mc", scope="shared:build")
    assert other["receipt"]["omitted"][0]["reason"] == "other_scope"


def test_f10_a_tampered_ledger_claims_nothing_loaded(book, tmp_path):
    ledger, receipts = book
    p = propose(ledger, "PB-disagreement", SEEDS[3]["text"])
    decide_card(ledger, proposal_id=p["proposal_id"], outcome="approved", is_owner=True, approval_ref=APPROVAL)
    raw = ledger.path.read_bytes().replace(b"evidence and the decision", b"evidence and no decision")
    ledger.path.write_bytes(raw)
    out = load(ledger, receipts, mandatory=[("PB-disagreement", 1)])
    assert out["status"] == "refused_context" and out["receipt"]["ledger"] == "damaged"
    assert out["receipt"]["included"] == []


def test_l04_cards_grant_nothing_and_permission_wording_is_refused(book):
    ledger, _ = book
    with pytest.raises(CardRefused, match="authority"):
        propose(ledger, "PB-bad", "You may deploy without asking when tests pass.")
    st = ledger.read()
    assert st.active() == []                                                 # cards never become lessons either


def test_l03_the_receipt_is_journalled_evidence(book):
    ledger, receipts = book
    p = propose(ledger, "PB-natural-dialogue", SEEDS[2]["text"])
    decide_card(ledger, proposal_id=p["proposal_id"], outcome="approved", is_owner=True, approval_ref=APPROVAL)
    out = load(ledger, receipts)
    rid = out["receipt"]["receipt_id"]
    assert receipts.receipt(rid)["answer"] == "recorded"
    stored = receipts.view(lambda: receipts.read(f"receipts/{rid}.json"))
    assert stored["included"] == out["receipt"]["included"] and stored["seat"] == "mc"
    assert "not comprehension or obedience" in stored["note"]
