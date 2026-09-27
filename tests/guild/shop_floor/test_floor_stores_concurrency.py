"""#233, many participants: concurrent writers on the floor store never lose a
change, never double-apply one, and never leave a row half-changed. Each
thread uses its own connection, as each portal request does."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from minimoi_portal.guild_ui.stores import MASTER_CRAFTSMAN, Author

from floor_db_helpers import floor_db  # noqa: F401  (pytest fixture)

ROBERT = Author("robert", "owner", "Robert")
THREADS = 12


def _together(n, fn):
    """Run fn(i) in n threads released at the same moment; return results in order."""
    gate = threading.Barrier(n)

    def run(i):
        gate.wait()
        return fn(i)
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(run, range(n)))


def test_concurrent_adds_are_all_kept(floor_db):
    store = floor_db.store()

    def add(i):
        return [store.add_postit(f"t{i}-{k}", ROBERT if i % 2 else MASTER_CRAFTSMAN,
                                 idempotency_key=f"add-{i:02d}-{k:03d}").value["id"] for k in range(15)]
    ids = [pid for batch in _together(THREADS, add) for pid in batch]
    assert len(ids) == len(set(ids)) == THREADS * 15
    assert floor_db.count("floor_postits") == THREADS * 15
    assert store.list_postits().data["postits"].__len__() == THREADS * 15


def test_one_post_it_binned_by_many_at_once_is_binned_exactly_once(floor_db):
    store = floor_db.store()
    pid = store.add_postit("contended", ROBERT, idempotency_key="contended-01").value["id"]
    removers = [ROBERT if i % 2 else MASTER_CRAFTSMAN for i in range(THREADS)]
    outcomes = _together(THREADS, lambda i: store.bin_postit(pid, removers[i], idempotency_key=f"bin-{i:04d}").outcome)
    assert outcomes.count("binned") == 1 and outcomes.count("already_binned") == THREADS - 1
    row = floor_db.rows("floor_postits")[0]
    assert row["binned_at"] and row["binned_by"] in ("robert", "master_craftsman")


def test_a_key_sent_by_many_at_once_is_applied_once(floor_db):
    store = floor_db.store()
    results = _together(THREADS, lambda i: store.add_postit("same request", ROBERT, idempotency_key="one-key-0001"))
    assert len({r.value["id"] for r in results}) == 1
    assert sum(1 for r in results if not r.repeated) == 1
    assert floor_db.count("floor_postits") == 1 and floor_db.count("floor_requests") == 1


def test_a_note_sent_by_many_at_once_is_kept_once(floor_db):
    store = floor_db.store()
    results = _together(THREADS, lambda i: store.add_note("note-req-0001", "once", ROBERT))
    assert len({r.value["id"] for r in results}) == 1 and sum(1 for r in results if not r.repeated) == 1
    assert floor_db.count("floor_messages") == 1


def test_continue_from_many_front_ends_at_once_leaves_one_whole_row(floor_db):
    store = floor_db.store()
    refs = [str(7 + i) for i in range(THREADS)]
    results = _together(THREADS, lambda i: store.set_continue("robert", kind="item", ref=refs[i],
                                                              label=f"#{refs[i]} item", idempotency_key=f"cont-{i:04d}"))
    assert all(r.outcome == "set" for r in results)
    rows = floor_db.rows("floor_continue")
    assert len(rows) == 1 and rows[0]["ref"] in refs and rows[0]["label"] == f"#{rows[0]['ref']} item"


def test_bin_and_restore_racing_always_leave_a_consistent_row(floor_db):
    store = floor_db.store()
    pid = store.add_postit("flip", ROBERT, idempotency_key="flip-000001").value["id"]

    def flip(i):
        move = store.bin_postit if i % 2 else store.restore_postit
        return [move(pid, ROBERT, idempotency_key=f"flip-{i:02d}-{k:03d}").outcome for k in range(10)]
    outcomes = [o for batch in _together(THREADS, flip) for o in batch]
    assert set(outcomes) <= {"binned", "already_binned", "restored", "already_active"}
    row = floor_db.rows("floor_postits")[0]
    # Binned rows name who binned them; binned_by is kept after a restore (review B1c #8).
    assert row["binned_at"] is None or row["binned_by"] == "robert"
    assert row["binned_at"] is not None or row["restored_by"] in (None, "robert")
    assert floor_db.count("floor_postits") == 1
    # Every successful move alternated the state: binned and restored differ by at most one.
    assert abs(outcomes.count("binned") - outcomes.count("restored")) <= 1
