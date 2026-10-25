"""No live data: policy, dependency and plugin evidence must all agree."""
import unittest
from core.mc_tools import availability as A, catalogue, house
from core.mc_tools.service import ToolService


class AvailabilityTests(unittest.TestCase):
    def service(self, **kw):
        return ToolService(lambda: None, principal="fixture", environment="dev", **kw)

    def test_every_disabled_layer_is_retained_and_healthy_domain_cannot_override_it(self):
        s = self.service()
        out = A.snapshot(s, set(), frozenset())
        r = out["minimoi_delegate_research"]
        self.assertEqual(r["state"], "disabled")
        self.assertEqual({x["layer"] for x in r["reasons"]}, {"policy", "portal", "plugin"})
        d = house.describe_domain("guild", enabled_tools=set(catalogue.TOOLS),
                                  availability=out, health_for=lambda _: {"state": "ok"})
        self.assertFalse(any(c["state"] == "available" for c in d["capabilities"]))

    def test_old_plugin_is_unknown_but_explicit_omission_is_disabled(self):
        s = self.service()
        self.assertEqual(A.snapshot(s, catalogue.TOOLS, None)["minimoi_host_health"]["state"], "unknown")
        self.assertEqual(A.snapshot(s, catalogue.TOOLS, frozenset())["minimoi_host_health"]["state"], "disabled")

    def test_direct_description_without_dependency_evidence_does_not_assume_available(self):
        d = house.describe_domain("guild", enabled_tools=catalogue.TOOLS,
                                  health_for=lambda _: {"state": "ok"})
        self.assertFalse(any(c["state"] == "available" for c in d["capabilities"]))

    def test_available_still_requires_liveness_and_planned_remains_absent(self):
        a = A.snapshot(self.service(), catalogue.TOOLS, frozenset(catalogue.TOOLS))
        for live, state in (("ok", "available"), ("down", "unreachable"), ("unknown", "unknown")):
            d = house.describe_domain("guild", enabled_tools=catalogue.TOOLS, availability=a,
                                      health_for=lambda _: {"state": live})
            self.assertEqual(next(c["state"] for c in d["capabilities"] if c["id"] == "host.health"), state)
        d = house.describe_domain("german", enabled_tools=catalogue.TOOLS, availability=a,
                                  health_for=lambda _: {"state": "ok"})
        self.assertTrue(all(c["state"] == "not_implemented" for c in d["capabilities"]))

    def test_portal_dependencies_are_not_replaced_by_policy_or_plugin_membership(self):
        s = self.service()
        cases = [("github", object(), "minimoi_github_issues"),
                 ("lessons", object(), "minimoi_revoke_preference"),
                 ("registers", object(), "minimoi_withdraw_gap")]
        for attr, value, name in cases:
            self.assertEqual(A.snapshot(s, catalogue.TOOLS, catalogue.TOOLS)[name]["state"], "disabled")
            setattr(s, attr, value)
            self.assertEqual(A.snapshot(s, catalogue.TOOLS, catalogue.TOOLS)[name]["state"], "enabled")

    def test_display_and_readback_both_required_for_offers(self):
        s = self.service(delegation=True, show=lambda *a: True)
        out = A.snapshot(s, catalogue.TOOLS, catalogue.TOOLS)["minimoi_delegate_research"]
        self.assertEqual(out["reasons"], [{"layer": "portal", "reason": "proposal_readback_unavailable"}])
        s.read_shown = lambda *a: {}
        self.assertEqual(A.snapshot(s, catalogue.TOOLS, catalogue.TOOLS)["minimoi_delegate_research"]["state"], "enabled")

    def test_metadata_is_strict_and_not_a_model_argument(self):
        for v in ("all", {}, [False], ["minimoi_host_health"]*2, ["made_up"]):
            with self.assertRaises(ValueError):
                A.offered_list(v)
        self.assertIsNone(A.offered_list(None))
        self.assertEqual(A.offered_list([]), frozenset())
        from core.minimoi_actions.context import ActionContractError
        with self.assertRaises(ActionContractError):
            catalogue.validate("minimoi_describe_house", {"offered_tools": list(catalogue.TOOLS)})
