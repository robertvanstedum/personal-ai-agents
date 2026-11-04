"""B2B: digest-bound review and acceptance (spec v1.1 E06, E07; acceptance F04 and the review part of J2). SYNTHETIC."""
from __future__ import annotations

import pytest

from core.canonical_json import digest
from services.partnership_tasks import contracts
from tests.partnership.synth import Flow, cmd, ref, service


@pytest.fixture
def rig(tmp_path):
    svc = service(tmp_path)
    f = Flow(svc)
    tid = f.promoted_task()
    f.transition(tid, "working", evidence=[ref("harness", "pickup-1")])
    return svc, f, tid


def task(svc, tid):
    return svc.get("task", tid, "tok-principal")


def review(svc, f, tid, set_digest, token, review_id, verdict="approve"):
    t = task(svc, tid)
    return svc.submit(cmd("task.record_review", "task", tid,
                          {"review_id": review_id, "reviewed_set_digest": set_digest, "verdict": verdict,
                           "input_digest": set_digest,
                           "review_ref": ref("artifacts", review_id.lower(), "sha256:" + digest(review_id)[7:], "1"),
                           "findings_ref": None}, expected=t["revision"]), token)


def submit(svc, tid, tag, token="tok-mc"):
    t = task(svc, tid)
    return svc.submit(cmd("task.submit_artifacts", "task", tid,
                          {"artifacts": [ref("artifacts", f"draft-{tag}", "sha256:" + digest(tag)[7:], "1")],
                           "fence_generation": t["fence_generation"]}, expected=t["revision"]), token)


def test_j2_draft_review_revision_acceptance_on_the_exact_set(rig):
    svc, f, tid = rig
    v1 = f.ready_for_acceptance(tid, tag="v1", verdict="changes_requested", review_id="RV-syn-v1-1")
    assert f.accept(tid, v1, ack=["RV-syn-v1-1"])["detail"] == "no_non_author_approval_of_this_set"
    submit(svc, tid, "v2")
    t = task(svc, tid)
    v2 = t["artifact_sets"][-1]["digest"]
    assert [r["applicable_to_current"] for r in t["reviews"]] == [False]       # history kept, not current
    assert review(svc, f, tid, v2, "tok-cos", "RV-syn-v2-1")["outcome"] == "accepted"
    assert f.accept(tid, v2)["outcome"] == "accepted"
    done = task(svc, tid)
    assert done["acceptances"][-1]["reviewed_set_digests"] == [v2]
    assert [s["digest"] for s in done["artifact_sets"]] == [v1, v2]
    assert contracts.errors("records", "task", done) == []
    assert svc.release_gate(tid, "tok-principal")["gate"] == "open"


def test_f04_a_new_digest_before_a_late_review_leaves_that_review_historical(rig):
    svc, f, tid = rig
    submit(svc, tid, "v1")
    v1 = task(svc, tid)["artifact_sets"][-1]["digest"]
    submit(svc, tid, "v2")
    assert review(svc, f, tid, v1, "tok-cos", "RV-syn-late-1")["outcome"] == "accepted"   # arrives late, for v1
    t = task(svc, tid)
    assert t["reviews"][-1]["applicable_to_current"] is False
    v2 = t["artifact_sets"][-1]["digest"]
    assert f.accept(tid, v2)["detail"] == "no_non_author_approval_of_this_set"
    assert f.accept(tid, v1)["detail"] == "not_the_current_artifact_set"


def test_f04_a_new_digest_racing_a_concurrent_acceptance_conflicts_instead_of_accepting_the_old_set(rig):
    svc, f, tid = rig
    v1 = f.ready_for_acceptance(tid, tag="v1")
    stale = task(svc, tid)
    submit(svc, tid, "v2")                                                     # lands first
    res = svc.submit(cmd("task.accept", "task", tid, {"artifact_set_digest": v1, "acknowledged_disagreements": [],
                                                      "fence_generation": stale["fence_generation"]},
                         expected=stale["revision"]), "tok-mc")
    assert res["outcome"] == "conflict"
    assert f.accept(tid, v1)["detail"] == "not_the_current_artifact_set"
    assert task(svc, tid)["state"] == "working"


def test_e06_disagreement_is_delivered_with_the_acceptance_never_hidden(rig):
    svc, f, tid = rig
    v1 = f.ready_for_acceptance(tid, tag="v1")                                 # cos approves
    assert review(svc, f, tid, v1, "tok-principal", "RV-syn-dis-1", verdict="disagree")["outcome"] == "accepted"
    assert f.accept(tid, v1)["detail"] == "disagreement_not_delivered"
    assert f.accept(tid, v1, ack=["RV-syn-other"])["detail"] == "disagreement_not_delivered"
    assert f.accept(tid, v1, ack=["RV-syn-dis-1"])["outcome"] == "accepted"
    assert task(svc, tid)["acceptances"][-1]["disagreements_delivered"] == ["RV-syn-dis-1"]


def test_e06_the_author_of_a_set_cannot_approve_it(rig):
    svc, f, tid = rig
    submit(svc, tid, "v1")
    v1 = task(svc, tid)["artifact_sets"][-1]["digest"]
    assert review(svc, f, tid, v1, "tok-mc", "RV-syn-self-1")["detail"] == "author_cannot_review_own_set"
    assert review(svc, f, tid, "sha256:" + "9" * 64, "tok-cos", "RV-syn-x-1")["detail"] == "unknown_artifact_set"


def test_e07_only_the_holder_or_lead_submits_and_a_stale_fence_is_refused(rig):
    svc, f, tid = rig
    assert submit(svc, tid, "v1", token="tok-cos")["detail"] == "only_the_holder_or_lead_submits"
    t = task(svc, tid)
    stale = cmd("task.submit_artifacts", "task", tid,
                {"artifacts": [ref("artifacts", "draft-x", "sha256:" + "1" * 64, "1")],
                 "fence_generation": t["fence_generation"] + 5}, expected=t["revision"])
    assert svc.submit(stale, "tok-mc")["code"] == "fenced"


def test_e06_release_gate_closes_when_a_newer_set_appears_after_acceptance(rig):
    svc, f, tid = rig
    v1 = f.ready_for_acceptance(tid, tag="v1")
    f.accept(tid, v1)
    t = task(svc, tid)
    f.ok(cmd("task.reopen", "task", tid, {"reason": "One more change."}, expected=t["revision"]), "tok-mc")
    f.transition(tid, "working", evidence=[ref("harness", "pickup-2")])
    submit(svc, tid, "v2")
    gate = svc.release_gate(tid, "tok-principal")
    assert gate["gate"] == "closed" and gate["accepted"] is None and gate["current"] != v1
