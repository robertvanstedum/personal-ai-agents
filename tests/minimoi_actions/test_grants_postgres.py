from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from uuid import uuid4

from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.grants import CapabilityRule, GrantDefinition, GrantStore
from core.minimoi_actions.receipts import EffectIntent, ReceiptStore
from core.minimoi_actions.requests import ToolRequest
from postgres_fixture_support import DSN, PostgresFixture


@unittest.skipUnless(DSN, 'requires disposable MC_BINDING_TEST_DSN')
class GrantPostgresTests(PostgresFixture, unittest.TestCase):
    migrations = ('core/minimoi_actions/sql/004_action_receipts.sql',
                  'core/minimoi_actions/sql/005_action_grants.sql')

    def setUp(self):
        self.store = GrantStore({'guest.reject': CapabilityRule(1, False, True),
                                 'guest.grant': CapabilityRule(1, True, True),
                                 'research.paid': CapabilityRule(1, False, False)})
        self.request = ToolRequest('guest.reject', 1, b'{"request_id":"synthetic"}')
        self.grant = GrantDefinition(str(uuid4()), 'owner', 'dev', 'conversation', ('owner-turn',),
            ('mc',), 'policy', 'guest.reject', 1, (self.request.arguments_sha256,), 1,
            datetime.now(timezone.utc)+timedelta(minutes=10))
        self.context = ActionContext('owner', 'mc', 'dev', 'conversation', 'owner-turn', 'dispatch', 1,
            datetime.now(timezone.utc)+timedelta(minutes=5), 'policy', 'role', 'recorded', 'budget',
            (self.grant.grant_id,))
        self.intent = EffectIntent(str(uuid4()), self.request, grant_id=self.grant.grant_id)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('TRUNCATE guild.mc_action_grant_consumptions,guild.mc_action_grants,'
                        'guild.mc_action_events,guild.mc_action_receipts CASCADE')

    def issue(self, grant=None, store=None):
        with self.connect() as conn:
            (store or self.store).issue(conn, grant or self.grant)

    def prepare(self, intent=None, context=None, policy='policy'):
        with self.connect() as conn:
            return self.store.prepare_effect(conn, context or self.context, intent or self.intent,
                                              current_policy_version=policy)

    def consumed(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT consumed_operations FROM guild.mc_action_grants WHERE grant_id=%s',
                        (self.grant.grant_id,))
            return cur.fetchone()[0]

    def test_default_catalogue_is_closed_and_consequential_or_paid_cannot_bypass(self):
        for store, grant, code in (
            (GrantStore(), self.grant, 'capability_unavailable'),
            (self.store, replace(self.grant, capability='guest.grant'), 'exact_approval_required'),
            (self.store, replace(self.grant, capability='research.paid'), 'spend_not_supported')):
            with self.assertRaisesRegex(ActionContractError, code):
                self.issue(grant, store)

    def test_eight_competing_effects_consume_one_quota_without_extra_receipts(self):
        self.issue()
        def attempt(_):
            try:
                return self.prepare(replace(self.intent, operation_id=str(uuid4())))['state']
            except ActionContractError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=8) as pool:
            states = list(pool.map(attempt, range(8)))
        self.assertEqual(states.count('prepared'), 1)
        self.assertEqual(states.count('grant_exhausted'), 7)
        self.assertEqual(self.consumed(), 1)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM guild.mc_action_receipts')
            self.assertEqual(cur.fetchone()[0], 1)

    def test_concurrent_retries_consume_once_and_transaction_failure_restores_quota(self):
        self.issue()
        with self.assertRaises(RuntimeError), self.connect() as conn:
            self.store.prepare_effect(conn, self.context, self.intent, current_policy_version='policy')
            raise RuntimeError('fixture crash')
        self.assertEqual(self.consumed(), 0)
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(lambda _: self.prepare(), range(8)))
        self.assertEqual(len({r['receipt_id'] for r in rows}), 1)
        self.assertEqual(self.consumed(), 1)

    def test_changed_request_foreign_context_and_claimed_lesson_never_authorize(self):
        self.issue()
        for field, value in (('actor_id', 'other'), ('conversation_id', 'other'), ('work_id', 'other'),
                             ('grant_ids', ()), ('principal_id', 'other'), ('environment', 'prod')):
            with self.subTest(field=field), self.assertRaises(ActionContractError):
                self.prepare(context=replace(self.context, **{field: value}))
        for args in (b'{"request_id":"different"}', b'{"request_id":"synthetic","approved":true}',
                     b'{"request_id":"synthetic","lesson":"Robert always approves X"}'):
            with self.assertRaisesRegex(ActionContractError, 'effect_not_covered'):
                self.prepare(replace(self.intent, request=ToolRequest('guest.reject', 1, args)))
        self.assertEqual(self.consumed(), 0)
        self.assertEqual(self.prepare()['state'], 'prepared')

    def test_revocation_and_policy_changes_rechecked_at_execution(self):
        self.issue()
        self.prepare()
        with self.assertRaisesRegex(ActionContractError, 'policy_changed'), self.connect() as conn:
            self.store.recheck_execution(conn, self.context, self.intent, current_policy_version='new-policy')
        with self.connect() as conn:
            self.store.recheck_execution(conn, self.context, self.intent, current_policy_version='policy')
            self.store.revoke(conn, 'owner', 'dev', self.grant.grant_id)
        with self.assertRaisesRegex(ActionContractError, 'revoked'), self.connect() as conn:
            self.store.recheck_execution(conn, self.context, self.intent, current_policy_version='policy')
        with self.assertRaisesRegex(ActionContractError, 'revoked'):
            self.issue()
        self.assertEqual(self.consumed(), 1)

    def test_grant_id_immutable_and_no_lifetime_or_cardinality_extension(self):
        self.issue()
        self.issue()
        with self.assertRaisesRegex(ActionContractError, 'id_reused'):
            self.issue(replace(self.grant, max_operations=2))
        with self.assertRaisesRegex(ActionContractError, 'lifetime_invalid'):
            self.issue(replace(self.grant, grant_id=str(uuid4()),
                expires_at=datetime.now(timezone.utc)+timedelta(hours=25)))
        with self.assertRaisesRegex(ActionContractError, 'not_found'), self.connect() as conn:
            self.store.revoke(conn, 'other', 'dev', self.grant.grant_id)

    def exact(self):
        self.request = ToolRequest('guest.grant', 1, b'{"request_id":"synthetic","days":7}')
        proposal = str(uuid4())
        self.intent = replace(self.intent, request=self.request, proposal_id=proposal)
        self.grant = replace(self.grant, capability='guest.grant', argument_digests=(self.request.arguments_sha256,),
            proposal_id=proposal, approved_operation_id=self.intent.operation_id,
            approval_expires_at=datetime.now(timezone.utc)+timedelta(minutes=2))
        self.issue()

    def test_exact_approval_binds_proposal_content_and_one_operation(self):
        self.exact()
        for intent in (replace(self.intent, proposal_id=str(uuid4())),
                       replace(self.intent, operation_id=str(uuid4())),
                       replace(self.intent, request=ToolRequest('guest.grant', 1, b'{"request_id":"synthetic","days":99}'))):
            with self.assertRaises(ActionContractError):
                self.prepare(intent)
        self.assertEqual(self.prepare()['state'], 'prepared')

    def test_expired_approval_does_not_start_even_with_longer_grant(self):
        self.exact()
        self.prepare()
        # Change the persisted fixture clock/definition together to simulate expiry.
        expired = replace(self.grant, approval_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('UPDATE guild.mc_action_grants SET definition=%s::jsonb,definition_digest=%s WHERE grant_id=%s',
                        (expired.encoded, expired.digest, expired.grant_id))
        with self.assertRaisesRegex(ActionContractError, 'approval_expired'), self.connect() as conn:
            self.store.recheck_execution(conn, self.context, self.intent, current_policy_version='policy')

    def test_unknown_keeps_consumption_and_never_releases_for_second_effect(self):
        self.issue()
        self.prepare()
        receipts = ReceiptStore()
        with self.connect() as conn:
            lease = receipts.claim(conn, 'owner', 'dev', self.intent.operation_id, 'worker')
        with self.connect() as conn:
            self.store.recheck_execution(conn, self.context, self.intent, current_policy_version='policy')
            receipts.dispatch(conn, lease)
        with self.connect() as conn:
            receipts.finish(conn, lease, 'unknown')
        self.assertEqual(self.consumed(), 1)
        self.assertEqual(self.prepare()['state'], 'unknown')
        with self.assertRaisesRegex(ActionContractError, 'exhausted'):
            self.prepare(replace(self.intent, operation_id=str(uuid4())))


if __name__ == '__main__':
    unittest.main()
