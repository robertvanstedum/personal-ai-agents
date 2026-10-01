"""Guild 1.1 slice 3 (spec §5.1, §11): the Board's store, on SQLite and, when
GUILD_FLOOR_TEST_DATABASE_URL names a disposable Postgres, on Postgres too.
Done and its precedence, labels and links, R3 order with conflicts and
renumbering, R2 Empty trash, photos and their references, retries."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from minimoi_portal.guild_ui.media import sanitize

from board_media_helpers import board, image_bytes, key  # noqa: F401  (fixtures)


def add(b, text, **kw):
    return b.floor.add_postit(text, b.author, idempotency_key=key(), **kw).value


def state(b):
    return b.floor.board().data


def ids(rows):
    return [r["id"] for r in rows]


# ── Done, Trash and their precedence ──────────────────────────────────────────

def test_done_leaves_active_trash_wins_and_restore_returns_to_the_prior_state(board):
    a = add(board, "decide the fade time")
    b = add(board, "remember the docs PDF")
    done = board.floor.set_done(a["id"], True, board.author, expect_version=a["version"], idempotency_key=key())
    assert done.outcome == "done" and done.value["state"] == "done" and done.value["version"] == a["version"] + 1
    s = state(board)
    assert ids(s["active"]) == [b["id"]] and ids(s["done"]) == [a["id"]]
    assert s["counts"] == {"active": 1, "done": 1, "trash": 0}
    # Binned while done: the Trash wins; done stays recorded.
    assert board.floor.bin_postit(a["id"], board.author, idempotency_key=key()).outcome == "binned"
    s = state(board)
    assert ids(s["trash"]) == [a["id"]] and s["done"] == [] and s["trash"][0]["done_at"]
    cur = s["trash"][0]
    assert board.floor.set_done(a["id"], False, board.author, expect_version=cur["version"],
                                idempotency_key=key()).outcome == "in_trash"
    # Restore returns it to Done, not Active.
    assert board.floor.restore_postit(a["id"], board.author, idempotency_key=key()).outcome == "restored"
    s = state(board)
    assert ids(s["done"]) == [a["id"]] and ids(s["active"]) == [b["id"]]
    cur = s["done"][0]
    undone = board.floor.set_done(a["id"], False, board.author, expect_version=cur["version"], idempotency_key=key())
    assert undone.outcome == "undone" and undone.value["state"] == "active"


def test_a_stale_version_is_a_conflict_with_the_current_note(board):
    a = add(board, "label me")
    assert board.floor.set_label(a["id"], "decide", board.author, expect_version=a["version"],
                                 idempotency_key=key()).outcome == "labelled"
    stale = board.floor.set_label(a["id"], "fyi", board.author, expect_version=a["version"], idempotency_key=key())
    assert stale.outcome == "conflict" and stale.value["label"] == "decide"
    with pytest.raises(ValueError):
        board.floor.set_label(a["id"], "urgent", board.author, expect_version=2, idempotency_key=key())


def test_link_records_the_item_and_a_retry_returns_the_first_outcome(board):
    a = add(board, "about #12")
    k = key()
    first = board.floor.set_link(a["id"], 12, board.author, expect_version=a["version"], idempotency_key=k)
    again = board.floor.set_link(a["id"], 12, board.author, expect_version=a["version"], idempotency_key=k)
    assert first.outcome == "linked" and first.value["item_ref"] == 12
    assert again.repeated and again.outcome == "linked" and again.value["version"] == first.value["version"]
    other = board.floor.set_link(a["id"], 13, board.author, expect_version=a["version"], idempotency_key=k)
    assert other.outcome == "idempotency_mismatch"


# ── order (R3) ────────────────────────────────────────────────────────────────

def test_reorder_moves_with_a_midpoint_and_bumps_the_revision(board):
    a, b, c = add(board, "a"), add(board, "b"), add(board, "c")
    s = state(board)
    assert ids(s["active"]) == [c["id"], b["id"], a["id"]]                  # newest on top, no keys yet
    first = board.floor.reorder(a["id"], before_id=c["id"], after_id=None, expect_order_rev=s["order_rev"],
                                by=board.author, idempotency_key=key())
    assert first.outcome == "moved" and first.value["renumbered"] is True       # keys assigned once
    assert first.value["order"] == [a["id"], c["id"], b["id"]] and first.value["order_rev"] == s["order_rev"] + 1
    second = board.floor.reorder(b["id"], before_id=None, after_id=a["id"], expect_order_rev=first.value["order_rev"],
                                 by=board.author, idempotency_key=key())
    assert second.outcome == "moved" and second.value["renumbered"] is False    # a midpoint was free
    assert second.value["order"] == [a["id"], b["id"], c["id"]]


def test_a_second_device_reordering_with_an_old_revision_gets_the_current_order(board):
    a, b, c = add(board, "a"), add(board, "b"), add(board, "c")
    rev = state(board)["order_rev"]
    assert board.floor.reorder(a["id"], before_id=c["id"], after_id=None, expect_order_rev=rev, by=board.author,
                               idempotency_key=key()).outcome == "moved"
    late = board.floor.reorder(b["id"], before_id=c["id"], after_id=None, expect_order_rev=rev, by=board.author,
                               idempotency_key=key())
    assert late.outcome == "conflict" and late.value["order"] == [a["id"], c["id"], b["id"]]
    assert ids(state(board)["active"]) == [a["id"], c["id"], b["id"]]          # nothing moved


def test_when_no_gap_is_left_the_whole_order_is_renumbered_in_one_transaction(board):
    notes = [add(board, f"n{i}") for i in range(4)]
    rev = state(board)["order_rev"]
    out = board.floor.reorder(notes[0]["id"], before_id=notes[3]["id"], after_id=None, expect_order_rev=rev,
                              by=board.author, idempotency_key=key())
    keys_before = {p["id"]: p["sort_key"] for p in state(board)["active"]}
    # Squeeze two neighbours to adjacent keys, then move a note between them.
    board.floor._run(lambda q: q("UPDATE guild.floor_postits SET sort_key = %s WHERE id = %s",
                                 [keys_before[notes[3]["id"]] + 1, notes[2]["id"]]), write=True)
    order = ids(state(board)["active"])
    assert order[1:3] == [notes[3]["id"], notes[2]["id"]]
    moved = board.floor.reorder(notes[1]["id"], before_id=notes[2]["id"], after_id=None,
                                expect_order_rev=out.value["order_rev"], by=board.author, idempotency_key=key())
    assert moved.outcome == "moved" and moved.value["renumbered"] is True
    keys = [p["sort_key"] for p in state(board)["active"]]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)                 # strictly ordered again
    assert moved.value["order"][1:4] == [notes[3]["id"], notes[1]["id"], notes[2]["id"]]


def test_reorder_refuses_nonsense(board):
    a = add(board, "a")
    with pytest.raises(ValueError):
        board.floor.reorder(a["id"], before_id=a["id"], after_id=None, expect_order_rev=0, by=board.author,
                            idempotency_key=key())
    with pytest.raises(ValueError):
        board.floor.reorder(a["id"], before_id=None, after_id=None, expect_order_rev=0, by=board.author,
                            idempotency_key=key())
    rev = state(board)["order_rev"]
    assert board.floor.reorder(a["id"], before_id=99999, after_id=None, expect_order_rev=rev, by=board.author,
                               idempotency_key=key()).outcome == "not_found"


# ── Empty trash (R2) ──────────────────────────────────────────────────────────

def _trash(board, *texts):
    out = []
    for t in texts:
        p = add(board, t)
        board.floor.bin_postit(p["id"], board.author, idempotency_key=key())
        out.append(p)
    return state(board)


def test_empty_trash_deletes_exactly_the_confirmed_set_and_a_retry_returns_the_receipt(board):
    keep = add(board, "linked to #12", item_ref=12)
    s = _trash(board, "old 1", "old 2")
    items = [{"id": p["id"], "version": p["version"]} for p in s["trash"]]
    k = key()
    done = board.floor.empty_trash(trash_rev=s["trash_rev"], items=items, by=board.author, idempotency_key=k)
    assert done.outcome == "emptied" and done.value["count"] == 2
    assert sorted(i["id"] for i in done.value["items"]) == sorted(i["id"] for i in items)
    assert done.value["principal"] == board.owner and done.value["receipt_id"].startswith("t-")
    after = state(board)
    assert after["trash"] == [] and after["trash_rev"] == s["trash_rev"] + 1 and ids(after["active"]) == [keep["id"]]
    again = board.floor.empty_trash(trash_rev=s["trash_rev"], items=items, by=board.author, idempotency_key=k)
    assert again.repeated and again.outcome == "emptied" and again.value == done.value     # never deletes again
    assert after["active"][0]["item_ref"] == 12                                           # linked work untouched


@pytest.mark.parametrize("change", ["other_ids", "other_version", "old_rev", "extra_item"])
def test_a_trash_that_differs_from_what_was_confirmed_is_a_conflict_and_deletes_nothing(board, change):
    s = _trash(board, "t1", "t2")
    items = [{"id": p["id"], "version": p["version"]} for p in s["trash"]]
    rev = s["trash_rev"]
    if change == "other_ids":                                   # same count, different ids
        fresh = add(board, "t3")
        board.floor.restore_postit(s["trash"][0]["id"], board.author, idempotency_key=key())
        board.floor.bin_postit(fresh["id"], board.author, idempotency_key=key())
        rev = state(board)["trash_rev"]
        assert len(state(board)["trash"]) == len(items)
    elif change == "other_version":
        items[0]["version"] += 1
    elif change == "old_rev":
        rev -= 1
    else:
        items.append({"id": 99999, "version": 1})
    before = state(board)["trash"]
    out = board.floor.empty_trash(trash_rev=rev, items=items, by=board.author, idempotency_key=key())
    assert out.outcome == "conflict" and ids(out.value["trash"]) == ids(before)
    assert ids(state(board)["trash"]) == ids(before)


# ── photos and references ─────────────────────────────────────────────────────

def _asset(board, color=(10, 200, 30)):
    item = sanitize(image_bytes(color=color))
    return board.media.create(board.owner, item, idempotency_key=key()).value


def _refs(board, asset_id):
    return board.rows('SELECT ref_id, released_at FROM media."references" WHERE asset_id = %s', [asset_id])


def test_a_photo_placement_keeps_its_reference_through_the_trash_until_it_is_purged(board):
    asset = _asset(board)
    photo = board.floor.add_photo(asset["id"], board.owner, "Lake ride", board.author, idempotency_key=key())
    assert photo.outcome == "added" and photo.value["kind"] == "photo" and photo.value["text"] == "Lake ride"
    assert [r[0] for r in _refs(board, asset["id"])] == [str(photo.value["id"])]
    board.floor.bin_postit(photo.value["id"], board.author, idempotency_key=key())
    assert _refs(board, asset["id"])[0][1] is None                          # still live in the Board Trash
    s = state(board)
    board.floor.empty_trash(trash_rev=s["trash_rev"], items=[{"id": p["id"], "version": p["version"]}
                                                             for p in s["trash"]],
                            by=board.author, idempotency_key=key())
    assert _refs(board, asset["id"])[0][1] is not None                      # released by the purge only
    assert board.media.get(board.owner, asset["id"])["state"] == "active"  # the library keeps the original


def test_placing_a_trashed_purged_or_foreign_asset_is_refused(board):
    asset = _asset(board)
    board.media.set_trashed(board.owner, asset["id"], True, expect_version=asset["version"], idempotency_key=key())
    assert board.floor.add_photo(asset["id"], board.owner, None, board.author,
                                 idempotency_key=key()).outcome == "asset_trashed"
    assert board.floor.add_photo(asset["id"], "someone_else", None, board.author,
                                 idempotency_key=key()).outcome == "not_found"
    trashed = board.media.get(board.owner, asset["id"])
    lib = board.media.library(board.owner, state="trash").data
    assert board.media.purge(board.owner, library_trash_rev=lib["library_trash_rev"],
                             items=[{"id": asset["id"], "version": trashed["version"]}],
                             idempotency_key=key()).outcome == "purged"
    assert board.floor.add_photo(asset["id"], board.owner, None, board.author,
                                 idempotency_key=key()).outcome == "gone"
    assert state(board)["active"] == []


def test_concurrent_placement_and_purge_never_leave_a_broken_placement(board):
    """R6: race a placement against a trash-then-purge many times; the end
    state is always either a placement with a live asset, or no placement."""
    if board.backend == "sqlite":
        rounds = 3
    else:
        rounds = 6
    for n in range(rounds):
        asset = _asset(board, color=(n * 30 % 255, 5, 5))
        gate = threading.Barrier(2)

        def place():
            gate.wait()
            return board.floor.add_photo(asset["id"], board.owner, None, board.author, idempotency_key=key()).outcome

        def trash_and_purge():
            gate.wait()
            cur = board.media.get(board.owner, asset["id"])
            t = board.media.set_trashed(board.owner, asset["id"], True, expect_version=cur["version"],
                                        idempotency_key=key())
            if t.outcome != "trashed":
                return t.outcome
            lib = board.media.library(board.owner, state="trash").data
            return board.media.purge(board.owner, library_trash_rev=lib["library_trash_rev"],
                                     items=[{"id": asset["id"], "version": t.value["version"]}],
                                     idempotency_key=key()).outcome
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_place, f_purge = pool.submit(place), pool.submit(trash_and_purge)
            placed, purged = f_place.result(timeout=30), f_purge.result(timeout=30)
        final = board.media.get(board.owner, asset["id"])
        placements = [p for p in state(board)["active"] if p.get("asset_id") == asset["id"]]
        if placements:
            assert final["purged_at"] is None, (placed, purged)            # never a placement of a purged asset
        assert placed in ("added", "asset_trashed", "gone") and purged in ("purged", "in_use")
