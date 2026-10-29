"""GrantStore's card rule (host runtime spec v0.5 R4.3-c, Robert's N1), without a database: one exact approval of a card
covers its enumerated steps, one operation each, and nothing else changes. Every existing capability keeps the exact
approval rule as it was (one operation on one exact request). The PostgreSQL half (prepare_effect, recheck_execution,
use_for_item) is test_card_grants_postgres.py."""
from datetime import datetime, timedelta, timezone
import json
import unittest
from uuid import uuid4

from core.mc_tools import effects
from core.minimoi_actions.context import ActionContractError
from core.minimoi_actions.grants import (CARD_CAPABILITY, GrantDefinition, card_step_operation_id,
                                         card_step_template)
from core.minimoi_actions.requests import ToolRequest

NOW = datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)
D = ["a" * 64, "b" * 64, "c" * 64]


def grant(capability, digests, ops, *, exact=True, work_id=None, **over):
    extra = dict(proposal_id=str(uuid4()), approved_operation_id=str(uuid4()),
                 approval_expires_at=NOW + timedelta(minutes=5)) if exact else {}
    extra.update(over)
    return GrantDefinition(str(uuid4()), "robert", "dev", "conversation", ("owner-turn",), ("mc",), "policy",
                           capability, 1, tuple(digests), ops, NOW + timedelta(hours=1), work_id=work_id, **extra)


def step_request(template, arts=(), **extra):
    return ToolRequest(CARD_CAPABILITY, 1, json.dumps({"step_template": template, "input_artifacts": list(arts),
                                                       **extra}).encode())


class ExactApprovalUnchanged(unittest.TestCase):
    def test_every_existing_capability_keeps_one_operation_on_one_exact_request(self):
        caps = set(effects.CATALOGUE) | {"delegate.start_research", "delegate.start_build"}
        self.assertIn("delegate.start_build", effects.EXACT_CAPABILITIES)
        self.assertNotIn(CARD_CAPABILITY, effects.CATALOGUE)              # MC's catalogue is unchanged
        for cap in sorted(caps):
            with self.subTest(cap=cap):
                grant(cap, D[:1], 1)                                       # one exact request, one operation
                for digests, ops in ((D[:2], 2), (D[:1], 2), (D[:2], 1), (D[:3], 3)):
                    with self.assertRaisesRegex(ActionContractError, "grant_approval_invalid"):
                        grant(cap, digests, ops)
                grant(cap, D[:2], 3, exact=False)                          # a plain grant: as before
                grant(cap, D[:1], 1, work_id="card-1")


class CardRule(unittest.TestCase):
    def test_a_card_approval_covers_its_enumerated_steps_one_operation_each(self):
        for n in (1, 3, 100):
            digests = [f"{i:064x}" for i in range(n)]
            self.assertEqual(grant(CARD_CAPABILITY, digests, n, work_id="C-17").max_operations, n)
        for digests, ops in ((D, 2), (D, 4), (D[:1], 2)):
            with self.assertRaisesRegex(ActionContractError, "grant_approval_invalid"):
                grant(CARD_CAPABILITY, digests, ops, work_id="C-17")
        with self.assertRaisesRegex(ActionContractError, "grant_constraints_invalid"):
            grant(CARD_CAPABILITY, [f"{i:064x}" for i in range(101)], 100, work_id="C-17")   # the schema's bound
        with self.assertRaisesRegex(ActionContractError, "grant_definition_invalid"):
            grant(CARD_CAPABILITY, [f"{i:064x}" for i in range(100)], 101, work_id="C-17")

    def test_a_card_grant_is_always_one_exact_approval_of_one_card(self):
        with self.assertRaisesRegex(ActionContractError, "grant_approval_invalid"):
            grant(CARD_CAPABILITY, D, 3)                                   # no card: no work id (R4.3-f)
        with self.assertRaisesRegex(ActionContractError, "grant_approval_invalid"):
            grant(CARD_CAPABILITY, D, 3, exact=False, work_id="C-17")      # never a plain grant
        with self.assertRaisesRegex(ActionContractError, "grant_approval_invalid"):
            grant(CARD_CAPABILITY, D, 3, work_id="C-17", approval_expires_at=None)

    def test_a_step_request_is_its_template_digest_and_artifact_digests_only(self):
        arts = [{"ref": "r1-g1.patch", "sha256": "e" * 64}]
        self.assertEqual(card_step_template(step_request(D[0])), D[0])
        self.assertEqual(card_step_template(step_request(D[0], arts)), D[0])
        self.assertEqual(card_step_template(step_request(D[0], arts * 16)), D[0])
        for bad in (step_request(D[0], arts, brief="do something else"),                  # a second free field
                    step_request(D[0], [{"ref": "r1", "sha256": "not-a-digest"}]),
                    step_request(D[0], [{"ref": "r1", "sha256": "e" * 64, "kind": "patch"}]),
                    step_request(D[0], [{"ref": "../x", "sha256": "e" * 64}]),
                    step_request(D[0], arts * 17),
                    step_request("A" * 64), step_request("a" * 63),
                    ToolRequest(CARD_CAPABILITY, 1, json.dumps({"step_template": D[0]}).encode())):
            self.assertIsNone(card_step_template(bad))

    def test_each_step_has_one_operation_id_derived_from_the_approval(self):
        approval = str(uuid4())
        ids = {card_step_operation_id(approval, d) for d in D}
        self.assertEqual(len(ids), 3)
        self.assertEqual(card_step_operation_id(approval, D[0]), card_step_operation_id(approval, D[0]))
        self.assertNotEqual(card_step_operation_id(approval, D[0]), card_step_operation_id(str(uuid4()), D[0]))


if __name__ == "__main__":
    unittest.main()
