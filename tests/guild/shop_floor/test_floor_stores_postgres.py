"""The floor store on a real Postgres, for the end-to-end tester (skipped
unless GUILD_FLOOR_TEST_DATABASE_URL is set).

Run it against the staging database after Robert has applied
sql/001_floor_b1.sql, for example from inside the portal container:

    GUILD_FLOOR_TEST_DATABASE_URL="$DATABASE_URL" python3 -m pytest \\
        tests/guild/shop_floor/test_floor_stores_postgres.py -q -p no:cacheprovider

Every row it writes is on a floor named ``test-<random>``, which no page
reads, and those rows are removed at the end. The URL is never printed.
"""
from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from minimoi_portal.guild_ui.stores import MASTER_CRAFTSMAN, Author, FloorStores

URL = os.environ.get("GUILD_FLOOR_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="GUILD_FLOOR_TEST_DATABASE_URL is not set")
ROBERT = Author("robert", "owner", "Robert")
TABLES = ("floor_messages", "floor_postits", "floor_continue", "floor_requests")


@pytest.fixture
def store():
    floor = f"test-{uuid.uuid4().hex[:12]}"
    s = FloorStores(lambda: URL, floor=floor)
    yield s
    import psycopg2
    conn = psycopg2.connect(URL, connect_timeout=2)
    try:
        with conn.cursor() as cur:
            for table in TABLES:
                cur.execute(f"DELETE FROM guild.{table} WHERE floor = %s", (floor,))
        conn.commit()
    finally:
        conn.close()


def _together(n, fn):
    gate = threading.Barrier(n)

    def run(i):
        gate.wait()
        return fn(i)
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(run, range(n)))


def test_round_trip_on_postgres(store):
    mc = store.add_postit("from MC", MASTER_CRAFTSMAN, idempotency_key=uuid.uuid4().hex).value
    mine = store.add_postit("from Robert", ROBERT, idempotency_key=uuid.uuid4().hex).value
    assert [p["author_label"] for p in store.list_postits().data["postits"]] == ["Robert", "Master Craftsman"]
    assert store.bin_postit(mc["id"], ROBERT, idempotency_key=uuid.uuid4().hex).outcome == "binned"
    assert store.list_bin().data["total"] == 1
    assert store.restore_postit(mc["id"], ROBERT, idempotency_key=uuid.uuid4().hex).outcome == "restored"
    note = store.add_note(uuid.uuid4().hex, "on the record", ROBERT, area="Build", item_ref=12, page="floor")
    assert note.outcome == "kept" and note.value["context"]["item_ref"] == 12
    cont = store.set_continue("robert", kind="item", ref="12", label="#12 x", idempotency_key=uuid.uuid4().hex)
    summary = store.summary("robert", notes_limit=5)
    assert summary.ok and summary.data["continue"]["ref"] == cont.value["ref"] == "12"
    assert summary.data["active_total"] == 2 and summary.data["bin_total"] == 0 and mine["id"] != mc["id"]


def test_concurrent_writers_on_postgres(store):
    pid = store.add_postit("contended", ROBERT, idempotency_key=uuid.uuid4().hex).value["id"]
    outcomes = _together(8, lambda i: store.bin_postit(pid, ROBERT, idempotency_key=uuid.uuid4().hex).outcome)
    assert outcomes.count("binned") == 1 and outcomes.count("already_binned") == 7
    same = _together(8, lambda i: store.add_postit("same", ROBERT, idempotency_key="pg-same-key-01"))
    assert len({r.value["id"] for r in same}) == 1 and sum(1 for r in same if not r.repeated) == 1
    notes = _together(8, lambda i: store.add_note("pg-note-key-01", "once", ROBERT))
    assert len({r.value["id"] for r in notes}) == 1


def test_off_record_rows_are_refused_by_postgres(store):
    import psycopg2
    conn = psycopg2.connect(URL, connect_timeout=2)
    try:
        with pytest.raises(psycopg2.errors.CheckViolation):
            with conn.cursor() as cur:
                cur.execute("INSERT INTO guild.floor_messages (floor, request_id, author, author_kind, author_label, "
                            "text, record_mode, created_at) VALUES (%s, 'r', 'robert', 'owner', 'Robert', 'x', "
                            "'off_record', now())", (store.floor,))
        conn.rollback()
    finally:
        conn.close()
