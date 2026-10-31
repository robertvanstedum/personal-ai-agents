"""GrantStore's work-item API (host runtime spec v0.5 R4.3): covers_item reads, use_for_item uses one operation, and
both share the grant's one cardinality with prepare_effect. Opt-in: a disposable mc12_test_* database."""
from datetime import timedelta
import time
import unittest
from uuid import uuid4

from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.grants import CapabilityRule, GrantDefinition, GrantStore
from core.minimoi_actions.receipts import EffectIntent
from core.minimoi_actions.requests import ToolRequest
from postgres_fixture_support import DSN, PostgresFixture

SUBJECT = "a" * 64


@unittest.skipUnless(DSN, 'requires disposable MC_BINDING_TEST_DSN')
class GrantItemTests(PostgresFixture, unittest.TestCase):
    migrations = ('core/minimoi_actions/sql/004_action_receipts.sql',
                  'core/minimoi_actions/sql/005_action_grants.sql')

    def setUp(self):
        self.store = GrantStore({'delegate.start_build': CapabilityRule(1, False, True),
                                 'delegate.start_exact': CapabilityRule(1, True, True),
                                 'guest.reject': CapabilityRule(1, False, True)})
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('TRUNCATE guild.mc_action_grant_consumptions,guild.mc_action_grants,'
                        'guild.mc_action_events,guild.mc_action_receipts CASCADE')

    def now(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT clock_timestamp()')
            return cur.fetchone()[0]

    def issue(self, *, ops=1, seconds=600, capability='delegate.start_build', digests=(SUBJECT,), work_id=None,
              exact=False):
        gid = str(uuid4())
        extra = {}
        if exact:
            extra = dict(proposal_id=str(uuid4()), approved_operation_id=str(uuid4()),
                         approval_expires_at=self.now() + timedelta(seconds=1))
        with self.connect() as conn:
            self.store.issue(conn, GrantDefinition(gid, 'robert', 'dev', 'conversation', ('owner-turn',), ('mc',),
                                                   'policy', capability, 1, tuple(digests), ops,
                                                   self.now() + timedelta(seconds=seconds), work_id=work_id, **extra))
        return gid

    def covers(self, gid, subject=SUBJECT, work_ids=(), prefix='delegate.'):
        with self.connect() as conn:
            return self.store.covers_item(conn, 'robert', 'dev', gid, subject_digest=subject,
                                          capability_prefix=prefix, work_ids=work_ids)

    def use(self, gid, subject=SUBJECT, work_ids=()):
        with self.connect() as conn:
            return self.store.use_for_item(conn, 'robert', 'dev', gid, subject_digest=subject,
                                           capability_prefix='delegate.', work_ids=work_ids)

    def consumed(self, gid):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT consumed_operations FROM guild.mc_action_grants WHERE grant_id=%s', (gid,))
            return cur.fetchone()[0]

    def test_covers_item_reads_and_never_uses_an_operation(self):
        gid = self.issue()
        for _ in range(3):
            self.assertIsNone(self.covers(gid))
        self.assertEqual(self.consumed(gid), 0)
        self.assertEqual(self.covers(gid, subject='b' * 64), 'grant_effect_not_covered')
        self.assertEqual(self.covers(str(uuid4())), 'grant_not_found')
        self.assertEqual(self.covers(gid, prefix='guest.'), 'grant_effect_not_covered')
        self.assertEqual(self.covers(self.issue(capability='guest.reject')), 'grant_effect_not_covered')
        bound = self.issue(work_id='card-7')
        self.assertEqual(self.covers(bound, work_ids=('job-1',)), 'grant_context_mismatch')
        self.assertIsNone(self.covers(bound, work_ids=('card-7', 'job-1')))
        with self.connect() as conn:
            self.store.revoke(conn, 'robert', 'dev', gid)
        self.assertEqual(self.covers(gid), 'grant_revoked')
        for bad in (dict(subject='x'), dict(prefix='delegate'), dict(work_ids=['job-1'])):
            with self.assertRaisesRegex(ActionContractError, 'grant_request_invalid'):
                self.covers(self.issue(), **bad)

    def test_an_expired_grant_neither_covers_nor_admits(self):
        gid = self.issue(seconds=1)
        time.sleep(1.2)
        self.assertEqual(self.covers(gid), 'grant_expired')
        self.assertEqual(self.use(gid), 'grant_expired')
        self.assertEqual(self.consumed(gid), 0)

    def test_one_operation_admits_one_item_and_k_admit_k(self):
        one = self.issue()
        self.assertIsNone(self.use(one))
        self.assertEqual(self.use(one), 'grant_exhausted')
        self.assertEqual(self.consumed(one), 1)
        three = self.issue(ops=3)
        self.assertEqual([self.use(three) for _ in range(4)], [None, None, None, 'grant_exhausted'])
        self.assertEqual(self.consumed(three), 3)
        self.assertIsNone(self.covers(three))                  # still current: claims and renewals read it

    def test_a_rolled_back_admission_gives_the_operation_back(self):
        gid = self.issue()
        conn = self.connect()
        try:
            self.assertIsNone(self.store.use_for_item(conn, 'robert', 'dev', gid, subject_digest=SUBJECT,
                                                      capability_prefix='delegate.'))
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(self.consumed(gid), 0)
        self.assertIsNone(self.use(gid))

    def test_an_exact_approval_admits_only_inside_its_window(self):
        gid = self.issue(capability='delegate.start_exact', exact=True)
        time.sleep(1.2)
        self.assertIsNone(self.covers(gid))                    # the window bounds admission, not later rechecks
        self.assertEqual(self.use(gid), 'grant_approval_expired')
        self.assertEqual(self.consumed(gid), 0)

    def test_an_item_and_an_effect_share_the_grants_operations(self):
        request = ToolRequest('delegate.start_build', 1, b'{"job":"synthetic"}')
        gid = self.issue(digests=(request.arguments_sha256,))
        self.assertIsNone(self.use(gid, subject=request.arguments_sha256))
        context = ActionContext('robert', 'mc', 'dev', 'conversation', 'owner-turn', 'dispatch', 1,
                                self.now() + timedelta(minutes=5), 'policy', 'role', 'recorded', 'budget', (gid,))
        with self.assertRaisesRegex(ActionContractError, 'grant_exhausted'):
            with self.connect() as conn:
                self.store.prepare_effect(conn, context, EffectIntent(str(uuid4()), request, grant_id=gid),
                                          current_policy_version='policy')
        self.assertEqual(self.consumed(gid), 1)


if __name__ == '__main__':
    unittest.main()
