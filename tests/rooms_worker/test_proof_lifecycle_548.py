"""#548: proof lifecycle, an unproven invite, and proof meetings tidying themselves away.

Production evidence (read-only, 13 Nov 02:52Z): Records' production database was prepared on 9 Nov 16:41Z, the
CoS and MC cards were created at 21:05Z with no proof, and the first proof turn in that database ever is
13 Nov 02:22Z. Nothing in Records clears ``proven_at``: a proof belongs to one Records database, so a fresh
database starts unproven. These tests pin that a proof does not lapse, that an unproven invite waits on Prove
first instead of reading as a timed-out invite, and that a finished proof room leaves the Active list.

Records runs in-process on a temporary folder; the worker relay is a scripted fake (no network, no model, no spend).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "guild" / "shop_floor"))
from rooms_helpers import RECORDS_DIR, records_app  # noqa: E402

from services.rooms_worker.clients import Records  # noqa: E402
from services.rooms_worker.journal import TurnJournal  # noqa: E402
from services.rooms_worker.worker import Worker  # noqa: E402

sys.path.insert(0, str(ROOT / "tests" / "rooms_worker"))
from worker_helpers import FakeClock, FakeRelay, Session  # noqa: E402

BACKEND = "http://minimoi-records:18880"


def _manage():
    name = "records_poc_manage_for_tests"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, RECORDS_DIR / "manage.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _stamp(delta_s=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def env(tmp_path):
    app = records_app(tmp_path / "records")
    store = app.extensions["records_store"]
    out = tmp_path / "outbox"
    _manage().provision_rooms(store, "mc", "Master Craftsman", out)
    tokens = {n: (out / f"{n}.token").read_text().strip() for n in ("mc", "rooms-worker")}
    worker = Worker(Records(BACKEND, tokens["rooms-worker"], session=Session(app)),
                    Records(BACKEND, tokens["mc"], session=Session(app)), FakeRelay(),
                    TurnJournal(tmp_path / "journal"), clock=FakeClock())
    owner = app.test_client(use_cookies=False)

    def call(method, path, body=None):
        headers = {"Authorization": "Bearer " + store.owner_key}
        if method != "GET":
            headers["Idempotency-Key"] = str(uuid4())
        r = owner.open("/api/v1" + path, method=method, headers=headers, json=body, base_url=BACKEND)
        return r.status_code, (r.json if r.data else None)

    room = call("POST", "/rooms", dict(title="Robert's meeting", purpose="Synthetic", recording_acknowledged=True))[1]["result"]["id"]

    def housekeep():
        worker._last_hosted = 0
        worker.housekeeping()

    def participant(who="mc", where=None):
        body = call("GET", f"/rooms/{where or room}/turns")[1]
        return [p for p in body["participants"] if p["id"] == who][0]

    def active_rooms():
        return {r["title"] for r in call("GET", "/rooms?view=active")[1]["rooms"]}

    def archived_rooms():
        return {r["title"] for r in call("GET", "/rooms?view=archived")[1]["rooms"]}

    return SimpleNamespace(app=app, store=store, worker=worker, call=call, room=room, housekeep=housekeep,
                           participant=participant, active_rooms=active_rooms, archived_rooms=archived_rooms)


def _prove_through_worker(env):
    status, body = env.call("POST", "/teammates/mc/prove", {"confirm": True})
    assert status == 202, body
    env.worker._last_hosted = 0
    env.worker.run_once()                     # accept the proof invite, claim, answer
    proof = env.call("GET", "/teammates/mc/prove")[1]
    assert proof["proven_at"] and proof["turn"]["state"] == "committed"
    return proof


# ── cause: a proof does not lapse ───────────────────────────────────────────

def test_a_fresh_records_database_starts_unproven_and_says_so(env):
    """The production cause: cards are created unproven in a new database, and nothing proves them for Robert."""
    card = [c for c in env.call("GET", "/teammates")[1]["teammates"] if c["principal"] == "mc"][0]
    assert card["proven_at"] is None and card["connected"] is True


def test_proven_survives_restart_card_update_and_failures(env, tmp_path):
    proof = _prove_through_worker(env)
    proven_at = proof["proven_at"]
    # A card edit (the release bind re-applies card fields) keeps the proof.
    assert env.call("PUT", "/teammates/mc", {"rsvp_timeout_s": 90})[0] == 200
    # A failed reply records last_failure_at but never clears the proof.
    with env.store.connect() as d:
        d.execute("UPDATE teammates SET last_failure_at=? WHERE principal='mc'", (_stamp(),))
    # A Records restart on the same state (a new app on the same folder) keeps it.
    again = records_app(tmp_path / "records")
    with again.extensions["records_store"].connect() as d:
        row = d.execute("SELECT proven_at FROM teammates WHERE principal='mc'").fetchone()
    assert row["proven_at"] == proven_at
    assert env.call("GET", "/teammates/mc/prove")[1]["proven_at"] == proven_at


def test_no_records_code_path_clears_proven_at():
    """Evidence for the cause, kept honest: only the proof commit writes proven_at, and nothing sets it back to NULL."""
    source = (ROOT / "domains" / "records" / "meetings.py").read_text()
    writes = [line.strip() for line in source.splitlines() if "SET proven_at" in line]
    assert writes == ['db.execute("UPDATE teammates SET proven_at=?,updated=? WHERE principal=?",']
    assert "proven_at=NULL" not in source.replace(" ", "")


# ── an unproven invite waits on Prove first, never a silent timeout ─────────

def test_unproven_invite_reads_needs_proof_not_timed_out(env):
    env.housekeep()                                      # the worker checks in: a route exists here (FF-05)
    assert env.call("POST", f"/rooms/{env.room}/invite", {"actor": "mc"})[0] == 201
    env.housekeep()                                      # the worker sees the invite and (rightly) ignores it
    mc = env.participant()
    assert mc["rsvp"] == "invited" and mc["needs_proof"] is True
    # Two minutes later it still says what joins it, not "no_response".
    with env.store.connect() as d:
        d.execute("UPDATE member_rsvp SET rsvp_at=? WHERE actor='mc'", (_stamp(-600),))
    mc = env.participant()
    assert mc["rsvp"] == "invited" and mc["needs_proof"] is True
    assert mc["reach"]["reason"] == "not yet proven — Prove first"


def test_prove_then_the_pending_invite_joins_on_the_next_check_in(env):
    env.housekeep()
    assert env.call("POST", f"/rooms/{env.room}/invite", {"actor": "mc"})[0] == 201
    _prove_through_worker(env)
    env.housekeep()                                      # next check-in (every 10 s in production)
    mc = env.participant()
    assert mc["rsvp"] == "accepted" and mc["needs_proof"] is False


def test_a_proven_teammate_that_does_not_answer_still_times_out(env):
    """The timeout keeps its meaning for a proven teammate whose worker is away."""
    with env.store.connect() as d:
        d.execute("UPDATE teammates SET proven_at=?,auto_accept=0 WHERE principal='mc'", (_stamp(-60),))
    assert env.call("POST", f"/rooms/{env.room}/invite", {"actor": "mc"})[0] == 201
    with env.store.connect() as d:
        d.execute("UPDATE member_rsvp SET rsvp_at=? WHERE actor='mc'", (_stamp(-600),))
    mc = env.participant()
    assert mc["rsvp"] == "no_response" and mc["needs_proof"] is False


# ── proof meetings tidy themselves away ─────────────────────────────────────

def test_finished_proof_room_leaves_the_active_list_and_is_kept(env):
    proof = _prove_through_worker(env)
    active, archived = env.active_rooms(), env.archived_rooms()
    assert "Robert's meeting" in active
    assert not any(t.startswith("Proof:") for t in active)
    assert any(t.startswith("Proof: Master Craftsman") for t in archived)
    # The record is kept: the proof's question and reply are still there.
    events = env.call("GET", f"/rooms/{proof['proof_room']}")[1]["events"]
    assert any(e["actor"] == "mc" and e["kind"] == "message" for e in events)


def test_proof_room_still_answering_stays_visible(env):
    assert env.call("POST", "/teammates/mc/prove", {"confirm": True})[0] == 202
    assert any(t.startswith("Proof:") for t in env.active_rooms())


def test_failed_proof_room_is_archived_too(env):
    status, body = env.call("POST", "/teammates/mc/prove", {"confirm": True})
    room = body["result"]["proof_room"]
    with env.store.connect() as d:
        d.execute("UPDATE turns SET state='expired',disposition='not_answered_busy' WHERE room=?", (room,))
    env.call("GET", "/teammates/mc/prove")                # any meeting route sweeps
    assert not any(t.startswith("Proof:") for t in env.active_rooms())
    assert env.call("GET", "/teammates/mc/prove")[1]["proven_at"] is None


def test_existing_finished_proof_rooms_are_tidied_by_the_sweep(env):
    """The two proof rooms already in production's Active list are archived on the first meeting read after deploy."""
    proof = _prove_through_worker(env)
    with env.store.connect() as d:
        d.execute("DELETE FROM room_archive WHERE room=?", (proof["proof_room"],))    # as before this change
    assert any(t.startswith("Proof:") for t in env.active_rooms())
    env.call("GET", "/teammates/mc/prove")                # a meeting route runs the sweep
    assert not any(t.startswith("Proof:") for t in env.active_rooms())


def test_proof_room_robert_used_or_restored_stays_where_he_put_it(env):
    proof = _prove_through_worker(env)
    room = proof["proof_room"]
    # Restore it: new activity brings it back and the sweep does not archive it again.
    assert env.call("POST", f"/rooms/{room}/events", {"body": "keeping this one"})[0] == 201
    env.call("GET", "/teammates/mc/prove")
    assert any(t.startswith("Proof:") for t in env.active_rooms())
