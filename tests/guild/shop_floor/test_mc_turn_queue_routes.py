"""Waiting MC turns through the real routes (issue #346 P2): ``queue: true`` on /mc/turns and
/mc/turns/stream, POST /mc/turns/queue/<id>/dispatch and /cancel, the queued view. The real portal,
owner guard, CSRF, SQLite floor store, conversation documents and topic items, a fake runtime
behind the real OpenClaw adapter, and an in-memory queue with the store's rules (the real-Postgres
rules are test_mc_turn_queue_postgres.py). No network, no model call, no database."""
from __future__ import annotations

import shutil
import uuid

import pytest

from minimoi_portal.guild_ui.mc import ledger_gate
from minimoi_portal.guild_ui.mc.turn_ledger import QUEUE_MAX, Admission, Claim, _clean_outcome

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from test_mc_turns import FakeRuntime, Resp, _keep, _openclaw, _turn_on
from test_mc_turn_ledger_routes import _ledger_on, owner
from test_mc_upload_reading import _doc, _new

API = "/guild-next/api/v1"


class FakeQueue:
    """The store's queue rules in memory: owner-wide order, head-only claim, requeue, cancel."""

    def __init__(self, barrier=None):
        self.rows, self.barrier, self.events, self.finished = [], barrier, [], []

    def _waiting(self):
        return [r for r in self.rows if r["state"] == "queued"]

    def _pos(self, row):
        return [r["id"] for r in self._waiting()].index(row["id"]) + 1

    def admit(self, **kw):
        self.events.append(("admit", kw))
        if self._waiting():
            return Admission("queue_pending")
        row = {"id": len(self.rows) + 1, "state": "claimed", "queued_at": None, **kw}
        self.rows.append(row)
        return Admission("claimed", entry=row, claim=Claim(row["id"], "tok", 1))

    def enqueue(self, **kw):
        self.events.append(("enqueue", kw))
        if len(self._waiting()) >= QUEUE_MAX:
            return Admission("queue_full")
        row = {"id": len(self.rows) + 1, "state": "queued", "queued_at": "2026-10-19T22:00:00+00:00",
               "claim_generation": 0, **kw}
        self.rows.append(row)
        return Admission("queued", entry=row, position=self._pos(row), barrier=self.barrier)

    def claim_queued(self, principal, ledger_id):
        self.events.append(("claim_queued", ledger_id))
        if self.barrier:
            return Admission("paused", barrier=self.barrier)
        if any(r["state"] == "claimed" for r in self.rows):
            return Admission("busy", entry=next(r for r in self.rows if r["state"] == "claimed"))
        row = next((r for r in self.rows if r["id"] == ledger_id), None)
        if row is None or row["state"] != "queued" or row["principal"] != principal:
            return Admission("gone", entry=row)
        if self._waiting()[0]["id"] != ledger_id:
            return Admission("not_next", entry=row, position=self._pos(row))
        row["state"] = "claimed"
        row["claim_generation"] += 1
        return Admission("claimed", entry=dict(row), claim=Claim(row["id"], "tok", row["claim_generation"]))

    def requeue(self, claim):
        self.events.append(("requeue", claim.ledger_id))
        row = self.rows[claim.ledger_id - 1]
        if row["state"] != "claimed" or not row.get("queued_at"):
            return False
        row["state"] = "queued"
        return True

    def cancel(self, principal, ledger_id):
        row = next((r for r in self.rows if r["id"] == ledger_id and r["principal"] == principal), None)
        if row is not None and row["state"] == "queued":
            row["state"] = "cancelled"
            return {"result": "cancelled", "ledger_id": ledger_id}
        return {"result": "not_queued", "ledger_id": ledger_id, "state": row["state"] if row else None}

    def held(self, claim, interval_s=30):
        events = self.events

        class Hold:
            def __enter__(self):
                events.append(("hold", claim.ledger_id))

            def __exit__(self, *a):
                events.append(("release", claim.ledger_id))
        return Hold()

    def finish(self, claim, state, outcome=None):
        clean = _clean_outcome(outcome)                   # the real allowlist
        self.rows[claim.ledger_id - 1]["state"] = state
        self.finished.append((state, clean))
        if state in ("failed", "unknown") and self.barrier is None:
            self.barrier = {"ledger_id": claim.ledger_id, "reason": state}
        return True

    def status(self, principal, recent=10):
        mine = [r for r in self.rows if r["principal"] == principal]
        shape = lambda r: {"id": r["id"], "conversation_id": r["conversation_id"],
                           "note_request_id": r["note_request_id"], "turn_request_id": r["turn_request_id"],
                           "kind": "typed", "state": r["state"], "attachment_ids": r.get("attachment_ids", []),
                           "topic_refs": r.get("topic_refs", []), "queued_at": r.get("queued_at"),
                           "created_at": "x", "updated_at": "y", "outcome": {}}
        return {"running": next((shape(r) for r in mine if r["state"] == "claimed"), None), "pause": self.barrier,
                "queued": [shape(r) for r in mine if r["state"] == "queued"],
                "recent": [shape(r) for r in reversed(mine)][:recent], "released": []}


@pytest.fixture
def turned(floored):
    runtime = FakeRuntime()
    _turn_on(floored, _openclaw(runtime))
    floored.extra["runtime"] = runtime
    return floored


def _queue(client, token, note_id, *, cid=None, ids=None, refs=None, request_id="auto", stream=False):
    body = {"note_request_id": note_id, "record_mode": "on_record", "queue": True}
    if request_id:
        body["request_id"] = "req-" + uuid.uuid4().hex if request_id == "auto" else request_id
    if cid:
        body["conversation_id"] = cid
    if ids is not None:
        body["attachment_ids"] = ids
    if refs is not None:
        body["topic_context"] = refs
    return client.post(f"{API}/mc/turns/stream" if stream else f"{API}/mc/turns", json=body,
                       headers=write_headers(token))


def _dispatch(client, token, ledger_id, headers=None, **body):
    return client.post(f"{API}/mc/turns/queue/{ledger_id}/dispatch", json=body or {"stream": False},
                       headers=write_headers(token) if headers is None else headers)


def _sent(portal):
    return portal.extra["runtime"].sent


# ── queuing records, and sends nothing ────────────────────────────────────────

def test_ledger_off_refuses_to_queue_and_sends_nothing(turned):
    q = FakeQueue()
    _ledger_on(turned, q, on=False)
    client, token = owner(turned)
    r = _queue(client, token, _keep(client, token, "later")["request_id"])
    assert r.status_code == 409 and r.get_json()["error"] == "queue_off"
    assert q.events == [] and _sent(turned) == []


@pytest.mark.parametrize("stream", [False, True])
def test_queue_true_records_a_waiting_turn_and_sends_nothing(turned, stream):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    note = _keep(client, token, "later")
    r = _queue(client, token, note["request_id"], request_id="req-queue-00000001", stream=stream)
    body = r.get_json()
    assert r.status_code == 202 and r.content_type.startswith("application/json")
    assert body == {"status": "queued", "ledger_id": 1, "position": 1, "request_id": "req-queue-00000001",
                    "note_request_id": note["request_id"], "paused": False, "pause": None}
    assert [e[0] for e in q.events] == ["enqueue"] and _sent(turned) == []
    kw = q.events[0][1]
    assert kw["turn_request_id"] == "req-queue-00000001" and kw["attachment_ids"] == [] and kw["topic_refs"] == []


def test_queuing_while_paused_records_and_says_so(turned):
    q = FakeQueue(barrier={"ledger_id": 7, "reason": "unknown"})
    _ledger_on(turned, q)
    client, token = owner(turned)
    body = _queue(client, token, _keep(client, token, "later")["request_id"]).get_json()
    assert body["paused"] is True and body["pause"] == {"ledger_id": 7, "reason": "unknown"}


def test_a_waiting_turn_needs_a_request_id(turned):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    r = _queue(client, token, _keep(client, token, "later")["request_id"], request_id=None)
    assert r.status_code == 422 and q.events == [] and _sent(turned) == []


def test_a_full_queue_records_nothing_more(turned):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    for n in range(QUEUE_MAX):
        assert _queue(client, token, _keep(client, token, f"later {n}")["request_id"]).status_code == 202
    r = _queue(client, token, _keep(client, token, "one too many")["request_id"])
    assert r.status_code == 409 and r.get_json()["error"] == "queue_full"
    assert len(q.rows) == QUEUE_MAX and _sent(turned) == []


def test_a_file_from_another_conversation_is_refused_at_queueing(turned):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    here, elsewhere = _new(client, token), _new(client, token)
    foreign = _doc(client, token, elsewhere, "Not in this conversation.")
    note = _keep(client, token, "Read this", conversation_id=here)
    r = _queue(client, token, note["request_id"], cid=here, ids=[foreign])
    assert r.status_code == 422 and r.get_json()["error"] == "not_found"
    assert q.events == [] and _sent(turned) == []


def test_another_owners_or_a_missing_topic_item_is_refused_at_queueing(turned):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    ref = {"topic_id": "t-000000000000", "item_id": "i-000000000000", "rev": None}
    r = _queue(client, token, _keep(client, token, "About this")["request_id"], refs=[ref])
    assert r.status_code == 422 and r.get_json()["error"] == "not_found"
    assert q.events == [] and _sent(turned) == []


def test_an_immediate_turn_cannot_jump_the_queue(turned):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    _queue(client, token, _keep(client, token, "first")["request_id"])
    r = client.post(f"{API}/mc/turns", json={"note_request_id": _keep(client, token, "second")["request_id"],
                                             "request_id": "req-" + uuid.uuid4().hex, "record_mode": "on_record"},
                    headers=write_headers(token))
    assert r.status_code == 409 and r.get_json()["error"] == "queue_pending" and _sent(turned) == []


# ── sending the head ─────────────────────────────────────────────────────────

def test_dispatch_sends_the_head_exactly_as_queued(turned):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "Budget: 4200 EUR")
    note = _keep(client, token, "Summarise the file", conversation_id=cid)
    lid = _queue(client, token, note["request_id"], cid=cid, ids=[doc]).get_json()["ledger_id"]
    r = _dispatch(client, token, lid)
    assert r.status_code == 200 and r.get_json()["status"] == "answered", r.get_json()
    [post] = _sent(turned)
    assert "Summarise the file" in post["body"]["messages"][0]["content"]
    assert "4200 EUR" in post["body"]["messages"][0]["content"]          # the file bound when it was queued
    assert q.finished[0][0] == "answered" and q.rows[0]["state"] == "answered"


def test_dispatch_is_refused_without_the_write_guard_and_claims_nothing(turned):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    lid = _queue(client, token, _keep(client, token, "later")["request_id"]).get_json()["ledger_id"]
    r = _dispatch(client, token, lid, headers={"X-Record-Mode": "on_record"})     # no CSRF token
    assert r.status_code in (400, 403) and ("claim_queued", lid) not in q.events and _sent(turned) == []


@pytest.mark.parametrize("body", [{"stream": "yes"}, {"stream": False, "extra": 1}])
def test_dispatch_takes_only_the_stream_switch(turned, body):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    lid = _queue(client, token, _keep(client, token, "later")["request_id"]).get_json()["ledger_id"]
    r = _dispatch(client, token, lid, **body)
    assert r.status_code == 422 and ("claim_queued", lid) not in q.events and _sent(turned) == []


def test_only_the_head_is_sent(turned):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    _queue(client, token, _keep(client, token, "first")["request_id"])
    second = _queue(client, token, _keep(client, token, "second")["request_id"]).get_json()["ledger_id"]
    r = _dispatch(client, token, second)
    assert r.status_code == 409 and r.get_json()["error"] == "not_next" and r.get_json()["position"] == 2
    assert _sent(turned) == []


def test_a_pause_holds_the_queue(turned):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    lid = _queue(client, token, _keep(client, token, "later")["request_id"]).get_json()["ledger_id"]
    q.barrier = {"ledger_id": 3, "reason": "failed"}
    r = _dispatch(client, token, lid)
    assert r.status_code == 409 and r.get_json()["error"] == "paused" and r.get_json()["ledger_id"] == 3
    assert _sent(turned) == [] and q.rows[0]["state"] == "queued"


def test_a_cancelled_or_unknown_entry_is_not_sent(turned):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    lid = _queue(client, token, _keep(client, token, "later")["request_id"]).get_json()["ledger_id"]
    assert client.post(f"{API}/mc/turns/queue/{lid}/cancel", json={}, headers=write_headers(token)).status_code == 200
    r = _dispatch(client, token, lid)
    assert r.status_code == 409 and r.get_json()["error"] == "not_queued" and r.get_json()["state"] == "cancelled"
    assert _dispatch(client, token, 999).get_json()["error"] == "not_queued" and _sent(turned) == []


# ── refused at dispatch: for good (failed, pause) or for now (stays first) ───

def test_a_file_removed_after_queueing_fails_and_pauses_with_nothing_sent(turned):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "Soon gone.")
    note = _keep(client, token, "Read it", conversation_id=cid)
    lid = _queue(client, token, note["request_id"], cid=cid, ids=[doc]).get_json()["ledger_id"]
    gone = client.post(f"{API}/conversations/{cid}/documents/remove", json=keyed(document_id=doc),
                       headers=write_headers(token))
    assert gone.status_code == 200, gone.get_json()
    r = _dispatch(client, token, lid)
    assert r.status_code == 422 and r.get_json()["error"] == "not_found"
    assert _sent(turned) == []
    assert q.finished == [("failed", {"failure_class": "dispatch_refused", "reason": "not_found"})]
    assert q.barrier == {"ledger_id": lid, "reason": "failed"} and ("requeue", lid) not in q.events


def test_a_topic_item_gone_after_queueing_fails_and_pauses_with_nothing_sent(turned):
    from minimoi_portal.guild_ui.topics import topics_of
    from test_topic_context import _topic_item
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    tid, iid = _topic_item(client, token)
    ref = {"topic_id": tid, "item_id": iid, "rev": None}
    lid = _queue(client, token, _keep(client, token, "About this")["request_id"], refs=[ref]).get_json()["ledger_id"]
    store = topics_of(turned.app.extensions["guild_ui_next"]["services"])
    shutil.rmtree(store._idir(tid, iid))                    # the item is gone from the workshop
    r = _dispatch(client, token, lid)
    assert r.status_code == 422 and r.get_json()["error"] == "not_found" and _sent(turned) == []
    assert q.finished[0][0] == "failed" and q.finished[0][1]["failure_class"] == "dispatch_refused"


def test_mc_unavailable_at_dispatch_keeps_the_turn_first_in_line(turned):
    class NotReady(FakeRuntime):
        def get(self, url, **kw):
            return Resp(503, '{"ready":false}')
    runtime = NotReady()
    _turn_on(turned, _openclaw(runtime))
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    first = _queue(client, token, _keep(client, token, "first")["request_id"]).get_json()["ledger_id"]
    second = _queue(client, token, _keep(client, token, "second")["request_id"]).get_json()["ledger_id"]
    r = _dispatch(client, token, first)
    assert r.status_code == 503 and r.get_json()["error"] == "mc_unavailable" and runtime.sent == []
    assert ("requeue", first) in q.events and q.finished == [] and q.barrier is None
    assert q.rows[0]["state"] == "queued"
    assert _dispatch(client, token, second).get_json()["error"] == "not_next"      # nothing overtakes it


def test_streaming_switched_off_keeps_the_turn_first_in_line(turned):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    lid = _queue(client, token, _keep(client, token, "later")["request_id"]).get_json()["ledger_id"]
    turned.app.extensions["guild_ui_next"]["services"].mc_stream = False
    r = _dispatch(client, token, lid, stream=True)
    assert r.status_code == 409 and r.get_json()["error"] == "stream_off" and _sent(turned) == []
    assert ("requeue", lid) in q.events and q.rows[0]["state"] == "queued" and q.barrier is None


# ── cancel and the view ──────────────────────────────────────────────────────

def test_cancel_only_a_waiting_turn_and_only_with_the_write_guard(turned):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    lid = _queue(client, token, _keep(client, token, "later")["request_id"]).get_json()["ledger_id"]
    nope = client.post(f"{API}/mc/turns/queue/{lid}/cancel", json={}, headers={"X-Record-Mode": "on_record"})
    assert nope.status_code in (400, 403) and q.rows[0]["state"] == "queued"
    assert _dispatch(client, token, lid).status_code == 200
    late = client.post(f"{API}/mc/turns/queue/{lid}/cancel", json={}, headers=write_headers(token))
    assert late.status_code == 409 and late.get_json() == {**late.get_json(), "error": "not_queued", "state": "answered"}


def test_cancel_is_off_with_the_ledger(turned):
    _ledger_on(turned, FakeQueue(), on=False)
    client, token = owner(turned)
    assert client.post(f"{API}/mc/turns/queue/1/cancel", json={}, headers=write_headers(token)).status_code == 404


def test_the_view_lists_waiting_turns_in_order_without_files_or_topics(turned):
    q = FakeQueue()
    _ledger_on(turned, q)
    client, token = owner(turned)
    a = _queue(client, token, _keep(client, token, "first")["request_id"], request_id="req-view-00000001")
    _queue(client, token, _keep(client, token, "second")["request_id"], request_id="req-view-00000002")
    body = client.get(f"{API}/mc/turns/ledger").get_json()
    assert [(e["position"], e["request_id"]) for e in body["queued"]] == [(1, "req-view-00000001"),
                                                                          (2, "req-view-00000002")]
    assert body["queued"][0]["id"] == a.get_json()["ledger_id"] and body["queued"][0]["queued_at"]
    for e in body["queued"] + body["recent"]:
        assert "attachment_ids" not in e and "topic_refs" not in e


# ── which refusals may pass later ────────────────────────────────────────────

@pytest.mark.parametrize("body,expected", [
    (None, True), ({"error": "unavailable"}, True), ({"error": "mc_unavailable"}, True),
    ({"error": "busy"}, True), ({"error": "stream_off"}, True), ({"error": "ledger_unavailable"}, True),
    ({"error": "mc_unavailable", "reason": "request_too_large"}, False), ({"error": "not_found"}, False),
    ({"error": "not_allowed"}, False), ({"error": "files_unsupported"}, False), ({"error": "invalid"}, False),
    ({"error": "already_dispatched"}, False), ({}, False), ("garbage", False)])
def test_transient_classification(body, expected):
    assert ledger_gate.transient(body) is expected
