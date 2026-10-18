"""A PRODUCTION Master Craftsman conversation begun in April and continued in May is active in May in production's Vault
(final composition of the sync, fix1-r2, MC capture and turn-dates packages, 17 Oct 2026).

The whole trip runs with every real job, as in ``test_mc_e2e``: the portal's conversation store and floor, ``vault-mc-export``,
``vault-mc-pull`` with the approval-gated ``minimoi-mc`` capture, ``vault-sync``, ``vault-apply``. Note times are written the way
production's Postgres floor gives them to the exporter (``str(datetime)``: a space, not a ``T``), which MC normalizer 1 dropped.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from core.memory_index import indexer, policy, search, store
from tests.memory_shelf.test_mc_capture import PRINCIPAL
from tests.vault.test_mc_e2e import Trip
from tests.vault.test_vault_apply import NeverCalled

RAW = policy.Caller(tier="all")
APRIL = ("2026-04-28 21:00:00+00:00", "2026-04-28 21:00:05+00:00")
MAY = ("2026-05-03 09:30:00.123456+00:00", "2026-05-04 08:00:00+00:00")


@pytest.fixture
def t(tmp_path, monkeypatch):
    monkeypatch.setenv("MOI_RESERVE_GIB", "0")
    monkeypatch.delenv("MOI_BATCH_DEADLINE_EPOCH", raising=False)
    trip = Trip(tmp_path, monkeypatch)
    trip.converse("mount", ("owner", "Plan the telescope mount"), ("mc", "Start with the tripod"))
    backdate(trip, trip.convs["mount"], APRIL, conversation="2026-04-28T21:00:00+00:00")
    trip.seed()
    return trip


def backdate(trip, conv, stamps, conversation=None):
    """Production's floor gave these notes these times (the newest len(stamps) notes of the conversation)."""
    con = sqlite3.connect(trip.portal.db.path)
    ids = [r[0] for r in con.execute("SELECT id FROM floor_messages ORDER BY id")]
    for note_id, stamp in zip(ids[-len(stamps):], stamps):
        con.execute("UPDATE floor_messages SET created_at=? WHERE id=?", (stamp, note_id))
    con.commit()
    con.close()
    if conversation:
        path = trip.portal.guild / "conversations" / f"{conv['id']}.json"
        doc = json.loads(path.read_text())
        doc["created_at"] = conversation
        path.write_text(json.dumps(doc))


def may(t, monkeypatch, query="What were we doing in May?"):
    with monkeypatch.context() as m:
        for mod in (store, indexer, search):
            m.setattr(mod, "sqlite_vec", None)
        return search.search(query, shelf_root=t.vault / "current" / "shelf", embedder=NeverCalled(),
                             index_path=t.vault / "current" / "index" / "memory.sqlite", caller=RAW, k=5,
                             created_after="2026-05-01", created_before="2026-06-01")


def production_record_id(t):
    return t.production_records()[t.key("mount")]["id"]


def test_a_production_mc_conversation_begun_in_april_and_continued_in_may_is_active_in_may(t, monkeypatch):
    assert t.export()["sent"] == 1
    t.approve()
    t.clock.advance(minutes=10)
    pulled, synced, recon = t.carry()
    assert pulled["results"]["capture"] == "ok" and recon["in_vault_current"] == 1
    rid = production_record_id(t)
    assert rid not in {h["record_id"] for h in may(t, monkeypatch)["keyword"]}   # April only, so far

    # The owner comes back to the same conversation in May (production), and the trip runs again.
    t.portal.say(t.convs["mount"], ("owner", "Back again: the counterweight wobbles"), ("mc", "Tighten the clutch"))
    backdate(t, t.convs["mount"], MAY)
    t.clock.advance(hours=2)
    out = t.export()
    assert out["changed"] == 1 and out["sent"] == 1
    t.clock.advance(minutes=30)
    pulled, synced, recon = t.carry()
    assert recon["in_vault_current"] == 1 and recon["in_vault_stale"] == 0
    assert production_record_id(t) == rid                                         # the same record, a new edition

    con = sqlite3.connect(f"file:{t.vault / 'current' / 'index' / 'memory.sqlite'}?mode=ro&immutable=1", uri=True)
    try:
        dated = con.execute("SELECT turn, created FROM chunks WHERE record_id=? ORDER BY turn", (rid,)).fetchall()
        span = con.execute("SELECT first_activity, last_activity, timed_turns FROM records WHERE record_id=?", (rid,)).fetchone()
    finally:
        con.close()
    assert dated == [(1, "2026-04-28T21:00:00Z"), (2, "2026-04-28T21:00:05Z"), (3, "2026-05-03T09:30:00Z"),
                     (4, "2026-05-04T08:00:00Z")]
    assert span == ("2026-04-28T21:00:00Z", "2026-05-04T08:00:00Z", 4)
    r = may(t, monkeypatch)
    got = {h["record_id"]: h for h in r["keyword"]}
    assert rid in got and "counterweight wobbles" in got[rid]["passage"] and "telescope" not in got[rid]["passage"]
    assert (got[rid]["date"], got[rid]["started"]) == ("2026-05-03", "2026-04-28")
    assert r["overview"]["by_provider"] == {"minimoi-mc": 1} and r["overview"]["continued_from_before"] == 1
    assert r["overview"]["dated_by_start_only"] == 0
    assert PRINCIPAL                                                               # the owner's own conversation (export rules)
