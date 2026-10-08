"""Request, receipt, claim, result (v0.6 section 7; v0.7 Unit 3, G9): what the record allows next."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from conftest import WORKSHOP, envelope, new_id, progress
from core.workshop_journal import workflow
from core.workshop_journal.journal import Journal
from fakes import Teammate


@pytest.fixture
def team(root):
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    return j, Teammate("claude-code", j), Teammate("codex", j), Teammate("grok-cli", j)


def refused(result, status="policy_refused", reason=None, code=None):
    assert (result.status, result.committed) == (status, False), result.to_json()
    if reason:
        assert result.reason == reason, result.to_json()
    assert result.exit_code == (code if code is not None else (3 if status == "claim_conflict" else 2))


# ── request / receipt / result ─────────────────────────────────────────────────────────────────────────────────────
def test_a_request_needs_a_recipient_and_not_the_sender_or_the_host(team):
    j, code, codex, _ = team
    none = j.append(envelope(recipients=[]))
    assert none.committed is False and none.exit_code == 2                                                 # refused by the schema or the rules
    refused(j.append(envelope(recipients=["claude-code"])), reason="request_recipient_not_allowed")
    refused(j.append(envelope(recipients=["host"])), reason="request_recipient_not_allowed")
    assert code.request(["codex"], "review", "ok").committed


def test_a_receipt_names_a_real_request_and_comes_from_its_recipient(team):
    j, code, codex, grok = team
    req = code.request(["codex"], "review", "please review")
    refused(codex.receive(new_id(), ok=False), reason="unknown_request")
    refused(grok.receive(req.event_id, ok=False), reason="recipient_not_on_the_request")                 # grok is not on the request
    refused(j.append({"actor": "grok-cli", "kind": "receipt", "item": "topic:scenario", "topic": "scenario", "text": "x",
                      "payload": {"request_id": req.event_id, "recipient": "codex"}}), reason="a_receipt_comes_from_its_recipient")
    assert codex.receive(req.event_id).committed
    again = codex.receive(req.event_id)                                                                    # a second receipt is harmless
    assert again.committed


def test_a_result_comes_from_the_recipient_and_a_receipt_is_not_a_result(team):
    j, code, codex, grok = team
    req = code.request(["codex"], "review", "please review")
    codex.receive(req.event_id)
    flow = workflow.derive(j.read().events)
    assert workflow.recipient_status(flow.requests[req.event_id], "codex") == "received"
    assert workflow.request_state(flow.requests[req.event_id]) == "open"
    forged = j.append({"actor": "grok-cli", "kind": "result", "item": "topic:scenario", "topic": "scenario", "text": "x",
                       "payload": {"request_id": req.event_id, "recipient": "codex", "outcome": "completed", "limitations": []}})
    refused(forged, reason="a_result_comes_from_its_recipient")
    assert codex.result(req.event_id).committed
    flow = workflow.derive(j.read().events)
    assert workflow.recipient_status(flow.requests[req.event_id], "codex") == "returned" and workflow.request_state(flow.requests[req.event_id]) == "closed"


def test_a_request_to_two_recipients_stays_open_until_both_return(team):
    j, code, codex, grok = team
    req = code.request(["codex", "grok-cli"], "review", "both please")
    codex.result(req.event_id, "declined", ["not my area"])
    flow = workflow.derive(j.read().events)
    assert workflow.request_state(flow.requests[req.event_id]) == "open"
    grok.result(req.event_id, "completed")
    assert workflow.request_state(workflow.derive(j.read().events).requests[req.event_id]) == "closed"


# ── claims: compare and append only ────────────────────────────────────────────────────────────────────────────────
def test_G9_the_first_claim_is_generation_one_and_a_second_claimant_is_refused_while_it_stands(team):
    j, code, codex, grok = team
    req = code.request(["codex", "grok-cli"], "build", "build it", resource="checkout-a")
    first = codex.claim(req.event_id, "checkout-a", 1)
    refused(grok.claim(req.event_id, "checkout-a", 1, ok=False), "claim_conflict", "not_the_next_generation")
    refused(grok.claim(req.event_id, "checkout-a", 2, ok=False), "claim_conflict", "resource_has_an_effective_claimant")
    other = grok.claim(req.event_id, "checkout-b", 1)                                                       # another resource is free
    assert first.committed and other.committed


def test_G9_a_claim_must_come_from_a_recipient_naming_itself(team):
    j, code, codex, grok = team
    req = code.request(["codex"], "build", "build it")
    refused(grok.claim(req.event_id, "checkout-a", 1, ok=False), reason="claimant_not_on_the_request")
    forged = j.append({"actor": "codex", "kind": "claim", "item": "topic:scenario", "topic": "scenario", "text": "x",
                       "payload": {"request_id": req.event_id, "resource": "checkout-a", "generation": 1, "claimant": "grok-cli"}})
    refused(forged, reason="a_claim_is_made_by_its_claimant")


def test_G9_only_a_release_that_says_stopped_frees_the_resource(team):
    j, code, codex, grok = team
    req = code.request(["codex", "grok-cli"], "build", "build it")
    claim = codex.claim(req.event_id, "checkout-a", 1)
    codex.release(claim.event_id, 1, stopped=False, reason="going quiet, may still be running")
    refused(grok.claim(req.event_id, "checkout-a", 2, ok=False), "claim_conflict", "resource_has_an_effective_claimant")
    assert workflow.derive(j.read().events).claims[claim.event_id].active
    codex.release(claim.event_id, 1, stopped=True)
    refused(codex.release(claim.event_id, 1, stopped=True, ok=False), reason="claim_already_stopped")
    assert grok.claim(req.event_id, "checkout-a", 2).committed


def test_R03_a_lease_that_has_run_out_does_not_authorize_a_second_writer(team):
    j, code, codex, grok = team
    req = code.request(["codex", "grok-cli"], "build", "build it")
    expired = "2020-01-01T00:00:00Z"
    claim = j.append({
        "actor": "codex", "kind": "claim", "item": "topic:scenario", "topic": "scenario", "text": "claims",
        "payload": {"request_id": req.event_id, "resource": "checkout-a", "generation": 1, "claimant": "codex", "lease_until": expired}})
    assert claim.committed
    refused(grok.claim(req.event_id, "checkout-a", 2, ok=False), "claim_conflict", "resource_has_an_effective_claimant")


def test_G9_a_release_needs_the_right_generation_and_the_claimant_or_the_owner(team):
    j, code, codex, grok = team
    req = code.request(["codex", "grok-cli"], "build", "build it")
    claim = codex.claim(req.event_id, "checkout-a", 1)
    refused(codex.release(claim.event_id, 2, True, ok=False), "claim_conflict", "not_this_claims_generation")
    refused(grok.release(claim.event_id, 1, True, ok=False), reason="only_the_claimant_or_the_owner_releases")
    refused(codex.release(new_id(), 1, True, ok=False), reason="unknown_claim")
    owner = j.append({"actor": "robert", "kind": "release", "item": "topic:scenario", "topic": "scenario", "text": "owner stops it",
                      "payload": {"claim_id": claim.event_id, "generation": 1, "stopped": True, "reason": "owner stopped the work"}})
    assert owner.committed and not workflow.derive(j.read().events).claims[claim.event_id].active


def test_started_and_progress_citing_a_claim_need_it_to_be_the_actors_and_still_effective(team):
    j, code, codex, grok = team
    req = code.request(["codex", "grok-cli"], "build", "build it")
    claim = codex.claim(req.event_id, "checkout-a", 1)
    assert codex.progress("on it", claim_id=claim.event_id, request_id=req.event_id).committed
    refused(j.append({"actor": "grok-cli", "kind": "progress", "item": "topic:scenario", "text": "x",
                      "payload": {"action": "w", "claim_id": claim.event_id}}), reason="not_the_claimant")
    codex.release(claim.event_id, 1, True)
    refused(j.append({"actor": "codex", "kind": "progress", "item": "topic:scenario", "text": "x",
                      "payload": {"action": "w", "claim_id": claim.event_id}}), reason="claim_already_stopped")
    refused(j.append({"actor": "codex", "kind": "progress", "item": "topic:scenario", "text": "x",
                      "payload": {"action": "w", "request_id": new_id()}}), reason="unknown_request")


def test_a_result_citing_a_claim_must_cite_a_claim_on_the_same_request(team):
    j, code, codex, grok = team
    r1, r2 = code.request(["codex"], "build", "one"), code.request(["codex"], "build", "two")
    claim = codex.claim(r1.event_id, "checkout-a", 1)
    refused(codex.result(r2.event_id, claim_id=claim.event_id, ok=False), reason="unknown_claim")
    assert codex.result(r1.event_id, claim_id=claim.event_id).committed


# ── idempotence and the dry run ────────────────────────────────────────────────────────────────────────────────────
def test_a_retried_receipt_with_the_same_id_is_a_duplicate_not_a_second_receipt(team):
    j, code, codex, _ = team
    req = code.request(["codex"], "review", "x")
    eid = new_id()
    assert codex.receive(req.event_id, event_id=eid).status == "committed"
    assert codex.receive(req.event_id, event_id=eid).status == "duplicate"


def test_preflight_says_what_would_happen_and_writes_nothing(root):
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    ok = j.preflight(envelope())
    assert (ok.status, ok.seq, ok.committed) == ("would_commit", 1, False)
    import os
    assert os.listdir(root) == []                                                                        # not even the folder
    assert j.append(envelope(event_id=ok.event_id)).seq == 1
    bad = j.preflight({"actor": "codex", "kind": "receipt", "item": "topic:x", "text": "x",
                       "payload": {"request_id": new_id(), "recipient": "codex"}})
    assert (bad.status, bad.reason) == ("policy_refused", "unknown_request")
    again = j.preflight(envelope(event_id=ok.event_id))
    assert again.status == "duplicate" and again.seq == 1
    assert j.preflight(envelope(event_id=ok.event_id, text="changed")).exit_code == 3
    assert len(j.read().events) == 1


def test_rules_can_be_switched_off_for_the_raw_engine(root):
    raw = Journal(root, WORKSHOP, lock_timeout=0.3, rules=None)
    assert raw.append({"actor": "codex", "kind": "receipt", "item": "topic:x", "text": "x",
                       "payload": {"request_id": new_id(), "recipient": "codex"}}).committed


# ── decisions: an agent proposes; approval cites owner control ────────────────────────────────────────────────────
def test_an_approval_rejection_or_verification_must_cite_owner_control_or_a_mandate(team):
    j, code, codex, _ = team
    for kind in ("approved-direct", "approved-under-mandate", "rejected", "verified", "superseded"):
        refused(j.append({"actor": "codex", "kind": "decision", "item": "topic:scenario", "text": "x",
                          "payload": {"record_event_kind": kind, "resolves": [], "reason": "r"}}), reason="approval_needs_owner_control")
    assert code.propose("a proposal needs no authority").committed


def test_only_the_author_can_withdraw_a_proposal(team):
    j, code, codex, _ = team
    mine = code.propose("my idea")

    def withdraw(actor, ids):
        return j.append({"actor": actor, "kind": "decision", "item": "topic:scenario", "text": "withdrawn",
                         "payload": {"record_event_kind": "withdrawn", "resolves": ids, "reason": "changed my mind"}})
    refused(withdraw("codex", [mine.event_id]), reason="withdraw_own_proposal_only")
    refused(withdraw("claude-code", []), reason="withdraw_own_proposal_only")
    refused(withdraw("claude-code", [new_id()]), reason="withdraw_own_proposal_only")
    assert withdraw("claude-code", [mine.event_id]).committed
