"""B3A: shared read views and meeting close (spec v1.1 C02, C04, C05, C07, D03; acceptance F08). SYNTHETIC.
Revised after REVIEW_B3A_INDEPENDENT_2026-11-04."""
from __future__ import annotations

from core import durable_files as fsio
from services.partnership_tasks.views import close_meeting, primary_status, work_for
from tests.partnership.synth import Flow, cmd, ref, service

MEETING = {"store": "records", "id": "room-syn-7/meeting-3", "edition": "seq-120"}
ITEMS = [{"text": "MC drafts the coordination note.", "responder": "mc", "position": 101},
         {"text": "CoS checks the JD list for leadership roles.", "responder": "cos", "position": 108},
         {"text": "Codex reviews the outbox design.", "responder": "codex", "position": 115}]


def close(items, close_id="close-syn-1", meeting=MEETING, scope="personal"):
    return {"close_id": close_id, "meeting_ref": meeting, "scope": scope, "items": items}


def requests_in(root):
    return sorted(p.stem for p in (root / "requests").glob("RQ-*.json"))


def test_f08_a_crash_between_an_items_source_event_and_its_request_reconciles_once(tmp_path, monkeypatch):
    svc = service(tmp_path)
    real, calls = fsio.write_doc, {"n": 0}
    def crash_on_fourth_write(path, record):
        calls["n"] += 1
        if calls["n"] == 4:                                       # item 2: its source event landed, not its request
            raise OSError("simulated crash")
        real(path, record)
    monkeypatch.setattr(fsio, "write_doc", crash_on_fourth_write)
    first = close_meeting(svc, "tok-cos", close(ITEMS))
    monkeypatch.setattr(fsio, "write_doc", real)
    # Item 2's request intent was journalled before the crash: the next write (item 3) finished it forward.
    assert [i["disposition"] for i in first["items"]] == ["saved", "pending", "saved"]
    restarted = service(tmp_path)
    assert restarted.get("request", first["items"][1]["request_id"], "tok-cos") is not None
    again = close_meeting(restarted, "tok-cos", close(ITEMS))
    assert [i["disposition"] for i in again["items"]] == ["saved", "saved", "saved"]
    assert again["state"] == "complete" and len(requests_in(tmp_path)) == 3


def test_f08_a_takeover_convener_reconciles_and_refusals_are_named_never_pending(tmp_path):
    svc = service(tmp_path)
    close_meeting(svc, "tok-cos", close(ITEMS[:2]))
    by_mc = close_meeting(svc, "tok-mc", close(ITEMS))                       # MC takes over and adds item 3
    assert [i["disposition"] for i in by_mc["items"]] == ["saved", "saved", "saved"]
    assert len(requests_in(tmp_path)) == 3
    reordered = close_meeting(svc, "tok-mc", close([ITEMS[1], ITEMS[0], ITEMS[2]]))
    assert [i["disposition"] for i in reordered["items"]] == ["refused", "refused", "saved"]
    assert "pending" not in reordered["say"] and "refused: a_different_item_is_saved_here" in reordered["say"]
    assert len(requests_in(tmp_path)) == 3                                    # nothing replaced or duplicated


def test_f08_the_same_close_id_in_another_meeting_or_scope_never_collides(tmp_path):
    svc = service(tmp_path)
    close_meeting(svc, "tok-mc", close(ITEMS))
    other = close_meeting(svc, "tok-mc", close(ITEMS, meeting={"store": "records", "id": "room-syn-8/meeting-1"}))
    assert other["state"] == "complete" and len(requests_in(tmp_path)) == 6
    build = close_meeting(svc, "tok-mc", close(ITEMS[:1], scope="shared:build"))
    assert build["state"] == "complete"


def test_f08_store_outage_is_pending_and_saves_on_the_next_close(tmp_path):
    svc = service(tmp_path)
    (tmp_path / "requests").mkdir(parents=True)
    fsio.write_doc(tmp_path / "requests" / "RQ-planted-0001.json", {"x": 1})  # outside the journal: store held
    out = close_meeting(svc, "tok-cos", close(ITEMS[:1]))
    assert out["items"][0]["disposition"] == "pending" and "closing again saves them" in out["say"]


def test_c04_one_primary_status_blocked_first_including_a_child_of_an_absent_lead(tmp_path):
    svc = service(tmp_path)
    f = Flow(svc)
    tid = f.promoted_task()
    status = lambda t: primary_status(svc, "tok-principal", svc.get("task", t, "tok-principal"))
    assert status(tid)["primary"] == {"status": "submitted", "note": "saved; not yet picked up"}
    f.transition(tid, "working", evidence=[ref("harness", "p")])
    t = svc.get("task", tid, "tok-principal")
    f.ok(cmd("task.delegate", "task", tid, {"child_task_id": "TK-syn-0001c1", "holder": "worker-a", "outcome": "o",
                                            "done_when": "d", "return_window": None,
                                            "fence_generation": t["fence_generation"]}, expected=t["revision"]), "tok-mc")
    f.transition("TK-syn-0001c1", "working", token="tok-worker", evidence=[ref("harness", "c")])
    assert status("TK-syn-0001c1")["primary"]["status"] == "in_progress"
    t = svc.get("task", tid, "tok-principal")
    f.ok(cmd("task.lead_unavailable", "task", tid, {"evidence_refs": [ref("harness", "chk")],
                                                    "fence_generation": t["fence_generation"]},
             expected=t["revision"]), "tok-scheduler")
    child = status("TK-syn-0001c1")
    assert child["primary"]["status"] == "blocked" and child["primary"]["reason"] == "lead_unavailable"
    assert status(tid)["primary"]["who_can_unblock"] == "principal"
    assert primary_status(svc, "tok-peer", svc.get("task", tid, "tok-principal")) is None


def test_c04_ready_only_on_a_non_author_approval_and_saved_is_not_working(tmp_path):
    svc = service(tmp_path)
    f = Flow(svc)
    tid = f.promoted_task()
    f.transition(tid, "working", evidence=[ref("harness", "p")])
    f.ready_for_acceptance(tid, tag="v1")
    st = primary_status(svc, "tok-principal", svc.get("task", tid, "tok-principal"))
    assert st["primary"]["status"] == "ready_for_acceptance" and st["ladder"]["lead"] == "mc"


def test_views_are_your_own_unless_you_are_the_principal(tmp_path):
    svc = service(tmp_path)
    f = Flow(svc)
    tid = f.promoted_task()
    assert work_for(svc, "tok-mc")["leads"] == [tid]
    assert work_for(svc, "tok-cos", seat="mc") is None
    assert work_for(svc, "tok-principal", seat="mc")["holds"] == [tid]
    assert work_for(svc, "tok-peer")["leads"] == []
