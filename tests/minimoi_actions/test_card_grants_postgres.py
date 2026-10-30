"""GrantStore's card rule on a real PostgreSQL (host runtime spec v0.5 R4.3-c, N1), on the work-item path the authority
uses: use_for_item at admission (prepare_effect's part) and covers_item at claim and renew (recheck_execution's).
One card grant has one operation per enumerated step, each step named by its template digest; its approval window
bounds the card's first operation only. On the effect path a card grant is what any exact approval is: one exact
request, and an expired window refused at execution. Binding each operation to one step is the authority's
(grant.step_bound, test_cards_postgres.py). Opt-in: a disposable mc12_test_* database."""
from datetime import timedelta
import json
import time
import unittest
from uuid import uuid4

from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.grants import CARD_CAPABILITY, CapabilityRule, GrantDefinition, GrantStore
from core.minimoi_actions.receipts import EffectIntent
from core.minimoi_actions.requests import ToolRequest
from postgres_fixture_support import DSN, PostgresFixture

STEPS = ("1" * 64, "2" * 64, "3" * 64)
POLICY = "minimoi.runtime.card.v1"


@unittest.skipUnless(DSN, 'requires disposable MC_BINDING_TEST_DSN')
class CardGrantTests(PostgresFixture, unittest.TestCase):
    migrations = ('core/minimoi_actions/sql/004_action_receipts.sql',
                  'core/minimoi_actions/sql/005_action_grants.sql')

    def setUp(self):
        self.store = GrantStore({CARD_CAPABILITY: CapabilityRule(1, True, True),
                                 'delegate.start_build': CapabilityRule(1, True, True)})
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('TRUNCATE guild.mc_action_grant_consumptions,guild.mc_action_grants,'
                        'guild.mc_action_events,guild.mc_action_receipts CASCADE')

    def now(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT clock_timestamp()')
            return cur.fetchone()[0]

    def grant(self, *, window=300, digests=STEPS, capability=CARD_CAPABILITY):
        gid, approval, proposal = str(uuid4()), str(uuid4()), str(uuid4())
        with self.connect() as conn:
            self.store.issue(conn, GrantDefinition(
                gid, 'robert', 'dev', 'C-17', ('owner-turn',), ('authority',), POLICY, capability, 1,
                tuple(digests), len(digests) if capability == CARD_CAPABILITY else 1,
                self.now() + timedelta(hours=1), work_id='C-17', proposal_id=proposal,
                approved_operation_id=approval, approval_expires_at=self.now() + timedelta(seconds=window)))
        return gid, approval, proposal

    def context(self, gid):
        return ActionContext('robert', 'authority', 'dev', 'C-17', 'turn', 'dispatch', 1,
                             self.now() + timedelta(minutes=5), POLICY, 'role', 'recorded', 'fakework', (gid,),
                             work_id='C-17')

    def consumed(self, gid):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT consumed_operations FROM guild.mc_action_grants WHERE grant_id=%s', (gid,))
            return cur.fetchone()[0]

    def use(self, gid, subject):
        with self.connect() as conn:
            return self.store.use_for_item(conn, 'robert', 'dev', gid, subject_digest=subject,
                                           capability_prefix='delegate.', work_ids=('C-17', 'C-17.build'))

    def covers(self, gid, subject):
        with self.connect() as conn:
            return self.store.covers_item(conn, 'robert', 'dev', gid, subject_digest=subject,
                                          capability_prefix='delegate.', work_ids=('C-17',))

    def test_each_step_is_admitted_and_rechecked_by_its_template_digest(self):
        gid, *_ = self.grant()
        stranger = "9" * 64                                   # a step this card does not enumerate
        self.assertEqual(self.use(gid, stranger), 'grant_effect_not_covered')
        self.assertEqual(self.covers(gid, stranger), 'grant_effect_not_covered')
        self.assertEqual(self.consumed(gid), 0)
        self.assertIsNone(self.use(gid, STEPS[0]))
        for step in STEPS:
            self.assertIsNone(self.covers(gid, step))         # claim and renew recheck, using nothing
        self.assertEqual(self.consumed(gid), 1)
        with self.connect() as conn:
            self.store.revoke(conn, 'robert', 'dev', gid)
        self.assertEqual(self.covers(gid, STEPS[0]), 'grant_revoked')
        self.assertEqual(self.use(gid, STEPS[1]), 'grant_revoked')

    def test_the_approval_window_bounds_the_cards_first_operation_only(self):
        gid, *_ = self.grant(window=1)
        self.assertIsNone(self.use(gid, STEPS[0]))                         # admitted inside the window
        time.sleep(1.2)
        self.assertIsNone(self.use(gid, STEPS[1]))                         # a later step of the admitted card
        self.assertIsNone(self.use(gid, STEPS[2]))
        self.assertEqual(self.use(gid, STEPS[2]), 'grant_exhausted')      # three steps, three operations
        late, *_ = self.grant(window=1)
        time.sleep(1.2)
        self.assertEqual(self.use(late, STEPS[0]), 'grant_approval_expired')       # never admitted in time
        self.assertEqual(self.consumed(late), 0)

    def test_on_the_effect_path_a_card_grant_is_one_exact_request_and_existing_approvals_are_unchanged(self):
        card, approval, proposal = self.grant()
        step = ToolRequest(CARD_CAPABILITY, 1, json.dumps({"step_template": STEPS[0]}).encode())
        with self.connect() as conn, self.assertRaisesRegex(ActionContractError, 'grant_effect_not_covered'):
            self.store.prepare_effect(conn, self.context(card), EffectIntent(approval, step, grant_id=card,
                                      proposal_id=proposal), current_policy_version=POLICY)
        request = ToolRequest('delegate.start_build', 1, b'{"job":"j"}')
        exact, approval, proposal = self.grant(window=1, digests=(request.arguments_sha256,),
                                               capability='delegate.start_build')
        intent = EffectIntent(approval, request, grant_id=exact, proposal_id=proposal)
        with self.connect() as conn:
            self.store.prepare_effect(conn, self.context(exact), intent, current_policy_version=POLICY)
        with self.connect() as conn:
            self.store.recheck_execution(conn, self.context(exact), intent, current_policy_version=POLICY)
        time.sleep(1.2)
        with self.connect() as conn, self.assertRaisesRegex(ActionContractError, 'grant_approval_expired'):
            self.store.recheck_execution(conn, self.context(exact), intent, current_policy_version=POLICY)


if __name__ == '__main__':
    unittest.main()
