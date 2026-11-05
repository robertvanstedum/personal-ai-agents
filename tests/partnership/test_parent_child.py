"""B2C: one-level delegation, child failure and bounded revision, parent never auto-closed, cancel with uncertain
effects, and lead failure without an unauthorized successor (spec v1.1 E07, E08; acceptance J3, F05). SYNTHETIC."""
from __future__ import annotations

import pytest

from core.canonical_json import digest
from services.partnership_tasks.service import MAX_CHILD_ROUNDS
from tests.partnership.synth import Flow, cmd, ref, service, drain, approve_admission


@pytest.fixture
def rig(tmp_path):
    svc = service(tmp_path)
    f = Flow(svc)
    tid = f.promoted_task()
    f.transition(tid, "working", evidence=[ref("harness", "pickup-1")])
    return svc, f, tid


def task(svc, tid):
    return svc.get("task", tid, "tok-principal")


def delegate(svc, parent, child="TK-syn-0001c1", holder="worker-a", token="tok-mc"):
    t = task(svc, parent)
    return svc.submit(cmd("task.delegate", "task", parent,
                          {"child_task_id": child, "holder": holder, "outcome": "Draft section 2.",
                           "done_when": "Lead accepts a reviewed section.", "return_window": None,
                           "fence_generation": t["fence_generation"]}, expected=t["revision"]), token)


def test_e07_one_level_delegation_keeps_the_parent_lead_accountable(rig):
    svc, f, tid = rig
    assert delegate(svc, tid, token="tok-cos")["detail"] == "only_the_lead_delegates"
    assert delegate(svc, tid)["outcome"] == "accepted"
    child = task(svc, "TK-syn-0001c1")
    assert (child["lead"], child["holder"], child["parent_ref"]) == ("mc", "worker-a", tid)
    assert child["request_ref"] == task(svc, tid)["request_ref"]
    assert [a for a in task(svc, tid)["assignments"] if a["role"] == "delegate"][0]["seat"] == "worker-a"
    f.transition("TK-syn-0001c1", "working", token="tok-worker", evidence=[ref("harness", "c-pickup")])
    assert delegate(svc, "TK-syn-0001c1", child="TK-syn-0001c2")["detail"] == "one_level_only"


def test_j3_child_done_is_submitted_and_the_parent_is_not_auto_closed(rig):
    svc, f, tid = rig
    delegate(svc, tid)
    f.transition("TK-syn-0001c1", "working", token="tok-worker", evidence=[ref("harness", "c-pickup")])
    child_set = f.ready_for_acceptance("TK-syn-0001c1", author="tok-worker", reviewer="tok-cos", tag="c1")
    assert task(svc, "TK-syn-0001c1")["state"] == "working"                   # worker done = submitted
    parent_set = f.ready_for_acceptance(tid, tag="p1")
    assert f.accept(tid, parent_set)["detail"] == "children_open"            # never auto-closed
    assert f.accept("TK-syn-0001c1", child_set)["outcome"] == "accepted"     # the parent lead accepts the child
    assert f.accept(tid, parent_set)["outcome"] == "accepted"


def test_j3_child_failure_bounded_revision_and_the_parent_stays_open(rig):
    svc, f, tid = rig
    delegate(svc, tid)
    c = "TK-syn-0001c1"
    for n in range(MAX_CHILD_ROUNDS + 1):
        f.transition(c, "working", token="tok-worker", evidence=[ref("harness", f"c-pickup-{n}")])
        assert f.transition(c, "failed", token="tok-worker", reason="Tests red.",
                            evidence=[ref("harness", f"c-fail-{n}")])["outcome"] == "accepted"
        t = task(svc, c)
        res = svc.submit(cmd("task.reopen", "task", c, {"reason": "Revise."}, expected=t["revision"]), "tok-mc")
        if n < MAX_CHILD_ROUNDS:
            assert res["outcome"] == "accepted"
        else:
            assert res["detail"] == "revision_limit"                          # bounded: the lead decides next
    assert task(svc, tid)["state"] == "working"                               # parent untouched by child failure


def test_j3_parent_cancel_reaches_children_and_reports_their_uncertain_effects(tmp_path):
    from services.partnership_tasks.outbox import drain_once
    from tests.partnership.fake_harness import FakeHarness
    svc = service(tmp_path / "t")
    f = Flow(svc)
    tid = f.promoted_task()
    f.transition(tid, "working", evidence=[ref("harness", "pickup-1")])
    delegate(svc, tid)
    c = "TK-syn-0001c1"
    t = task(svc, c)
    bd = digest({"child": "bundle"})
    f.ok(cmd("task.request_admission", "task", c, {"operation_id": "OB-syn-c1-1", "bundle_digest": bd,
                                                   "bundle_ref": ref("artifacts", "b", bd),
                                                   "fence_generation": t["fence_generation"]},
             expected=t["revision"]), "tok-mc")
    h = FakeHarness(tmp_path / "h")
    h.faults = ["lose_answer_after_commit"]
    assert drain(svc, "tok-drainer", h, "OB-syn-c1-1") == "unknown"
    res = f.transition(tid, "canceled", reason="Owner stopped it.", evidence=[ref("records", "evt-stop")])
    assert res["outcome"] == "accepted" and res["open_effects"] == ["OB-syn-c1-1"]
    assert task(svc, c)["state"] == "canceled"


def test_f05_lead_absent_records_a_blocked_episode_and_appoints_no_successor(rig):
    svc, f, tid = rig
    delegate(svc, tid)
    f.transition("TK-syn-0001c1", "working", token="tok-worker", evidence=[ref("harness", "c-pickup")])
    t = task(svc, tid)
    miss = lambda tok: svc.submit(cmd("task.lead_unavailable", "task", tid,
                                      {"evidence_refs": [ref("harness", "checkpoint-missed-1")],
                                       "fence_generation": t["fence_generation"]}, expected=t["revision"]), tok)
    assert miss("tok-cos")["code"] == "not_authorized"                         # an agent cannot declare it
    assert miss("tok-scheduler")["outcome"] == "accepted"
    blocked = task(svc, tid)
    assert blocked["state"] == "waiting"
    assert blocked["waiting"] == {"reason": "lead_unavailable", "who_can_unblock": "principal"}
    assert blocked["lead"] == "mc"                                             # no autonomous substitute
    # an already-admitted child step may still finish within its bounds...
    child_set = f.ready_for_acceptance("TK-syn-0001c1", author="tok-worker", reviewer="tok-cos", tag="c1")
    # ...but nothing that needs a lead decision moves without the lead or the principal
    assert delegate(svc, tid, child="TK-syn-0001c9")["outcome"] == "refused"
    t = task(svc, tid)
    f.ok(cmd("task.assign", "task", tid, {"role": "lead", "seat": "cos", "reason": "Principal reassigns."},
             expected=t["revision"]), "tok-principal")
    assert task(svc, tid)["lead"] == "cos"
    assert child_set


# ---- REVIEW_B2CD_INDEPENDENT_2026-11-04 ------------------------------------------------------------------------

def block_lead(svc, tid):
    t = task(svc, tid)
    return svc.submit(cmd("task.lead_unavailable", "task", tid, {"evidence_refs": [ref("harness", f"chk-{t['revision']}")],
                                                                 "fence_generation": t["fence_generation"]},
                          expected=t["revision"]), "tok-scheduler")


def test_s1_1_only_the_principal_or_a_reassignment_ends_a_lead_unavailable_episode(rig):
    svc, f, tid = rig
    t = task(svc, tid)
    f.ok(cmd("task.assign", "task", tid, {"role": "holder", "seat": "cos", "reason": "Execute."},
             expected=t["revision"]), "tok-mc")
    block_lead(svc, tid)
    assert f.transition(tid, "working", token="tok-cos", evidence=[ref("harness", "x")])["detail"] == \
        "lead_unavailable_needs_the_principal"                                 # the holder cannot
    assert f.transition(tid, "working", token="tok-mc", evidence=[ref("harness", "x")])["detail"] == \
        "lead_unavailable"                                                     # nor the absent lead
    t = task(svc, tid)
    f.ok(cmd("task.assign", "task", tid, {"role": "lead", "seat": "codex", "reason": "Principal reassigns."},
             expected=t["revision"]), "tok-principal")
    assert task(svc, tid)["waiting"] == {"reason": "input", "who_can_unblock": "codex"}


def test_s1_2_a_childs_authority_follows_the_parents_current_lead(rig):
    svc, f, tid = rig
    delegate(svc, tid)
    c = "TK-syn-0001c1"
    t = task(svc, tid)
    f.ok(cmd("task.assign", "task", tid, {"role": "lead", "seat": "cos", "reason": "Handover."},
             expected=t["revision"]), "tok-principal")
    assert task(svc, c)["fence_generation"] == 1                               # children fenced on lead change
    assert f.transition(c, "canceled", token="tok-mc", reason="x",
                        evidence=[ref("records", "e")])["detail"] == "seat_may_not_canceled"
    ct = task(svc, c)
    grab = cmd("task.assign", "task", c, {"role": "lead", "seat": "worker-a", "reason": "x"}, expected=ct["revision"])
    assert svc.submit(grab, "tok-principal")["detail"] == "child_lead_follows_parent"
    holder = cmd("task.assign", "task", c, {"role": "holder", "seat": "codex", "reason": "x"}, expected=ct["revision"])
    assert svc.submit(holder, "tok-mc")["code"] == "not_authorized"
    assert svc.submit(dict(holder, command_id="cmd-h-cos"), "tok-cos")["outcome"] == "accepted"


def test_s2_1_children_come_only_from_delegation(rig):
    svc, f, tid = rig
    f.source("SE-syn-0002", "Another ask.")
    f.interpret("IN-syn-0002", ["SE-syn-0002"], "Another.")
    f.adopt("IN-syn-0002")
    f.request("RQ-syn-0002", "SE-syn-0002", "Another.", iid="IN-syn-0002")
    res = svc.submit(cmd("request.promote", "request", "RQ-syn-0002",
                         {"task_id": "TK-syn-0002", "lead": "cos", "holder": "cos", "outcome": "o", "done_when": "d",
                          "return_window": None, "parent_ref": tid}, expected=svc.get("request", "RQ-syn-0002",
                                                                                         "tok-cos")["revision"]),
                     "tok-cos")
    assert res["detail"] == "children_come_from_task_delegate"


@pytest.mark.parametrize("end", ["failed", "canceled"])
def test_s2_2_children_stop_when_the_parent_ends_and_cannot_be_accepted_after(rig, end):
    svc, f, tid = rig
    delegate(svc, tid)
    c = "TK-syn-0001c1"
    f.transition(c, "working", token="tok-worker", evidence=[ref("harness", "c")])
    cset = f.ready_for_acceptance(c, author="tok-worker", reviewer="tok-cos", tag="c1")
    assert f.transition(tid, end, reason="Stopped.", evidence=[ref("records", "e")])["outcome"] == "accepted"
    assert task(svc, c)["state"] == "canceled"
    assert f.accept(c, cset)["outcome"] == "refused"


def test_s2_4_a_child_of_an_absent_lead_holds_acceptance_and_new_admissions(rig):
    svc, f, tid = rig
    delegate(svc, tid)
    c = "TK-syn-0001c1"
    f.transition(c, "working", token="tok-worker", evidence=[ref("harness", "c")])
    cset = f.ready_for_acceptance(c, author="tok-worker", reviewer="tok-cos", tag="c1")
    block_lead(svc, tid)
    assert f.accept(c, cset)["detail"] == "lead_unavailable"
    ct = task(svc, c)
    bd = digest({"b": 1})
    adm = cmd("task.request_admission", "task", c, {"operation_id": "OB-syn-c-9", "bundle_digest": bd,
                                                    "bundle_ref": ref("artifacts", "b", bd),
                                                    "fence_generation": ct["fence_generation"]}, expected=ct["revision"])
    assert svc.submit(adm, "tok-mc")["detail"] == "lead_unavailable"


def test_s1_a_the_absent_lead_cannot_reassign_or_reopen_its_way_out_of_the_episode(rig):
    svc, f, tid = rig
    delegate(svc, tid)
    block_lead(svc, tid)
    t = task(svc, tid)
    for role, seat in (("lead", "codex"), ("holder", "codex")):
        res = svc.submit(cmd("task.assign", "task", tid, {"role": role, "seat": seat, "reason": "x"},
                             expected=t["revision"]), "tok-mc")
        assert res["detail"] == "lead_unavailable"
    c = "TK-syn-0001c1"
    f.transition(c, "working", token="tok-worker", evidence=[ref("harness", "c")])
    f.transition(c, "failed", token="tok-worker", reason="Red.", evidence=[ref("harness", "c-f")])
    ct = task(svc, c)
    assert svc.submit(cmd("task.reopen", "task", c, {"reason": "again"}, expected=ct["revision"]),
                      "tok-mc")["detail"] == "lead_unavailable"
    assert task(svc, tid)["waiting"]["reason"] == "lead_unavailable"


def test_s2_2_a_rejected_parent_cancels_its_children(tmp_path):
    svc = service(tmp_path)
    f = Flow(svc)
    tid = f.promoted_task()
    delegate(svc, tid)
    assert f.transition(tid, "rejected", reason="Not doing this.")["outcome"] == "accepted"
    assert task(svc, "TK-syn-0001c1")["state"] == "canceled"


def test_episode_guard_covers_rebase_child_holder_cancel_and_submit_but_not_a_holders_failure_report(rig):
    svc, f, tid = rig
    delegate(svc, tid)
    c = "TK-syn-0001c1"
    f.transition(c, "working", token="tok-worker", evidence=[ref("harness", "c")])
    f.interpret("IN-syn-0002", ["SE-syn-0001"], "Better reading.", supersedes="IN-syn-0001")
    f.adopt("IN-syn-0002")
    block_lead(svc, tid)
    t = task(svc, tid)
    rebase = cmd("task.adopt_interpretation", "task", tid, {"interpretation_ref": "IN-syn-0002",
                                                            "fence_generation": t["fence_generation"]},
                 expected=t["revision"])
    assert svc.submit(rebase, "tok-mc")["detail"] == "lead_unavailable"
    ct = task(svc, c)
    hold = cmd("task.assign", "task", c, {"role": "holder", "seat": "codex", "reason": "x"}, expected=ct["revision"])
    assert svc.submit(hold, "tok-mc")["detail"] == "lead_unavailable"
    assert f.transition(c, "canceled", token="tok-mc", reason="x",
                        evidence=[ref("records", "e")])["detail"] == "lead_unavailable"
    assert f.transition(c, "failed", token="tok-worker", reason="Red.",
                        evidence=[ref("harness", "c-f")])["outcome"] == "accepted"    # a fact about the attempt


def test_s2_b_the_absent_lead_cannot_fail_the_parent_and_only_the_holder_submits_in_the_episode(rig):
    svc, f, tid = rig
    delegate(svc, tid)
    c = "TK-syn-0001c1"
    f.transition(c, "working", token="tok-worker", evidence=[ref("harness", "c")])
    block_lead(svc, tid)
    assert f.transition(tid, "failed", token="tok-mc", reason="x",
                        evidence=[ref("harness", "f")])["detail"] == "lead_unavailable"   # would cascade
    ct = task(svc, c)
    sub = lambda tok, tag: svc.submit(cmd("task.submit_artifacts", "task", c,
                                          {"artifacts": [ref("artifacts", tag, "sha256:" + digest(tag)[7:], "1")],
                                           "fence_generation": ct["fence_generation"]}, expected=ct["revision"]), tok)
    assert sub("tok-mc", "by-lead")["detail"] == "lead_unavailable"
    assert sub("tok-worker", "by-holder")["outcome"] == "accepted"


def test_s3_the_absent_lead_cannot_steer_a_child_but_its_holder_reports(rig):
    svc, f, tid = rig
    delegate(svc, tid)
    c = "TK-syn-0001c1"
    block_lead(svc, tid)
    assert f.transition(c, "working", token="tok-mc", evidence=[ref("harness", "x")])["detail"] == "lead_unavailable"
    assert f.transition(c, "working", token="tok-worker", evidence=[ref("harness", "c")])["outcome"] == "accepted"
