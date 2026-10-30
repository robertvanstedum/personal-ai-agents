"""The delegate.card floor (host runtime spec v0.5 R3.4-a, R4.3-c): a capability whose one exact approval covers a card's
enumerated steps is tier 3 only, under its own name as well as under the delegate. family. No MC tool for cards exists
yet (the owner path is S1-G's), so a handler is patched in here; the shipped policy and its tiers are unchanged
(test_policy.py)."""
import json
import unittest
from unittest import mock

from core.mc_tools import policy as P
from core.minimoi_actions.grants import CARD_CAPABILITY


def compile_one(handler_id, tier):
    rule = {"capability": P.HANDLERS[handler_id].capability, "version": 1, "actors": ["mc"],
            "origins": ["owner_turn"], "tier": tier, "approval": P._APPROVAL_FOR_TIER[tier],
            "source_requirement": "current_owner_turn", "handler": handler_id,
            "verify": P.HANDLERS[handler_id].verify, "undo": None, "egress": "delegation",
            "max_operations_per_turn": 1}
    doc = {"schema": P.SCHEMA, "revision": "card-floor-1", "rules": [rule]}
    auth = {"schema": P.AUTHORITY_SCHEMA, "revision": doc["revision"], "digest": P.digest_of(doc),
            "approved_by": "robert", "approved_at": "2026-10-28T20:00:00-05:00", "ref": "test"}
    return P.compile_policy(json.dumps(doc).encode(), json.dumps(auth).encode())


CARD = {"card_v1": P.Handler("minimoi_delegate_card", CARD_CAPABILITY, frozenset({1, 2, 3}), "job_started_v1",
                             "delegation"),
        "build_v1_any": P.Handler("minimoi_delegate_build", "delegate.start_build", frozenset({1, 2, 3}),
                                  "job_started_v1", "delegation")}


class CardFloor(unittest.TestCase):
    def test_delegate_card_is_tier_3_only(self):
        self.assertEqual(P.FLOOR_3_CARD, "delegate.card")
        with mock.patch.dict(P.HANDLERS, CARD):
            self.assertEqual(compile_one("card_v1", 3).rule_for("minimoi_delegate_card").tier, 3)
            for tier in (1, 2):
                with self.assertRaisesRegex(P.PolicyInvalid, "policy_below_floor"):
                    compile_one("card_v1", tier)

    def test_its_floor_holds_even_if_the_family_floor_were_narrowed(self):
        with mock.patch.dict(P.HANDLERS, CARD), mock.patch.object(P, "FLOOR_3", ("guest.reject",)):
            with self.assertRaisesRegex(P.PolicyInvalid, "policy_below_floor"):
                compile_one("card_v1", 2)
            self.assertEqual(compile_one("build_v1_any", 2).rule_for("minimoi_delegate_build").tier, 2)  # the contrast

    def test_no_handler_for_cards_is_shipped(self):
        self.assertFalse(any(h.capability == CARD_CAPABILITY for h in P.HANDLERS.values()))


if __name__ == "__main__":
    unittest.main()
