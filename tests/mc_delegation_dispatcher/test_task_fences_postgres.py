"""Partnership v1.1 harness seam (decision 6; C06 fence, E03 operation ledger) on a real PostgreSQL.

Covers: correction (fence raise) vs admission, two drainers sending the same operation, a reply lost after commit,
a restart (a new store on new connections), an absent lookup followed by a delayed old send, and concurrent first use
of one operation id answering a refusal code rather than a raw unique violation. Interleavings are forced: a second
connection holds the row a store call needs, the call is seen waiting in pg_stat_activity, then the holder commits.
Opt-in: a disposable mc12_test_* database (tests/minimoi_actions/postgres_fixture_support.py)."""
from datetime import timedelta
from pathlib import Path
import sys
import threading
import time
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "minimoi_actions"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

from core.mc_delegation_dispatcher.store import AuthorityRefused, DispatcherStore  # noqa: E402

if DSN:
    from test_cards_postgres import AUTH, GRANTS, card  # noqa: E402

MIGRATIONS = ("core/mc_delegation_dispatcher/sql/001_delegations.sql",
              "core/mc_delegation_dispatcher/sql/002_host_runtime_v2.sql",
              "core/minimoi_actions/sql/004_action_receipts.sql",
              "core/minimoi_actions/sql/005_action_grants.sql",
              "core/mc_delegation_dispatcher/sql/003_task_fences.sql")


def code(exc):
    return str(exc).split(":")[0].strip()


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class TaskFences(PostgresFixture, unittest.TestCase):
    migrations = MIGRATIONS

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""TRUNCATE guild.mc_delegations, guild.mc_delegation_runners, guild.mc_delegation_events,
                guild.mc_delegation_reservations, guild.mc_action_grant_consumptions, guild.mc_action_grants,
                guild.mc_action_events, guild.mc_action_receipts, guild.mc_task_fences, guild.mc_task_operations
                CASCADE""")
        self.store = self.new_store()

    # ── helpers ──
    def new_store(self):
        return DispatcherStore(self.connect, principal="robert", environment="dev", authority=AUTH, grants=GRANTS)

    def sql(self, query, args=()):
        from psycopg2.extras import RealDictCursor
        with self.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, args)
            return cur.fetchall() if cur.description else None

    def send(self, op, doc=None, *, task="T-1", fence=0, attempt=1, store=None):
        now = self.sql("SELECT clock_timestamp() AS now")[0]["now"]
        return (store or self.store).admit_card_for_task(
            op, doc or card(), task_id=task, fence=fence, attempt=attempt, grant_id=str(uuid4()),
            owner_turn_ids=("owner-turn-1",), proposal_id=str(uuid4()), approved_operation_id=str(uuid4()),
            approval_expires_at=now + timedelta(seconds=300), by="robert", note="task admission")

    def refused(self, fn, *a, **kw):
        with self.assertRaises(AuthorityRefused) as caught:
            fn(*a, **kw)
        return code(caught.exception)

    def op(self, op_id):
        rows = self.sql("SELECT * FROM guild.mc_task_operations WHERE operation_id=%s", (op_id,))
        return rows[0] if rows else None

    def cards(self):
        return [r["card_id"] for r in self.sql("SELECT DISTINCT card_id FROM guild.mc_delegations")]

    def in_thread(self, fn, *a, **kw):
        box = {}

        def run():
            try:
                box["value"] = fn(*a, **kw)
            except Exception as exc:          # noqa: BLE001 - surfaced to the test
                box["error"] = exc
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t, box

    def wait_until_blocked(self, holder_pid):
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            rows = self.sql("""SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock'
                AND %s = ANY(pg_blocking_pids(pid))""", (holder_pid,))
            if rows:
                return
            time.sleep(0.02)
        self.fail("the store call never waited on the held row")

    def holding(self, statements):
        """Open a second transaction, run statements in it and keep it open: -> (conn, pid)."""
        conn = self.connect()
        cur = conn.cursor()
        for query, args in statements:
            cur.execute(query, args)
        cur.execute("SELECT pg_backend_pid()")
        return conn, cur.fetchone()[0]

    # ── sequential contract ──
    def test_an_admission_binds_its_operation_once_and_a_retry_answers_duplicate_without_rewriting_it(self):
        first = self.send("op-1", fence=3, task="T-1")
        self.assertEqual(first["state"], "admitted")
        self.assertEqual(first["receipt"]["edition"], "C-17")
        before = self.op("op-1")
        self.assertTrue(before["applied"])
        self.assertEqual((before["task_id"], before["fence"], before["card_id"]), ("T-1", 3, "C-17"))
        again = self.send("op-1", fence=3, task="T-1", attempt=2)
        self.assertEqual(again["state"], "duplicate")
        self.assertEqual(self.op("op-1"), before)                         # H2: nothing rewritten
        self.assertEqual(self.cards(), ["C-17"])

    def test_the_same_operation_under_another_task_or_card_is_refused_and_changes_nothing(self):
        self.send("op-1", task="T-1")
        before = self.op("op-1")
        self.assertEqual(self.refused(self.send, "op-1", task="T-2"), "operation_id_reused")
        self.assertEqual(self.refused(self.send, "op-1", card("C-18"), task="T-1"), "operation_id_reused")
        self.assertEqual(self.op("op-1"), before)
        self.assertEqual(self.cards(), ["C-17"])

    def test_an_applied_operation_retried_after_a_correction_still_answers_duplicate(self):
        self.send("op-1", fence=0)
        self.store.set_task_fence("fence-1", task_id="T-1", fence=5, digest="corr-1")
        self.assertEqual(self.send("op-1", fence=0)["state"], "duplicate")
        self.assertEqual(self.refused(self.send, "op-2", card("C-18"), fence=0), "fenced")
        self.assertEqual(self.send("op-3", card("C-18"), fence=5)["state"], "admitted")

    def test_the_fence_only_rises_and_a_fence_operation_is_bound_to_its_task_fence_and_digest(self):
        self.assertEqual(self.store.set_task_fence("fence-1", task_id="T-1", fence=5, digest="d1")["state"], "applied")
        self.assertEqual(self.store.set_task_fence("fence-2", task_id="T-1", fence=2, digest="d2")["state"], "applied")
        self.assertEqual(self.sql("SELECT fence FROM guild.mc_task_fences WHERE task_id='T-1'")[0]["fence"], 5)
        self.assertEqual(self.store.set_task_fence("fence-1", task_id="T-1", fence=5, digest="d1")["state"], "duplicate")
        for change in ({"task_id": "T-2", "fence": 5, "digest": "d1"}, {"task_id": "T-1", "fence": 6, "digest": "d1"},
                       {"task_id": "T-1", "fence": 5, "digest": "other"}):
            self.assertEqual(self.refused(self.store.set_task_fence, "fence-1", **change), "operation_id_reused")
        self.assertEqual(self.refused(self.send, "fence-1"), "operation_id_reused")
        self.assertEqual(self.refused(self.store.lookup_task_operation, "fence-1", attempt=1), "operation_id_reused")
        self.assertEqual(self.refused(self.store.set_task_fence, "fence-9", task_id="T-1", fence=2**63, digest="d"),
                         "fence_invalid")

    def test_an_absent_lookup_kills_that_attempt_so_a_delayed_old_send_is_refused(self):
        self.assertEqual(self.store.lookup_task_operation("op-1", attempt=1), {"state": "absent"})
        self.assertEqual(self.refused(self.send, "op-1", attempt=1), "attempt_declared_absent")
        self.assertEqual(self.cards(), [])
        self.assertEqual(self.store.lookup_task_operation("op-1", attempt=2), {"state": "absent"})
        self.assertEqual(self.refused(self.send, "op-1", attempt=2), "attempt_declared_absent")
        self.assertEqual(self.send("op-1", attempt=3)["state"], "admitted")
        self.assertEqual(self.store.lookup_task_operation("op-1", attempt=3)["state"], "applied")
        self.assertEqual(self.send("op-1", attempt=1)["state"], "duplicate")   # applied: the answer is the receipt

    def test_a_reply_lost_after_commit_is_recovered_by_lookup_and_a_restart_changes_nothing(self):
        sent = self.send("op-1")                                          # the caller never sees this reply
        restarted = self.new_store()                                      # restart: a new store, new connections
        found = restarted.lookup_task_operation("op-1", attempt=1)
        self.assertEqual(found["state"], "applied")
        self.assertEqual(found["receipt"], sent["receipt"])
        self.assertEqual(restarted.peek_task_operation("op-1"), "applied")
        self.assertEqual(self.send("op-1", store=restarted)["state"], "duplicate")
        self.assertEqual(self.cards(), ["C-17"])

    def test_peek_writes_nothing_and_kills_no_attempt(self):
        self.assertEqual(self.store.peek_task_operation("op-1"), "absent")
        self.assertIsNone(self.op("op-1"))
        self.assertEqual(self.send("op-1")["state"], "admitted")

    def test_a_refused_admission_leaves_no_placeholder_behind(self):
        self.store.set_task_fence("fence-1", task_id="T-1", fence=5, digest="d1")
        self.assertEqual(self.refused(self.send, "op-1", fence=4), "fenced")
        self.assertIsNone(self.op("op-1"))

    # ── forced interleavings ──
    def test_a_correction_committed_while_an_admission_waits_fences_that_admission(self):
        self.store.set_task_fence("fence-0", task_id="T-1", fence=0, digest="d0")
        conn, pid = self.holding([("SELECT fence FROM guild.mc_task_fences WHERE task_id='T-1' FOR UPDATE", ()),
                                  ("UPDATE guild.mc_task_fences SET fence=5 WHERE task_id='T-1'", ())])
        try:
            t, box = self.in_thread(self.send, "op-1", fence=0)
            self.wait_until_blocked(pid)
            conn.commit()
        finally:
            conn.close()
        t.join(5)
        self.assertIn("error", box, box)
        self.assertEqual(code(box["error"]), "fenced")
        self.assertEqual(self.cards(), [])
        self.assertIsNone(self.op("op-1"))

    def test_a_correction_waits_for_an_inflight_fence_row_and_then_raises_it(self):
        conn, pid = self.holding([("""INSERT INTO guild.mc_task_fences (principal, environment, task_id, fence)
            VALUES ('robert','dev','T-1',0)""", ())])
        try:
            t, box = self.in_thread(self.store.set_task_fence, "fence-1", task_id="T-1", fence=5, digest="d1")
            self.wait_until_blocked(pid)
            conn.commit()
        finally:
            conn.close()
        t.join(5)
        self.assertEqual(box.get("value", {}).get("state"), "applied", box)
        self.assertEqual(self.sql("SELECT fence FROM guild.mc_task_fences WHERE task_id='T-1'")[0]["fence"], 5)

    def test_a_lookup_racing_a_first_send_gives_the_send_a_refusal_code_not_a_unique_violation(self):
        conn, pid = self.holding([("""INSERT INTO guild.mc_task_operations (principal, environment, operation_id, kind,
            dead_through) VALUES ('robert','dev','op-1','admit_bundle',1)""", ())])     # a lookup, mid-transaction
        try:
            t, box = self.in_thread(self.send, "op-1", attempt=1)
            self.wait_until_blocked(pid)
            conn.commit()
        finally:
            conn.close()
        t.join(5)
        self.assertIn("error", box, box)
        self.assertIsInstance(box["error"], AuthorityRefused)
        self.assertEqual(code(box["error"]), "attempt_declared_absent")
        self.assertEqual(self.cards(), [])

    def test_a_send_committed_while_a_lookup_waits_is_reported_applied(self):
        first = self.send("op-0", card("C-18"))                                     # a real applied row to copy
        conn, pid = self.holding([("""INSERT INTO guild.mc_task_operations (principal, environment, operation_id, kind,
            task_id, digest, fence, card_id, applied) VALUES ('robert','dev','op-1','admit_bundle','T-1',%s,0,'C-18',true)""",
                                   (first["card_digest"],))])
        try:
            t, box = self.in_thread(self.store.lookup_task_operation, "op-1", attempt=1)
            self.wait_until_blocked(pid)
            conn.commit()
        finally:
            conn.close()
        t.join(5)
        self.assertEqual(box.get("value", {}).get("state"), "applied", box)

    def test_one_operation_id_used_for_two_tasks_at_once_refuses_the_loser_by_code(self):
        conn, pid = self.holding([("""INSERT INTO guild.mc_task_operations (principal, environment, operation_id, kind)
            VALUES ('robert','dev','op-1','set_fence')""", ())])
        try:
            t, box = self.in_thread(self.send, "op-1", task="T-2")
            self.wait_until_blocked(pid)
            conn.commit()
        finally:
            conn.close()
        t.join(5)
        self.assertIn("error", box, box)
        self.assertEqual(code(box["error"]), "operation_id_reused")

    def test_two_drainers_sending_the_same_operation_admit_one_card_and_neither_errors(self):
        start = threading.Barrier(2)
        results = []

        def drain():
            store = self.new_store()
            start.wait()
            try:
                results.append(self.send("op-1", store=store)["state"])
            except Exception as exc:                  # noqa: BLE001
                results.append(repr(exc))
        threads = [threading.Thread(target=drain) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        self.assertEqual(sorted(results), ["admitted", "duplicate"], results)
        self.assertEqual(self.cards(), ["C-17"])


if __name__ == "__main__":
    unittest.main()
