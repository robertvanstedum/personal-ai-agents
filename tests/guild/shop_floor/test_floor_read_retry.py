"""A Board read that hits a slow or briefly stalled database is repeated once before the Board is called unreadable (10 Oct: Dev showed "The Board is
unknown: it could not be read" while the Mac was under load and a connect took 2.2 s). Two failures in a row are still an honest "unknown", never an empty
Board, and the cause is logged. Writes are never repeated."""
from __future__ import annotations

import logging

from floor_db_helpers import SqliteFloor
from floor_db_helpers import floor_db  # noqa: F401  (pytest fixture)
from minimoi_portal.guild_ui.stores import FloorStores


def _flaky(floor: SqliteFloor, fail_times: int):
    calls = {"n": 0}

    def connect(url=None):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise TimeoutError("connection timed out")
        return floor.connect(url)
    store = FloorStores(lambda: "sqlite:test-floor", connect=connect, paramstyle="qmark", floor="guild")
    return store, calls


def test_one_stall_is_retried_and_the_board_reads(floor_db):
    store, calls = _flaky(floor_db, 1)
    res = store.board()
    assert res.ok and calls["n"] == 2                                   # the first connect failed, the second read it


def test_two_stalls_in_a_row_are_unknown_not_empty_and_are_logged(floor_db, caplog):
    store, calls = _flaky(floor_db, 5)
    with caplog.at_level(logging.WARNING, logger="minimoi_portal.guild_ui.stores"):
        res = store.board()
    assert not res.ok and calls["n"] == 2                               # one more try, then an honest failure
    assert res.data is None                                              # nothing is passed off as an empty Board
    assert any("floor store read failed" in r.message and "attempt 2" in r.message for r in caplog.records)


def test_a_write_is_never_repeated(floor_db):
    store, calls = _flaky(floor_db, 5)
    try:
        store._run(lambda q: None, write=True)
    except Exception:
        pass
    assert calls["n"] == 1
