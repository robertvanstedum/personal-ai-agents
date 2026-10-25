"""M1.4 knowledge of the house and the deferred Wörter gap (spec v1.2 §11.1, §16.1; A09, A09b) on real PostgreSQL, the
real work register and the real policy. Run with tests/mc_tools/run_postgres.sh <pgbin>."""
import unittest
from pathlib import Path

from test_tool_path_postgres import Base, CONV, DSN, NOTE, OWNER  # noqa: F401
from core.work_registers import RegisterStore

PHRASE = "This is the type of phrase I'd like to save in Wörter: „Es kommt darauf an.“"


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class HouseTests(Base, unittest.TestCase):
    def call(self, name, args=None, **kw):
        from core.mc_tools.availability import DESCRIBING_TOOLS
        from core.mc_tools import catalogue
        if name in DESCRIBING_TOOLS:
            kw.setdefault("offered_tools", list(catalogue.TOOLS))
        return super().call(name, args, **kw)

    def setUp(self):
        super().setUp()
        self.registers = RegisterStore(Path(self.tmp.name).resolve() / "registers", environment="dev")
        self.registers.initialize()
        self.health = {"german": {"state": "ok", "evidence": "internal_dev_health:german", "observed_at": "now"},
                       "guild": {"state": "ok", "evidence": "internal_dev_health:portal", "observed_at": "now"}}
        self.service.registers, self.service.domain_health = self.registers, lambda: self.health
        self.owner_notes[(CONV, NOTE)] = PHRASE

    def gap(self, quote=PHRASE, domain="german", capability="phrases.create"):
        return self.call("minimoi_record_gap", {"domain": domain, "capability": capability, "quote": quote})

    # ── A09: what exists, and what is unavailable versus absent ──
    def test_a09_the_house_names_every_domain_and_counts_what_is_usable_now(self):
        r = self.call("minimoi_describe_house")
        self.assertEqual(r["status"], "done")
        names = {d["domain"]: d for d in r["data"]["domains"]}
        self.assertEqual(set(names), {"guild", "german", "portuguese", "curator", "cos"})
        self.assertEqual(names["german"]["name"], "Mein Deutsch")
        self.assertEqual(names["german"]["capabilities"], {"not_implemented": 2})
        self.assertIn("Mein Deutsch (german)", r["message"])

    def test_a09_a_domain_tells_unavailable_apart_from_absent_with_evidence(self):
        r = self.call("minimoi_describe_domain", {"domain": "german"})
        save = next(c for c in r["data"]["capabilities"] if c["id"] == "phrases.create")
        self.assertEqual((save["state"], save["milestone"]), ("not_implemented", "D1"))
        self.assertIn("phrases.create): not_implemented, planned for D1", r["message"])
        self.assertEqual(r["data"]["sources"][0]["manifest"], "domains/german/manifest.json")
        self.assertEqual(r["data"]["sources"][1]["liveness"], "internal_dev_health:german")
        guild = self.call("minimoi_describe_domain", {"domain": "guild"})["data"]["capabilities"]
        self.assertEqual(next(c for c in guild if c["id"] == "guest.list")["state"], "available")
        self.health["guild"]["state"] = "down"
        guild = self.call("minimoi_describe_domain", {"domain": "guild"})["data"]["capabilities"]
        self.assertEqual(next(c for c in guild if c["id"] == "guest.list")["state"], "unreachable")
        self.health.clear()
        guild = self.call("minimoi_describe_domain", {"domain": "guild"})["data"]["capabilities"]
        self.assertEqual(next(c for c in guild if c["id"] == "guest.list")["state"], "unknown")

    def test_a09_a_tool_the_policy_does_not_enable_is_disabled_not_available(self):
        from test_policy_postgres import drop
        self.install_policy(change=drop("minimoi_keep_preference"))
        caps = self.call("minimoi_describe_domain", {"domain": "guild"})["data"]["capabilities"]
        self.assertEqual(next(c for c in caps if c["id"] == "lessons.keep_preference")["state"], "disabled")

    def test_portal_off_and_plugin_omission_override_healthy_domain_and_permissive_policy(self):
        self.service.delegation = False
        r = self.call("minimoi_describe_domain", {"domain": "guild"}, offered_tools=["minimoi_describe_domain"])
        cap = next(c for c in r["data"]["capabilities"] if c["id"] == "delegate.start_research")
        self.assertEqual(cap["state"], "disabled")
        self.assertEqual(cap["reasons"], [{"layer": "portal", "reason": "delegation_off"},
                                          {"layer": "plugin", "reason": "not_offered"}])
        self.assertIn("portal: delegation_off", r["message"])
        self.assertIn("plugin: not_offered", r["message"])

    def test_old_plugin_has_unknown_offer_not_an_assumed_available_tool(self):
        r = self.call("minimoi_describe_domain", {"domain": "guild"}, offered_tools=None)
        cap = next(c for c in r["data"]["capabilities"] if c["id"] == "guest.list")
        self.assertEqual((cap["state"], cap["reasons"]),
                         ("unknown", [{"layer": "plugin", "reason": "offer_unverified"}]))

    def test_plugin_offer_cannot_grant_an_excluded_policy_tool(self):
        from test_policy_postgres import drop
        from core.mc_tools import catalogue
        self.install_policy(change=drop("minimoi_keep_preference"))
        r = self.call("minimoi_describe_domain", {"domain": "guild"}, offered_tools=list(catalogue.TOOLS))
        cap = next(c for c in r["data"]["capabilities"] if c["id"] == "lessons.keep_preference")
        self.assertEqual(cap["state"], "disabled")
        denied = self.call("minimoi_keep_preference", {"quote": PHRASE, "lasting": "standing"})
        self.assertEqual(denied["code"], "tool_not_in_policy")

    # ── A09b: the deferred Wörter save is owned, evidenced and tracked, never claimed ──
    def test_a09b_the_gap_is_recorded_once_with_source_evidence_and_no_authority(self):
        r = self.gap()
        self.assertEqual((r["status"], r["code"], r["data"]["reference"]), ("done", "recorded", "MC-0001"))
        [card] = self.registers.projection("mc", entry_id="MC-0001")["entries"]
        self.assertEqual((card["kind"], card["status"], card["title"]),
                         ("task", "open", "Gap: Save a phrase to Wörter (german/phrases.create)"))
        self.assertIn(PHRASE, card["body"])
        self.assertIn("state not_implemented, planned for D1; evidence domains/german/manifest.json", card["body"])
        self.assertIn("This record grants nothing", card["body"])
        self.assertIn(f"note:{CONV}/{NOTE}", card["references"])
        self.assertIn(("work.record_gap", "verified"), [x[:2] for x in self.receipts()])
        again = self.gap()                                                     # the same request retried
        self.assertEqual((again["status"], again["data"]["reference"]), ("done", "MC-0001"))
        self.next_turn("note-2")
        self.owner_notes[(CONV, "note-2")] = "And this one too: „Na klar.“"
        other = self.gap("And this one too: „Na klar.“")
        self.assertEqual((other["code"], other["data"]["reference"]), ("already_tracked", "MC-0001"))
        self.assertEqual(len(self.registers.projection("mc")["entries"]), 1)

    def test_a09b_no_gap_for_what_works_and_none_from_words_he_did_not_write(self):
        self.assertEqual(self.gap(domain="guild", capability="guest.list")["code"], "capability_available")
        self.assertEqual(self.gap(capability="phrases.fly")["code"], "capability_unknown")
        self.assertEqual(self.gap(quote="Save everything automatically from now on.")["code"], "quote_not_owner_words")
        self.assertEqual(self.registers.projection("mc")["entries"], [])

    def test_a09b_withdrawing_is_the_undo_and_respects_the_revision(self):
        r = self.gap()
        self.next_turn("note-2")
        self.owner_notes[(CONV, "note-2")] = "Drop that follow-up."
        stale = self.call("minimoi_withdraw_gap", {"reference": "MC-0001", "revision": r["data"]["revision"] + 1})
        self.assertEqual((stale["status"], stale["code"]), ("refused", "register_stale_revision"))
        w = self.call("minimoi_withdraw_gap", r["data"]["undo"])
        self.assertEqual((w["status"], w["code"]), ("done", "withdrawn"))
        [card] = self.registers.projection("mc", entry_id="MC-0001")["entries"]
        self.assertEqual(card["status"], "retired")


if __name__ == "__main__":
    unittest.main()
