from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from uuid import uuid4

from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.receipts import EffectIntent, ReceiptStore
from core.minimoi_actions.requests import ToolRequest
from postgres_fixture_support import DSN, PostgresFixture


@unittest.skipUnless(DSN, 'requires disposable MC_BINDING_TEST_DSN')
class ReceiptPostgresTests(PostgresFixture, unittest.TestCase):
    migrations = ('core/minimoi_actions/sql/004_action_receipts.sql',)

    def setUp(self):
        self.store = ReceiptStore()
        self.context = ActionContext('owner', 'mc', 'dev', 'conversation', 'turn', 'dispatch', 1,
            datetime.now(timezone.utc)+timedelta(minutes=5), 'policy', 'role', 'recorded', 'budget',
            ('grant',))
        self.intent = EffectIntent(str(uuid4()), ToolRequest('guest.reject', 1, b'{"id":"synthetic"}'),
                                   grant_id='grant', reservation_ids=(str(uuid4()),))
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('TRUNCATE guild.mc_action_events,guild.mc_action_receipts CASCADE')
            cur.execute('CREATE TABLE IF NOT EXISTS guild.mc_receipt_fixture (id text PRIMARY KEY)')
            cur.execute('TRUNCATE guild.mc_receipt_fixture')

    def prepare(self, intent=None, context=None):
        with self.connect() as conn:
            return self.store.prepare(conn, context or self.context, intent or self.intent)

    def claim(self, **kwargs):
        with self.connect() as conn:
            return self.store.claim(conn, 'owner', 'dev', self.intent.operation_id, 'worker', **kwargs)

    def read(self):
        with self.connect() as conn:
            return self.store.read(conn, 'owner', 'dev', self.intent.operation_id)

    def expire(self):
        # Synthetic fixture-only time travel; no sleeping or application DB.
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_action_receipts SET lease_until=clock_timestamp()-interval '1 second'")

    def test_concurrent_preparation_returns_one_receipt_and_one_event(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(lambda _: self.prepare(), range(8)))
        self.assertEqual(len({r['receipt_id'] for r in rows}), 1)
        self.assertTrue(all(r['event_sequence'] == 1 for r in rows))
        # Retry in a later admitted turn is still the same effect, not a new ID.
        self.assertEqual(self.prepare(context=replace(self.context, turn_id='next'))['receipt_id'], rows[0]['receipt_id'])
        with self.assertRaisesRegex(ActionContractError, 'operation_reused'):
            self.prepare(replace(self.intent, request=ToolRequest('guest.reject', 1, b'{"id":"other"}')))
        self.assertEqual(self.read()['event_sequence'], 1)

    def test_scope_isolation_and_expired_context(self):
        self.prepare()
        for principal, env in (('other', 'dev'), ('owner', 'prod')):
            with self.assertRaisesRegex(ActionContractError, 'not_found'), self.connect() as conn:
                self.store.read(conn, principal, env, self.intent.operation_id)
        with self.assertRaisesRegex(ActionContractError, 'expired'):
            self.prepare(replace(self.intent, operation_id=str(uuid4())),
                         replace(self.context, expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM guild.mc_action_receipts')
            self.assertEqual(cur.fetchone()[0], 1)

    def test_concurrent_claim_has_one_winner_and_expiry_fences_old_worker(self):
        self.prepare()
        def claim(_):
            try:
                return self.claim()
            except ActionContractError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(claim, range(8)))
        self.assertEqual(values.count('receipt_busy'), 7)
        first = next(v for v in values if not isinstance(v, str))
        self.expire()
        second = self.claim()
        self.assertEqual(second.generation, first.generation+1)
        for stale in (first, replace(second, token='x'*43), replace(second, worker_id='other')):
            with self.assertRaisesRegex(ActionContractError, 'lease_stale'), self.connect() as conn:
                self.store.dispatch(conn, stale)
        with self.connect() as conn:
            self.store.dispatch(conn, second)
        self.assertEqual(self.read()['state'], 'dispatched')

    def test_local_effect_and_receipt_rollback_together(self):
        self.prepare()
        lease = self.claim()
        with self.assertRaisesRegex(RuntimeError, 'crash'), self.connect() as conn:
            self.store.dispatch(conn, lease)
            with conn.cursor() as cur:
                cur.execute("INSERT INTO guild.mc_receipt_fixture VALUES ('effect')")
            self.store.finish(conn, lease, 'verified', evidence_refs=(str(uuid4()),))
            raise RuntimeError('crash')
        self.assertEqual(self.read()['state'], 'prepared')
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM guild.mc_receipt_fixture')
            self.assertEqual(cur.fetchone()[0], 0)
            self.store.dispatch(conn, lease)
            cur.execute("INSERT INTO guild.mc_receipt_fixture VALUES ('effect')")
            self.store.finish(conn, lease, 'verified', evidence_refs=(str(uuid4()),))
        self.assertEqual(self.read()['state'], 'verified')
        self.assertIsNotNone(self.read()['verified_at'])
        # A lost final chat response leaves a real terminal receipt on replay.
        self.assertEqual(self.prepare()['state'], 'verified')
        with self.assertRaisesRegex(ActionContractError, 'terminal'):
            self.claim()

    def test_uncertain_dispatch_can_only_reconcile_and_old_completion_is_fenced(self):
        self.prepare()
        first = self.claim()
        with self.connect() as conn:
            self.store.dispatch(conn, first)
        self.expire()
        with self.assertRaisesRegex(ActionContractError, 'reconciliation_required'):
            self.claim()
        second = self.claim(mode='reconcile')
        self.assertEqual(self.read()['state'], 'unknown')
        with self.assertRaisesRegex(ActionContractError, 'dispatch_refused'), self.connect() as conn:
            self.store.dispatch(conn, second)
        with self.assertRaisesRegex(ActionContractError, 'lease_stale'), self.connect() as conn:
            self.store.finish(conn, first, 'verified', evidence_refs=(str(uuid4()),))
        with self.connect() as conn:
            self.store.finish(conn, second, 'unknown')
        third = self.claim(mode='reconcile')
        with self.connect() as conn:
            self.store.finish(conn, third, 'verified', evidence_refs=(str(uuid4()),))
        self.assertEqual(self.read()['state'], 'verified')

    def test_cancel_before_dispatch_prevents_effect_after_dispatch_does_not_undo(self):
        self.prepare()
        lease = self.claim()
        with self.connect() as conn:
            row = self.store.cancel(conn, 'owner', 'dev', self.intent.operation_id)
        self.assertEqual(row['state'], 'cancelled')
        with self.assertRaisesRegex(ActionContractError, 'lease_stale'), self.connect() as conn:
            self.store.dispatch(conn, lease)
        self.intent = replace(self.intent, operation_id=str(uuid4()))
        self.prepare()
        lease = self.claim()
        with self.connect() as conn:
            self.store.dispatch(conn, lease)
        with self.connect() as conn:
            row = self.store.cancel(conn, 'owner', 'dev', self.intent.operation_id)
            again = self.store.cancel(conn, 'owner', 'dev', self.intent.operation_id)
        self.assertEqual(row['state'], 'dispatched')
        self.assertTrue(row['cancel_requested'])
        self.assertEqual(row['event_sequence'], again['event_sequence'])
        with self.connect() as conn:
            self.store.finish(conn, lease, 'verified', evidence_refs=(str(uuid4()),))
        self.assertEqual(self.read()['state'], 'verified')
        self.assertTrue(self.read()['cancel_requested'])

    def test_evidence_required_and_events_have_no_input_or_lease_secret(self):
        self.prepare()
        lease = self.claim()
        with self.connect() as conn:
            self.store.dispatch(conn, lease)
        for state, refs in (('verified', ()), ('failed', ()), ('verified', ('raw response secret',)),
                            ('cancelled', ())):
            with self.assertRaisesRegex(ActionContractError, 'result_invalid'), self.connect() as conn:
                self.store.finish(conn, lease, state, evidence_refs=refs)
        with self.connect() as conn:
            self.store.finish(conn, lease, 'unknown')
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT sequence,payload FROM guild.mc_action_events ORDER BY sequence')
            rows = cur.fetchall()
            self.assertEqual([r[0] for r in rows], list(range(1, len(rows)+1)))
            serialized = json.dumps(rows)
            self.assertNotIn('synthetic', serialized)
            self.assertNotIn(lease.token, serialized)
            cur.execute('SELECT reservation_ids FROM guild.mc_action_receipts')
            self.assertEqual(cur.fetchone()[0], list(self.intent.reservation_ids))

    def test_dispatch_twice_and_finish_before_dispatch_are_refused(self):
        self.prepare()
        lease = self.claim()
        with self.assertRaisesRegex(ActionContractError, 'transition_invalid'), self.connect() as conn:
            self.store.finish(conn, lease, 'verified', evidence_refs=(str(uuid4()),))
        with self.connect() as conn:
            self.store.dispatch(conn, lease)
        with self.assertRaisesRegex(ActionContractError, 'dispatch_refused'), self.connect() as conn:
            self.store.dispatch(conn, lease)


if __name__ == '__main__':
    unittest.main()
