"""Negative tests for the B1C/B1D independent review (REVIEW_B1CD_INDEPENDENT_2026-11-04). SYNTHETIC."""
from __future__ import annotations

import pytest

from core import durable_files as fsio
from services.partnership_tasks.service import StoreUnavailable
from tests.partnership.synth import Flow, cmd, ref, service


@pytest.fixture
def svc(tmp_path):
    return service(tmp_path)


@pytest.fixture
def flow(svc):
    return Flow(svc)


def task(svc, tid="TK-syn-0001"):
    return svc.get("task", tid, "tok-principal")


def correction(svc, flow, new_iid, *, adopt, cid_suffix="1"):
    flow.source("SE-syn-0009", "Change it.", correction_of="SE-syn-0001")
    flow.interpret(new_iid, ["SE-syn-0001"], "Changed outcome.", corrections=["SE-syn-0009"])
    if adopt:
        flow.adopt(new_iid)
    req = svc.get("request", "RQ-syn-0001", "tok-cos")
    return svc.submit(cmd("correction.apply", "request", "RQ-syn-0001",
                          {"correction_event_id": "SE-syn-0009", "changes": ["outcome"], "interpretation_ref": new_iid,
                           "intent_text": None}, expected=req["revision"]), "tok-cos")


def test_s1a_a_promoted_task_cannot_move_onto_an_unadopted_interpretation(svc, flow):
    flow.promoted_task()
    res = correction(svc, flow, "IN-syn-0009", adopt=False)
    assert (res["code"], res["detail"]) == ("not_authorized", "interpretation_not_adopted")
    assert task(svc)["interpretation_ref"] == "IN-syn-0001"


def test_s1a_superseding_the_reading_fences_the_task_and_blocks_resume_and_dispatch(svc, flow):
    tid = flow.promoted_task()
    old = task(svc)
    flow.source("SE-syn-0009", "Change it.", correction_of="SE-syn-0001")
    flow.interpret("IN-syn-0009", ["SE-syn-0001"], "Changed.", corrections=["SE-syn-0009"], supersedes="IN-syn-0001")
    now = task(svc)
    assert now["fence_generation"] == old["fence_generation"] + 1 and now["state"] == "waiting"
    assert svc.dispatch_check(tid, old["fence_generation"], "IN-syn-0001", "tok-mc")["code"] == "fenced"
    resume = flow.transition(tid, "working", evidence=[ref("harness", "a-1")])
    assert resume["detail"] == "interpretation_not_adopted"                  # still points at the superseded one


def test_s1a_dispatch_refuses_when_a_visible_correction_is_unreconciled(svc, flow):
    tid = flow.promoted_task()
    t = task(svc)
    flow.source("SE-syn-0009", "Wait, not that.", correction_of="SE-syn-0001")
    out = svc.dispatch_check(tid, t["fence_generation"], t["interpretation_ref"], "tok-mc")
    assert (out["dispatch"], out["detail"]) == ("refused", "unreconciled_correction")


def test_s1b_a_seat_cannot_rewrite_a_record_in_a_scope_it_may_only_read(svc, flow):
    flow.source("SE-syn-0001", "Personal ask.")
    flow.interpret("IN-syn-0001", ["SE-syn-0001"], "Personal reading.")
    flow.source("SE-syn-0002", "Build note.", token="tok-reader", scope="shared:build")
    res = svc.submit(cmd("interpretation.record", "interpretation", "IN-syn-0002",
                         {"source_event_ids": ["SE-syn-0002"], "correction_event_ids": [],
                          "proposed": {"outcome": "x", "scope": "shared:build", "recipients": [], "continuing": False},
                          "context_refs": [], "supersedes": "IN-syn-0001"}, scope="shared:build"), "tok-reader")
    assert res["code"] == "not_found"
    assert svc.get("interpretation", "IN-syn-0001", "tok-principal")["superseded_by"] is None


def test_s2_1_refine_cannot_touch_a_promoted_request_or_borrow_another_requests_interpretation(svc, flow):
    flow.promoted_task()
    flow.source("SE-syn-0003", "Only the summary.")
    req = svc.get("request", "RQ-syn-0001", "tok-cos")
    refine = cmd("request.refine", "request", "RQ-syn-0001",
                 {"source_event_id": "SE-syn-0003", "intent_text": "Summary.", "interpretation_ref": None},
                 expected=req["revision"])
    assert svc.submit(refine, "tok-cos")["detail"] == "promoted"
    # Another request's adopted, granted reading cannot be laundered into this one.
    flow.source("SE-syn-0004", "Unrelated ask.")
    flow.request("RQ-syn-0004", "SE-syn-0004", "Unrelated.")
    stolen = cmd("request.refine", "request", "RQ-syn-0004",
                 {"source_event_id": "SE-syn-0004", "intent_text": "x", "interpretation_ref": "IN-syn-0001"},
                 expected=1)
    assert svc.submit(stolen, "tok-cos")["detail"] == "interpretation_not_of_this_request"


def test_s2_2_accept_refused_while_waiting_for_authorization(svc, flow):
    tid = flow.promoted_task()
    flow.source("SE-syn-0009", "Change it.", correction_of="SE-syn-0001")
    flow.interpret("IN-syn-0009", ["SE-syn-0001"], "Changed.", corrections=["SE-syn-0009"], supersedes="IN-syn-0001")
    res = flow.accept(tid, "sha256:" + "a" * 64)
    assert res["detail"] == "waiting_for_authorization"


def test_s2_3_corrections_do_not_rewrite_ended_requests_or_terminal_tasks(svc, flow):
    flow.source("SE-syn-0001", "Ask.")
    flow.request("RQ-syn-0001", "SE-syn-0001", "Ask.")
    flow.ok(cmd("request.set_state", "request", "RQ-syn-0001", {"state": "withdrawn", "reason": "Not needed."},
                expected=1), "tok-cos")
    flow.source("SE-syn-0002", "Cancel.", correction_of="SE-syn-0001")
    res = svc.submit(cmd("correction.apply", "request", "RQ-syn-0001",
                         {"correction_event_id": "SE-syn-0002", "changes": ["cancel"], "interpretation_ref": None,
                          "intent_text": None}, expected=2), "tok-cos")
    assert res["detail"] == "withdrawn"


def test_s2_3_cancel_of_an_answered_request_is_refused(svc, flow):
    flow.source("SE-syn-0001", "Ask.")
    flow.request("RQ-syn-0001", "SE-syn-0001", "Ask.")
    flow.ok(cmd("request.set_state", "request", "RQ-syn-0001", {"state": "active", "reason": None}, expected=1), "tok-cos")
    flow.ok(cmd("request.answer", "request", "RQ-syn-0001",
                {"result_refs": [], "coverage": {"sources": [], "gaps": ["none found"]}, "fence_generation": 0},
                expected=2), "tok-cos")
    flow.source("SE-syn-0002", "Cancel.", correction_of="SE-syn-0001")
    res = svc.submit(cmd("correction.apply", "request", "RQ-syn-0001",
                         {"correction_event_id": "SE-syn-0002", "changes": ["cancel"], "interpretation_ref": None,
                          "intent_text": None}, expected=3), "tok-cos")
    assert res["detail"] == "answered_nothing_to_cancel"


def test_s2_4_a_correction_of_a_correction_is_still_unreconciled(svc, flow):
    flow.source("SE-syn-0001", "Ask A.")
    flow.source("SE-syn-0002", "No, B.", correction_of="SE-syn-0001")
    flow.source("SE-syn-0003", "Actually C.", correction_of="SE-syn-0002")
    partial = cmd("interpretation.record", "interpretation", "IN-syn-0001",
                  {"source_event_ids": ["SE-syn-0001"], "correction_event_ids": ["SE-syn-0002"],
                   "proposed": {"outcome": "B", "scope": "personal", "recipients": [], "continuing": False},
                   "context_refs": [], "supersedes": None})
    assert svc.submit(partial, "tok-cos")["detail"] == "unreconciled_correction"
    flow.interpret("IN-syn-0001", ["SE-syn-0001"], "C", corrections=["SE-syn-0002", "SE-syn-0003"])


def test_s2_4_an_out_of_scope_correction_is_neither_revealed_nor_blocking(svc, flow):
    flow.source("SE-syn-0001", "Ask.", token="tok-mc", scope="shared:build")
    flow.source("SE-syn-0002", "Personal aside.", token="tok-principal")
    flow.source("SE-syn-0003", "Correction in build scope.", token="tok-mc", correction_of="SE-syn-0001",
                scope="shared:build")
    res = svc.submit(cmd("interpretation.record", "interpretation", "IN-syn-0002",
                         {"source_event_ids": ["SE-syn-0002"], "correction_event_ids": [],
                          "proposed": {"outcome": "x", "scope": "personal", "recipients": [], "continuing": False},
                          "context_refs": [], "supersedes": None}), "tok-cos")
    assert res["outcome"] == "accepted"


def test_s2_5_creating_an_existing_id_answers_the_same_whatever_its_scope(svc, flow):
    flow.source("SE-syn-0001", "Mine.", token="tok-principal")
    flow.source("SE-syn-0002", "Build.", token="tok-mc", scope="shared:build")
    mk = lambda sid, cid: cmd("source_event.record", "source_event", sid,
                              {"source": {"store": "records", "id": "e", "position": 1}, "content": "x",
                               "correction_of": None}, cid=cid)
    a, b = svc.submit(mk("SE-syn-0001", "cmd-dup-a"), "tok-cos"), svc.submit(mk("SE-syn-0002", "cmd-dup-b"), "tok-cos")
    assert a["code"] == b["code"] == "id_taken" and "current_revision" not in a


def test_s2_6_a_held_store_answers_unavailable_never_not_found(tmp_path, svc, flow):
    tid = flow.promoted_task()
    t = task(svc)
    fsio.write_doc(tmp_path / "tasks" / f"{tid}.json", dict(t, lead="cos"))     # edited outside the journal
    fresh = service(tmp_path)
    with pytest.raises(StoreUnavailable):
        fresh.get("task", tid, "tok-principal")
    assert fresh.dispatch_check(tid, t["fence_generation"], t["interpretation_ref"], "tok-mc") == \
        {"dispatch": "unknown", "code": "store_unavailable"}


def test_s2_7_a_document_edited_after_this_process_verified_it_is_caught_on_read(tmp_path, svc, flow):
    flow.source("SE-syn-0001", "Draft it.")
    flow.interpret("IN-syn-0001", ["SE-syn-0001"], "Draft.")
    flow.request("RQ-syn-0001", "SE-syn-0001", "Draft.", iid="IN-syn-0001")
    path = tmp_path / "interpretations" / "IN-syn-0001.json"
    doc = fsio.read_doc(path)
    fsio.write_doc(path, dict(doc, disposition="adopted", disposition_by="cos",
                              authority={"kind": "owner_confirmation", "ref": ref("records", "forged")}))
    res = svc.submit(cmd("request.promote", "request", "RQ-syn-0001",
                         {"task_id": "TK-syn-0001", "lead": "mc", "holder": "mc", "outcome": "o", "done_when": "d",
                          "return_window": None, "parent_ref": None}, expected=1), "tok-cos")
    assert res["code"] == "store_unavailable"
    assert not (tmp_path / "tasks" / "TK-syn-0001.json").exists()


def test_d01_only_the_lead_or_owner_moves_the_holder_and_cancel_needs_evidence(svc, flow):
    tid = flow.promoted_task()
    t = task(svc)
    flow.ok(cmd("task.assign", "task", tid, {"role": "holder", "seat": "cos", "reason": "Execute step 1."},
                expected=t["revision"]), "tok-mc")
    t = task(svc)
    grab = cmd("task.assign", "task", tid, {"role": "holder", "seat": "codex", "reason": "passing it on"},
               expected=t["revision"])
    assert svc.submit(grab, "tok-cos")["detail"] == "may_not_assign_holder"   # holder has the operation, not the right
    assert flow.transition(tid, "canceled", reason="Dropped.")["detail"] == "evidence_required"


# ---- fix-verification follow-ups (REVIEW_B1_FIXES_INDEPENDENT_2026-11-04) -----------------------------------------

def test_s2_1_a_source_event_backs_one_request_only(svc, flow):
    flow.promoted_task()                                          # RQ-syn-0001 on SE-syn-0001 with IN-syn-0001
    second = cmd("request.record", "request", "RQ-syn-0002",
                 {"source_event_id": "SE-syn-0001", "intent_text": "Same words, my request.",
                  "interpretation_ref": "IN-syn-0001", "responder": "mc"})
    assert svc.submit(second, "tok-mc")["detail"] == "source_already_has_a_request"


def test_s2_1_refine_cannot_borrow_another_requests_source(svc, flow):
    flow.promoted_task()
    flow.source("SE-syn-0004", "Unrelated ask.")
    flow.request("RQ-syn-0004", "SE-syn-0004", "Unrelated.")
    borrow = cmd("request.refine", "request", "RQ-syn-0004",
                 {"source_event_id": "SE-syn-0001", "intent_text": "x", "interpretation_ref": "IN-syn-0001"},
                 expected=1)
    assert svc.submit(borrow, "tok-cos")["detail"] == "source_belongs_to_another_request"


def test_n1_the_lead_rebases_a_waiting_task_onto_the_adopted_successor_and_resumes(svc, flow):
    tid = flow.promoted_task()
    flow.interpret("IN-syn-0002", ["SE-syn-0001"], "Same ask, better reading.", supersedes="IN-syn-0001")
    t = task(svc)
    assert t["state"] == "waiting"
    rebase = lambda iid, tok, fence=None: svc.submit(cmd("task.adopt_interpretation", "task", tid,
                                                         {"interpretation_ref": iid,
                                                          "fence_generation": t["fence_generation"] if fence is None else fence},
                                                         expected=t["revision"]), tok)
    assert rebase("IN-syn-0002", "tok-mc")["detail"] == "interpretation_not_adopted"
    flow.adopt("IN-syn-0002")
    assert rebase("IN-syn-0002", "tok-cos")["detail"] == "only_the_lead_rebases"
    flow.source("SE-syn-0005", "Other.")
    flow.interpret("IN-syn-0005", ["SE-syn-0005"], "Other.")
    assert rebase("IN-syn-0005", "tok-mc")["detail"] == "not_a_successor_of_the_current_interpretation"
    assert rebase("IN-syn-0002", "tok-mc")["outcome"] == "accepted"
    now = task(svc)
    assert now["interpretation_ref"] == "IN-syn-0002" and now["fence_generation"] == t["fence_generation"] + 1
    assert flow.transition(tid, "working", evidence=[ref("harness", "pickup-1")])["outcome"] == "accepted"


def test_n2_only_the_interpreter_owner_or_a_citing_lead_may_supersede_and_only_on_the_same_source(svc, flow):
    flow.promoted_task()                                          # IN-syn-0001 interpreted by cos, task lead mc
    flow.source("SE-syn-0006", "Different words.")
    by_platform = cmd("interpretation.record", "interpretation", "IN-syn-0007",
                      {"source_event_ids": ["SE-syn-0001"], "correction_event_ids": [],
                       "proposed": {"outcome": "x", "scope": "personal", "recipients": [], "continuing": True},
                       "context_refs": [], "supersedes": "IN-syn-0001"})
    assert svc.submit(by_platform, "tok-platform")["detail"] == "may_not_supersede"
    other_source = dict(by_platform, command_id="cmd-sup-2",
                        payload=dict(by_platform["payload"], source_event_ids=["SE-syn-0006"]))
    other_source = cmd("interpretation.record", "interpretation", "IN-syn-0007", other_source["payload"])
    assert svc.submit(other_source, "tok-cos")["detail"] == "supersede_needs_the_same_source"
    assert task(svc)["state"] == "submitted"                      # nothing was fenced by the refused attempts
