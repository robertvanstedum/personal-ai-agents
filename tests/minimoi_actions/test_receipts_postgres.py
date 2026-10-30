from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from uuid import uuid4

from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.grants import CapabilityRule, GrantDefinition, GrantStore, GrantExecution
from core.minimoi_actions.receipts import EffectIntent, ReceiptStore
from core.minimoi_actions.requests import ToolRequest
from postgres_fixture_support import DSN, PostgresFixture


@unittest.skipUnless(DSN, 'requires disposable MC_BINDING_TEST_DSN')
class ReceiptPostgresTests(PostgresFixture, unittest.TestCase):
    migrations = ('core/minimoi_actions/sql/004_action_receipts.sql',
                  'core/minimoi_actions/sql/005_action_grants.sql')

    def setUp(self):
        self.store = ReceiptStore()
        grant_id = str(uuid4())
        self.grants = GrantStore({'guest.reject':CapabilityRule(1,False,True)})
        self.context = ActionContext('owner', 'mc', 'dev', 'conversation', 'turn', 'dispatch', 1,
            datetime.now(timezone.utc)+timedelta(minutes=5), 'policy', 'role', 'recorded', 'budget',
            (grant_id,))
        self.intent = EffectIntent(str(uuid4()), ToolRequest('guest.reject', 1, b'{"id":"synthetic"}'),
                                   grant_id=grant_id)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('TRUNCATE guild.mc_action_grants,guild.mc_action_events,guild.mc_action_receipts CASCADE')
            cur.execute('CREATE TABLE IF NOT EXISTS guild.mc_receipt_fixture (id text PRIMARY KEY)')
            cur.execute('TRUNCATE guild.mc_receipt_fixture')

        with self.connect() as conn:
            self.grants.issue(conn,GrantDefinition(grant_id,'owner','dev','conversation',('turn',),('mc',),
                'policy','guest.reject',1,(self.intent.request.arguments_sha256,),100,self.context.expires_at))

    def authority(self):
        return GrantExecution(self.grants,self.context,self.intent,'policy')

    def prepare(self, intent=None, context=None):
        with self.connect() as conn:
            return self.grants.prepare_effect(conn,context or self.context,intent or self.intent,current_policy_version='policy')

    def claim(self, **kwargs):
        with self.connect() as conn:
            return self.store.claim(conn, 'owner', 'dev', self.intent.operation_id, 'worker', authority=self.authority(), **kwargs)

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
        with self.assertRaisesRegex(ActionContractError, 'operation_reused|effect_not_covered'):
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
                self.store.dispatch(conn, stale,authority=self.authority())
        with self.connect() as conn:
            self.store.dispatch(conn, second,authority=self.authority())
        self.assertEqual(self.read()['state'], 'dispatched')

    def test_local_effect_and_receipt_rollback_together(self):
        self.prepare()
        lease = self.claim()
        with self.assertRaisesRegex(RuntimeError, 'crash'), self.connect() as conn:
            self.store.dispatch(conn, lease,authority=self.authority())
            with conn.cursor() as cur:
                cur.execute("INSERT INTO guild.mc_receipt_fixture VALUES ('effect')")
            self.store.finish(conn, lease, 'verified', evidence_refs=(str(uuid4()),),authority=self.authority())
            raise RuntimeError('crash')
        self.assertEqual(self.read()['state'], 'prepared')
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM guild.mc_receipt_fixture')
            self.assertEqual(cur.fetchone()[0], 0)
            self.store.dispatch(conn, lease,authority=self.authority())
            cur.execute("INSERT INTO guild.mc_receipt_fixture VALUES ('effect')")
            self.store.finish(conn, lease, 'verified', evidence_refs=(str(uuid4()),),authority=self.authority())
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
            self.store.dispatch(conn, first,authority=self.authority())
        self.expire()
        with self.assertRaisesRegex(ActionContractError, 'reconciliation_required'):
            self.claim()
        second = self.claim(mode='reconcile')
        self.assertEqual(self.read()['state'], 'unknown')
        with self.assertRaisesRegex(ActionContractError, 'dispatch_refused'), self.connect() as conn:
            self.store.dispatch(conn, second,authority=self.authority())
        with self.assertRaisesRegex(ActionContractError, 'lease_stale'), self.connect() as conn:
            self.store.finish(conn, first, 'verified', evidence_refs=(str(uuid4()),),authority=self.authority())
        with self.connect() as conn:
            self.store.finish(conn, second, 'unknown')
        third = self.claim(mode='reconcile')
        with self.connect() as conn:
            self.store.finish(conn, third, 'verified', evidence_refs=(str(uuid4()),),authority=self.authority())
        self.assertEqual(self.read()['state'], 'verified')

    def test_cancel_before_dispatch_prevents_effect_after_dispatch_does_not_undo(self):
        self.prepare()
        lease = self.claim()
        with self.connect() as conn:
            row = self.store.cancel(conn, 'owner', 'dev', self.intent.operation_id)
        self.assertEqual(row['state'], 'cancelled')
        with self.assertRaisesRegex(ActionContractError, 'lease_stale'), self.connect() as conn:
            self.store.dispatch(conn, lease,authority=self.authority())
        self.intent = replace(self.intent, operation_id=str(uuid4()))
        self.prepare()
        lease = self.claim()
        with self.connect() as conn:
            self.store.dispatch(conn, lease,authority=self.authority())
        with self.connect() as conn:
            row = self.store.cancel(conn, 'owner', 'dev', self.intent.operation_id)
            again = self.store.cancel(conn, 'owner', 'dev', self.intent.operation_id)
        self.assertEqual(row['state'], 'dispatched')
        self.assertTrue(row['cancel_requested'])
        self.assertEqual(row['event_sequence'], again['event_sequence'])
        with self.connect() as conn:
            self.store.finish(conn, lease, 'verified', evidence_refs=(str(uuid4()),),authority=self.authority())
        self.assertEqual(self.read()['state'], 'verified')
        self.assertTrue(self.read()['cancel_requested'])

    def test_evidence_required_and_events_have_no_input_or_lease_secret(self):
        self.prepare()
        lease = self.claim()
        with self.connect() as conn:
            self.store.dispatch(conn, lease,authority=self.authority())
        for state, refs in (('verified', ()), ('failed', ()), ('verified', ('raw response secret',)),
                            ('cancelled', ())):
            with self.assertRaisesRegex(ActionContractError, 'result_invalid'), self.connect() as conn:
                self.store.finish(conn, lease, state, evidence_refs=refs,authority=self.authority())
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

    def test_expired_prepare_caught_and_committed_leaves_no_orphan(self):
        expired = replace(self.context,expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
        with self.connect() as conn:
            with self.assertRaisesRegex(ActionContractError,'context_expired'):
                self.store.prepare(conn,expired,self.intent)
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM guild.mc_action_receipts')
            self.assertEqual(cur.fetchone()[0],0)
            cur.execute('SELECT count(*) FROM guild.mc_action_events')
            self.assertEqual(cur.fetchone()[0],0)

    def test_near_expiry_dispatch_is_refused_before_effect(self):
        self.prepare(); lease = self.claim()
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_action_receipts SET lease_until=clock_timestamp()+interval '1 second'")
            with self.assertRaisesRegex(ActionContractError,'dispatch_window_insufficient'):
                self.store.dispatch(conn,lease,authority=self.authority())
        self.assertEqual(self.read()['state'],'prepared')

    def test_execute_transitions_require_current_grant_inside_the_transition(self):
        self.prepare()
        with self.connect() as conn:
            with self.assertRaisesRegex(ActionContractError,'execution_authority_required'):
                self.store.claim(conn,'owner','dev',self.intent.operation_id,'worker')
        lease = self.claim()
        with self.connect() as conn:
            with self.assertRaisesRegex(ActionContractError,'execution_authority_required'):
                self.store.dispatch(conn,lease)
            self.grants.revoke(conn,'owner','dev',self.intent.grant_id)
        with self.connect() as conn:
            with self.assertRaisesRegex(ActionContractError,'grant_revoked'):
                self.store.dispatch(conn,lease,authority=self.authority())
        self.assertEqual(self.read()['state'],'prepared')

    def test_policy_expiry_and_scope_are_checked_at_dispatch(self):
        self.prepare(); lease = self.claim()
        for authority,reason in (
            (replace(self.authority(),current_policy_version='changed'),'policy_changed'),
            (replace(self.authority(),context=replace(self.context,
                expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))),'grant_expired'),
            (replace(self.authority(),intent=replace(self.intent,operation_id=str(uuid4()))),'scope_mismatch'),
        ):
            with self.subTest(reason=reason),self.connect() as conn:
                with self.assertRaisesRegex(ActionContractError,reason):
                    self.store.dispatch(conn,lease,authority=authority)
        self.assertEqual(self.read()['state'],'prepared')

    def test_revoke_after_dispatch_requires_reconciliation_for_final_verification(self):
        self.prepare(); lease = self.claim()
        with self.connect() as conn:
            self.store.dispatch(conn,lease,authority=self.authority())
            self.grants.revoke(conn,'owner','dev',self.intent.grant_id)
        with self.connect() as conn:
            with self.assertRaisesRegex(ActionContractError,'grant_revoked'):
                self.store.finish(conn,lease,'verified',evidence_refs=(str(uuid4()),),authority=self.authority())
            # Uncertainty recording is allowed, never a new effect.
            self.store.finish(conn,lease,'unknown')
        reconciler = self.claim(mode='reconcile')
        with self.connect() as conn:
            self.store.finish(conn,reconciler,'verified',evidence_refs=(str(uuid4()),))
        self.assertEqual(self.read()['state'],'verified')

    def test_dispatch_twice_and_finish_before_dispatch_are_refused(self):
        self.prepare()
        lease = self.claim()
        with self.assertRaisesRegex(ActionContractError, 'transition_invalid'), self.connect() as conn:
            self.store.finish(conn, lease, 'verified', evidence_refs=(str(uuid4()),),authority=self.authority())
        with self.connect() as conn:
            self.store.dispatch(conn, lease,authority=self.authority())
        with self.assertRaisesRegex(ActionContractError, 'dispatch_refused'), self.connect() as conn:
            self.store.dispatch(conn, lease,authority=self.authority())

    def test_lease_until_reads_the_current_lease_and_changes_nothing(self):
        self.prepare()
        with self.connect() as conn:
            self.assertIsNone(self.store.lease_until(conn, 'owner', 'dev', self.intent.operation_id))
        lease = self.claim(seconds=20)
        with self.connect() as conn, conn.cursor() as cur:
            until = self.store.lease_until(conn, 'owner', 'dev', self.intent.operation_id)
            cur.execute('SELECT lease_until, event_sequence FROM guild.mc_action_receipts')
            self.assertEqual(cur.fetchone(), (until, 2))           # the claim's lease; no event written by the read
        with self.connect() as conn:
            self.store.dispatch(conn, lease, authority=self.authority())
            self.assertEqual(self.store.lease_until(conn, 'owner', 'dev', self.intent.operation_id), until)
        with self.connect() as conn:
            self.store.finish(conn, lease, 'unknown')
            self.assertIsNone(self.store.lease_until(conn, 'owner', 'dev', self.intent.operation_id))
        with self.assertRaisesRegex(ActionContractError, 'receipt_not_found'), self.connect() as conn:
            self.store.lease_until(conn, 'owner', 'dev', str(uuid4()))


if __name__ == '__main__':
    unittest.main()
