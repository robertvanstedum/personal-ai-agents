"""guest_requests against a real PostgreSQL (opt-in). Set GUEST_REQUESTS_TEST_DSN to a disposable
database named mc12_test_*; the test creates guild.guest_requests (spec DDL + 002 CHECK) there.
Never point this at an application database."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

DSN = os.environ.get("GUEST_REQUESTS_TEST_DSN", "")
pytestmark = pytest.mark.skipif("dbname=mc12_test_" not in DSN, reason="needs a disposable mc12_test_* database")

DDL = """
CREATE SCHEMA IF NOT EXISTS guild;
DROP TABLE IF EXISTS guild.guest_requests;
CREATE TABLE guild.guest_requests (
  id SERIAL PRIMARY KEY, name TEXT, email TEXT NOT NULL UNIQUE, reason TEXT,
  domain VARCHAR(50) DEFAULT 'portuguese',
  requested_at TIMESTAMPTZ DEFAULT NOW(), status TEXT DEFAULT 'requested'
    CHECK (status IN ('requested','pending','granted','rejected','approved_pending_email')),
  actioned_at TIMESTAMPTZ, last_nudged_at TIMESTAMPTZ);
"""


@pytest.fixture
def db(tmp_path, monkeypatch):
    import psycopg2
    from minimoi_portal import auth, guest_requests as gr
    monkeypatch.setattr(auth, "AUTH_DIR", tmp_path)
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute("INSERT INTO guild.guest_requests (name,email,status) VALUES "
                    "('Robert Test','rt@x.test','requested'),('Granted One','g@x.test','granted'),"
                    "('Other','o@x.test','requested')")
    conn.close()
    return gr.PostgresStore(DSN)


def test_concurrent_rejects_change_the_row_once(db):
    from minimoi_portal import guest_requests as gr
    barrier = threading.Barrier(12)

    def go(_):
        barrier.wait()
        return gr.reject("guild:1", db).status

    with ThreadPoolExecutor(12) as pool:
        results = list(pool.map(go, range(12)))
    assert results.count("done") == 1 and results.count("already_applied") == 11
    assert gr.get("guild:1", db).status == "rejected"


def test_granted_is_never_rejected_and_summary_is_read_only(db):
    from minimoi_portal import guest_requests as gr
    assert gr.reject("guild:2", db).status == "conflict"
    before = db.query("SELECT last_nudged_at FROM guild.guest_requests ORDER BY id")
    s = gr.pending_summary(db)
    assert s["coverage"] == "complete" and s["stores"]["guild"]["count"] == 2
    assert db.query("SELECT last_nudged_at FROM guild.guest_requests ORDER BY id") == before


def test_resolution_and_verified_nudge_exclusion(db):
    from minimoi_portal import guest_requests as gr
    res = gr.resolve("robert test", db)
    assert res.unique and res.unique.ref == "guild:1"
    out = gr.reject(res.unique.ref, db)
    assert out.status == "done" and out.verified_state["nudge_eligible"] is False
    nudgeable = db.query("SELECT id FROM guild.guest_requests WHERE status='requested' ORDER BY id")
    assert [r["id"] for r in nudgeable] == [3]


def test_concurrent_grants_create_one_account_and_one_password(db):
    from minimoi_portal import auth, guest_requests as gr
    barrier = threading.Barrier(8)
    secrets_seen = []

    def go(i):
        barrier.wait()
        out = gr.grant("guild:3", db=db, password_factory=lambda: f"Temp-{i}-xyz")
        if out.delivery_secret:
            secrets_seen.append(out.delivery_secret)
        return out.status

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(go, range(8)))
    assert results.count("done") == 1 and results.count("already_applied") == 7
    assert len(secrets_seen) == 1 and len(auth.load_guests()) == 1
    assert gr.get("guild:3", db).status == "granted"
    assert auth.authenticate("o@x.test", secrets_seen[0])[0] is not None
