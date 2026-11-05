"""Blue Lantern, 5 Nov 2026 (option A, Robert's choice). A parent's set that is exactly a completed child's accepted
set carries that acceptance's reviews and the child's authors, so the parent can be accepted and its return packet
notice written. A review child's set is the review it delivered, so the lead accepts it without a review of the review.
SYNTHETIC."""
from __future__ import annotations

import pytest

from core.canonical_json import digest
from services.partnership_tasks.reconstruct import outbox_queue
from tests.partnership.synth import Flow, cmd, ref, service

BUILD, REVIEW = "TK-syn-0001b", "TK-syn-0001r"


@pytest.fixture
def rig(tmp_path):
    svc = service(tmp_path)
    f = Flow(svc)
    tid = f.promoted_task()
    f.transition(tid, "working", evidence=[ref("harness", "pickup-1")])
    return svc, f, tid


def task(svc, tid):
    return svc.get("task", tid, "tok-principal")


def delegate(svc, parent, child, holder):
    t = task(svc, parent)
    res = svc.submit(cmd("task.delegate", "task", parent,
                         {"child_task_id": child, "holder": holder, "outcome": "o", "done_when": "d",
                          "return_window": None, "fence_generation": t["fence_generation"]}, expected=t["revision"]),
                     "tok-mc")
    assert res["outcome"] == "accepted", res


def submit(svc, tid, arts, token):
    t = task(svc, tid)
    return svc.submit(cmd("task.submit_artifacts", "task", tid, {"artifacts": arts,
                                                                 "fence_generation": t["fence_generation"]},
                          expected=t["revision"]), token)


def review(svc, tid, set_digest, review_id, verdict, review_ref, token):
    t = task(svc, tid)
    res = svc.submit(cmd("task.record_review", "task", tid,
                         {"review_id": review_id, "reviewed_set_digest": set_digest, "verdict": verdict,
                          "input_digest": set_digest, "review_ref": review_ref, "findings_ref": None},
                         expected=t["revision"]), token)
    assert res["outcome"] == "accepted", res


def working(f, tid, token):
    assert f.transition(tid, "working", token=token, evidence=[ref("harness", f"pickup-{tid}")])["outcome"] == "accepted"


REF_NO = ref("harness", "rv-1", "sha256:" + digest("no")[7:], edition="1")
REF_OK = ref("harness", "rv-2.verdict", "sha256:" + digest("ok")[7:], edition="1")
ARTS = [ref("harness", "b.patch", "sha256:" + digest("patch")[7:]), ref("harness", "b.output", "sha256:" + digest("o")[7:])]


def built_and_reviewed(svc, f, tid):
    """Codex builds; claude-code reviews (one changes_requested from the gap, then approve) in a review child whose
    set is the approve it delivered; MC accepts the build naming the dissent, then the review child."""
    delegate(svc, tid, BUILD, "codex")
    delegate(svc, tid, REVIEW, "claude-code")
    working(f, BUILD, "tok-codex")
    assert submit(svc, BUILD, ARTS, "tok-codex")["outcome"] == "accepted"
    s = task(svc, BUILD)["artifact_sets"][-1]["digest"]
    review(svc, BUILD, s, "RV-gap-0001", "changes_requested", REF_NO, "tok-claude-code")
    review(svc, BUILD, s, "RV-ok-00001", "approve", REF_OK, "tok-claude-code")
    assert f.accept(BUILD, s, ack=["RV-gap-0001"])["outcome"] == "accepted"
    working(f, REVIEW, "tok-claude-code")
    assert submit(svc, REVIEW, [dict(REF_OK, edition=None)], "tok-claude-code")["outcome"] == "accepted"
    return s


def test_a_parent_carries_its_completed_childs_reviews_and_its_acceptance_writes_the_return_notice(rig):
    svc, f, tid = rig
    s = built_and_reviewed(svc, f, tid)
    r = task(svc, REVIEW)["artifact_sets"][-1]["digest"]
    assert f.accept(REVIEW, r)["outcome"] == "accepted"                     # a delivered review: no review of it
    assert submit(svc, tid, ARTS, "tok-mc")["outcome"] == "accepted"
    p = task(svc, tid)
    assert p["artifact_sets"][-1]["digest"] == s
    assert p["artifact_sets"][-1]["authored_by"] == ["codex", "mc"]           # the child's author travels too
    assert sorted((x["reviewer"], x["verdict"], x["applicable_to_current"]) for x in p["reviews"]) == [
        ("claude-code", "approve", True), ("claude-code", "changes_requested", True)]
    assert all(x["review_id"].startswith("RV-carry-") for x in p["reviews"])
    gap = next(x["review_id"] for x in p["reviews"] if x["verdict"] != "approve")
    assert f.accept(tid, s)["detail"] == "disagreement_not_delivered"        # E06: the dissent travels and is named
    assert f.accept(tid, s, ack=[gap])["outcome"] == "accepted"
    assert task(svc, tid)["state"] == "completed"
    notes = [o for o in outbox_queue(svc, "tok-drainer") if o["operation"] == "notify"]
    assert len(notes) == 1


def test_a_set_submitted_before_the_child_was_accepted_carries_its_reviews_on_a_second_submit_once(rig):
    svc, f, tid = rig
    delegate(svc, tid, BUILD, "codex")
    working(f, BUILD, "tok-codex")
    submit(svc, BUILD, ARTS, "tok-codex")
    s = task(svc, BUILD)["artifact_sets"][-1]["digest"]
    assert submit(svc, tid, ARTS, "tok-mc")["outcome"] == "accepted"          # nothing to carry yet
    assert task(svc, tid)["reviews"] == []
    review(svc, BUILD, s, "RV-ok-00001", "approve", REF_OK, "tok-claude-code")
    assert f.accept(BUILD, s)["outcome"] == "accepted"
    assert submit(svc, tid, ARTS, "tok-mc")["outcome"] == "accepted"          # the same set again: carries only
    p = task(svc, tid)
    assert len(p["artifact_sets"]) == 1 and p["artifact_sets"][-1]["authored_by"] == ["codex", "mc"]
    assert [(x["reviewer"], x["verdict"]) for x in p["reviews"]] == [("claude-code", "approve")]
    assert submit(svc, tid, ARTS, "tok-mc")["detail"] == "already_current"   # nothing new: refused as before


def test_nothing_is_carried_from_a_child_that_is_not_completed_or_accepted_another_set(rig):
    svc, f, tid = rig
    delegate(svc, tid, BUILD, "codex")
    working(f, BUILD, "tok-codex")
    submit(svc, BUILD, ARTS, "tok-codex")
    s = task(svc, BUILD)["artifact_sets"][-1]["digest"]
    review(svc, BUILD, s, "RV-ok-00001", "approve", REF_OK, "tok-claude-code")   # reviewed but not accepted
    assert submit(svc, tid, ARTS, "tok-mc")["outcome"] == "accepted"
    assert task(svc, tid)["reviews"] == []
    assert f.accept(tid, task(svc, tid)["artifact_sets"][-1]["digest"])["detail"] in (
        "children_open", "no_non_author_approval_of_this_set")


def test_only_a_review_its_holder_delivered_on_a_sibling_is_accepted_without_approval(rig):
    svc, f, tid = rig
    built_and_reviewed(svc, f, tid)
    delegate(svc, tid, "TK-syn-0001x", "claude-code")
    working(f, "TK-syn-0001x", "tok-claude-code")
    other = ref("harness", "made-up.verdict", "sha256:" + digest("x")[7:])
    submit(svc, "TK-syn-0001x", [other], "tok-claude-code")
    x = task(svc, "TK-syn-0001x")["artifact_sets"][-1]["digest"]
    assert f.accept("TK-syn-0001x", x)["detail"] == "no_non_author_approval_of_this_set"
    delegate(svc, tid, "TK-syn-0001y", "codex")                               # another seat cannot claim it
    working(f, "TK-syn-0001y", "tok-codex")
    submit(svc, "TK-syn-0001y", [dict(REF_OK, edition=None)], "tok-codex")
    y = task(svc, "TK-syn-0001y")["artifact_sets"][-1]["digest"]
    assert f.accept("TK-syn-0001y", y)["detail"] == "no_non_author_approval_of_this_set"
