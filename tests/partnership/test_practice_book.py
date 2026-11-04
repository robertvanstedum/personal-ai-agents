"""B3C: Practice Book on the lesson ledger code, in its own ledger instance, with context-load receipts (spec v1.1
L01-L06; acceptance F10). Revised after REVIEW_B3C_INDEPENDENT_2026-11-04. SYNTHETIC: seed templates are proposals
only; no private incident association or live adoption is made."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.mc_lessons.ledger import LessonLedger, text_sha
from services.partnership_tasks.practice import (CardRefused, assemble, decide_card, practice_ledger, propose_card,
                                                 receipts_journal, retire_card)
from tests.partnership.synth import NOW, bindings

SEEDS = json.loads((Path(__file__).parent / "fixtures" / "practice_seeds.json").read_text("utf-8"))["seeds"]
APPROVAL = {"store": "records", "id": "evt-syn-owner-approval", "edition": "1", "digest": None}
AT = "2026-11-05T09:00:00+00:00"
B = bindings()


@pytest.fixture
def book(tmp_path):
    return (practice_ledger(tmp_path, principal="principal", environment="dev"),
            receipts_journal(tmp_path / "receipts", clock=lambda: AT))


def propose(ledger, card_id, text, n=1, applicability=("*",), scope="personal", condenses=()):
    return propose_card(ledger, proposal_id=f"prop-{card_id}-{n}", card_id=card_id, text=text, scope=scope,
                        applicability=list(applicability), source_refs=[{"store": "records", "id": "evt-syn-x"}],
                        proposed_by="cos", condenses=condenses)


def approve(ledger, proposal, token="tok-principal", approval=APPROVAL):
    return decide_card(ledger, proposal_id=proposal["proposal_id"], outcome="approved", bindings=B, token=token,
                       now=NOW, session_id="sess-owner-1", approval_ref=approval)


def load(ledger, receipts, seat="mc", pins=(), scope="personal", **kw):
    return assemble(ledger, receipts, seat=seat, session_id="sess-syn-1", scope=scope, assembly_version="asm-1",
                    pins=pins, at=AT, **kw)


def test_s2_a_cards_live_in_their_own_ledger_never_the_live_lesson_ledger(tmp_path, book):
    ledger, _ = book
    lessons = LessonLedger(tmp_path / "lessons", principal="principal", environment="dev")
    assert ledger.dir != lessons.dir and "practice-book" in str(ledger.dir)
    with lessons.writing() as (st, append):
        append({"type": "preference_kept", "lesson_no": 1, "text": "Keep answers short.",
                "sha256": text_sha("Keep answers short."), "operation_id": "op-1", "original": "x",
                "source": {"kind": "turn"}})
    with pytest.raises(CardRefused, match="not_a_practice_ledger"):
        propose(lessons, "PB-x", SEEDS[0]["text"])


def test_seed_templates_are_proposals_and_two_collide_with_the_shared_authority_filter(book):
    ledger, _ = book
    refused = []
    for seed in SEEDS:
        try:
            propose(ledger, seed["card_id"], seed["text"])
        except CardRefused as exc:
            refused.append((seed["card_id"], exc.code))
    # Recorded for Robert/Codex: the spec's wording mentions spending and approvals, which the one shared filter
    # refuses; the filter stays strict (L04) and the wording is the decision to make.
    assert refused == [("PB-bounded-investigation", "authority"), ("PB-approval-fatigue", "authority")]
    assert len(ledger.read().card_proposals) == 5 and ledger.read().cards == {}


def test_s2_d_only_the_principals_binding_decides_and_the_decision_is_attributed(book):
    ledger, _ = book
    p = propose(ledger, "PB-return-window", SEEDS[0]["text"])
    with pytest.raises(CardRefused, match="only_the_principal_decides"):
        approve(ledger, p, token="tok-cos")
    with pytest.raises(CardRefused, match="only_the_principal_decides"):
        approve(ledger, p, token="tok-revoked")
    with pytest.raises(CardRefused, match="approval_ref_required"):
        approve(ledger, p, approval=None)
    approve(ledger, p)
    card = ledger.read().cards["PB-return-window"]
    assert card["editions"][1]["approved_by"] == "principal" and card["history"][0]["by"] == "principal"


def test_s2_b_an_older_edition_cannot_roll_a_card_back(book):
    ledger, _ = book
    p2 = propose(ledger, "PB-return-window", SEEDS[0]["text"], n=2)
    p3 = propose(ledger, "PB-return-window", "State the expected return so the principal can correct it early.", n=3)
    approve(ledger, p3)
    with pytest.raises(CardRefused, match="older_than_an_approved_edition"):
        approve(ledger, p2)
    assert ledger.read().cards["PB-return-window"]["current"] == 2       # p3 is edition 2 (p2 was edition 1)


def test_f10_versions_retirement_and_exact_receipts(book):
    ledger, receipts = book
    p1 = propose(ledger, "PB-return-window", SEEDS[0]["text"])
    approve(ledger, p1)
    first = load(ledger, receipts)
    assert first["status"] == "assembled" and first["receipt"]["included"] == [
        {"card_id": "PB-return-window", "edition": 1, "sha256": p1["sha256"]}]
    assert first["receipt"]["block_sha256"] == text_sha(first["block"]) and "not permissions" in first["block"]
    p2 = propose(ledger, "PB-return-window", "State the expected return so the principal can correct it early.", n=2)
    approve(ledger, p2)
    assert load(ledger, receipts)["receipt"]["included"][0]["edition"] == 2
    retire_card(ledger, card_id="PB-return-window", bindings=B, token="tok-principal", now=NOW, session_id="s",
                approval_ref=APPROVAL, reason="Superseded by charter.")
    third = load(ledger, receipts)
    assert third["receipt"]["omitted"] == [{"card_id": "PB-return-window", "edition": 2, "reason": "retired"}]
    assert [h["event"] for h in ledger.read().cards["PB-return-window"]["history"]] == ["approved", "approved",
                                                                                         "retired"]


def test_s2_c_pins_carry_the_hash_and_mismatches_fail_closed_with_a_reason(book):
    ledger, receipts = book
    p = propose(ledger, "PB-receipt-honesty", SEEDS[1]["text"], applicability=("mc",))
    approve(ledger, p)
    good = ("PB-receipt-honesty", 1, p["sha256"])
    assert load(ledger, receipts, pins=[good])["status"] == "assembled"
    wrong_hash = load(ledger, receipts, pins=[("PB-receipt-honesty", 1, "0" * 64)])
    assert wrong_hash["status"] == "refused_context" and wrong_hash["block"] == ""
    filtered = load(ledger, receipts, seat="cos", pins=[good])
    assert filtered["receipt"]["pins_missing"][0]["reason"] == "not_applicable_to_seat"


def test_l02_budget_and_condensing_bound_the_context(book):
    ledger, receipts = book
    for n, seed in enumerate(SEEDS[:4]):
        if seed["card_id"] in ("PB-bounded-investigation", "PB-approval-fatigue"):
            continue
        approve(ledger, propose(ledger, seed["card_id"], seed["text"], n=n))
    tight = load(ledger, receipts, max_cards=2)
    assert len(tight["receipt"]["included"]) == 2
    assert [o["reason"] for o in tight["receipt"]["omitted"]] == ["over_budget"] * 2
    merged = propose(ledger, "PB-conversation", "State the return window, never claim saved without a receipt, "
                                                "and ask clarifications plainly.",
                     condenses=("PB-return-window", "PB-receipt-honesty", "PB-natural-dialogue"))
    approve(ledger, merged)
    after = load(ledger, receipts)
    assert {c["card_id"] for c in after["receipt"]["included"]} == {"PB-conversation", "PB-disagreement"}
    hist = ledger.read().cards["PB-return-window"]["history"][-1]
    assert hist["event"] == "condensed_into" and hist["into"] == "PB-conversation"


def test_f10_a_damaged_ledger_says_unavailable_and_pins_fail_closed(book):
    ledger, receipts = book
    p = propose(ledger, "PB-disagreement", SEEDS[3]["text"])
    approve(ledger, p)
    ledger.path.write_bytes(ledger.path.read_bytes().replace(b"the decision required", b"no decision at all"))
    loose = load(ledger, receipts)
    assert loose["receipt"]["ledger"] == "damaged" and "unavailable" in loose["block"]
    pinned = load(ledger, receipts, pins=[("PB-disagreement", 1, p["sha256"])])
    assert pinned["status"] == "refused_context" and pinned["receipt"]["pins_missing"][0]["reason"] == "ledger_damaged"


def test_l04_permission_wording_and_bad_shapes_are_refused_and_retries_must_match(book):
    ledger, _ = book
    with pytest.raises(CardRefused, match="authority"):
        propose(ledger, "PB-bad", "You may deploy without asking when tests pass.")
    with pytest.raises(CardRefused, match="payload_invalid"):
        propose_card(ledger, proposal_id="prop-shape", card_id="PB-shape", text=SEEDS[0]["text"], scope="personal",
                     applicability="mc", source_refs=[], proposed_by="cos")
    propose(ledger, "PB-worker-results", SEEDS[5]["text"])
    with pytest.raises(CardRefused, match="proposal_id_reused"):
        propose(ledger, "PB-worker-results", SEEDS[5]["text"], scope="shared:build")


def test_l03_the_receipt_is_journalled_and_a_failed_journal_means_no_context(book):
    ledger, receipts = book
    approve(ledger, propose(ledger, "PB-natural-dialogue", SEEDS[2]["text"]))
    out = load(ledger, receipts)
    rid = out["receipt"]["receipt_id"]
    assert receipts.receipt(rid)["answer"] == "recorded"
    stored = receipts.view(lambda: receipts.read(f"receipts/{rid}.json"))
    assert stored["included"] == out["receipt"]["included"] and stored["status"] == "assembled"
    assert "not comprehension or obedience" in stored["note"]
    (receipts.root / "receipts" / f"{rid}.json").write_text('{"tampered": true}')
    fresh = receipts_journal(receipts.root, clock=lambda: AT)               # the next process verifies the store
    held = load(ledger, fresh, seat="cos")
    assert held["status"] == "refused_context" and held["block"] == "" and held["receipt"]["status"] == "refused_context"


@pytest.mark.parametrize("seed_event", [None, "proposal", "suggestion"])
def test_s2_1_no_lesson_family_ledger_ever_takes_a_card(tmp_path, seed_event):
    lessons = LessonLedger(tmp_path / "lessons", principal="principal", environment="dev")
    if seed_event == "proposal":
        with lessons.writing() as (st, append):
            append({"type": "proposed", "proposal_id": "p-1", "text": "Keep it short."})
    elif seed_event == "suggestion":
        with lessons.writing() as (st, append):
            append({"type": "suggestion_recorded", "suggestion_id": "s-1", "operation_id": "op-1", "text": "x"})
    with pytest.raises(CardRefused, match="not_a_practice_ledger"):
        propose(lessons, "PB-x", SEEDS[0]["text"])                        # even an empty live ledger


def test_s2_2_pinned_cards_take_the_budget_first(book):
    ledger, receipts = book
    approved = {}
    for n, seed in enumerate(SEEDS):
        if seed["card_id"] in ("PB-bounded-investigation", "PB-approval-fatigue"):
            continue
        p = propose(ledger, seed["card_id"], seed["text"], n=n)
        approve(ledger, p)
        approved[seed["card_id"]] = p
    pin = ("PB-worker-results", 1, approved["PB-worker-results"]["sha256"])   # sorts last
    out = load(ledger, receipts, pins=[pin], max_cards=1)
    assert out["status"] == "assembled" and out["receipt"]["included"][0]["card_id"] == "PB-worker-results"


def test_condensing_requires_existing_cards_in_the_same_scope(book):
    ledger, _ = book
    approve(ledger, propose(ledger, "PB-disagreement", SEEDS[3]["text"]))
    with pytest.raises(CardRefused, match="condenses_unknown_or_other_scope_card"):
        propose(ledger, "PB-merge", "Deliver disagreement with evidence and the decision required.",
                scope="shared:build", condenses=("PB-disagreement",))
    with pytest.raises(CardRefused, match="condenses_unknown_or_other_scope_card"):
        propose(ledger, "PB-merge2", "Deliver disagreement with evidence and the decision required.",
                condenses=("PB-missing",))
