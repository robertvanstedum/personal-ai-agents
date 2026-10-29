"""C2, the sources and their lifecycle rules (spec v0.2 §3.3–3.4): T5, T5b, T6, T7, T8, T9."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from minimoi_portal import auth, guest_requests as gr
from minimoi_portal.action_waiting import sources as src, store as st
from aw_fakes import FakeGuildDB
from aw_fakes import auth_dir, clock, make_settings, token_file

PYTEST_FIXTURES = (auth_dir, clock, make_settings, token_file,)                  # used by name in the tests below


@pytest.fixture
def world(tmp_path, auth_dir, make_settings, clock):
    db = FakeGuildDB()
    store = st.Store(tmp_path / "notices", clock=clock)
    rows = {"value": []}

    class World:
        pass
    w = World()
    w.db, w.store, w.clock, w.decisions = db, store, clock, rows
    w.settings = make_settings(supervision=True)
    w.sources = src.Sources(w.settings, store.digest, guild_db=db, decision_rows=lambda: rows["value"],
                            decision_state=lambda did: None, clock=clock)

    def sweep():
        snap = w.sources.collect()
        with store.edit() as tx:
            counts = src.reconcile(tx, snap, clock())
        w.snap = snap
        return counts
    w.sweep = sweep
    w.open = lambda: st.open_items(store.read())
    w.item = lambda code: store.read()["items"][code]
    return w


def _now_iso(clock, minus=0):
    return datetime.fromtimestamp(clock() - minus, timezone.utc).isoformat()


def test_invite_reason_matches_the_mc_tools_constant():
    from core.mc_tools.proposals import INVITE_REASON
    assert src.INVITE_REASON == INVITE_REASON


def test_open_with_coverage_reports_unavailable_never_none(auth_dir):
    db = FakeGuildDB()
    db.add(1, "a@x.test")
    out = gr.open_with_coverage(db)
    assert [r["id"] for r in out["guild"]] == [1] and out["pending"] == []
    assert " name" not in gr._SELECT_OPEN_WITH_REASON and "reason" in gr._SELECT_OPEN_WITH_REASON   # no names
    db.fail = True
    (auth_dir / "pending.json").write_text("{broken")
    assert gr.open_with_coverage(db) == {"guild": None, "pending": None}


def test_one_email_in_both_stores_is_one_item_and_answering_one_ref_resolves_it(world):
    """T5: /register writes pending + guild for one email → one item, two refs. Answering either ref resolves it."""
    auth.create_pending("Ann Guest", "ann@x.test", "pw-12345678")
    world.db.add(41, "ann@x.test")
    world.sweep()
    [item] = world.open()
    assert item["code"] == "W-1" and len(item["refs"]) == 2 and item["app"] == "Meu Português"
    blob = json.dumps(world.store.read())
    assert "ann@x.test" not in blob and "Ann Guest" not in blob
    token = auth.load_pending()[0]["token"]
    assert token not in blob
    world.db.rows[41]["status"] = "granted"                  # answered on Operate; the pending entry remains
    world.sweep()
    assert world.open() == [] and world.item("W-1")["resolution"] == "source_closed"
    world.sweep()                                            # the lingering pending entry is seen: nothing new
    assert world.open() == []


def test_answering_the_pending_ref_alone_also_resolves(world):
    auth.create_pending("Ann Guest", "ann@x.test", "pw-12345678")
    world.db.add(41, "ann@x.test")
    world.sweep()
    assert auth.pending_reject(auth.load_pending()[0]["token"]) == "rejected"   # answered on /admin/guests
    world.sweep()
    assert world.open() == []
    world.sweep()                                            # the guild row lingers as requested: already seen
    assert world.open() == []


def test_staggered_inserts_join_and_a_reregistration_after_an_answer_is_new(world):
    """T5b."""
    auth.create_pending("Ann Guest", "ann@x.test", "pw-12345678")
    world.sweep()
    [item] = world.open()
    assert item["app"] is None and len(item["refs"]) == 1
    world.db.add(41, "ann@x.test")                           # the guild insert seen one sweep later
    counts = world.sweep()
    [item] = world.open()
    assert counts["joined"] == 1 and item["code"] == "W-1" and item["app"] == "Meu Português"
    auth.create_pending("Ann Guest", "ann@x.test", "pw-12345678")   # re-submit swaps the token: joins, no new item
    world.sweep()
    assert [i["code"] for i in world.open()] == ["W-1"]
    auth.pending_reject(auth.load_pending()[0]["token"])     # answered on /admin/guests; guild row still requested
    world.sweep()
    assert world.open() == []
    auth.create_pending("Ann Guest", "ann@x.test", "pw-12345678")   # a new registration while the old guild ref lingers
    world.sweep()
    [item] = world.open()
    assert item["code"] == "W-2" and item["generation"] == 2 and any(r.startswith("guild:41@") for r in item["refs"])


def test_rerequest_after_rejection_is_new_and_a_flicker_is_not(world):
    """T6."""
    world.db.add(7, "bo@x.test")
    world.sweep()
    world.db.fail = True                                     # flicker: unavailable resolves nothing
    world.sweep()
    assert [i["code"] for i in world.open()] == ["W-1"] and world.snap.coverage["guild"] == "unavailable"
    world.db.fail = False
    world.sweep()
    assert [i["code"] for i in world.open()] == ["W-1"]
    world.db.rows[7]["status"] = "rejected"
    world.sweep()
    assert world.open() == []
    world.db.rows[7].update(status="requested", requested_at=world.db.rows[7]["requested_at"] + timedelta(days=1))
    world.sweep()
    [item] = world.open()
    assert item["code"] == "W-2" and item["generation"] == 2


def test_my_own_invite_is_not_waiting_on_me(world):
    world.db.add(9, "inv@x.test", reason=src.INVITE_REASON)
    world.sweep()
    assert world.open() == []


def test_unreadable_stores_resolve_nothing(world, auth_dir):
    """T7: Postgres down, pending.json malformed."""
    world.db.add(7, "bo@x.test")
    auth.create_pending("Cy", "cy@x.test", "pw-12345678")
    world.sweep()
    assert len(world.open()) == 2
    world.db.rows.clear()
    world.db.fail = True
    (auth_dir / "pending.json").write_text("{broken")
    world.sweep()
    assert len(world.open()) == 2
    assert world.snap.coverage["guild"] == "unavailable" and world.snap.coverage["pending"] == "unavailable"


def test_missing_or_replaced_pending_file_never_resolves_and_flags_source_lost(world, auth_dir):
    """T7 (Codex B1): a missing file is not 'empty'; a replaced file (new inode) loses its records honestly."""
    auth.create_pending("Cy", "cy@x.test", "pw-12345678")
    world.sweep()
    [item] = world.open()
    os.unlink(auth_dir / "pending.json")
    world.sweep()
    assert world.snap.coverage["pending"] == "missing" and [i["code"] for i in world.open()] == [item["code"]]
    # The file comes back as a different file (an image's empty copy): its entry is gone.
    tmp = auth_dir / "pending.json.new"
    tmp.write_text(json.dumps({"pending": []}))
    os.replace(tmp, auth_dir / "pending.json")
    world.sweep()
    [lost] = world.open()
    assert lost["source_lost"] is True and lost["state"] == "open"
    world.sweep()                                            # same file now: still never resolved from it
    assert world.open()[0]["source_lost"] is True
    events = (world.store.root / st.EVENTS).read_text()
    assert '"source_lost"' in events


def test_a_fresh_setup_without_a_reset_file_is_simply_empty(world, auth_dir):
    world.sweep()
    assert world.snap.coverage["resets"] == "ok"


def test_resets_new_consumed_expired_unreadable_and_joined(world, auth_dir, clock, monkeypatch):
    """T8."""
    monkeypatch.setattr(auth, "datetime", _FrozenDatetime(clock))
    req = auth.create_reset_request("guest_ab12cd34", "Ann Guest", "ann@x.test")
    world.sweep()
    [item] = world.open()
    assert item["kind"] == "password_reset" and item["facts"]["expires_at"] == pytest.approx(clock() + 48 * 3600)
    second = auth.create_reset_request("guest_ab12cd34", "Ann Guest", "ann@x.test")
    world.sweep()
    [joined] = world.open()
    assert joined["code"] == item["code"] and len(joined["refs"]) == 2
    (auth_dir / "reset_requests.json").write_text("{broken")
    world.sweep()
    assert world.snap.coverage["resets"] == "unavailable" and len(world.open()) == 1
    (auth_dir / "reset_requests.json").write_text(json.dumps({"requests": [req, second]}))
    world.sweep()                                            # same inode (written in place): accepted
    assert auth.consume_reset_request(req["id"]) and auth.consume_reset_request(second["id"])
    world.sweep()
    assert world.open() == [] and world.item(item["code"])["resolution"] == "source_closed"
    # Expiry: a request left alone past 48 h resolves as expired.
    auth.create_reset_request("guest_ff00ee11", "Bo", "bo@x.test")
    world.sweep()
    [late] = world.open()
    clock.advance(49 * 3600)
    world.sweep()
    assert world.open() == [] and world.item(late["code"])["resolution"] == "expired"


class _FrozenDatetime:
    """auth's datetime.now(timezone.utc) pinned to the test clock (fromisoformat etc. unchanged)."""

    def __init__(self, clock):
        self.clock = clock

    def now(self, tz=None):
        return datetime.fromtimestamp(self.clock(), tz or timezone.utc)

    def __getattr__(self, name):
        return getattr(datetime, name)


def test_decisions_by_supervisor_grace_resolve_and_skip(world, clock):
    """T9 (the source side; the send side is in the sender tests)."""
    created = datetime.fromtimestamp(clock(), timezone.utc)
    world.decisions["value"] = [
        {"id": 14, "requester": {"agent": "codex", "branch": "codex/x", "session": "abc12345"},
         "text": "Merge PR #9 with token ghp_ABCDEFGHIJKLMNOPQRSTUV", "state": "new", "created_at": created,
         "expires_at": created + timedelta(hours=24)},
        {"id": 15, "requester": {"agent": "claude-code"}, "text": "Deploy the bundle now please", "state": "sent",
         "created_at": created, "expires_at": created + timedelta(hours=24)}]
    world.sweep()
    items = {i["facts"]["decision"]: i for i in world.open()}
    assert items["D-14"]["delivery"]["notify_after"] == pytest.approx(clock() + 180)
    assert "ghp_" not in items["D-14"]["words"] and "[credential removed]" in items["D-14"]["words"]
    assert items["D-15"]["delivery"]["state"] == "by_supervisor"
    world.decisions["value"] = world.decisions["value"][1:]  # D-14 decided
    world.sweep()
    assert {i["facts"]["decision"] for i in world.open()} == {"D-15"}


def test_decisions_skipped_when_supervision_is_off_or_the_table_is_absent(world, make_settings, clock):
    off = src.Sources(make_settings(supervision=False), world.store.digest, guild_db=world.db,
                      decision_rows=lambda: pytest.fail("must not be read"), clock=clock)
    assert off.collect().coverage["decisions"] == "skipped"
    absent = src.Sources(make_settings(supervision=True), world.store.digest, guild_db=world.db,
                         decision_rows=lambda: None, clock=clock)
    assert absent.collect().coverage["decisions"] == "skipped"


def test_one_failing_source_does_not_stop_the_others(world, monkeypatch):
    world.db.add(7, "bo@x.test")

    def boom(snap):
        raise RuntimeError("resets exploded")
    monkeypatch.setattr(world.sources, "_resets", boom)
    world.sweep()
    assert world.snap.coverage["resets"] == "unavailable" and len(world.open()) == 1
