"""Waiting MC turns on a real Postgres (issue #346 P2; mc/turn_ledger.py enqueue/claim_queued/requeue/cancel).

Same rules as test_mc_turn_ledger_postgres.py: skipped unless GUILD_FLOOR_TEST_DATABASE_URL names a
disposable test database with migrations 001–006 applied; applies 007 itself; every row belongs to a
``test-ledger-<random>`` principal and is removed at the end. A skip here is not acceptance.
"""
from __future__ import annotations

import os
from pathlib import Path
import threading
import uuid

import pytest

from minimoi_portal.guild_ui.mc.turn_ledger import QUEUE_MAX, QUEUE_TTL_S, Claim, TurnLedger

URL = os.environ.get("GUILD_FLOOR_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="GUILD_FLOOR_TEST_DATABASE_URL is not set")
SQL = Path(__file__).resolve().parents[3] / "minimoi_portal/guild_ui/sql"


def _sql(query, args=()):
    import psycopg2
    conn = psycopg2.connect(URL, connect_timeout=4)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(query, args)
            return cur.fetchall() if cur.description else None
    finally:
        conn.close()


@pytest.fixture(scope="module", autouse=True)
def migrated():
    import psycopg2
    conn = psycopg2.connect(URL, connect_timeout=4)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute((SQL / "007_mc_turn_ledger.sql").read_text())
    finally:
        conn.close()
    yield


@pytest.fixture
def owner():
    principal = "test-ledger-" + uuid.uuid4().hex[:10]
    yield principal
    _sql("DELETE FROM guild.mc_turn_barrier WHERE principal = %s", (principal,))
    _sql("DELETE FROM guild.mc_turn_ledger WHERE principal = %s", (principal,))


def ledger(**kw):
    kw.setdefault("worker", "test")
    return TurnLedger(lambda: URL, **kw)


def _ids(note):
    note = note or "note-" + uuid.uuid4().hex
    return note, "turn-" + note


def enqueue(led, owner, conv="c-1", note=None, **kw):
    note, turn = _ids(note)
    return led.enqueue(principal=owner, conversation_id=conv, note_request_id=note, turn_request_id=turn, **kw)


def admit(led, owner, conv="c-1", note=None, **kw):
    note, turn = _ids(note)
    return led.admit(principal=owner, conversation_id=conv, note_request_id=note, turn_request_id=turn, **kw)


def states(owner):
    return [r[1] for r in _sql("SELECT id, state FROM guild.mc_turn_ledger WHERE principal = %s ORDER BY id", (owner,))]


# ── recording a waiting turn ──────────────────────────────────────────────────

def test_waiting_turns_are_recorded_in_order_and_nothing_is_claimed(owner):
    led = ledger()
    a = enqueue(led, owner, attachment_ids=["d-0123456789abcdef"],
                topic_refs=[{"topic_id": "t-0123456789ab", "item_id": "i-0123456789ab", "rev": None}])
    b = enqueue(led, owner, conv="c-2")
    assert (a.result, a.position, b.result, b.position) == ("queued", 1, "queued", 2)
    assert a.claim is None and a.entry["queued_at"] and a.entry["claim_generation"] == 0
    assert a.entry["attachment_ids"] == ["d-0123456789abcdef"] and a.entry["topic_refs"][0]["item_id"]
    assert states(owner) == ["queued", "queued"]
    assert led.status(owner)["running"] is None


def test_at_most_five_wait_and_a_full_queue_records_nothing(owner):
    led = ledger()
    for _ in range(QUEUE_MAX):
        assert enqueue(led, owner).result == "queued"
    assert enqueue(led, owner).result == "queue_full"
    assert states(owner) == ["queued"] * QUEUE_MAX


def test_an_immediate_turn_cannot_jump_the_queue_and_records_nothing(owner):
    led = ledger()
    enqueue(led, owner)
    assert admit(led, owner, conv="c-9").result == "queue_pending"
    assert states(owner) == ["queued"]


def test_enqueue_reports_a_pause_and_still_records(owner):
    led = ledger()
    first = admit(led, owner)
    led.finish(first.claim, "unknown", {"reason": "timeout"})
    q = enqueue(led, owner)
    assert q.result == "queued" and q.position == 1 and q.barrier["ledger_id"] == first.entry["id"]
    assert q.barrier["reason"] == "unknown"


def test_the_queued_answer_says_paused_on_a_real_store(owner):
    from types import SimpleNamespace
    from minimoi_portal.guild_ui.mc import ledger_gate
    led = ledger()
    services = SimpleNamespace(turn_ledger=led)
    env = {ledger_gate.FLAG: "1"}
    clear = ledger_gate.enqueue(services, env, principal=owner, conversation_id="c-1",
                                note_request_id="note-clear-0001", request_id="req-clear-0001")
    assert clear["paused"] is False and clear["pause"] is None and clear["position"] == 1
    first = led.claim_queued(owner, clear["ledger_id"])
    led.finish(first.claim, "failed", {"failure_class": "upstream"})
    body = ledger_gate.enqueue(services, env, principal=owner, conversation_id="c-1",
                               note_request_id="note-held-0001", request_id="req-held-0001")
    assert body["status"] == "queued" and body["position"] == 1
    assert body["paused"] is True and body["pause"] == {"ledger_id": clear["ledger_id"], "reason": "failed"}
    again = ledger_gate.enqueue(services, env, principal=owner, conversation_id="c-1",
                                note_request_id="note-held-0001", request_id="req-held-0001")     # exact replay
    assert again["ledger_id"] == body["ledger_id"] and again["position"] == 1
    assert again["paused"] is True and again["pause"] == body["pause"]


def test_exact_replay_returns_the_same_waiting_entry_and_a_mismatch_is_refused(owner):
    led = ledger()
    q = enqueue(led, owner, note="note-replay-0001")
    again = enqueue(led, owner, note="note-replay-0001")
    assert again.result == "queued" and again.entry["id"] == q.entry["id"] and again.position == 1
    assert again.barrier is None
    with pytest.raises(ValueError, match="request_mismatch"):
        enqueue(led, owner, conv="c-other", note="note-replay-0001")
    # the same ids through the immediate path never dispatch it
    assert admit(led, owner, note="note-replay-0001").result == "queued"
    assert states(owner) == ["queued"]


# ── sending: the head only ────────────────────────────────────────────────────

def test_only_the_head_can_be_claimed(owner):
    led = ledger()
    a, b = enqueue(led, owner), enqueue(led, owner)
    nope = led.claim_queued(owner, b.entry["id"])
    assert nope.result == "not_next" and nope.position == 2 and states(owner) == ["queued", "queued"]
    got = led.claim_queued(owner, a.entry["id"])
    assert got.result == "claimed" and got.claim.generation == 1 and got.entry["queued_at"]
    assert states(owner) == ["claimed", "queued"]
    assert led.status(owner)["running"]["id"] == a.entry["id"]


def test_the_head_waits_while_another_turn_runs(owner):
    led = ledger()
    running = admit(led, owner)
    q = enqueue(led, owner)
    assert led.claim_queued(owner, q.entry["id"]).result == "busy"
    led.finish(running.claim, "answered", {})
    assert led.claim_queued(owner, q.entry["id"]).result == "claimed"


def test_a_pause_holds_the_whole_queue_until_continue(owner):
    led = ledger()
    a, b = enqueue(led, owner), enqueue(led, owner)
    claimed = led.claim_queued(owner, a.entry["id"])
    led.finish(claimed.claim, "failed", {"failure_class": "upstream"})
    held = led.claim_queued(owner, b.entry["id"])
    assert held.result == "paused" and held.barrier["ledger_id"] == a.entry["id"]
    assert led.continue_after(owner, a.entry["id"])["result"] == "continued"
    assert led.claim_queued(owner, b.entry["id"]).result == "claimed"


def test_two_workers_claiming_the_head_send_it_once(owner):
    q = enqueue(ledger(), owner)
    results, gate = [], threading.Barrier(2)

    def go(worker):
        gate.wait()
        results.append(ledger(worker=worker).claim_queued(owner, q.entry["id"]).result)
    threads = [threading.Thread(target=go, args=(w,)) for w in ("w1", "w2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["busy", "claimed"]          # the loser sees the winner's claim
    assert states(owner) == ["claimed"]


def test_an_entry_that_is_not_waiting_or_not_yours_is_gone(owner):
    led = ledger()
    done = admit(led, owner)
    led.finish(done.claim, "answered", {})
    assert led.claim_queued(owner, done.entry["id"]).result == "gone"
    q = enqueue(led, owner)
    assert led.claim_queued("someone-else-" + owner, q.entry["id"]).result == "gone"
    with pytest.raises(ValueError):
        led.claim_queued(owner, 0)


# ── not sent after all: back to the head ──────────────────────────────────────

def test_requeue_puts_the_turn_back_first_in_line(owner):
    led = ledger()
    a, b = enqueue(led, owner), enqueue(led, owner)
    claimed = led.claim_queued(owner, a.entry["id"])
    assert led.requeue(claimed.claim) is True
    assert states(owner) == ["queued", "queued"]
    assert led.claim_queued(owner, b.entry["id"]).result == "not_next"
    again = led.claim_queued(owner, a.entry["id"])
    assert again.result == "claimed" and again.claim.generation == 2


def test_requeue_needs_the_current_claim_and_a_turn_that_waited(owner):
    led = ledger()
    q = enqueue(led, owner)
    claimed = led.claim_queued(owner, q.entry["id"])
    assert led.requeue(Claim(claimed.claim.ledger_id, "0" * 32, claimed.claim.generation)) is False
    assert led.requeue(Claim(claimed.claim.ledger_id, claimed.claim.token, 99)) is False
    led.finish(claimed.claim, "answered", {})
    immediate = admit(led, owner)
    assert led.requeue(immediate.claim) is False            # never queued: it cannot be put in a queue
    assert states(owner) == ["answered", "claimed"]


# ── cancel and expiry ────────────────────────────────────────────────────────

def test_cancel_only_a_waiting_turn(owner):
    led = ledger()
    a, b = enqueue(led, owner), enqueue(led, owner)
    assert led.cancel(owner, a.entry["id"])["result"] == "cancelled"
    assert led.claim_queued(owner, b.entry["id"]).result == "claimed"     # b is the head now
    out = led.cancel(owner, b.entry["id"])
    assert out["result"] == "not_queued" and out["state"] == "claimed"
    assert led.cancel("someone-else-" + owner, b.entry["id"])["state"] is None
    assert states(owner) == ["cancelled", "claimed"]


def test_an_old_head_expires_and_the_turns_after_it_are_cancelled_with_their_own_reason(owner):
    led = ledger()
    a, b, c = enqueue(led, owner), enqueue(led, owner), enqueue(led, owner)
    _sql("UPDATE guild.mc_turn_ledger SET queued_at = now() - make_interval(secs => %s) WHERE id = %s",
         (QUEUE_TTL_S + 60, a.entry["id"]))
    view = led.status(owner)
    assert view["queued"] == [] and view["pause"] is None
    assert states(owner) == ["expired", "cancelled", "cancelled"]
    seen = {e["id"]: e for e in view["recent"]}
    assert seen[a.entry["id"]]["outcome"]["reason"] == "queue_expired"
    # b and c did not wait 24 hours: they are cancelled because the message before them expired
    assert seen[b.entry["id"]]["outcome"]["reason"] == "earlier_expired"
    assert seen[c.entry["id"]]["outcome"]["reason"] == "earlier_expired"
    assert led.claim_queued(owner, b.entry["id"]).result == "gone"
    assert admit(led, owner).result == "claimed"            # nothing waits any more


def test_a_young_queue_is_left_alone(owner):
    led = ledger()
    a, b = enqueue(led, owner), enqueue(led, owner)
    _sql("UPDATE guild.mc_turn_ledger SET queued_at = now() - make_interval(secs => %s) WHERE id = %s",
         (QUEUE_TTL_S - 60, a.entry["id"]))
    assert [e["id"] for e in led.status(owner)["queued"]] == [a.entry["id"], b.entry["id"]]


def test_expiry_never_touches_a_running_turn(owner):
    led = ledger()
    a, b = enqueue(led, owner), enqueue(led, owner)
    claimed = led.claim_queued(owner, a.entry["id"])
    _sql("UPDATE guild.mc_turn_ledger SET queued_at = now() - make_interval(secs => %s) WHERE principal = %s",
         (QUEUE_TTL_S + 60, owner))
    led.status(owner)
    assert states(owner) == ["claimed", "expired"]
    assert led.finish(claimed.claim, "answered", {}) is True


def test_status_lists_waiting_turns_in_order_with_the_running_one_apart(owner):
    led = ledger()
    a, b, c = enqueue(led, owner), enqueue(led, owner), enqueue(led, owner)
    led.claim_queued(owner, a.entry["id"])
    view = led.status(owner)
    assert view["running"]["id"] == a.entry["id"]
    assert [e["id"] for e in view["queued"]] == [b.entry["id"], c.entry["id"]]


def test_confirmed_owner_stop_releases_next_turn_without_replaying_stopped_work(owner):
    led = ledger()
    first = admit(led, owner, note="note-stop-0001")
    waiting = enqueue(led, owner, note="note-after-stop-0001")
    assert led.finish(first.claim, "cancelled", {"status": "stopped", "reason": "owner_stop_acknowledged"})
    assert led.status(owner)["pause"] is None
    assert admit(led, owner, note="note-stop-0001").result == "done"
    assert not led.finish(first.claim, "answered", {"status": "answered"})
    assert led.claim_queued(owner, first.entry["id"]).result == "gone"
    assert led.claim_queued(owner, waiting.entry["id"]).result == "claimed"


def test_a_dispatched_cancellation_cannot_silently_clear_uncertainty(owner):
    led = ledger()
    first = admit(led, owner)
    waiting = enqueue(led, owner)
    with pytest.raises(ValueError, match="acknowledged owner Stop"):
        led.finish(first.claim, "cancelled", {"status": "stopped"})
    assert led.status(owner)["running"]["id"] == first.entry["id"]
    assert led.finish(first.claim, "unknown", {"status": "stopped"})
    assert led.status(owner)["pause"]["reason"] == "unknown"
    assert led.claim_queued(owner, waiting.entry["id"]).result == "paused"
