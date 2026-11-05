"""B2A: admission outbox both directions with a SYNTHETIC harness (spec v1.1 E02, E03, E07, C06, Q05; acceptance F02
and the effect-admission part of F03). The real harness (Postgres) is not joined: heavy jobs are held below 8 GiB free.
Revised after REVIEW_B2A_INDEPENDENT_2026-11-04 (fence at the authority, attempt numbers, drainer-only records)."""
from __future__ import annotations

import pytest

from core.canonical_json import digest
from services.partnership_tasks.outbox import drain_once
from tests.partnership.fake_harness import FakeHarness
from tests.partnership.synth import Flow, cmd, ref, service, drain, approve_admission

BUNDLE = {"steps": ["draft", "review"], "budget_usd": 0, "expiry": "2026-11-05T14:00:00+00:00"}
BD = digest(BUNDLE)
D = "tok-drainer"


@pytest.fixture
def rig(tmp_path):
    svc = service(tmp_path / "tasks")
    f = Flow(svc)
    tid = f.promoted_task()
    return svc, f, tid, FakeHarness(tmp_path / "harness")


def request_admission(svc, tid, op="OB-syn-0001", token="tok-mc"):
    t = svc.get("task", tid, "tok-principal")
    return svc.submit(cmd("task.request_admission", "task", tid,
                          {"operation_id": op, "bundle_digest": BD, "bundle_ref": ref("artifacts", "bundle-1", BD),
                           "fence_generation": t["fence_generation"]}, expected=t["revision"]), token)


def outbox(svc, op="OB-syn-0001"):
    return svc.get("outbox", op, "tok-principal")


def cancel(f, tid):
    return f.transition(tid, "canceled", reason="Owner said stop.", evidence=[ref("records", "evt-stop")])


def fence_ops(svc, tid):
    folder = svc.journal.root / "outbox"
    return sorted(p.stem for p in folder.glob("OB-fence-*.json")
                  if svc.get("outbox", p.stem, "tok-principal")["task_id"] == tid)


def test_e02_task_change_and_outbox_intent_are_one_journal_intent_and_nothing_is_sent(rig):
    svc, f, tid, h = rig
    assert request_admission(svc, tid, token="tok-cos")["detail"] == "only_the_lead_requests_admission"
    assert request_admission(svc, tid)["outcome"] == "accepted"
    intent = [r for r in svc.journal.entries() if r["t"] == "intent"][-1]
    assert sorted(w["path"] for w in intent["writes"]) == ["outbox/OB-syn-0001.json", f"tasks/{tid}.json"]
    assert outbox(svc)["state"] == "pending" and h.admissions() == []


def test_happy_path_attempt_recorded_before_send_then_receipt_with_method(rig):
    svc, f, tid, h = rig
    request_admission(svc, tid)
    assert drain(svc, D, h, "OB-syn-0001") == "acknowledged"
    ob = outbox(svc)
    assert [(a["observation"], a["method"], a["send_attempt"]) for a in ob["attempts"]] == \
        [("attempted", "drainer", 1), ("applied", "destination_answer", 1)]
    assert h.admissions() == ["OB-syn-0001"]


def test_s2_3_only_a_drainer_records_and_the_receipt_must_name_this_operation(rig):
    svc, f, tid, h = rig
    request_admission(svc, tid)
    approve_admission(svc, "OB-syn-0001")                                     # Robert's approval first
    ob = outbox(svc)
    as_lead = cmd("outbox.record", "outbox", "OB-syn-0001",
                  {"observation": "attempted", "destination_receipt": None, "code": None}, expected=ob["revision"])
    assert svc.submit(as_lead, "tok-mc")["detail"] == "only_a_drainer_records"
    svc.submit(dict(as_lead, command_id="cmd-d-att"), D)
    ob = outbox(svc)
    wrong = cmd("outbox.record", "outbox", "OB-syn-0001",
                {"observation": "applied", "code": "answer",
                 "destination_receipt": {"store": "harness", "id": "OB-syn-9999", "edition": "admitted", "digest": BD}},
                expected=ob["revision"])
    assert svc.submit(wrong, D)["detail"] == "receipt_not_for_this_operation"


def test_f02_destination_commits_but_the_answer_is_lost_then_lookup_settles_it_once(rig):
    svc, f, tid, h = rig
    request_admission(svc, tid)
    h.faults = ["lose_answer_after_commit"]
    assert drain(svc, D, h, "OB-syn-0001") == "unknown"
    assert drain(svc, D, h, "OB-syn-0001") == "acknowledged"
    assert outbox(svc)["attempts"][-1]["method"] == "destination_lookup"
    assert h.admissions() == ["OB-syn-0001"] and h.admits_seen == 1


def test_e03_failure_before_commit_then_absent_then_resent_under_the_same_id_with_a_new_attempt(rig):
    svc, f, tid, h = rig
    request_admission(svc, tid)
    h.faults = ["fail_before_commit"]
    assert drain(svc, D, h, "OB-syn-0001") == "unknown"
    assert drain(svc, D, h, "OB-syn-0001") == "pending"
    assert drain(svc, D, h, "OB-syn-0001") == "acknowledged"
    sends = [a["send_attempt"] for a in outbox(svc)["attempts"] if a["observation"] == "attempted"]
    assert sends == [1, 2] and h.admissions() == ["OB-syn-0001"]


def test_s2_1_a_late_send_after_lookup_said_absent_is_refused_so_the_record_stays_true(rig):
    svc, f, tid, h = rig
    request_admission(svc, tid)
    approve_admission(svc, "OB-syn-0001")                                     # Robert's approval first
    ob = outbox(svc)
    svc.submit(cmd("outbox.record", "outbox", "OB-syn-0001",                          # drainer A records attempt 1
                   {"observation": "attempted", "destination_receipt": None, "code": None},
                   expected=ob["revision"]), D)
    assert drain(svc, D, h, "OB-syn-0001") == "pending"                          # drainer B: lookup -> absent
    from services.partnership_tasks.outbox import HarnessRefused
    with pytest.raises(HarnessRefused, match="attempt_declared_absent"):              # A's send finally arrives
        h.admit("OB-syn-0001", BD, task_id=tid, fence_generation=0, attempt=1)
    assert drain(svc, D, h, "OB-syn-0001") == "acknowledged"                     # B resends as attempt 2
    assert h.admissions() == ["OB-syn-0001"]


def test_s1_1_fence_reaches_the_authority_first_then_a_stale_send_is_refused_there(rig):
    svc, f, tid, h = rig
    request_admission(svc, tid)
    approve_admission(svc, "OB-syn-0001")                                     # Robert's approval first
    ob = outbox(svc)
    svc.submit(cmd("outbox.record", "outbox", "OB-syn-0001",                          # stale drainer recorded attempt
                   {"observation": "attempted", "destination_receipt": None, "code": None},
                   expected=ob["revision"]), D)
    res = cancel(f, tid)
    assert res["outcome"] == "accepted" and res["open_effects"] == ["OB-syn-0001"]    # E07: may already exist
    fence_op = fence_ops(svc, tid)
    assert fence_op and drain(svc, D, h, fence_op[0]) == "acknowledged"          # authority's fence raised
    from services.partnership_tasks.outbox import HarnessRefused
    with pytest.raises(HarnessRefused, match="fenced"):
        h.admit("OB-syn-0001", BD, task_id=tid, fence_generation=0, attempt=1)
    assert h.admissions() == []


def test_s1_1_cancel_inside_the_authority_commit_window_is_reported_not_hidden(rig):
    """The cancel commits in the task service while the authority is admitting: the authority has not seen the fence,
    so the admission is real. The record says so: applied, and the cancel listed it as an open effect."""
    svc, f, tid, h = rig
    request_admission(svc, tid)
    seen = {}
    def cancel_mid_commit():
        h.before_commit = None
        seen["cancel"] = cancel(f, tid)
    h.before_commit = cancel_mid_commit
    assert drain(svc, D, h, "OB-syn-0001") == "acknowledged"
    assert seen["cancel"]["open_effects"] == ["OB-syn-0001"]
    assert svc.get("task", tid, "tok-principal")["state"] == "canceled"
    assert h.admissions() == ["OB-syn-0001"]                                          # actual, not hidden


def test_f02_a_stale_pending_entry_after_cancel_is_closed_and_never_sent(rig):
    svc, f, tid, h = rig
    request_admission(svc, tid)
    cancel(f, tid)
    assert outbox(svc)["state"] == "fenced"
    assert drain(svc, D, h, "OB-syn-0001") == "fenced" and h.admissions() == []


def test_s2_2_accept_waits_for_every_admission_to_settle_and_reopen_fences_pending_sends(rig):
    svc, f, tid, h = rig
    f.transition(tid, "working", evidence=[ref("harness", "pickup-1")])
    request_admission(svc, tid)
    current = f.ready_for_acceptance(tid)
    assert f.accept(tid, current)["detail"] == "effects_unreconciled"          # pending counts too
    drain(svc, D, h, "OB-syn-0001")
    assert f.accept(tid, current)["outcome"] == "accepted"
    t = svc.get("task", tid, "tok-principal")
    f.ok(cmd("task.reopen", "task", tid, {"reason": "Redo."}, expected=t["revision"]), "tok-mc")
    assert fence_ops(svc, tid)                                                         # reopen told the authority
    request_admission(svc, tid, op="OB-syn-0002")
    t = svc.get("task", tid, "tok-principal")
    f.ok(cmd("task.assign", "task", tid, {"role": "lead", "seat": "cos", "reason": "Handover."},
             expected=t["revision"]), "tok-principal")
    assert outbox(svc, "OB-syn-0002")["state"] == "fenced"


def test_s2_2_a_conflict_ends_only_by_the_owners_decision(rig):
    svc, f, tid, h = rig
    from core import durable_files as fsio
    other = "sha256:" + "f" * 64
    fsio.write_doc(h._doc("admissions", "OB-syn-0001"), {"bundle_digest": other, "task_id": tid, "fence": 0,
                   "receipt": {"store": "harness", "id": "OB-syn-0001", "edition": "admitted", "digest": other}})
    request_admission(svc, tid)
    assert drain(svc, D, h, "OB-syn-0001") == "conflict"
    ob = outbox(svc)
    abandon = cmd("outbox.record", "outbox", "OB-syn-0001",
                  {"observation": "abandoned", "destination_receipt": None, "code": "owner_reviewed"},
                  expected=ob["revision"])
    assert svc.submit(abandon, "tok-mc")["detail"] == "only_the_owner_abandons"
    assert svc.submit(dict(abandon, command_id="cmd-abandon-2"), "tok-principal")["outcome"] == "accepted"
    assert outbox(svc)["state"] == "abandoned"


def test_s2_4_no_new_admission_over_an_unknown_one(rig):
    svc, f, tid, h = rig
    request_admission(svc, tid)
    h.faults = ["lose_answer_after_commit"]
    drain(svc, D, h, "OB-syn-0001")
    h.faults = ["lookup_down"]
    assert drain(svc, D, h, "OB-syn-0001") == "unknown"                          # never retried blind
    assert request_admission(svc, tid, op="OB-syn-0002")["detail"] == "effects_unreconciled"


def test_e03_the_same_operation_id_with_another_digest_is_a_conflict_not_a_second_admission(rig):
    svc, f, tid, h = rig
    from core import durable_files as fsio
    other = "sha256:" + "f" * 64
    fsio.write_doc(h._doc("admissions", "OB-syn-0001"), {"bundle_digest": other, "task_id": tid, "fence": 0,
                   "receipt": {"store": "harness", "id": "OB-syn-0001", "edition": "admitted", "digest": other}})
    request_admission(svc, tid)
    assert drain(svc, D, h, "OB-syn-0001") == "conflict"


def test_harness_to_task_direction_resends_its_persisted_command_bytes_and_replays(rig):
    """The authority persists the exact command before sending and resends only those bytes (S2-5); the task service
    replays. Rebuilding the command after re-reading the task would be command_id_reused, which is why it must not."""
    svc, f, tid, h = rig
    t = svc.get("task", tid, "tok-principal")
    pickup = cmd("task.transition", "task", tid,
                 {"to": "working", "reason": None, "waiting": None, "evidence_refs": [ref("harness", "evt-pickup-1")],
                  "fence_generation": t["fence_generation"]}, expected=t["revision"], cid="harness-evt-pickup-1")
    from core import durable_files as fsio
    fsio.write_doc(h._doc("task_outbox", "harness-evt-pickup-1"), pickup)               # persisted before sending
    first = svc.submit(fsio.read_doc(h._doc("task_outbox", "harness-evt-pickup-1")), "tok-mc")
    again = svc.submit(fsio.read_doc(h._doc("task_outbox", "harness-evt-pickup-1")), "tok-mc")
    assert first["outcome"] == "accepted" and again["replayed"] is True
    rebuilt = dict(pickup, expected_revision=t["revision"] + 1)
    assert svc.submit(rebuilt, "tok-mc")["code"] == "command_id_reused"
    assert svc.receipt(pickup, "tok-mc")["answer"] == "recorded"                       # how it reconciles instead


def test_s2a_fence_operations_live_in_a_reserved_namespace(rig):
    svc, f, tid, h = rig
    assert request_admission(svc, tid, op="OB-fence-0123456789")["detail"] == "reserved_operation_id"
    request_admission(svc, tid)
    cancel(f, tid)
    [fop] = fence_ops(svc, tid)
    assert len(fop) == len("OB-fence-") + 40 and outbox(svc, fop)["operation"] == "set_fence"
    assert outbox(svc)["operation"] == "admit_bundle"                     # the admission record is untouched


def test_s2b_a_long_task_id_can_still_be_canceled_after_an_admission(tmp_path):
    svc = service(tmp_path / "tasks")
    f = Flow(svc)
    long_n = "L" * 60
    tid = f.promoted_task(n=long_n)
    request_admission(svc, tid)
    assert cancel(f, tid)["outcome"] == "accepted"
    assert fence_ops(svc, tid)


def test_two_fence_bumps_reach_the_authority_out_of_order_and_the_higher_wins(rig):
    svc, f, tid, h = rig
    request_admission(svc, tid)
    t = svc.get("task", tid, "tok-principal")
    f.ok(cmd("task.assign", "task", tid, {"role": "holder", "seat": "cos", "reason": "Step 1."},
             expected=t["revision"]), "tok-mc")
    t = svc.get("task", tid, "tok-principal")
    f.ok(cmd("task.assign", "task", tid, {"role": "holder", "seat": "mc", "reason": "Back."},
             expected=t["revision"]), "tok-mc")
    ops = sorted(fence_ops(svc, tid), key=lambda o: outbox(svc, o)["fence_generation"])
    assert [outbox(svc, o)["fence_generation"] for o in ops] == [1, 2]
    drain(svc, D, h, ops[1])
    drain(svc, D, h, ops[0])                                         # the late, lower one
    assert h.fence(tid) == 2


def test_b1_a_correction_must_be_in_the_requests_scope(tmp_path):
    svc = service(tmp_path / "tasks")
    f = Flow(svc)
    f.source("SE-syn-0001", "Personal ask.")
    f.request("RQ-syn-0001", "SE-syn-0001", "Ask.")
    cross = cmd("source_event.record", "source_event", "SE-syn-0002",
                {"source": {"store": "records", "id": "e2", "position": 2}, "content": "Cancel.",
                 "correction_of": "SE-syn-0001"}, scope="shared:build")
    assert svc.submit(cross, "tok-mc")["detail"] == "source_event"            # cannot even be recorded


def test_finding_2_another_platform_seat_with_outbox_record_cannot_record(rig, tmp_path):
    """Codex finding 2: a platform-origin binding granted outbox.record is still refused unless it is the drainer."""
    svc, f, tid, h = rig
    request_admission(svc, tid)
    approve_admission(svc, "OB-syn-0001")                                     # Robert's approval first
    ob = outbox(svc)
    record = cmd("outbox.record", "outbox", "OB-syn-0001",
                 {"observation": "attempted", "destination_receipt": None, "code": None}, expected=ob["revision"])
    refused = svc.submit(record, "tok-platform-recorder")      # platform origin, granted outbox.record, not the drainer
    assert refused["outcome"] == "refused" and refused["detail"] == "only_a_drainer_records"
    assert svc.submit(dict(record, command_id="cmd-d-ok"), D)["outcome"] == "accepted"


# ---- B4 execution design v0.5 M2-M3: an admission is sent only on Robert's own fresh, unspent approval of that card --

def test_v05_no_send_without_robert_approval_and_one_approval_is_one_send(rig):
    svc, f, tid, h = rig
    request_admission(svc, tid)
    assert drain(svc, D, h, "OB-syn-0001", approve=False) == "pending" and h.admissions() == []   # nothing sent
    assert approve_admission(svc, "OB-syn-0001")["outcome"] == "accepted"
    again = approve_admission(svc, "OB-syn-0001", event="SE-syn-ok-second")
    assert again["outcome"] == "refused" and again["detail"] == "already_approved"            # fresh one stands
    h.faults = ["fail_before_commit"]
    assert drain(svc, D, h, "OB-syn-0001", approve=False) == "unknown"
    assert outbox(svc)["approval"]["spent_at"] is not None                                    # spent by the send
    assert drain(svc, D, h, "OB-syn-0001", approve=False) == "pending"                        # lookup: absent
    assert drain(svc, D, h, "OB-syn-0001", approve=False) == "pending" and h.admissions() == []  # resend needs a new one
    assert drain(svc, D, h, "OB-syn-0001") == "acknowledged" and h.admissions() == ["OB-syn-0001"]


def test_v05_an_approval_names_exactly_this_card_from_robert_session_and_is_used_once(rig):
    import json as _json
    svc, f, tid, h = rig
    request_admission(svc, tid)
    ob = outbox(svc)
    def event(sid, content, token="tok-principal", store="portal-owner-action"):
        svc.submit(cmd("source_event.record", "source_event", sid,
                       {"source": {"store": store, "id": sid, "position": 1}, "content": content,
                        "correction_of": None}), token)
    def approve(sid, digest_=None, token="tok-principal"):
        o = outbox(svc)
        return svc.submit(cmd("outbox.approve", "outbox", "OB-syn-0001",
                              {"card_digest": digest_ or o["bundle_digest"], "approval_event_id": sid},
                              expected=o["revision"]), token)
    good = _json.dumps({"action": "admit", "operation_id": "OB-syn-0001", "card_digest": ob["bundle_digest"]})
    event("SE-syn-ok-a1", good)
    assert approve("SE-syn-ok-a1", token="tok-mc")["detail"] == "only_the_owner_approves"       # MC cannot
    other = "sha256:" + "0" * 64
    assert approve("SE-syn-ok-a1", digest_=other)["detail"] == "approval_digest_mismatch"
    event("SE-syn-ok-a2", _json.dumps({"action": "admit", "operation_id": "OB-syn-9999", "card_digest": ob["bundle_digest"]}))
    assert approve("SE-syn-ok-a2")["detail"] == "approval_not_for_this_card"                   # another operation
    event("SE-syn-ok-a3", good, store="records")
    assert approve("SE-syn-ok-a3")["detail"] == "approval_not_for_this_card"                   # not an owner action
    assert approve("SE-syn-ok-a1")["outcome"] == "accepted"
    assert drain(svc, D, h, "OB-syn-0001", approve=False) == "acknowledged"


def test_v05_an_approval_older_than_15_minutes_sends_nothing_and_robert_can_approve_again(tmp_path):
    from datetime import datetime, timedelta, timezone
    now = {"t": datetime(2026, 11, 4, 16, 0, tzinfo=timezone.utc)}
    svc = service(tmp_path / "tasks", clock=lambda: now["t"].isoformat())
    f = Flow(svc); tid = f.promoted_task(); h = FakeHarness(tmp_path / "harness")
    request_admission(svc, tid)
    assert approve_admission(svc, "OB-syn-0001", event="SE-syn-ok-first")["outcome"] == "accepted"
    now["t"] += timedelta(minutes=15, seconds=1)
    assert drain(svc, D, h, "OB-syn-0001", approve=False) == "pending" and h.admissions() == []   # expired: no send
    assert outbox(svc)["approval"]["spent_at"] is None
    again = approve_admission(svc, "OB-syn-0001", event="SE-syn-ok-second")
    assert again["outcome"] == "accepted"                                       # an expired approval may be replaced
    assert drain(svc, D, h, "OB-syn-0001", approve=False) == "acknowledged" and h.admissions() == ["OB-syn-0001"]


def test_v05_an_approval_event_counts_for_one_entry_only(rig):
    import json as _json
    svc, f, tid, h = rig
    request_admission(svc, tid)
    request_admission(svc, tid, op="OB-syn-0002")
    assert approve_admission(svc, "OB-syn-0001", event="SE-syn-ok-shared")["outcome"] == "accepted"
    ob2 = outbox(svc, "OB-syn-0002")
    res = svc.submit(cmd("outbox.approve", "outbox", "OB-syn-0002",
                         {"card_digest": ob2["bundle_digest"], "approval_event_id": "SE-syn-ok-shared"},
                         expected=ob2["revision"]), "tok-principal")
    assert res["outcome"] == "refused" and res["detail"] in ("approval_not_for_this_card", "approval_already_used")
