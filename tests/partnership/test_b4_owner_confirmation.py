"""B4 connection design v0.2/v0.3, build step 2: Robert's portal confirmation is a recorded owner action that counts
only for the proposal it names, once; promotion must match the confirmed task terms; the owner links a meeting
source explicitly without changing intent."""
import json

import pytest

from core.canonical_json import digest
from tests.partnership.synth import Flow, cmd, ref, service

TERMS = {"lead": "mc", "holder": "mc", "outcome": "A three-item review checklist.",
         "done_when": "MC accepts a reviewed checklist.", "return_window": None}


@pytest.fixture
def rig(tmp_path):
    svc = service(tmp_path / "tasks")
    f = Flow(svc)
    f.source("SE-b4-0001", "Track a small test task for our codebase map.")
    proposed = {"outcome": "Track the codebase-map checklist task.", "scope": "personal",
                "recipients": ["principal"], "continuing": True, "task": TERMS}
    f.ok(cmd("interpretation.record", "interpretation", "IN-b4-0001",
             {"source_event_ids": ["SE-b4-0001"], "correction_event_ids": [], "proposed": proposed,
              "context_refs": [], "supersedes": None}), "tok-cos")
    return svc, f, proposed


def owner_action(f, sid, content, token="tok-principal"):
    return f.svc.submit(cmd("source_event.record", "source_event", sid,
                            {"source": {"store": "portal-owner-action", "id": "nonce-" + sid, "position": 1},
                             "content": json.dumps(content), "correction_of": None}), token)


def confirm_content(iid, proposed):
    return {"action": "confirm", "interpretation_id": iid, "proposal_digest": digest(proposed)}


def dispose(f, iid, sid, token="tok-principal"):
    return f.svc.submit(cmd("interpretation.dispose", "interpretation", iid,
                            {"disposition": "adopted",
                             "authority": {"kind": "owner_confirmation", "ref": ref("task_service", sid)}},
                            expected=f.rev("interpretation", iid)), token)


def promote(f, rid, tid, **over):
    terms = dict(TERMS, **over)
    return f.svc.submit(cmd("request.promote", "request", rid,
                            {"task_id": tid, "parent_ref": None, **terms}, expected=f.rev("request", rid)), "tok-principal")


def test_confirmed_proposal_promotes_exactly_as_confirmed(rig):
    svc, f, proposed = rig
    assert owner_action(f, "SE-b4-conf", confirm_content("IN-b4-0001", proposed))["outcome"] == "accepted"
    assert dispose(f, "IN-b4-0001", "SE-b4-conf")["outcome"] == "accepted"
    f.request("RQ-b4-0001", "SE-b4-0001", "Codebase-map checklist.", iid="IN-b4-0001", token="tok-principal")
    wrong = promote(f, "RQ-b4-0001", "TK-b4-0001", lead="cos")
    assert (wrong["outcome"], wrong["detail"]) == ("refused", "promote_not_as_confirmed")
    ok = promote(f, "RQ-b4-0001", "TK-b4-0001")
    assert ok["outcome"] == "accepted", ok
    assert svc.get("task", "TK-b4-0001", "tok-principal")["lead"] == "mc"


@pytest.mark.parametrize("content", [
    {"action": "confirm", "interpretation_id": "IN-b4-9999", "proposal_digest": "x"},     # another proposal
    {"action": "confirm", "interpretation_id": "IN-b4-0001", "proposal_digest": "sha256:" + "0" * 64},  # edited
    {"action": "decline", "interpretation_id": "IN-b4-0001"},
])
def test_a_confirmation_counts_only_for_what_it_names(rig, content):
    svc, f, proposed = rig
    owner_action(f, "SE-b4-conf", content)
    r = dispose(f, "IN-b4-0001", "SE-b4-conf")
    assert (r["outcome"], r["detail"]) == ("refused", "confirmation_not_for_this_proposal")


def test_a_confirmation_recorded_by_an_agent_or_reused_does_not_count(rig):
    svc, f, proposed = rig
    owner_action(f, "SE-b4-byco", confirm_content("IN-b4-0001", proposed), token="tok-cos")
    assert dispose(f, "IN-b4-0001", "SE-b4-byco")["detail"] in ("confirmation_not_for_this_proposal",
                                                                "owner_confirmation_needs_owner_session")
    owner_action(f, "SE-b4-conf", confirm_content("IN-b4-0001", proposed))
    assert dispose(f, "IN-b4-0001", "SE-b4-conf")["outcome"] == "accepted"
    # A second proposal citing the same confirmation is refused.
    p2 = dict(proposed, outcome="Something else.")
    f.ok(cmd("interpretation.record", "interpretation", "IN-b4-0002",
             {"source_event_ids": ["SE-b4-0001"], "correction_event_ids": [], "proposed": p2,
              "context_refs": [], "supersedes": None}), "tok-cos")
    assert dispose(f, "IN-b4-0002", "SE-b4-conf")["detail"] == "confirmation_not_for_this_proposal"


def test_owner_confirmed_promotion_needs_task_terms(tmp_path):
    svc = service(tmp_path / "tasks")
    f = Flow(svc)
    f.source("SE-b4-0002", "Do the thing.")
    f.interpret("IN-b4-0003", ["SE-b4-0002"], "The thing.")
    f.adopt("IN-b4-0003", token="tok-principal",
            authority={"kind": "owner_confirmation", "ref": ref("records", "evt-confirm-1")})
    f.request("RQ-b4-0002", "SE-b4-0002", "The thing.", iid="IN-b4-0003", token="tok-principal")
    r = promote(f, "RQ-b4-0002", "TK-b4-0002")
    assert (r["outcome"], r["detail"]) == ("refused", "promote_needs_proposed_task")


def test_owner_links_a_meeting_source_without_changing_intent(rig):
    svc, f, proposed = rig
    owner_action(f, "SE-b4-conf", confirm_content("IN-b4-0001", proposed))
    dispose(f, "IN-b4-0001", "SE-b4-conf")
    f.request("RQ-b4-0001", "SE-b4-0001", "Codebase-map checklist.", iid="IN-b4-0001", token="tok-principal")
    promote(f, "RQ-b4-0001", "TK-b4-0001")
    f.source("SE-b4-meet", "CoS and MC, confirm the plan.")
    before = svc.get("request", "RQ-b4-0001", "tok-principal")
    link = lambda token: svc.submit(cmd("request.link_source", "request", "RQ-b4-0001", {"source_event_id": "SE-b4-meet"},
                                        expected=f.rev("request", "RQ-b4-0001")), token)
    assert link("tok-cos")["detail"] == "only_the_owner_links_sources"
    assert link("tok-principal")["outcome"] == "accepted"
    after = svc.get("request", "RQ-b4-0001", "tok-principal")
    assert after["linked_sources"] == ["SE-b4-meet"]
    assert after["intent"] == before["intent"] and after["fence_generation"] == before["fence_generation"]
    assert link("tok-principal")["detail"] == "already_linked"
    # D02: the linked source cannot start or refine another request.
    r = svc.submit(cmd("request.record", "request", "RQ-b4-0009",
                       {"source_event_id": "SE-b4-meet", "intent_text": "x", "interpretation_ref": None,
                        "responder": "cos"}), "tok-cos")
    assert r["detail"] == "source_already_has_a_request"
