"""B2E: joined evidence for F13-F16 (spec v1.1 Q03-Q08), revised after REVIEW_B2E_INDEPENDENT_2026-11-04.

SYNTHETIC: the obligation store is the real CoS store in a temporary folder; monitor receipts, harness attempts and
billing lines are fixtures; the harness is the synthetic harness. No real monitor, provider or billing data is read,
and no charge attribution claim follows from these passes."""
from __future__ import annotations

import shutil

import pytest

from core.canonical_json import digest
from domains.cos.store import StoreWriter
from services.partnership_tasks.joins import charges, occurrence, restore_check
from services.partnership_tasks.outbox import drain_once
from services.partnership_tasks.service import DIRS
from tests.cos.test_store_writer import ENV, Stream, define, event, verified
from tests.partnership.fake_harness import FakeHarness
from tests.partnership.synth import Flow, cmd, ref, service, drain, approve_admission

OCC = "OBL-disk-weekly@2026-11-02"
EDITION = "OBL-disk-weekly@r1"           # the obligation definition's revision is the policy edition
WINDOW = ("2026-11-02T10:40:00+00:00", "2026-11-02T12:40:00+00:00")
MONITORS = {"disk-monitor"}
LATER = "2026-11-04T00:00:00+00:00"


@pytest.fixture
def cos(tmp_path):
    store = StoreWriter(tmp_path / "cos", ENV, verify_robert_source=verified)
    define(store, "OBL-disk-weekly", what="Weekly disk check",
           recurrence={"kind": "weekly", "weekday": 0, "tz": "America/Chicago"},
           reminder_window={"from": "04:40", "to": "06:40"})                       # 10:40-12:40 UTC on 2 Nov
    assert Stream(store).send(event("ev-open-1102", "opened", 0, occ=OCC))["outcome"] == "accepted"
    return store


@pytest.fixture
def rig(tmp_path):
    """The task service holding the duty task: duty passes from cos to mc-ops in the middle of the window."""
    now = {"t": "2026-10-20T00:00:00+00:00"}
    svc = service(tmp_path / "tasks", clock=lambda: now["t"])
    f = Flow(svc)
    tid = f.promoted_task()
    for seat, when in (("cos", "2026-10-20T00:00:00+00:00"), ("mc-ops", "2026-11-02T11:30:00+00:00")):
        now["t"] = when
        t = svc.get("task", tid, "tok-principal")
        f.ok(cmd("task.assign", "task", tid, {"role": "duty", "seat": seat, "reason": "Disk check duty."},
                 expected=t["revision"]), "tok-mc")
    return svc, f, tid


def monitor(heartbeats=(), receipts=()):
    return {"expected": [{"occurrence_id": OCC, "window_start": WINDOW[0], "window_end": WINDOW[1],
                          "policy_edition": EDITION, "threshold": {"warn_below_gib": 25, "urgent_below_gib": 10}}],
            "receipts": list(receipts), "heartbeats": list(heartbeats)}


def check(at, seat="disk-monitor", edition=EDITION, measured=None, result="ok"):
    return {"occurrence_id": OCC, "kind": "check", "at": at, "seat": seat, "policy_edition": edition,
            "measured": {"free_gib": 31.0} if measured is None else measured, "threshold_result": result}


def run(rig, cos, mon, now=LATER, token="tok-principal"):
    svc, f, tid = rig
    return occurrence(svc, token, OCC, cos_store=cos, monitor=mon, duty_task_id=tid,
                      authorized_monitor_seats=MONITORS, now=now)


HB_OK = [{"at": "2026-11-02T11:00:00+00:00", "ok": True}]


# ---- F14 ------------------------------------------------------------------------------------------------------------

def test_f14_missed_check_names_every_owner_across_the_window(rig, cos):
    out = run(rig, cos, monitor(HB_OK))
    assert out["status"] == "missed"
    assert [o["seat"] for o in out["owners_during_window"]] == ["cos", "mc-ops"]        # handover mid-window
    assert any("changed hands" in g for g in out["gaps"])
    assert {"store": "obligations", "status": "verified"} in out["coverage"]


def test_f14_monitor_outage_is_a_gap_never_health(rig, cos):
    out = run(rig, cos, monitor([{"at": "2026-10-30T11:00:00+00:00", "ok": True},
                                 {"at": "2026-11-02T11:00:00+00:00", "ok": False}]))
    assert out["status"] == "unknown"
    assert out["monitor"]["last_successful_check"] == "2026-10-30T11:00:00+00:00"


@pytest.mark.parametrize("bad", [check("2026-11-02T11:00:00+00:00", seat="random-agent"),
                                 check("2026-11-02T11:00:00+00:00", edition="disk-policy-ed-1"),
                                 check("2026-11-02T11:00:00+00:00", measured={}),
                                 check("2026-11-02T11:00:00+00:00", result="error")])
def test_f14_untrustworthy_receipts_never_make_it_fulfilled(rig, cos, bad):
    out = run(rig, cos, monitor(HB_OK, [bad]))
    assert out["status"] == "unverified" and len(out["untrusted_receipts"]) == 1


def test_f14_on_time_late_open_and_early_receipts(rig, cos):
    assert run(rig, cos, monitor(HB_OK, [check("2026-11-02T11:05:00+00:00")]))["status"] == "fulfilled"
    assert run(rig, cos, monitor(HB_OK, [check("2026-11-02T13:30:00+00:00")]))["status"] == "late"
    assert run(rig, cos, monitor(), now="2026-11-02T11:00:00+00:00")["status"] == "open"
    early = run(rig, cos, monitor(HB_OK, [check("2026-11-01T11:00:00+00:00")]))
    assert early["status"] == "missed" and any("before the window" in g for g in early["gaps"])


def test_f14_breach_needs_an_escalation_with_a_delivered_notice(rig, cos):
    breach = check("2026-11-02T11:05:00+00:00", measured={"free_gib": 8.1}, result="breach")
    out = run(rig, cos, monitor(HB_OK, [breach]))
    assert any("was not escalated" in g for g in out["gaps"])
    esc = {"occurrence_id": OCC, "kind": "escalation", "at": "2026-11-02T11:06:00+00:00", "seat": "disk-monitor",
           "notice_operation_id": "OB-notice-missing"}
    out = run(rig, cos, monitor(HB_OK, [breach, esc]))
    assert out["escalations"][0]["notice_state"] is None and any("no delivered notice" in g for g in out["gaps"])


def test_f14_an_occurrence_unknown_to_the_obligation_store_is_not_established(rig, cos):
    svc, f, tid = rig
    mon = monitor(HB_OK)
    mon["expected"][0]["occurrence_id"] = "OBL-disk-weekly@2026-11-09"
    out = occurrence(svc, "tok-principal", "OBL-disk-weekly@2026-11-09", cos_store=cos, monitor=mon,
                     duty_task_id=tid, authorized_monitor_seats=MONITORS, now=LATER)
    assert {"store": "obligations", "status": "absent"} in out["coverage"]


def test_s2_4_the_monitors_own_expectation_never_defines_the_window(rig, cos):
    mon = monitor(HB_OK, [check("2026-11-02T13:30:00+00:00")])
    mon["expected"][0]["window_end"] = "2026-11-02T14:00:00+00:00"                  # the monitor stretches it
    out = run(rig, cos, mon)
    assert out["status"] == "late" and any("differs from the obligation definition" in g for g in out["gaps"])


def test_f14_q07_a_caller_without_the_scope_gets_nothing(rig, cos):
    assert run(rig, cos, monitor(HB_OK), token="tok-peer") is None


# ---- F13 ------------------------------------------------------------------------------------------------------------

def attempt(aid, rid, status="completed", retry_of=None, task="TK-syn-0001", currency="USD", amount="0.52", **over):
    a = {"attempt_id": aid, "operation_id": "OB-syn-0001", "task_id": task, "executor_seat": "codex",
         "provider": "synthetic-provider", "route": "synthetic/model-x", "status": status, "retry_of": retry_of,
         "grant_id": "grant-syn-spend", "reservation_id": f"rsv-{aid}", "reserved": {"amount": "0.50", "unit": "USD"},
         "reported_usage": {"tokens": 52000}, "estimate": {"amount": amount, "currency": currency,
                                                           "rate_version": "rates-syn-3"},
         "provider_request_id": rid}
    a.update(over)
    return a


GRANTS = {"grant-syn-spend": {"cap": "5.00", "currency": "USD", "unit": "USD/day"}}


def test_f13_failed_and_retried_attempts_estimate_vs_billed_and_unallocated_kept_apart(rig):
    svc, f, tid = rig
    atts = [attempt("att-1", "req-a1", status="failed", amount="0.41"), attempt("att-2", "req-a2", retry_of="att-1"),
            attempt("att-9", "req-z9", task="TK-other-0009")]
    billing = {"grants": GRANTS, "invoice_lines": [
        {"line_id": "inv-1", "amount": "0.44", "currency": "USD", "provider_request_id": "req-a1"},
        {"line_id": "inv-2", "amount": "0.55", "currency": "USD", "provider_request_id": "req-a2"},
        {"line_id": "inv-2", "amount": "0.55", "currency": "USD", "provider_request_id": "req-a2"},   # exact repeat
        {"line_id": "inv-3", "amount": "-0.40", "currency": "USD", "provider_request_id": None},
        {"line_id": "inv-4", "amount": "1.10", "currency": "USD", "provider_request_id": "req-z9"}]}
    out = charges(svc, "tok-principal", tid, attempts=atts, billing=billing)
    assert [(a["attempt_id"], a["status"]) for a in out["attempts"]] == [("att-1", "failed"), ("att-2", "completed")]
    assert out["totals"] == {"estimated_by_currency": {"USD": "0.93"}, "billed_allocated_by_currency": {"USD": "0.99"}}
    assert out["unallocated"]["account_level"] == {"count": 1, "by_currency": {"USD": "-0.40"}}   # principal's view
    assert "inv-4" not in str(out) and out["authorized_caps"]["grant-syn-spend"]["cap"] == "5.00"
    lead_view = charges(svc, "tok-mc", tid, attempts=atts, billing=billing)
    assert lead_view["unallocated"]["shown"] is False and "req-z9" not in str(lead_view)  # S2-A


def test_f13_currencies_are_never_mixed_and_conflicting_duplicates_are_named(rig):
    svc, f, tid = rig
    atts = [attempt("att-1", "req-a1", currency="EUR", amount="0.40"), attempt("att-2", "req-a2")]
    billing = {"grants": GRANTS, "invoice_lines": [
        {"line_id": "inv-1", "amount": "0.44", "currency": "EUR", "provider_request_id": "req-a1"},
        {"line_id": "inv-2", "amount": "0.55", "currency": "USD", "provider_request_id": "req-a2"},
        {"line_id": "inv-2", "amount": "5.55", "currency": "USD", "provider_request_id": "req-a2"}]}
    out = charges(svc, "tok-principal", tid, attempts=atts, billing=billing)
    assert out["totals"]["estimated_by_currency"] == {"EUR": "0.40", "USD": "0.52"}
    assert out["totals"]["billed_allocated_by_currency"] == {"EUR": "0.44"}            # inv-2 excluded: conflict
    assert any("inv-2 appears with different contents" in g for g in out["gaps"])


def test_f13_shared_request_ids_and_missing_reservation_links_are_gaps(rig):
    svc, f, tid = rig
    atts = [attempt("att-1", "req-x"), attempt("att-2", "req-x", reservation_id=None)]
    out = charges(svc, "tok-principal", tid, attempts=atts,
                  billing={"grants": GRANTS, "invoice_lines": [
                      {"line_id": "inv-1", "amount": "0.5", "currency": "USD", "provider_request_id": "req-x"}]})
    assert out["totals"]["billed_allocated_by_currency"] == {}
    assert any("share request id req-x" in g for g in out["gaps"])
    assert any("att-2: no reservation link" in g for g in out["gaps"])
    assert charges(svc, "tok-peer", tid, attempts=atts, billing={}) is None            # out of scope


# ---- F15 ------------------------------------------------------------------------------------------------------------

def test_f15_the_joined_query_shows_divergence_payload_effect_review_and_fencing(tmp_path):
    from services.partnership_tasks.joins import from_charge, reconstruct_joined
    svc = service(tmp_path / "t")
    f = Flow(svc)
    f.source("SE-syn-0001", "Open an issue about the signup page.")
    f.interpret("IN-syn-0001", ["SE-syn-0001"], "Open an issue about the login page.")
    f.adopt("IN-syn-0001")
    f.request("RQ-syn-0001", "SE-syn-0001", "Issue: login page.", iid="IN-syn-0001")
    f.promote("RQ-syn-0001", "TK-syn-0001")
    f.transition("TK-syn-0001", "working", evidence=[ref("harness", "pickup-1")])
    pd = digest({"title": "Login page broken"})
    t = svc.get("task", "TK-syn-0001", "tok-principal")
    f.ok(cmd("task.request_admission", "task", "TK-syn-0001",
             {"operation_id": "OB-syn-issue-1", "bundle_digest": pd, "bundle_ref": ref("artifacts", "issue", pd),
              "fence_generation": t["fence_generation"]}, expected=t["revision"]), "tok-mc")
    h = FakeHarness(tmp_path / "h")
    assert drain(svc, "tok-drainer", h, "OB-syn-issue-1") == "acknowledged"     # the wrong issue was written
    f.ready_for_acceptance("TK-syn-0001", tag="draft")                               # reviewed under the misreading
    f.source("SE-syn-0002", "No, the signup page.", correction_of="SE-syn-0001")
    f.interpret("IN-syn-0002", ["SE-syn-0001"], "Open an issue about the signup page.",
                corrections=["SE-syn-0002"], supersedes="IN-syn-0001")
    t = svc.get("task", "TK-syn-0001", "tok-principal")
    pd2 = digest({"title": "Signup page broken"})
    stale = cmd("task.request_admission", "task", "TK-syn-0001",
                {"operation_id": "OB-syn-issue-2", "bundle_digest": pd2, "bundle_ref": ref("artifacts", "issue2", pd2),
                 "fence_generation": 0}, expected=t["revision"])
    assert t["waiting"] == {"reason": "authorization", "who_can_unblock": "mc"}      # fenced by the supersession
    assert svc.submit(stale, "tok-mc")["code"] == "transition_not_allowed"           # nothing more is dispatched
    atts = [{"attempt_id": "att-1", "operation_id": "OB-syn-issue-1", "task_id": "TK-syn-0001", "executor_seat": "mc",
             "provider": "synthetic-issues", "route": "synthetic/issues", "status": "completed", "retry_of": None,
             "grant_id": "grant-syn-issues", "reservation_id": "rsv-1", "reserved": None, "reported_usage": None,
             "estimate": None, "provider_request_id": "req-issue-1"}]
    billing = {"grants": {"grant-syn-issues": {"cap": "0", "currency": "USD"}}, "invoice_lines": []}
    out = from_charge(svc, "tok-principal", "req-issue-1", attempts=atts, billing=billing, harness=h)
    [div] = out["divergence"]
    assert (div["interpretation_id"], div["corrections"]) == ("IN-syn-0001", ["SE-syn-0002"])
    correction_pos = [x["position"] for x in out["timeline"] if x["kind"] == "source_event.record"
                      and "source_events/SE-syn-0002.json" in x["targets"]][0]
    assert min(div["recorded_at_positions"]) < correction_pos                        # first recorded before the correction
    assert out["effects"] == [{"operation_id": "OB-syn-issue-1", "payload_digest": pd, "harness": "applied"}]
    assert [r["applicable_to_current"] for r in out["reviews"]] == [False]           # review of the misreading
    assert out["costs"]["status"] == "synthetic_joined"
    assert {"store": "harness", "status": "synthetic_joined"} in out["coverage"]
    assert from_charge(svc, "tok-peer", "req-issue-1", attempts=atts, billing=billing, harness=h) is None
    from services.partnership_tasks import contracts
    assert contracts.errors("reconstruction", "answer", out) == []                     # joined output pinned
    dup = atts + [dict(atts[0], attempt_id="att-x", task_id="TK-other-0001")]
    assert from_charge(svc, "tok-principal", "req-issue-1", attempts=dup, billing=billing)["answer"] == "ambiguous"
    assert from_charge(svc, "tok-peer", "req-issue-1", attempts=dup, billing=billing) is None   # no existence leak
    shared = charges(svc, "tok-principal", "TK-syn-0001", attempts=dup, billing={"grants": {}, "invoice_lines": [
        {"line_id": "inv-1", "amount": "1", "currency": "USD", "provider_request_id": "req-issue-1"}]})
    assert shared["totals"]["billed_allocated_by_currency"] == {} and "TK-other" not in str(shared)
    assert reconstruct_joined(svc, "tok-principal", "task", "TK-syn-0001")["costs"]["status"] == "no_cost_evidence"


# ---- F16 ------------------------------------------------------------------------------------------------------------

DOC_DIRS = tuple(DIRS.values())


def test_f16_restore_names_lost_history_effects_unknown_to_it_and_never_writes_the_restore(tmp_path):
    svc = service(tmp_path / "live")
    f = Flow(svc)
    tid = f.promoted_task()
    early = svc.journal.head()
    shutil.copytree(tmp_path / "live", tmp_path / "backup")
    t = svc.get("task", tid, "tok-principal")
    bd = digest({"b": 1})
    f.ok(cmd("task.request_admission", "task", tid, {"operation_id": "OB-syn-0001", "bundle_digest": bd,
                                                     "bundle_ref": ref("artifacts", "b", bd),
                                                     "fence_generation": t["fence_generation"]},
             expected=t["revision"]), "tok-mc")
    h = FakeHarness(tmp_path / "h")
    drain(svc, "tok-drainer", h, "OB-syn-0001")
    late = svc.journal.head()
    before = sorted(p.read_bytes() for p in (tmp_path / "backup").rglob("*") if p.is_file())
    out = restore_check(tmp_path / "backup", doc_dirs=DOC_DIRS, exported_heads=[early, late], harness=h)
    assert out["journal"] == "verified" and out["anchored"] is True
    assert [x["reproduced"] for x in out["heads"]] == [True, False]
    assert out["unknown_effects"] == ["OB-syn-0001"]                                    # effect the restore forgot
    assert sorted(p.read_bytes() for p in (tmp_path / "backup").rglob("*") if p.is_file()) == before   # no writes
    # The forgotten effect cannot be duplicated: the same operation id is deduplicated by the harness.
    assert h.admit("OB-syn-0001", bd, task_id=tid, fence_generation=0, attempt=9)["state"] == "duplicate"


def test_f16_unanchored_torn_and_unavailable_cases_are_named(tmp_path):
    svc = service(tmp_path / "live")
    Flow(svc).promoted_task()
    out = restore_check(tmp_path / "live", doc_dirs=DOC_DIRS, exported_heads=[], harness=None)
    assert out["journal"] == "verified_unanchored" and any("unanchored" in g for g in out["gaps"])
    with open(tmp_path / "live" / "journal" / "journal.jsonl", "ab") as fh:
        fh.write(b'{"t":"intent"')
    torn = restore_check(tmp_path / "live", doc_dirs=DOC_DIRS, exported_heads=[], harness=None)
    assert any("torn line" in g for g in torn["gaps"])
    (tmp_path / "live" / "tasks" / "TK-syn-0001.json").write_text('{"tampered": true}\n')
    assert restore_check(tmp_path / "live", doc_dirs=DOC_DIRS, exported_heads=[], harness=None)["journal"] == "damaged"


def test_f16_an_acknowledged_effect_missing_from_the_harness_is_named(tmp_path):
    svc = service(tmp_path / "live")
    f = Flow(svc)
    tid = f.promoted_task()
    t = svc.get("task", tid, "tok-principal")
    bd = digest({"b": 2})
    f.ok(cmd("task.request_admission", "task", tid, {"operation_id": "OB-syn-0002", "bundle_digest": bd,
                                                     "bundle_ref": ref("artifacts", "b", bd),
                                                     "fence_generation": t["fence_generation"]},
             expected=t["revision"]), "tok-mc")
    drain(svc, "tok-drainer", FakeHarness(tmp_path / "h-old"), "OB-syn-0002")
    head = svc.journal.head()
    out = restore_check(tmp_path / "live", doc_dirs=DOC_DIRS, exported_heads=[head],
                        harness=FakeHarness(tmp_path / "h-restored-empty"))
    assert any("acknowledged in the journal but absent in the harness" in g for g in out["gaps"])


def test_f16_a_pending_entry_already_applied_in_the_harness_is_named(tmp_path):
    svc = service(tmp_path / "live")
    f = Flow(svc)
    tid = f.promoted_task()
    t = svc.get("task", tid, "tok-principal")
    bd = digest({"b": 3})
    f.ok(cmd("task.request_admission", "task", tid, {"operation_id": "OB-syn-0003", "bundle_digest": bd,
                                                     "bundle_ref": ref("artifacts", "b", bd),
                                                     "fence_generation": t["fence_generation"]},
             expected=t["revision"]), "tok-mc")
    h = FakeHarness(tmp_path / "h")
    h.admit("OB-syn-0003", bd, task_id=tid, fence_generation=0, attempt=1)          # landed, journal never knew
    out = restore_check(tmp_path / "live", doc_dirs=DOC_DIRS, exported_heads=[svc.journal.head()], harness=h)
    assert any("pending in the journal but already applied" in g for g in out["gaps"])
