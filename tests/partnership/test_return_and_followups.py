"""B2D return/delivery (record-only notices) and the B2B/B2C follow-ups (REVIEW_B2B_INDEPENDENT_2026-11-04).
SYNTHETIC: notices are written to a fake channel; nobody is messaged."""
from __future__ import annotations

import pytest

from core.canonical_json import digest
from services.partnership_tasks.outbox import drain_once
from services.partnership_tasks.service import NOTICE_OP_PREFIX
from tests.partnership.fake_harness import FakeHarness, FakeNotices
from tests.partnership.synth import Flow, cmd, ref, service, drain, approve_admission

D = "tok-drainer"


@pytest.fixture
def rig(tmp_path):
    svc = service(tmp_path / "t")
    f = Flow(svc)
    tid = f.promoted_task()
    f.transition(tid, "working", evidence=[ref("harness", "pickup-1")])
    return svc, f, tid, FakeHarness(tmp_path / "h"), FakeNotices(tmp_path / "n")


def task(svc, tid):
    return svc.get("task", tid, "tok-principal")


def notices_for(svc, tid, kind=None):
    folder = svc.journal.root / "outbox"
    out = []
    for p in sorted(folder.glob(f"{NOTICE_OP_PREFIX}*.json")):
        ob = svc.get("outbox", p.stem, "tok-principal")
        if ob["task_id"] == tid and (kind is None or ob["packet"]["kind"] == kind):
            out.append(ob)
    return out


def submit(svc, tid, tag, token="tok-mc"):
    t = task(svc, tid)
    return svc.submit(cmd("task.submit_artifacts", "task", tid,
                          {"artifacts": [ref("artifacts", f"draft-{tag}", "sha256:" + digest(tag)[7:], "1")],
                           "fence_generation": t["fence_generation"]}, expected=t["revision"]), token)


def review(svc, tid, set_digest, token, rid, verdict="approve", input_digest=None):
    t = task(svc, tid)
    return svc.submit(cmd("task.record_review", "task", tid,
                          {"review_id": rid, "reviewed_set_digest": set_digest, "verdict": verdict,
                           "input_digest": input_digest or set_digest,
                           "review_ref": ref("artifacts", rid.lower(), "sha256:" + digest(rid)[7:], "1"),
                           "findings_ref": None}, expected=t["revision"]), token)


# ---- B2D --------------------------------------------------------------------------------------------------------

def test_j2_return_packet_reaches_the_principal_with_the_browser_closed_and_read_is_separate(rig):
    svc, f, tid, h, n = rig
    v1 = f.ready_for_acceptance(tid, tag="v1")
    f.accept(tid, v1)
    [packet] = notices_for(svc, tid, "return_packet")
    assert packet["state"] == "pending" and packet["bundle_ref"]["digest"] == v1
    n.faults = ["down"]
    assert drain(svc, D, h, packet["operation_id"], notices=n) == "unknown"     # delivery down: not lost
    assert drain(svc, D, h, packet["operation_id"], notices=n) == "pending"     # never arrived
    assert drain(svc, D, h, packet["operation_id"], notices=n) == "acknowledged"
    assert n.delivered() == [packet["operation_id"]]
    ob = svc.get("outbox", packet["operation_id"], "tok-principal")
    read = cmd("outbox.record", "outbox", ob["operation_id"],
               {"observation": "read", "destination_receipt": None, "code": None}, expected=ob["revision"])
    assert svc.submit(read, "tok-mc")["detail"] == "only_the_owner_reads"
    assert svc.submit(dict(read, command_id="cmd-read-2"), "tok-principal")["outcome"] == "accepted"


def test_e08_e09_lead_failure_gives_one_durable_needs_owner_notice_per_episode(rig):
    svc, f, tid, h, n = rig
    t = task(svc, tid)
    miss = cmd("task.lead_unavailable", "task", tid, {"evidence_refs": [ref("harness", "chk-1")],
                                                      "fence_generation": t["fence_generation"]},
               expected=t["revision"])
    assert svc.submit(miss, "tok-scheduler")["outcome"] == "accepted"
    assert svc.submit(miss, "tok-scheduler")["replayed"] is True                    # same detection, same notice
    assert len(notices_for(svc, tid, "needs_owner")) == 1
    [notice] = notices_for(svc, tid, "needs_owner")
    assert drain(svc, D, h, notice["operation_id"], notices=n) == "acknowledged"
    assert drain(svc, D, h, notice["operation_id"], notices=n) == "acknowledged"  # no repeat sends
    assert len(n.delivered()) == 1


def test_a_notice_without_a_channel_stays_pending_and_is_never_dropped(rig):
    svc, f, tid, h, n = rig
    v1 = f.ready_for_acceptance(tid, tag="v1")
    f.accept(tid, v1)
    [packet] = notices_for(svc, tid, "return_packet")
    assert drain(svc, D, h, packet["operation_id"]) == "pending"


# ---- B2B follow-ups ---------------------------------------------------------------------------------------------

def test_s2_1_an_objection_outlives_a_resubmission_until_delivered(rig):
    svc, f, tid, h, n = rig
    submit(svc, tid, "v1")
    v1 = task(svc, tid)["artifact_sets"][-1]["digest"]
    review(svc, tid, v1, "tok-principal", "RV-syn-obj-1", verdict="disagree")
    submit(svc, tid, "v2")
    v2 = task(svc, tid)["artifact_sets"][-1]["digest"]
    review(svc, tid, v2, "tok-cos", "RV-syn-ok-2")
    assert f.accept(tid, v2)["detail"] == "disagreement_not_delivered"
    assert f.accept(tid, v2, ack=["RV-syn-obj-1"])["outcome"] == "accepted"
    assert task(svc, tid)["acceptances"][-1]["disagreements_delivered"] == ["RV-syn-obj-1"]


def test_s2_2_reviews_stop_applying_when_the_interpretation_changes(rig):
    svc, f, tid, h, n = rig
    v1 = f.ready_for_acceptance(tid, tag="v1")
    f.interpret("IN-syn-0002", ["SE-syn-0001"], "Different outcome.", supersedes="IN-syn-0001")
    f.adopt("IN-syn-0002")
    t = task(svc, tid)
    f.ok(cmd("task.adopt_interpretation", "task", tid, {"interpretation_ref": "IN-syn-0002",
                                                        "fence_generation": t["fence_generation"]},
             expected=t["revision"]), "tok-mc")
    assert [r["applicable_to_current"] for r in task(svc, tid)["reviews"]] == [False]
    f.transition(tid, "working", evidence=[ref("harness", "pickup-2")])
    assert f.accept(tid, v1)["detail"] == "no_non_author_approval_of_this_set"


def test_s2_3_a_review_must_cite_the_set_it_reviewed_as_its_input(rig):
    svc, f, tid, h, n = rig
    submit(svc, tid, "v1")
    v1 = task(svc, tid)["artifact_sets"][-1]["digest"]
    assert review(svc, tid, v1, "tok-cos", "RV-syn-x-1",
                  input_digest="sha256:" + "0" * 64)["detail"] == "review_input_is_not_this_set"


def test_s2_4_an_assigned_verifier_narrows_who_may_review(rig):
    svc, f, tid, h, n = rig
    t = task(svc, tid)
    f.ok(cmd("task.assign", "task", tid, {"role": "verifier", "seat": "codex", "reason": "Cross-model review."},
             expected=t["revision"]), "tok-mc")
    submit(svc, tid, "v1")
    v1 = task(svc, tid)["artifact_sets"][-1]["digest"]
    assert review(svc, tid, v1, "tok-cos", "RV-syn-cos-1")["detail"] == "not_an_assigned_verifier"


def test_s3_the_lead_cannot_approve_and_then_accept_alone_and_holders_are_authors(rig):
    svc, f, tid, h, n = rig
    t = task(svc, tid)
    f.ok(cmd("task.assign", "task", tid, {"role": "holder", "seat": "cos", "reason": "Execute."},
             expected=t["revision"]), "tok-mc")
    assert submit(svc, tid, "v1", token="tok-mc")["outcome"] == "accepted"         # lead submits cos's work
    v1 = task(svc, tid)["artifact_sets"][-1]["digest"]
    assert review(svc, tid, v1, "tok-cos", "RV-syn-holder-1")["detail"] == "author_cannot_review_own_set"
    assert review(svc, tid, v1, "tok-principal", "RV-syn-own-1")["outcome"] == "accepted"
    assert f.accept(tid, v1)["outcome"] == "accepted"                               # principal's approval counts


def test_s3_duplicate_artifacts_are_refused(rig):
    svc, f, tid, h, n = rig
    t = task(svc, tid)
    a = ref("artifacts", "draft", "sha256:" + "1" * 64, "1")
    res = svc.submit(cmd("task.submit_artifacts", "task", tid, {"artifacts": [a, dict(a, digest="sha256:" + "2" * 64)],
                                                                "fence_generation": t["fence_generation"]},
                         expected=t["revision"]), "tok-mc")
    assert res["detail"] == "duplicate_artifact"


def test_release_gate_checks_a_pinned_edition_and_never_counts_changes_requested(rig):
    svc, f, tid, h, n = rig
    v1 = f.ready_for_acceptance(tid, tag="v1", verdict="changes_requested", review_id="RV-syn-c-1")
    assert svc.release_gate(tid, "tok-principal")["gate"] == "closed"
    submit(svc, tid, "v2")
    v2 = task(svc, tid)["artifact_sets"][-1]["digest"]
    review(svc, tid, v2, "tok-cos", "RV-syn-ok-2")
    assert f.accept(tid, v2, ack=["RV-syn-c-1"])["detail"] == "disagreement_not_delivered"   # cos since approved v2
    assert f.accept(tid, v2)["outcome"] == "accepted"
    assert svc.release_gate(tid, "tok-principal")["gate"] == "open"
    assert svc.release_gate(tid, "tok-principal", artifact_set_digest=v1)["gate"] == "closed"


# ---- B2C need: child acceptance follows the parent's current lead -----------------------------------------------

def test_e07_a_child_is_accepted_by_the_parents_current_lead(rig):
    svc, f, tid, h, n = rig
    t = task(svc, tid)
    f.ok(cmd("task.delegate", "task", tid, {"child_task_id": "TK-syn-0001c1", "holder": "worker-a",
                                            "outcome": "Section 2.", "done_when": "Accepted.", "return_window": None,
                                            "fence_generation": t["fence_generation"]}, expected=t["revision"]), "tok-mc")
    f.transition("TK-syn-0001c1", "working", token="tok-worker", evidence=[ref("harness", "c")])
    cset = f.ready_for_acceptance("TK-syn-0001c1", author="tok-worker", reviewer="tok-principal", tag="c1")
    t = task(svc, tid)
    f.ok(cmd("task.assign", "task", tid, {"role": "lead", "seat": "cos", "reason": "Handover."},
             expected=t["revision"]), "tok-principal")
    assert f.accept("TK-syn-0001c1", cset, token="tok-mc")["detail"] == "only_the_lead_accepts"
    assert f.accept("TK-syn-0001c1", cset, token="tok-cos")["outcome"] == "accepted"


# ---- REVIEW_B2CD_INDEPENDENT_2026-11-04 (B2D) ------------------------------------------------------------------

def test_s2_5_s2_6_each_acceptance_gets_its_own_packet_carrying_the_delivered_disagreement(rig):
    svc, f, tid, h, n = rig
    v1 = f.ready_for_acceptance(tid, tag="v1")
    review(svc, tid, v1, "tok-principal", "RV-syn-dis-1", verdict="disagree")
    f.accept(tid, v1, ack=["RV-syn-dis-1"])
    t = task(svc, tid)
    f.ok(cmd("task.reopen", "task", tid, {"reason": "Look again."}, expected=t["revision"]), "tok-mc")
    f.transition(tid, "working", evidence=[ref("harness", "pickup-2")])
    assert f.accept(tid, v1, ack=["RV-syn-dis-1"])["outcome"] == "accepted"       # same set, accepted again
    packets = notices_for(svc, tid, "return_packet")
    assert len(packets) == 2
    for p in packets:
        assert [d["review_id"] for d in p["packet"]["disagreements_delivered"]] == ["RV-syn-dis-1"]
        assert p["bundle_digest"] == digest(p["packet"])


def test_e09_a_second_lead_failure_episode_raises_a_second_notice(rig):
    svc, f, tid, h, n = rig
    def miss():
        t = task(svc, tid)
        return svc.submit(cmd("task.lead_unavailable", "task", tid,
                              {"evidence_refs": [ref("harness", f"chk-{t['revision']}")],
                               "fence_generation": t["fence_generation"]}, expected=t["revision"]), "tok-scheduler")
    miss()
    f.transition(tid, "working", token="tok-principal", evidence=[ref("records", "owner-resumed")])
    miss()
    kinds = [p["packet"]["choices"] for p in notices_for(svc, tid, "needs_owner")]
    assert len(kinds) == 2 and "reassign the lead" in kinds[0]


def test_reconstruction_shows_notices_with_their_delivery_state(rig):
    from services.partnership_tasks.reconstruct import Reconstructor
    svc, f, tid, h, n = rig
    v1 = f.ready_for_acceptance(tid, tag="v1")
    f.accept(tid, v1)
    [packet] = notices_for(svc, tid, "return_packet")
    drain(svc, D, h, packet["operation_id"], notices=n)
    out = Reconstructor(svc).reconstruct("task", tid, "tok-principal")
    assert [(x["kind"], x["state"]) for x in out["notices"]] == [("return_packet", "acknowledged")]
    assert {"store": "notices", "status": "not_joined"} not in out["coverage"]


def test_s2_b_check_a_pending_return_packet_survives_a_reopen_and_queues_no_fence_op(rig):
    svc, f, tid, h, n = rig
    v1 = f.ready_for_acceptance(tid, tag="v1")
    f.accept(tid, v1)
    t = task(svc, tid)
    f.ok(cmd("task.reopen", "task", tid, {"reason": "again"}, expected=t["revision"]), "tok-mc")
    [packet] = notices_for(svc, tid, "return_packet")
    assert packet["state"] == "pending"                                            # not fenced
    assert not list((svc.journal.root / "outbox").glob("OB-fence-*.json"))         # no admission, no set_fence


def test_f14_a_breach_with_a_delivered_escalation_notice_has_no_gap(rig):
    """S3 sweep: the positive delivered-notice case for F14 escalation."""
    from domains.cos.store import StoreWriter
    from services.partnership_tasks.joins import occurrence
    from tests.cos.test_store_writer import ENV, Stream, define, event, verified
    svc, f, tid, h, n = rig
    t = task(svc, tid)
    f.ok(cmd("task.lead_unavailable", "task", tid, {"evidence_refs": [ref("harness", "chk-1")],
                                                    "fence_generation": t["fence_generation"]},
             expected=t["revision"]), "tok-scheduler")
    [notice] = notices_for(svc, tid, "needs_owner")
    drain(svc, D, h, notice["operation_id"], notices=n)
    cos = StoreWriter(svc.journal.root.parent / "cos", ENV, verify_robert_source=verified)
    occ = "OBL-disk-weekly@2026-11-02"
    define(cos, "OBL-disk-weekly", recurrence={"kind": "weekly", "weekday": 0, "tz": "America/Chicago"},
           reminder_window={"from": "04:40", "to": "06:40"})
    Stream(cos).send(event("ev-1", "opened", 0, occ=occ))
    mon = {"expected": [], "heartbeats": [{"at": "2026-11-02T11:00:00+00:00", "ok": True}], "receipts": [
        {"occurrence_id": occ, "kind": "check", "at": "2026-11-02T11:05:00+00:00", "seat": "disk-monitor",
         "policy_edition": "OBL-disk-weekly@r1", "measured": {"free_gib": 8}, "threshold_result": "breach"},
        {"occurrence_id": occ, "kind": "escalation", "at": "2026-11-02T11:06:00+00:00", "seat": "disk-monitor",
         "notice_operation_id": notice["operation_id"]}]}
    out = occurrence(svc, "tok-principal", occ, cos_store=cos, monitor=mon, duty_task_id=None,
                     authorized_monitor_seats={"disk-monitor"}, now="2026-11-04T00:00:00+00:00")
    assert out["escalations"][0]["notice_state"] == "acknowledged"
    assert not [g for g in out["gaps"] if "escalat" in g or "notice" in g]
