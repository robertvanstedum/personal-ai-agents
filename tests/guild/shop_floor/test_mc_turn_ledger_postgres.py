"""The durable MC turn ledger on a real Postgres (sql/007_mc_turn_ledger.sql, mc/turn_ledger.py).

Skipped unless GUILD_FLOOR_TEST_DATABASE_URL names a disposable test database with migrations
001–006 applied. These tests apply 007 themselves (twice: it is repeatable). Every row they write
belongs to a principal named ``test-ledger-<random>`` and is removed at the end. The rollback test
runs last and only checks that 007's down migration REFUSES while a turn is running.
A skip here is not acceptance: the operator runs this file against the scratch database.
"""
from __future__ import annotations

import os
from pathlib import Path
import threading
import time
import uuid

import pytest

from minimoi_portal.guild_ui.mc.turn_ledger import Claim, TurnLedger

URL = os.environ.get("GUILD_FLOOR_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="GUILD_FLOOR_TEST_DATABASE_URL is not set")
SQL = Path(__file__).resolve().parents[3] / "minimoi_portal/guild_ui/sql"


def _exec_file(name):
    import psycopg2
    conn = psycopg2.connect(URL, connect_timeout=4)
    conn.autocommit = True                     # the file carries its own BEGIN/COMMIT
    try:
        with conn.cursor() as cur:
            cur.execute((SQL / name).read_text())
    finally:
        conn.close()


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
    _exec_file("007_mc_turn_ledger.sql")
    _exec_file("007_mc_turn_ledger.sql")      # repeatable
    yield


@pytest.fixture
def owner():
    principal = "test-ledger-" + uuid.uuid4().hex[:10]
    yield principal
    _sql("DELETE FROM guild.mc_turn_barrier WHERE principal = %s", (principal,))
    _sql("DELETE FROM guild.mc_turn_ledger WHERE principal = %s", (principal,))


def ledger(**kw):
    return TurnLedger(lambda: URL, worker="test", **kw)


def admit(led, owner, conv="c-1", note=None, **kw):
    note = note or "note-" + uuid.uuid4().hex
    return led.admit(principal=owner, conversation_id=conv, note_request_id=note,
                     turn_request_id="turn-" + note, **kw)


def rows(owner):
    return _sql("SELECT id, state FROM guild.mc_turn_ledger WHERE principal = %s ORDER BY id", (owner,))


# ── one running turn per owner, across conversations and workers ──────────────

def test_first_turn_is_recorded_and_claimed_before_dispatch(owner):
    a = admit(ledger(), owner, attachment_ids=["d-0123456789abcdef"],
              topic_refs=[{"topic_id": "t-0123456789ab", "item_id": "i-0123456789ab"}])
    assert a.result == "claimed" and a.claim is not None and a.entry["state"] == "claimed"
    assert a.entry["attachment_ids"] == ["d-0123456789abcdef"]
    assert a.entry["topic_refs"] == [{"topic_id": "t-0123456789ab", "item_id": "i-0123456789ab", "rev": None}]
    assert rows(owner) == [(a.entry["id"], "claimed")]


def test_busy_refusal_in_another_conversation_records_nothing(owner):
    led = ledger()
    first = admit(led, owner, conv="c-1")
    second = admit(led, owner, conv="c-2")
    assert second.result == "busy" and second.claim is None and second.entry["id"] == first.entry["id"]
    assert len(rows(owner)) == 1                                     # no silent queued row, no barrier
    assert _sql("SELECT count(*) FROM guild.mc_turn_barrier WHERE principal = %s", (owner,))[0][0] == 0


def test_concurrent_admissions_from_two_workers_claim_exactly_once(owner):
    results = []
    barrier = threading.Barrier(6)

    def go(i):
        barrier.wait()
        results.append(admit(TurnLedger(lambda: URL, worker=f"w{i}"), owner, conv=f"c-{i}").result)
    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results).count("claimed") == 1 and results.count("busy") == 5
    assert [s for _, s in rows(owner)] == ["claimed"]


def test_partial_unique_index_is_a_backstop_even_without_the_lock(owner):
    import psycopg2
    admit(ledger(), owner)
    with pytest.raises(psycopg2.IntegrityError):
        _sql("INSERT INTO guild.mc_turn_ledger (principal, conversation_id, note_request_id, turn_request_id, kind, "
             "state, claim_token, lease_until) VALUES (%s, 'c-x', %s, %s, 'typed', 'claimed', 'x', now())",
             (owner, "n-" + uuid.uuid4().hex, "t-" + uuid.uuid4().hex))


# ── completed and running entries are never dispatched again ──────────────────

def test_same_note_while_running_says_running_and_after_finish_returns_prior_outcome(owner):
    led = ledger()
    a = admit(led, owner, note="note-same-" + owner)
    again = admit(led, owner, note="note-same-" + owner)
    assert again.result == "running" and again.claim is None
    assert led.finish(a.claim, "answered", {"turn_id": "abc", "backend_kind": "platform",
                                            "operation": {"type": "board_postit", "saved": True, "id": 7}})
    done = admit(led, owner, note="note-same-" + owner)
    assert done.result == "done" and done.claim is None
    assert done.entry["outcome"] == {"turn_id": "abc", "backend_kind": "platform",
                                     "operation": {"type": "board_postit", "saved": True, "id": 7}}
    assert len(rows(owner)) == 1


def test_exact_replay_of_a_not_sent_turn_returns_it_and_is_never_redispatched(owner):
    led = ledger()
    a = admit(led, owner, note="note-a-" + owner)
    led.finish(a.claim, "not_sent", {"failure_class": "mc_unavailable"})
    again = admit(led, owner, note="note-a-" + owner)
    assert again.result == "done" and again.entry["id"] == a.entry["id"] and again.entry["state"] == "not_sent"
    assert len(rows(owner)) == 1


@pytest.mark.parametrize("change", [dict(conversation_id="c-other"), dict(turn_request_id="turn-different"),
                                    dict(note_request_id="note-different"), dict(kind="voice"),
                                    dict(attachment_ids=["d-0123456789abcdef"]),
                                    dict(topic_refs=[{"topic_id": "t-0123456789ab", "item_id": "i-0123456789ab"}])])
def test_mismatched_replay_is_refused_never_answered_with_an_unrelated_outcome(owner, change):
    led = ledger()
    note = "note-m-" + owner
    a = admit(led, owner, note=note)
    led.finish(a.claim, "answered", {"turn_id": "t1"})
    args = dict(principal=owner, conversation_id="c-1", note_request_id=note, turn_request_id="turn-" + note, kind="typed")
    args.update(change)            # a new note keeps the old turn id; a new turn id keeps the old note
    with pytest.raises(ValueError, match="request_mismatch"):
        led.admit(**args)
    assert len(rows(owner)) == 1


def test_another_owners_request_id_is_refused(owner):
    led = ledger()
    a = admit(led, owner, note="note-x-" + owner)
    other = "test-ledger-" + uuid.uuid4().hex[:10]
    try:
        with pytest.raises(ValueError):
            led.admit(principal=other, conversation_id="c-1", note_request_id="note-x-" + owner, turn_request_id="t-other")
    finally:
        _sql("DELETE FROM guild.mc_turn_ledger WHERE principal = %s", (other,))
    led.finish(a.claim, "answered")


# ── claims: token and generation are the authority ────────────────────────────

def test_only_the_claim_token_and_generation_can_finish_or_heartbeat(owner):
    led = ledger()
    a = admit(led, owner)
    forged = Claim(a.claim.ledger_id, "f" * 32, a.claim.generation)
    stale = Claim(a.claim.ledger_id, a.claim.token, a.claim.generation + 1)
    assert not led.heartbeat(forged) and not led.finish(forged, "answered")
    assert not led.heartbeat(stale) and not led.finish(stale, "answered")
    assert led.heartbeat(a.claim) and led.finish(a.claim, "answered")
    assert not led.finish(a.claim, "failed")                         # finished once
    assert rows(owner)[0][1] == "answered"


def test_a_finished_owner_can_start_the_next_turn(owner):
    led = ledger()
    led.finish(admit(led, owner).claim, "answered")
    assert admit(led, owner).result == "claimed"


# ── leases: expiry means unknown and a persisted pause, never a replay ────────

def test_expired_lease_becomes_unknown_with_a_pause_and_is_never_replayed(owner):
    led = ledger(lease_s=1)
    a = admit(led, owner)
    time.sleep(1.6)
    view = led.status(owner)
    assert [e["id"] for e in view["released"]] == [a.entry["id"]]
    assert view["pause"]["ledger_id"] == a.entry["id"] and view["pause"]["reason"] == "unknown"
    assert rows(owner)[0][1] == "unknown"
    assert not led.heartbeat(a.claim) and not led.finish(a.claim, "answered")    # the old worker cannot overwrite
    assert admit(led, owner).result == "paused"                                  # nothing new until Continue
    assert admit(led, owner, note=a.entry["note_request_id"]).result == "done"    # and never replayed


def test_late_finish_before_anyone_looked_still_records_the_real_outcome(owner):
    led = ledger(lease_s=1)
    a = admit(led, owner)
    time.sleep(1.4)
    assert led.finish(a.claim, "answered", {"turn_id": "late"})
    assert rows(owner)[0][1] == "answered" and led.status(owner)["pause"] is None


def test_heartbeat_keeps_a_long_turn_alive(owner):
    led = ledger(lease_s=2)
    a = admit(led, owner)
    with led.held(a.claim, interval_s=0.5):
        time.sleep(3)
        assert led.status(owner)["running"]["id"] == a.entry["id"]
    assert led.finish(a.claim, "answered")


# ── pause and Continue ────────────────────────────────────────────────────────

def test_unknown_finish_pauses_and_only_a_matching_continue_lifts_it(owner):
    led = ledger()
    a = admit(led, owner)
    led.finish(a.claim, "unknown", {"failure_class": "timeout_uncertain"})
    assert admit(led, owner).result == "paused"
    assert led.continue_after(owner, a.entry["id"] + 999)["result"] == "not_paused"
    out = led.continue_after(owner, a.entry["id"])
    assert out["result"] == "continued" and "may still be running" in out["message"]
    assert admit(led, owner).result == "claimed"


def test_failure_after_dispatch_pauses_even_for_the_first_immediate_turn(owner):
    led = ledger()
    a = admit(led, owner)
    led.finish(a.claim, "failed", {"failure_class": "runtime_error"})
    view = led.status(owner)
    assert view["pause"]["ledger_id"] == a.entry["id"] and view["pause"]["reason"] == "failed"
    assert admit(led, owner).result == "paused"
    out = led.continue_after(owner, a.entry["id"])
    assert out["result"] == "continued" and admit(led, owner).result == "claimed"


def test_refusal_before_dispatch_is_not_sent_and_does_not_pause(owner):
    led = ledger()
    led.finish(admit(led, owner).claim, "not_sent", {"failure_class": "not_connected"})
    assert led.status(owner)["pause"] is None and admit(led, owner).result == "claimed"


@pytest.mark.parametrize("bad", [{"reply": "x"}, {"message": "hello"}, {"secret": "s"},
                                 {"operation": {"type": "board_postit", "text": "buy milk"}},
                                 {"failure_class": "Has Spaces"}, {"turn_id": "x" * 81}, {"reply_note_id": "7"}])
def test_outcomes_are_allowlisted_and_refused_otherwise(owner, bad):
    led = ledger()
    a = admit(led, owner)
    with pytest.raises(ValueError):
        led.finish(a.claim, "answered", bad)
    assert rows(owner)[0][1] == "claimed"                                            # nothing was written
    assert led.finish(a.claim, "answered", {"turn_id": "t1", "backend_kind": "platform", "reply_note_id": 9,
                                            "operation": {"type": "board_postit", "saved": True, "id": 3}})


# ── rollback refuses while work is running (runs last) ────────────────────────

def _down_refused():
    import psycopg2
    with pytest.raises(psycopg2.Error):
        _exec_file("007_mc_turn_ledger_down.sql")
    assert _sql("SELECT to_regclass('guild.mc_turn_ledger') IS NOT NULL")[0][0]


def test_zz_down_migration_refuses_while_a_turn_is_running(owner):
    admit(ledger(), owner)
    _down_refused()


def test_zz_down_migration_refuses_after_continue_cleared_an_unknown(owner):
    led = ledger()
    a = admit(led, owner)
    led.finish(a.claim, "unknown", {"failure_class": "local_timeout"})
    led.continue_after(owner, a.entry["id"])
    assert _sql("SELECT count(*) FROM guild.mc_turn_barrier WHERE principal = %s", (owner,))[0][0] == 0
    _down_refused()                                     # the unknown id is never-replay evidence


def test_zz_down_migration_refuses_with_only_finished_history(owner):
    led = ledger()
    led.finish(admit(led, owner).claim, "answered", {"turn_id": "t1"})
    _down_refused()
