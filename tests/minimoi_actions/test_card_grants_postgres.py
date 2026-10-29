"""GrantStore's card rule on a real PostgreSQL (host runtime spec v0.5 R4.3-c, N1): each step of an approved card is
checked against its template digest at prepare_effect and at recheck_execution, once each, under one operation id per
step; the approval window bounds the card's first operation only; use_for_item admits steps the same way. An existing
exact approval is unchanged. Opt-in: a disposable mc12_test_* database."""
from datetime import timedelta
import json
import time
import unittest
from uuid import uuid4

from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.grants import (CARD_CAPABILITY, CapabilityRule, GrantDefinition, GrantStore,
                                         card_step_operation_id)
from core.minimoi_actions.receipts import EffectIntent
from core.minimoi_actions.requests import ToolRequest
from postgres_fixture_support import DSN, PostgresFixture

STEPS = ("1" * 64, "2" * 64, "3" * 64)
POLICY = "minimoi.runtime.card.v1"
ART = {"ref": "r-g1.patch", "sha256": "e" * 64}


def step_request(template, arts=(ART,), **extra):
    return ToolRequest(CARD_CAPABILITY, 1, json.dumps({"step_template": template, "input_artifacts": list(arts),
                                                       **extra}).encode())


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

    def card_grant(self, *, window=300, digests=STEPS, capability=CARD_CAPABILITY, work_id='C-17'):
        gid, approval, proposal = str(uuid4()), str(uuid4()), str(uuid4())
        with self.connect() as conn:
            self.store.issue(conn, GrantDefinition(
                gid, 'robert', 'dev', 'C-17', ('owner-turn',), ('authority',), POLICY, capability, 1,
                tuple(digests), len(digests) if capability == CARD_CAPABILITY else 1,
                self.now() + timedelta(hours=1), work_id=work_id, proposal_id=proposal,
                approved_operation_id=approval, approval_expires_at=self.now() + timedelta(seconds=window)))
        return gid, approval, proposal

    def context(self, gid):
        return ActionContext('robert', 'authority', 'dev', 'C-17', 'turn', 'dispatch', 1,
                             self.now() + timedelta(minutes=5), POLICY, 'role', 'recorded', 'fakework', (gid,),
                             work_id='C-17')

    def intent(self, gid, approval, proposal, template, request=None, operation_id=None):
        return EffectIntent(operation_id or card_step_operation_id(approval, template),
                            request or step_request(template), grant_id=gid, proposal_id=proposal)

    def prepare(self, gid, intent):
        with self.connect() as conn:
            return self.store.prepare_effect(conn, self.context(gid), intent, current_policy_version=POLICY)

    def recheck(self, gid, intent):
        with self.connect() as conn:
            self.store.recheck_execution(conn, self.context(gid), intent, current_policy_version=POLICY)

    def consumed(self, gid):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT consumed_operations FROM guild.mc_action_grants WHERE grant_id=%s', (gid,))
            return cur.fetchone()[0]

    def use(self, gid, subject):
        with self.connect() as conn:
            return self.store.use_for_item(conn, 'robert', 'dev', gid, subject_digest=subject,
                                           capability_prefix='delegate.', work_ids=('C-17', 'C-17.build'))

    def test_each_step_is_checked_against_its_template_digest_at_prepare_and_at_recheck(self):
        gid, approval, proposal = self.card_grant()
        first = self.intent(gid, approval, proposal, STEPS[0])
        self.assertEqual(self.prepare(gid, first)['state'], 'prepared')
        self.recheck(gid, first)
        stranger = "9" * 64                                   # a step this card does not enumerate
        for template, request in ((stranger, None),
                                  (STEPS[1], step_request(STEPS[1], brief='another brief')),       # a second free field
                                  (STEPS[1], step_request(STEPS[1], [{'ref': 'x', 'sha256': 'nope'}]))):
            bad = self.intent(gid, approval, proposal, template, request)
            with self.assertRaisesRegex(ActionContractError, 'grant_effect_not_covered'):
                self.prepare(gid, bad)
            with self.assertRaisesRegex(ActionContractError, 'grant_effect_not_covered'):
                self.recheck(gid, bad)
        # The prepared step's own operation, executed with a request that names another template, is refused too.
        swapped = self.intent(gid, approval, proposal, STEPS[0], step_request(stranger))
        with self.assertRaisesRegex(ActionContractError, 'grant_effect_not_covered'):
            self.recheck(gid, swapped)
        self.assertEqual(self.consumed(gid), 1)

    def test_one_operation_per_step_under_the_approvals_own_operation_ids(self):
        gid, approval, proposal = self.card_grant()
        with self.assertRaisesRegex(ActionContractError, 'grant_approval_operation_mismatch'):
            self.prepare(gid, self.intent(gid, approval, proposal, STEPS[0], operation_id=approval))
        with self.assertRaisesRegex(ActionContractError, 'grant_approval_operation_mismatch'):
            self.prepare(gid, self.intent(gid, approval, proposal, STEPS[0],
                                          operation_id=card_step_operation_id(approval, STEPS[1])))
        for template in STEPS:
            self.prepare(gid, self.intent(gid, approval, proposal, template))
        self.assertEqual(self.consumed(gid), 3)
        again = self.intent(gid, approval, proposal, STEPS[0])
        self.assertEqual(self.prepare(gid, again)['state'], 'prepared')        # the same step again: the same receipt
        with self.assertRaisesRegex(ActionContractError, 'grant_operation_reused'):
            self.prepare(gid, self.intent(gid, approval, proposal, STEPS[0], step_request(STEPS[0], [])))
        self.assertEqual(self.consumed(gid), 3)

    def test_the_approval_window_bounds_the_cards_first_operation_only(self):
        gid, approval, proposal = self.card_grant(window=1)
        self.assertIsNone(self.use(gid, STEPS[0]))                         # admitted inside the window
        time.sleep(1.2)
        self.assertIsNone(self.use(gid, STEPS[1]))                         # a later step of the admitted card
        self.prepare(gid, self.intent(gid, approval, proposal, STEPS[2]))
        self.assertEqual(self.use(gid, STEPS[2]), 'grant_exhausted')      # three steps, three operations
        late, approval2, proposal2 = self.card_grant(window=1)
        time.sleep(1.2)
        self.assertEqual(self.use(late, STEPS[0]), 'grant_approval_expired')       # never admitted in time
        with self.assertRaisesRegex(ActionContractError, 'grant_approval_expired'):
            self.prepare(late, self.intent(late, approval2, proposal2, STEPS[0]))
        self.assertEqual(self.consumed(late), 0)

    def test_an_existing_exact_approval_is_unchanged_and_the_window_rule_is_the_cards_alone(self):
        request = ToolRequest('delegate.start_build', 1, b'{"job":"j"}')
        exact, approval, proposal = self.card_grant(window=1, digests=(request.arguments_sha256,),
                                                    capability='delegate.start_build')
        intent = EffectIntent(approval, request, grant_id=exact, proposal_id=proposal)
        with self.connect() as conn:
            self.store.prepare_effect(conn, self.context(exact), intent, current_policy_version=POLICY)
        self.recheck(exact, intent)
        card, card_approval, card_proposal = self.card_grant(window=1)
        first = self.intent(card, card_approval, card_proposal, STEPS[0])
        self.prepare(card, first)
        time.sleep(1.2)
        with self.assertRaisesRegex(ActionContractError, 'grant_approval_expired'):
            self.recheck(exact, intent)            # as before: an expired exact approval is refused at execution
        self.recheck(card, first)                  # a card's admitted step runs on after its window


if __name__ == '__main__':
    unittest.main()
