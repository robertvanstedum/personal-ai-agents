"""#553: minimoi_open_card, the one way MC opens a card on every model route (Grok, OpenAI failover, the Claude plan
route where its own Read and Skill tools are off by policy). Read-only, by name only, exactly the pinned release's cards."""
import json
import shutil
import subprocess
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.mc_tools import availability, cards, policy as P, read_only
from core.mc_tools.catalogue import ArgumentsRefused, validate

ROOT = Path(__file__).resolve().parents[2]
PIN = json.loads((ROOT / "services/mc_operations/personas/mc-chat/pin.json").read_text())["release"]
RELEASE = ROOT / "services/mc_operations/personas/mc-chat/releases" / PIN
CONFIG = json.loads((ROOT / "docker/mc-agent-container/openclaw.json").read_text())
ALLOWED = sorted(CONFIG["agents"]["entries"]["mc-chat"]["skills"])


class Cards(unittest.TestCase):
    def test_the_set_is_exactly_the_allow_listed_cards_of_the_pinned_release(self):
        self.assertEqual(sorted(cards.catalogue()), ALLOWED)
        enum = next(t for t in json.loads((ROOT / "core/mc_tools/tools.json").read_text())["tools"]
                    if t["name"] == "minimoi_open_card")["parameters"]["properties"]["name"]["enum"]
        self.assertEqual(sorted(enum), ALLOWED)

    def test_it_returns_the_card_text(self):
        for name in ALLOWED:
            state, text = cards.open_card(name)
            self.assertEqual(state, "ok", name)
            self.assertTrue(text.startswith(f"---\nname: {name}\ndescription: "), name)
        self.assertEqual(cards.open_card("mc-guildhall")[1], (RELEASE / "skills/mc-guildhall/SKILL.md").read_text())
        self.assertEqual(cards.open_card("minimoi-engineering")[1],
                         (ROOT / "services/mc_operations/handbook/v0.1.3/skills/minimoi-engineering/SKILL.md").read_text())

    def test_unknown_names_and_any_path_are_refused(self):
        for bad in ("mc-unknown", "minimoi-operations", "../pin", "skills/mc-guildhall/SKILL.md", "/etc/passwd",
                    "mc-guildhall/SKILL.md", "MC-GUILDHALL", "", None, 3, ["mc-guildhall"], "mc-guildhall\n", "a" * 80):
            self.assertEqual(cards.open_card(bad), ("card_unknown", ""), bad)
        for bad in ({}, {"name": "../SOUL"}, {"name": "mc-guildhall", "path": "x"}, {"name": "SOUL.md"},
                    {"name": "minimoi-operations"}, {"name": "/minimoi/repo/AGENTS.md"}):
            with self.assertRaises(ArgumentsRefused, msg=bad):
                validate("minimoi_open_card", bad)
        self.assertEqual(validate("minimoi_open_card", {"name": "mc-delivery"}), {"name": "mc-delivery"})

    def test_a_card_that_differs_from_its_manifest_is_not_served(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            persona = Path(tmp) / "mc-chat"
            shutil.copytree(ROOT / "services/mc_operations/personas/mc-chat", persona)
            with open(persona / "releases" / PIN / "skills/mc-guildhall/SKILL.md", "a") as f:
                f.write("tamper")
            self.assertEqual(cards.open_card("mc-guildhall", persona=persona), ("card_unverified", ""))
            self.assertEqual(cards.open_card("mc-delivery", persona=persona)[0], "ok")
            (persona / "pin.json").write_text('{"release": "9.9.9"}')
            self.assertEqual(cards.open_card("mc-delivery", persona=persona), ("cards_unavailable", ""))

    def test_a_release_without_cards_serves_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            persona = Path(tmp) / "mc-chat"
            shutil.copytree(ROOT / "services/mc_operations/personas/mc-chat", persona)
            (persona / "pin.json").write_text('{"release": "0.1.33"}')
            self.assertEqual(cards.catalogue(persona=persona), {})
            self.assertEqual(cards.open_card("mc-delivery", persona=persona), ("card_unknown", ""))


class Policy(unittest.TestCase):
    def test_it_is_a_tier_1_read_with_no_egress_in_both_policies_and_a_lookup_turn_admits_it(self):
        handler = P.HANDLERS["open_card_v1"]
        self.assertEqual((handler.tool, handler.capability, handler.tiers, handler.verify, handler.egress),
                         ("minimoi_open_card", "mc.skill.read", frozenset({1}), None, "none"))
        self.assertIn("minimoi_open_card", read_only.READ_TOOLS)
        docs = {}
        for name in ("mc-policy.prod.json", "mc-policy.example.json"):
            raw = (ROOT / "services/mc_operations/policy" / name).read_bytes()
            doc = json.loads(raw)
            authority = json.dumps({"schema": P.AUTHORITY_SCHEMA, "revision": doc["revision"], "digest": P.digest_of(doc),
                                    "approved_by": "robert", "approved_at": "2026-11-13T00:00:00Z", "ref": "#553"}).encode()
            rule = P.compile_policy(raw, authority).rule_for("minimoi_open_card")
            self.assertEqual((rule.tier, rule.egress, rule.capability), (1, "none", "mc.skill.read"), name)
            docs[name] = {r["handler"]: r for r in doc["rules"]}
        self.assertEqual(docs["mc-policy.prod.json"]["open_card_v1"], docs["mc-policy.example.json"]["open_card_v1"])

    def test_the_prod_revision_builds_on_547s_so_one_approval_covers_both(self):
        prod = json.loads((ROOT / "services/mc_operations/policy/mc-policy.prod.json").read_text())
        self.assertEqual(prod["revision"], "mc-prod-20261113-open-card")
        handlers = [r["handler"] for r in prod["rules"]]
        self.assertIn("github_get_v1", handlers)            # #547's rule, unchanged
        self.assertEqual(handlers.index("open_card_v1"), handlers.index("github_get_v1") + 1)
        base = subprocess.run(["git", "show", "origin/release/1.2.1:services/mc_operations/policy/mc-policy.prod.json"],
                              cwd=ROOT, capture_output=True, text=True)
        if base.returncode == 0:                              # the only change from #547's revision is this rule
            before = json.loads(base.stdout)
            self.assertEqual(before["revision"], "mc-prod-20261112-github-get")
            self.assertEqual([r for r in prod["rules"] if r["handler"] != "open_card_v1"], before["rules"])


class Routing(unittest.TestCase):
    def test_the_container_plugin_and_domain_offer_it(self):
        self.assertIn("minimoi_open_card", CONFIG["plugins"]["entries"]["minimoi-tools"]["config"]["tools"])
        plugin = json.loads((ROOT / "services/mc_operations/plugins/minimoi-tools/openclaw.plugin.json").read_text())
        self.assertIn("minimoi_open_card", plugin["contracts"]["tools"])
        self.assertEqual(plugin["configSchema"]["properties"]["tools"]["maxItems"], len(plugin["contracts"]["tools"]))
        manifest = json.loads((ROOT / "domains/guild/manifest.json").read_text())
        self.assertIn({"id": "mc.skill.read", "kind": "read", "label": "Open one of MC's cards (its procedures)",
                       "mc_tool": "minimoi_open_card", "status": "implemented"}, manifest["capabilities"])

    def test_availability_follows_the_cards(self):
        from core.mc_tools import catalogue
        from core.mc_tools.service import ToolService
        s = ToolService(lambda: None, principal="fixture", environment="dev")
        self.assertEqual(availability.snapshot(s, catalogue.TOOLS, catalogue.TOOLS)["minimoi_open_card"]["state"], "enabled")
        with mock.patch.object(cards, "catalogue", side_effect=cards.Unavailable("x")):
            self.assertEqual(availability.snapshot(s, catalogue.TOOLS, catalogue.TOOLS)["minimoi_open_card"]["state"], "disabled")

    def test_the_service_opens_a_card_and_refuses_with_words(self):
        from core.mc_tools import service as svc
        s = svc.ToolService(lambda: None, principal="robert", environment="dev")
        with mock.patch.object(svc.ToolService, "_tx", lambda self: nullcontext(None)), \
                mock.patch.object(svc.ToolService, "_bind", lambda self, conn, caller: SimpleNamespace(ledger_id=1)):
            done = s._open_card({"name": "mc-delivery"}, None, None)
            self.assertEqual((done["status"], done["code"], done["data"]), ("done", "card_read", {"name": "mc-delivery"}))
            self.assertIn((RELEASE / "skills/mc-delivery/SKILL.md").read_text(), done["message"])
            no = s._open_card({"name": "mc-nope"}, None, None)
        self.assertEqual((no["status"], no["code"]), ("refused", "card_unknown"))


class ClaudePlanRoute(unittest.TestCase):
    def test_on_the_claude_plan_route_the_tool_is_allowed_while_native_read_and_skill_stay_off(self):
        env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "MC_RUNTIME_MODEL_ACCESS": "plan",
               "MC_PLAN_MODEL": "anthropic/claude-opus-5"}
        script = (f"const m=require({json.dumps(str(ROOT / 'docker/mc-agent-container/model-access.cjs'))});"
                  "console.log(JSON.stringify(m.claudeSettings(process.env)));")
        r = subprocess.run(["node", "-e", script], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        settings = json.loads(r.stdout)
        deny = settings["permissions"]["deny"]
        self.assertIn("Read", deny)
        self.assertIn("Skill", deny)
        tool = "mcp__openclaw__minimoi_open_card"                 # how Claude sees MiniMoi's tools (RUNTIME binding)
        import fnmatch, re
        self.assertFalse(any(fnmatch.fnmatchcase(tool, rule) for rule in deny), "a deny rule would block the card tool")
        matcher = settings["hooks"]["PreToolUse"][0]["matcher"]
        self.assertIsNone(re.search(matcher, tool))                # the refuse-anything-else hook lets it through
        self.assertIsNotNone(re.search(matcher, "Read"))
        # The MiniMoi plugin offers it to mc-chat whatever the model.
        cfg = CONFIG["plugins"]["entries"]["minimoi-tools"]["config"]
        self.assertIn("minimoi_open_card", cfg["tools"])
        self.assertIn("mc-chat", cfg["agents"])


if __name__ == "__main__":
    unittest.main()
