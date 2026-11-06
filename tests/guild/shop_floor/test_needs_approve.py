"""B9: Needs you counts the decisions waiting for Robert in the task service (contract approval_and_needs v0.1 §2).

The proposals and cards here are real records in a real task service over HTTP (test_tasks_owner_api's rig); only the
network is synthetic. A service that cannot be read is unknown, never "Nothing needs you".
"""
from datetime import datetime, timedelta, timezone

import requests

from minimoi_portal.guild_ui import briefing, floor_state, tasks_owner_api as T
from minimoi_portal.guild_ui.adapters.contract import live_ok, live_unknown
from minimoi_portal.guild_ui.needs import needs_you
from tests.guild.shop_floor.floor_helpers import load_portal  # noqa: F401  (fixture)
from tests.guild.shop_floor.test_tasks_owner_api import _queued_card, post, rig  # noqa: F401  (fixture)

CALM = live_ok([], "queue")
JOURNAL = live_ok([], "journal")


def _connected(monkeypatch):
    monkeypatch.setenv("PARTNERSHIP_TASKS_URL", "http://tasks:8772")


class _SyntheticClock(datetime):
    """The portal's clock at the synthetic service's fixed time, so a just-given approval is still standing."""
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 4, 16, 1, tzinfo=timezone.utc)


# ---- needs_you itself ---------------------------------------------------------------------------------------------

def _row(i, at="2026-11-06T10:00:00+00:00"):
    return {"kind": "proposal", "id": f"IN-{i}", "text": f"Proposal {i}", "at": at, "href": "/guild/tasks"}


def test_a_waiting_decision_counts_and_keeps_the_shape():
    out = needs_you(CALM, JOURNAL, approvals_res=live_ok([_row(1)], "t"))
    assert out["status"] == "ok" and out["total"] == 1 and out["text"] == "1 needs you" and out["error"] is None
    assert out["items"] == [{"tag": "Approve", "item_id": "IN-1", "op_id": None, "text": "Proposal 1",
                             "at": "2026-11-06T10:00:00+00:00", "href": "/guild/tasks"}]
    assert set(out) == {"status", "items", "total", "observed_at", "text", "error"}


def test_an_unreadable_task_service_is_unknown_never_nothing():
    out = needs_you(CALM, JOURNAL, approvals_res=live_unknown("read_failed", "task service read failed: X", "t"))
    assert out["status"] == "unknown" and out["total"] is None and out["items"] == []
    assert "Nothing needs you" not in out["text"] and out["error"] == "task service read failed: X"


def test_an_unreadable_task_service_still_shows_what_is_known():
    blocked = {"id": 7, "title": "T", "status": "blocked", "status_known": True, "blocked_reason": "r"}
    out = needs_you(live_ok([blocked], "q"), JOURNAL, approvals_res=live_unknown("read_failed", "e", "t"))
    assert out["status"] == "unknown" and out["total"] is None and [i["tag"] for i in out["items"]] == ["Decide"]


def test_a_failed_queue_read_keeps_the_decisions_it_did_read():
    failed = live_unknown("read_failed", "queue down", "q")
    out = needs_you(failed, JOURNAL, approvals_res=live_ok([_row(1)], "t"))
    assert out["status"] == "unknown" and out["total"] is None and [i["tag"] for i in out["items"]] == ["Approve"]


def test_the_cap_never_hides_a_decision_behind_older_trouble():
    blocked = [{"id": n, "title": "T", "status": "blocked", "status_known": True} for n in range(1, 6)]
    out = needs_you(live_ok(blocked, "q"), JOURNAL, cap=3, approvals_res=live_ok([_row(1)], "t"))
    assert out["total"] == 6 and out["items"][0]["tag"] == "Approve" and len(out["items"]) == 3


def test_no_task_service_on_this_host_leaves_the_old_answer():
    out = needs_you(CALM, JOURNAL, approvals_res=None)
    assert out["status"] == "ok" and out["total"] == 0 and out["text"] == "Nothing needs you"
    assert needs_you(CALM, JOURNAL)["text"] == "Nothing needs you"


def test_the_opening_briefing_names_the_decision_without_a_record_id():
    needs = needs_you(CALM, JOURNAL, approvals_res=live_ok([_row(1)], "t"))
    assert "1 needs you (Approve)" in briefing.opening_briefing([], needs, needs["observed_at"])["text"]


# ---- approvals_waiting against the real service ----------------------------------------------------------------------

def test_a_proposal_and_a_card_are_both_read_from_the_service(rig, monkeypatch):
    svc, c = rig
    _connected(monkeypatch)
    rows = T.approvals_waiting().data
    assert [(r["kind"], r["id"]) for r in rows] == [("proposal", "IN-own-0001")]
    assert rows[0]["text"] == "Proposal waiting for you: A three-item review checklist."
    _queued_card(svc, c, "OB-own-need-1", "C-own-0101")              # confirms the proposal, then queues a card
    rows = T.approvals_waiting().data
    assert [(r["kind"], r["id"]) for r in rows] == [("card", "OB-own-need-1")]
    assert rows[0]["text"] == "Work card waiting for you: A three-item review checklist."


def test_an_approved_card_no_longer_counts_and_a_declined_one_is_gone(rig, monkeypatch):
    svc, c = rig
    _connected(monkeypatch)
    monkeypatch.setattr(T, "datetime", _SyntheticClock)
    d = _queued_card(svc, c, "OB-own-need-2", "C-own-0102")
    assert post(c, "/tasks/admit", operation_id="OB-own-need-2", card_digest=d).status_code == 200
    assert T.approvals_waiting().data == []
    d = _queued_card(svc, c, "OB-own-need-3", "C-own-0103")
    assert [r["id"] for r in T.approvals_waiting().data] == ["OB-own-need-3"]
    assert post(c, "/tasks/admission-decline", operation_id="OB-own-need-3", card_digest=d).status_code == 200
    assert T.approvals_waiting().data == []


def test_an_approval_stands_until_it_is_spent_or_runs_out():
    now = datetime.now(timezone.utc)
    row = {"approval": {"approved_at": (now - timedelta(seconds=901)).isoformat(), "spent_at": None}}
    assert T._standing_approval(row, now) is None
    row["approval"]["approved_at"] = (now - timedelta(seconds=899)).isoformat()
    assert T._standing_approval(row, now) == row["approval"]
    row["approval"]["spent_at"] = now.isoformat()
    assert T._standing_approval(row, now) is None
    assert T._standing_approval({"approval": None}, now) is None


def test_every_way_of_failing_to_read_is_unknown(rig, monkeypatch):
    _connected(monkeypatch)

    def refused(self):
        raise requests.exceptions.ConnectionError("refused http://secret")
    client = T._client()
    for fail in (refused, lambda self: "not a list of proposals"):
        monkeypatch.setattr(type(client), "pending", fail)
        res = T.approvals_waiting()
        assert not res.ok and res.status == "unknown" and "secret" not in res.error
    monkeypatch.setattr(T, "_client", lambda user=None: None)
    res = T.approvals_waiting()
    assert res.status == "unknown" and res.reason == "not_configured"


def test_no_task_service_url_reads_nothing_at_all(monkeypatch):
    monkeypatch.delenv("PARTNERSHIP_TASKS_URL", raising=False)
    monkeypatch.setattr(T, "_client", lambda user=None: (_ for _ in ()).throw(AssertionError("must not read")))
    assert T.approvals_waiting() is None


# ---- the floor, through the real page state --------------------------------------------------------------------------

def test_the_floor_links_each_decision_to_where_it_is_made(load_portal, monkeypatch):
    portal = load_portal(items=[{"id": 1, "spec_title": "x", "status": "in_build"}])
    rows = [{"kind": "proposal", "id": "IN-1", "text": "P", "at": "2026-11-06T10:00:00+00:00"},
            {"kind": "card", "id": "OB-1", "text": "C", "at": "2026-11-06T09:00:00+00:00"}]
    monkeypatch.setattr(T, "approvals_waiting", lambda user=None: live_ok(rows, "t"))
    needs = portal.owner().get("/guild-next/api/v1/floor").get_json()["needs"]
    assert needs["total"] == 2 and needs["text"] == "2 need you"
    assert [(i["item_id"], i["tag"], i["href"]) for i in needs["items"]] == [
        ("IN-1", "Approve", "/guild-next/guild/tasks"), ("OB-1", "Approve", floor_state.COS_WORK_CARDS_HREF)]


def test_the_floor_and_page_say_unknown_when_the_service_cannot_be_read(load_portal, monkeypatch):
    portal = load_portal(items=[{"id": 1, "spec_title": "x", "status": "in_build"}])
    monkeypatch.setattr(T, "approvals_waiting", lambda user=None: live_unknown("read_failed", "e", "t"))
    client = portal.owner()
    needs = client.get("/guild-next/api/v1/floor").get_json()["needs"]
    assert needs["status"] == "unknown" and needs["total"] is None and needs["text"] != "Nothing needs you"
    assert "Nothing needs you" not in client.get("/guild-next/guild/build").get_data(as_text=True)


def test_a_lapsed_approval_counts_again_and_the_card_offers_its_buttons_again(rig, monkeypatch):
    svc, c = rig
    _connected(monkeypatch)
    monkeypatch.setattr(T, "datetime", _SyntheticClock)
    d = _queued_card(svc, c, "OB-own-need-6", "C-own-0106")
    assert post(c, "/tasks/admit", operation_id="OB-own-need-6", card_digest=d).status_code == 200
    assert c.get("/tasks/admissions").json["admissions"][0]["approval"] is not None
    monkeypatch.setattr(T, "datetime", datetime)                     # the real clock: days after the service's time
    assert c.get("/tasks/admissions").json["admissions"][0]["approval"] is None
    assert [r["id"] for r in T.approvals_waiting().data] == ["OB-own-need-6"]


# ---- conversation_id on the work card --------------------------------------------------------------------------------

def test_the_card_names_the_conversation_its_proposal_came_from(rig):
    svc, c = rig
    _queued_card(svc, c, "OB-own-need-4", "C-own-0104")
    rows = c.get("/tasks/admissions").json["admissions"]
    assert rows[0]["conversation_id"] == "cos-owner"                 # the rig's source is cos-history "cos-owner:t-1"


def test_a_card_whose_origin_cannot_be_read_still_shows_with_null(rig, monkeypatch):
    svc, c = rig
    _queued_card(svc, c, "OB-own-need-5", "C-own-0105")
    client = T._client()
    real = type(client).get

    def lost(self, target_type, target_id):
        if target_type == "source_event":
            raise requests.exceptions.ConnectionError("gone")
        return real(self, target_type, target_id)
    monkeypatch.setattr(type(client), "get", lost)
    rows = c.get("/tasks/admissions").json["admissions"]
    assert [r["operation_id"] for r in rows] == ["OB-own-need-5"] and rows[0]["conversation_id"] is None


def test_a_source_outside_cos_history_is_null():
    class Records:
        def get(self, kind, _id):
            return {"task": {"interpretation_ref": "IN-1"}, "interpretation": {"source_event_ids": ["SE-1"]},
                    "source_event": {"source": {"store": "portal-owner-action", "id": "a:b"}}}[kind]
    assert T._origin_conversation(Records(), "TK-1") is None
