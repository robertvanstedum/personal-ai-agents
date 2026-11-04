"""B1C/B1D: the task service's authority, lifecycle, promotion and correction fencing.

SYNTHETIC (tests/partnership/synth.py). Acceptance IDs: F01 (promotion crash, duplicate/different digest), F03
(correction before classification and after persistence), F06 (synthetic seats only: platform/peer text, expired
grant, read scope refusal), F07 (via the journal), F11 (no CoS/Records/harness import). Requirement IDs in names."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import durable_files as fsio
from services.partnership_tasks import contracts
from tests.partnership.synth import GRANT_REF, Flow, cmd, ref, service

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def svc(tmp_path):
    return service(tmp_path)


@pytest.fixture
def flow(svc):
    return Flow(svc)


def stored(svc, kind, id_):
    return svc.get(kind, id_, "tok-principal")


# ---- A02: identity comes from the credential ----------------------------------------------------------------------

@pytest.mark.parametrize("token,code", [("nope", "not_authenticated"), ("tok-expired", "not_authenticated"),
                                        ("tok-revoked", "not_authenticated"), (None, "not_authenticated")])
def test_a02_unknown_expired_or_revoked_tokens_write_nothing(svc, token, code):
    c = cmd("source_event.record", "source_event", "SE-syn-0001",
            {"source": {"store": "records", "id": "e", "position": 1}, "content": "x", "correction_of": None})
    assert svc.submit(c, token)["code"] == code
    assert stored(svc, "source_event", "SE-syn-0001") is None


def test_a02_provenance_is_the_binding_not_the_payload(svc, flow):
    flow.source("SE-syn-0001", "Hello", token="tok-platform")
    rec = stored(svc, "source_event", "SE-syn-0001")
    assert rec["producer"] == "platform" and rec["provenance"]["origin"] == "platform"
    completed = [r for r in svc.journal.entries() if r["t"] == "completed"][-1]
    assert (completed["actor_seat"], completed["origin"], completed["binding_id"]) == ("platform", "platform",
                                                                                       "bind-platform")


def test_a02_scope_operation_and_environment_come_from_the_binding(svc, flow):
    flow.source("SE-syn-0001", "x")
    worker_tries = cmd("request.record", "request", "RQ-syn-0001",
                       {"source_event_id": "SE-syn-0001", "intent_text": "x", "interpretation_ref": None,
                        "responder": "worker-a"})
    assert svc.submit(worker_tries, "tok-worker")["detail"] == "operation"
    other_scope = cmd("source_event.record", "source_event", "SE-syn-0002",
                      {"source": {"store": "records", "id": "e", "position": 2}, "content": "x", "correction_of": None},
                      scope="shared:build")
    assert svc.submit(other_scope, "tok-cos")["detail"] == "scope"
    wrong_env = cmd("source_event.record", "source_event", "SE-syn-0003",
                    {"source": {"store": "records", "id": "e", "position": 3}, "content": "x", "correction_of": None},
                    environment="prod")
    assert svc.submit(wrong_env, "tok-cos")["code"] == "wrong_environment"


def test_d05_same_id_same_command_replays_from_any_process_and_a_changed_one_is_refused(tmp_path, flow, svc):
    c = cmd("source_event.record", "source_event", "SE-syn-0001",
            {"source": {"store": "records", "id": "e", "position": 1}, "content": "x", "correction_of": None})
    first = svc.submit(c, "tok-principal")
    other_process = service(tmp_path)
    assert other_process.submit(c, "tok-principal") == dict(first, replayed=True)
    changed = dict(c, target={"type": "source_event", "id": "SE-syn-0002"})
    assert other_process.submit(changed, "tok-principal")["code"] == "command_id_reused"
    assert svc.submit(c, "tok-cos")["code"] == "command_id_reused"          # same id from another seat
    assert other_process.receipt(c, "tok-principal")["answer"] == "recorded"
    assert other_process.receipt(changed, "tok-principal")["answer"] == "different"


# ---- Q02: interpretation, adoption and authority are separate -------------------------------------------------------

def test_q02_interpretation_records_who_read_it_with_which_runtime_and_context(svc, flow):
    flow.source("SE-syn-0001", "Find the sales JDs in my target list.")
    flow.interpret("IN-syn-0001", ["SE-syn-0001"], "List sales roles with JD text and coverage.")
    rec = stored(svc, "interpretation", "IN-syn-0001")
    assert rec["interpreter"] == {"seat": "cos", "session_id": "syn-sess",
                                  "runtime": {"kind": "model", "model": "synthetic-model", "provider": "synthetic",
                                              "build": None, "config_version": "syn-1"}}
    assert rec["disposition"] == "proposed" and rec["authority"] == {"kind": "none", "ref": None}
    assert contracts.errors("records", "interpretation", rec) == []


def test_q02_an_agent_cannot_record_owner_confirmation_only_the_owner_session_can(svc, flow):
    flow.source("SE-syn-0001", "Do it.")
    flow.interpret("IN-syn-0001", ["SE-syn-0001"], "Do the thing.")
    owner_ref = {"kind": "owner_confirmation", "ref": ref("records", "evt-confirm-1")}
    attempt = cmd("interpretation.dispose", "interpretation", "IN-syn-0001",
                  {"disposition": "adopted", "authority": owner_ref}, expected=1)
    assert svc.submit(attempt, "tok-cos")["detail"] == "owner_confirmation_needs_owner_session"
    assert svc.submit(attempt, "tok-platform")["detail"] == "platform_or_peer_cannot_carry_authority"
    assert svc.submit(dict(attempt, command_id="cmd-own-1"), "tok-principal")["outcome"] == "accepted"


def test_q02_a_grant_must_cover_the_adopting_seat_scope_and_still_be_in_force(svc, flow):
    flow.source("SE-syn-0001", "Do it.")
    flow.interpret("IN-syn-0001", ["SE-syn-0001"], "Do the thing.")
    for grant in ("grant-syn-expired", "grant-unknown"):
        c = cmd("interpretation.dispose", "interpretation", "IN-syn-0001",
                {"disposition": "adopted", "authority": {"kind": "existing_grant", "ref": ref("grants", grant)}},
                expected=1)
        assert svc.submit(c, "tok-cos")["detail"] == "grant_does_not_cover"
    worker_grant = cmd("interpretation.dispose", "interpretation", "IN-syn-0001",
                       {"disposition": "adopted", "authority": {"kind": "existing_grant", "ref": GRANT_REF}},
                       expected=1)
    assert svc.submit(worker_grant, "tok-mc")["outcome"] == "accepted"      # a grant naming the seat works


def test_q02_adoption_without_authority_cannot_promote(svc, flow):
    flow.source("SE-syn-0001", "Draft it.")
    flow.interpret("IN-syn-0001", ["SE-syn-0001"], "Draft.")
    flow.adopt("IN-syn-0001", authority={"kind": "none", "ref": None})
    flow.request("RQ-syn-0001", "SE-syn-0001", "Draft.", iid="IN-syn-0001")
    res = svc.submit(cmd("request.promote", "request", "RQ-syn-0001",
                         {"task_id": "TK-syn-0001", "lead": "mc", "holder": "mc", "outcome": "o", "done_when": "d",
                          "return_window": None, "parent_ref": None}, expected=1), "tok-cos")
    assert res["detail"] == "adoption_is_not_authority"
    assert stored(svc, "task", "TK-syn-0001") is None


# ---- D02/D03/J1-shaped request history ------------------------------------------------------------------------------

def test_d03_quick_request_keeps_coverage_and_results_and_refinement_keeps_history(svc, flow):
    flow.source("SE-syn-0001", "Find the sales JDs in my target list.")
    flow.request("RQ-syn-0001", "SE-syn-0001", "Sales JDs in the target list.")
    answer = {"result_refs": [ref("artifacts", "lookup-1", edition="1")],
              "coverage": {"sources": [{"ref": ref("artifacts", "target-list"), "status": "searched"},
                                       {"ref": ref("external", "jd-archive"), "status": "unavailable"}],
                           "gaps": ["JD archive unavailable."]}, "fence_generation": 0}
    early = svc.submit(cmd("request.answer", "request", "RQ-syn-0001", answer, expected=1), "tok-cos")
    assert early["code"] == "transition_not_allowed"                          # recorded -> active -> answered
    flow.ok(cmd("request.set_state", "request", "RQ-syn-0001", {"state": "active", "reason": None}, expected=1),
            "tok-cos")
    flow.ok(cmd("request.answer", "request", "RQ-syn-0001", answer, expected=2), "tok-cos")
    flow.source("SE-syn-0002", "Only leadership roles.")
    flow.ok(cmd("request.refine", "request", "RQ-syn-0001",
                {"source_event_id": "SE-syn-0002", "intent_text": "Sales leadership JDs.", "interpretation_ref": None},
                expected=3), "tok-cos")
    rec = stored(svc, "request", "RQ-syn-0001")
    assert rec["state"] == "active" and rec["intent"] == {"text": "Sales leadership JDs.", "revision": 2,
                                                         "source_event_id": "SE-syn-0002"}
    assert rec["refinements"][0]["result_refs"] == answer["result_refs"]
    assert rec["refinements"][0]["coverage"]["gaps"] == ["JD archive unavailable."]
    assert rec["task_ref"] is None                                             # no promotion, no open-list item
    stale = cmd("request.answer", "request", "RQ-syn-0001", answer, expected=4)
    assert svc.submit(stale, "tok-cos")["code"] == "fenced"                    # answer to the old edition


def test_d02_one_intent_one_promotion_and_links_both_ways(svc, flow):
    tid = flow.promoted_task()
    req, task = stored(svc, "request", "RQ-syn-0001"), stored(svc, "task", tid)
    assert req["state"] == "promoted" and req["task_ref"] == tid and task["request_ref"] == "RQ-syn-0001"
    assert [(a["role"], a["seat"]) for a in task["assignments"]] == [("lead", "mc"), ("holder", "mc")]
    again = cmd("request.promote", "request", "RQ-syn-0001",
                {"task_id": "TK-syn-0099", "lead": "mc", "holder": "mc", "outcome": "o", "done_when": "d",
                 "return_window": None, "parent_ref": None}, expected=req["revision"])
    assert svc.submit(again, "tok-cos")["outcome"] == "refused"
    assert stored(svc, "task", "TK-syn-0099") is None


@pytest.mark.parametrize("fail_on_write", [0, 1])
def test_f01_service_promotion_crash_at_either_document_ends_in_one_linked_task(tmp_path, monkeypatch, fail_on_write):
    svc = service(tmp_path)
    flow = Flow(svc)
    flow.source("SE-syn-0001", "Draft it.")
    flow.interpret("IN-syn-0001", ["SE-syn-0001"], "Draft.")
    flow.adopt("IN-syn-0001")
    flow.request("RQ-syn-0001", "SE-syn-0001", "Draft.", iid="IN-syn-0001")
    real, calls = fsio.write_doc, {"n": 0}
    def crashing(path, record):
        if calls["n"] == fail_on_write:
            raise OSError("simulated crash")
        calls["n"] += 1
        real(path, record)
    monkeypatch.setattr(fsio, "write_doc", crashing)
    promote = cmd("request.promote", "request", "RQ-syn-0001",
                  {"task_id": "TK-syn-0001", "lead": "mc", "holder": "mc", "outcome": "o", "done_when": "d",
                   "return_window": None, "parent_ref": None}, expected=1, cid="cmd-promote-1")
    assert svc.submit(promote, "tok-cos")["code"] == "store_unavailable"
    monkeypatch.setattr(fsio, "write_doc", real)
    restarted = service(tmp_path)
    retry = restarted.submit(promote, "tok-cos")
    assert retry["outcome"] == "accepted" and retry["replayed"] is True
    assert stored(restarted, "request", "RQ-syn-0001")["task_ref"] == "TK-syn-0001"
    assert stored(restarted, "task", "TK-syn-0001")["request_ref"] == "RQ-syn-0001"
    assert sorted(p.name for p in (tmp_path / "tasks").iterdir() if not p.name.startswith(".")) == ["TK-syn-0001.json"]


# ---- D01/D04/E07: lead, holder, acceptance ----------------------------------------------------------------------

def test_e07_worker_done_is_submitted_not_accepted_and_only_the_lead_accepts_the_exact_reviewed_set(svc, flow):
    tid = flow.promoted_task()
    assert flow.transition(tid, "working", evidence=[ref("harness", "attempt-1")])["outcome"] == "accepted"
    current = flow.ready_for_acceptance(tid)                                   # mc submits, cos reviews
    assert stored(svc, "task", tid)["state"] == "working"                     # submitted is not accepted
    assert flow.accept(tid, current, token="tok-worker")["outcome"] == "refused"
    assert flow.accept(tid, current, token="tok-cos")["detail"] == "only_the_lead_accepts"
    assert flow.accept(tid, "sha256:" + "b" * 64)["detail"] == "not_the_current_artifact_set"
    assert flow.accept(tid, current, fence=99)["code"] == "fenced"
    assert flow.accept(tid, current)["outcome"] == "accepted"
    done = stored(svc, "task", tid)
    assert done["state"] == "completed" and done["acceptances"][-1]["status"] == "current"
    assert done["acceptances"][-1]["artifact_set_digest"] == current
    flow.ok(cmd("task.reopen", "task", tid, {"reason": "New draft needed."}, expected=done["revision"]), "tok-mc")
    reopened = stored(svc, "task", tid)
    assert reopened["state"] == "submitted" and reopened["acceptances"][0]["status"] == "superseded"
    assert reopened["fence_generation"] == done["fence_generation"] + 1


def test_d01_lead_changes_only_by_the_lead_or_owner_and_history_keeps_who_owed_it_when(svc, flow):
    tid = flow.promoted_task()
    t = stored(svc, "task", tid)
    take = cmd("task.assign", "task", tid, {"role": "lead", "seat": "cos", "reason": "taking over"}, expected=t["revision"])
    assert svc.submit(take, "tok-cos")["detail"] == "may_not_assign_lead"
    flow.ok(dict(take, command_id="cmd-assign-owner"), "tok-principal")
    t2 = stored(svc, "task", tid)
    leads = [a for a in t2["assignments"] if a["role"] == "lead"]
    assert [(a["seat"], a["to_revision"]) for a in leads] == [("mc", t2["revision"]), ("cos", None)]
    assert t2["fence_generation"] == t["fence_generation"] + 1               # stale execution fenced
    stale = flow.transition(tid, "working", evidence=[ref("harness", "a-1")], fence=t["fence_generation"])
    assert stale["code"] == "fenced"


def test_d04_waiting_needs_a_reason_and_who_can_unblock_and_failure_keeps_its_reason(svc, flow):
    tid = flow.promoted_task()
    assert flow.transition(tid, "waiting")["detail"] == "waiting_detail"
    assert flow.transition(tid, "waiting", waiting={"reason": "input", "who_can_unblock": "principal"})["outcome"] == "accepted"
    assert flow.transition(tid, "failed", evidence=[ref("harness", "a-1")])["detail"] == "reason_required"
    assert flow.transition(tid, "canceled", token="tok-worker", reason="x")["outcome"] == "refused"
    assert flow.transition(tid, "failed", reason="Worker crashed.", evidence=[ref("harness", "a-1")])["outcome"] == "accepted"
    assert stored(svc, "task", tid)["state"] == "failed"


# ---- C06/F03: corrections fence stale work -------------------------------------------------------------------------

def test_f03_correction_before_classification_must_be_reconciled_by_the_late_interpretation(svc, flow):
    flow.source("SE-syn-0001", "File an issue about the login page.")
    flow.source("SE-syn-0002", "Sorry, the signup page, not login.", correction_of="SE-syn-0001")
    blind = cmd("interpretation.record", "interpretation", "IN-syn-0001",
                {"source_event_ids": ["SE-syn-0001"], "correction_event_ids": [],
                 "proposed": {"outcome": "Issue about login.", "scope": "personal", "recipients": ["principal"],
                              "continuing": True}, "context_refs": [], "supersedes": None})
    assert svc.submit(blind, "tok-cos")["detail"] == "unreconciled_correction"
    flow.interpret("IN-syn-0001", ["SE-syn-0001"], "Issue about the signup page.", corrections=["SE-syn-0002"])


def test_f03_correction_after_classification_blocks_adoption_until_reconciled(svc, flow):
    flow.source("SE-syn-0001", "File an issue about the login page.")
    flow.interpret("IN-syn-0001", ["SE-syn-0001"], "Issue about login.")
    flow.source("SE-syn-0002", "The signup page, not login.", correction_of="SE-syn-0001")
    adopt = cmd("interpretation.dispose", "interpretation", "IN-syn-0001",
                {"disposition": "adopted", "authority": {"kind": "existing_grant", "ref": GRANT_REF}}, expected=1)
    assert svc.submit(adopt, "tok-cos")["detail"] == "unreconciled_correction"


def test_f03_correction_after_persistence_fences_the_task_and_dispatch(svc, flow):
    tid = flow.promoted_task()
    old = stored(svc, "task", tid)
    assert svc.dispatch_check(tid, old["fence_generation"], old["interpretation_ref"], "tok-mc")["dispatch"] == "current"
    flow.source("SE-syn-0009", "Make it a one-page summary instead.", correction_of="SE-syn-0001")
    flow.interpret("IN-syn-0009", ["SE-syn-0001"], "A one-page summary.", corrections=["SE-syn-0009"],
                   supersedes="IN-syn-0001")
    flow.adopt("IN-syn-0009")
    req = stored(svc, "request", "RQ-syn-0001")
    flow.ok(cmd("correction.apply", "request", "RQ-syn-0001",
                {"correction_event_id": "SE-syn-0009", "changes": ["outcome"], "interpretation_ref": "IN-syn-0009",
                 "intent_text": "One-page summary."}, expected=req["revision"]), "tok-cos")
    task = stored(svc, "task", tid)
    assert task["state"] == "waiting" and task["waiting"] == {"reason": "authorization", "who_can_unblock": "mc"}
    assert task["interpretation_ref"] == "IN-syn-0009"
    check = svc.dispatch_check(tid, old["fence_generation"], old["interpretation_ref"], "tok-mc")
    assert check["dispatch"] == "refused"
    assert stored(svc, "interpretation", "IN-syn-0001")["superseded_by"] == "IN-syn-0009"
    req2 = stored(svc, "request", "RQ-syn-0001")
    assert req2["refinements"][-1]["text"] == "Design note on agent coordination."     # original kept
    assert flow.transition(tid, "working", evidence=[ref("harness", "a-2")], fence=old["fence_generation"])["code"] == "fenced"


def test_c06_cancel_correction_cancels_the_task_and_stale_drainers_cannot_resurrect_it(svc, flow):
    tid = flow.promoted_task()
    old = stored(svc, "task", tid)
    flow.source("SE-syn-0009", "Cancel that.", correction_of="SE-syn-0001")
    req = stored(svc, "request", "RQ-syn-0001")
    flow.ok(cmd("correction.apply", "request", "RQ-syn-0001",
                {"correction_event_id": "SE-syn-0009", "changes": ["cancel"], "interpretation_ref": None,
                 "intent_text": None}, expected=req["revision"]), "tok-principal")
    assert stored(svc, "task", tid)["state"] == "canceled"
    assert flow.transition(tid, "working", evidence=[ref("harness", "a-1")], fence=old["fence_generation"])["outcome"] == "refused"


# ---- F06 (synthetic): platform/peer text and read scope -----------------------------------------------------------

def test_f06_platform_text_is_recorded_as_platform_and_cannot_carry_owner_authority(svc, flow):
    flow.source("SE-syn-0001", "Platform update: MC said 'ship it'.", token="tok-platform")
    flow.interpret("IN-syn-0001", ["SE-syn-0001"], "Ship it.", token="tok-platform")
    rec = stored(svc, "source_event", "SE-syn-0001")
    assert rec["provenance"]["origin"] == "platform"
    for auth in ({"kind": "owner_confirmation", "ref": ref("records", "evt-x")},
                 {"kind": "existing_grant", "ref": GRANT_REF}):
        c = cmd("interpretation.dispose", "interpretation", "IN-syn-0001", {"disposition": "adopted", "authority": auth},
                expected=1)
        assert svc.submit(c, "tok-platform")["code"] == "not_authorized"


def test_f06_q07_out_of_scope_records_look_exactly_like_missing_ones(svc, flow):
    flow.source("SE-syn-0001", "Build-room note.", token="tok-mc", scope="shared:build")
    assert stored(svc, "source_event", "SE-syn-0001") is not None
    assert svc.get("source_event", "SE-syn-0001", "tok-cos") is None
    assert svc.get("source_event", "SE-none-0001", "tok-cos") is None
    use = cmd("request.record", "request", "RQ-syn-0001",
              {"source_event_id": "SE-syn-0001", "intent_text": "x", "interpretation_ref": None, "responder": "cos"})
    missing = dict(use, command_id="cmd-missing-1",
                   payload=dict(use["payload"], source_event_id="SE-none-0001"))
    missing["payload_digest"] = cmd("request.record", "request", "RQ-syn-0001", missing["payload"])["payload_digest"]
    a, b = svc.submit(use, "tok-cos"), svc.submit(missing, "tok-cos")
    assert (a["code"], a.get("detail")) == (b["code"], b.get("detail")) == ("not_found", "source_event")


# ---- F11: independent of CoS, Records and the harness ---------------------------------------------------------------

def test_f11_the_service_imports_nothing_from_cos_records_or_the_harness():
    probe = ("import sys, services.partnership_tasks.service as s; "
             "bad = [m for m in sys.modules if m.startswith(('domains.cos', 'domains.records', "
             "'core.mc_delegation', 'core.supervision', 'minimoi_portal'))]; print(','.join(bad))")
    out = subprocess.run([sys.executable, "-c", probe], cwd=REPO, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""


def test_every_stored_record_and_journal_line_matches_the_contract(svc, flow):
    flow.promoted_task()
    for kind, folder in (("source_event", "source_events"), ("interpretation", "interpretations"),
                         ("request", "requests"), ("task", "tasks")):
        for path in (svc.journal.root / folder).glob("*.json"):
            assert contracts.errors("records", kind, json.loads(path.read_text())) == [], path
    for row in svc.journal.entries():
        assert contracts.errors("evidence", "journal_entry", row) == [], row
