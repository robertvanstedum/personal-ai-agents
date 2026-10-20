"""Board operations for MC's action adapter, run against the floor store's own SQL on SQLite."""
from __future__ import annotations

from minimoi_portal.guild_ui import board_operations as ops
from minimoi_portal.guild_ui.stores import POSTIT_MAX

from floor_db_helpers import DownFloor, SqliteFloor

OP = "op-20261020-0001"


def _store(tmp_path):
    db = SqliteFloor(tmp_path / "floor")
    return db, db.store()


def test_add_then_retry_is_one_note(tmp_path):
    db, store = _store(tmp_path)
    first = ops.add(store, "Call the plumber about the leak", operation_id=OP, label="followup")
    assert first.status == "done" and first.postit["text"] == "Call the plumber about the leak"
    assert first.verified_state["binned"] is False and first.verified_state["label"] == "followup"
    again = ops.add(store, "Call the plumber about the leak", operation_id=OP, label="followup")
    assert again.status == "already_applied"
    assert db.count("floor_postits") == 1


def test_same_operation_id_for_different_text_is_conflict(tmp_path):
    db, store = _store(tmp_path)
    ops.add(store, "one", operation_id=OP)
    out = ops.add(store, "two", operation_id=OP)
    assert out.status == "conflict" and db.count("floor_postits") == 1


def test_two_intended_notes_need_two_operation_ids(tmp_path):
    db, store = _store(tmp_path)
    ops.add(store, "same text", operation_id="op-aaaaaaaa")
    ops.add(store, "same text", operation_id="op-bbbbbbbb")
    assert db.count("floor_postits") == 2


def test_input_rules_match_the_website(tmp_path):
    db, store = _store(tmp_path)
    assert ops.add(store, "   ", operation_id=OP).code == "text_empty"
    assert ops.add(store, "x" * (POSTIT_MAX + 1), operation_id=OP).code == "text_too_long"
    assert ops.add(store, "ok", operation_id=OP, label="urgent").code == "label_invalid"
    assert ops.add(store, "ok", operation_id="short").code == "operation_id_invalid"
    assert ops.add(store, "ok", operation_id="has space here").code == "operation_id_invalid"
    assert db.count("floor_postits") == 0


def test_payment_details_are_scrubbed_like_the_ui(tmp_path):
    db, store = _store(tmp_path)
    out = ops.add(store, "pay with 4111 1111 1111 1111 tomorrow", operation_id=OP)
    assert out.status == "done" and "4111 1111 1111 1111" not in db.all_text()


def test_bin_and_restore_are_recoverable_and_idempotent(tmp_path):
    db, store = _store(tmp_path)
    pid = ops.add(store, "stale idea", operation_id=OP).postit["id"]
    out = ops.bin_postit(store, pid, operation_id="op-bin-00001")
    assert (out.status, out.code, out.verified_state["binned"]) == ("done", "binned", True)
    assert ops.bin_postit(store, pid, operation_id="op-bin-00001").status == "already_applied"
    assert ops.bin_postit(store, pid, operation_id="op-bin-00002").code == "already_binned"
    board = ops.list_board(store, include_bin=True)
    assert board["postits"] == [] and [p["id"] for p in board["bin"]] == [pid]
    back = ops.restore_postit(store, pid, operation_id="op-restore-1")
    assert (back.status, back.verified_state["binned"]) == ("done", False)
    assert db.count("floor_postits") == 1        # never purged


def test_bin_unknown_and_bad_ids(tmp_path):
    _, store = _store(tmp_path)
    assert ops.bin_postit(store, 999, operation_id="op-bin-00009").status == "not_found"
    assert ops.bin_postit(store, "abc", operation_id="op-bin-00010").status == "refused"
    assert ops.bin_postit(store, True, operation_id="op-bin-00011").status == "refused"
    assert ops.bin_postit(store, 0, operation_id="op-bin-00012").status == "refused"


def test_non_integral_ids_never_reach_the_store(tmp_path):
    """Codex review B-F1: 1.9 must not silently bin note 1."""
    db, store = _store(tmp_path)
    pid = ops.add(store, "keep me", operation_id=OP).postit["id"]
    calls = []
    real_bin, real_restore = store.bin_postit, store.restore_postit
    store.bin_postit = lambda *a, **k: calls.append("bin") or real_bin(*a, **k)
    store.restore_postit = lambda *a, **k: calls.append("restore") or real_restore(*a, **k)
    bad = [pid + 0.9, float(pid), float("nan"), float("inf"), -1, 2**63, "1.0", " 1", "+1", "01", None, [pid]]
    for i, value in enumerate(bad):
        assert ops.bin_postit(store, value, operation_id=f"op-bad-bin-{i:04d}").code == "postit_id_invalid"
        assert ops.restore_postit(store, value, operation_id=f"op-bad-rst-{i:04d}").code == "postit_id_invalid"
    assert calls == []
    assert ops.list_board(store)["postits"][0]["id"] == pid      # still active, untouched
    assert ops.bin_postit(store, str(pid), operation_id="op-bin-string").status == "done"


def test_unreachable_store_is_unknown_and_listing_says_unavailable():
    down = DownFloor()
    assert ops.add(down, "note", operation_id=OP).status == "unknown"
    assert ops.bin_postit(down, 1, operation_id="op-bin-00001").status == "unknown"
    board = ops.list_board(down)
    assert board["available"] is False and board["bin_total"] is None
