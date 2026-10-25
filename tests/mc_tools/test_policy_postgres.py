"""Policy v0 in the admission pipeline on real PostgreSQL (spec v1.2 §8.1–8.3, §7.4): no policy, no tool; only what the
policy enables; per-turn quotas; a policy change revokes live admission and old offers; grants carry the version.
Run with tests/mc_tools/run_postgres.sh <pgbin>."""
import json
import unittest
from pathlib import Path

from test_tool_path_postgres import Base, DSN, OWNER  # noqa: F401
from core.mc_tools import policy as policy_mod

COMMENT = {"number": 69, "summary": "Workaround noted for the record here.", "refs": ["guild:1"]}


def drop(tool):
    def change(doc):
        doc["rules"] = [r for r in doc["rules"] if policy_mod.HANDLERS[r["handler"]].tool != tool]
    return change


def quota(tool, n):
    def change(doc):
        next(r for r in doc["rules"] if policy_mod.HANDLERS[r["handler"]].tool == tool)["max_operations_per_turn"] = n
    return change


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class PolicyTests(Base, unittest.TestCase):
    def version(self):
        return self.policy.current()[0].version

    def test_no_policy_means_no_tool_and_says_why(self):
        self.service.policy = None
        r = self.call("minimoi_guest_requests")
        self.assertEqual((r["status"], r["code"], r["data"]["problem"]), ("refused", "policy_unavailable",
                                                                         "policy_not_configured"))
        Path(self.tmp.name, "authority.json").write_text("{}")
        self.service.policy = self.policy
        r = self.call("minimoi_guest_requests")
        self.assertEqual((r["code"], r["data"]["problem"]), ("policy_unavailable", "policy_authority_invalid"))
        self.assertEqual(self.receipts(), [])

    def test_only_what_the_policy_enables(self):
        self.install_policy(change=drop("minimoi_github_issues"))
        self.assertEqual(self.call("minimoi_github_issues", {"number": 69})["code"], "tool_not_in_policy")
        self.assertEqual(self.call("minimoi_guest_requests")["status"], "done")

    def test_the_per_turn_quota_counts_admitted_calls_and_a_retried_call_id_once(self):
        self.install_policy(change=quota("minimoi_guest_requests", 2))
        self.assertEqual(self.call("minimoi_guest_requests", call_id="c1")["status"], "done")
        self.assertEqual(self.call("minimoi_guest_requests", call_id="c1")["status"], "done")      # the same call again
        self.assertEqual(self.call("minimoi_guest_requests", call_id="c2")["status"], "done")
        self.assertEqual(self.call("minimoi_guest_requests", call_id="c3")["code"], "tool_quota_exceeded")
        self.assertEqual(self.call("minimoi_host_health", observation={"host": "mac", "disks": [{"mount": "/", "total_gb": 1, "free_gb": 1}], "memory": {"total_gb": 1, "free_gb": 1}, "containers": []})["status"], "done")  # per tool
        self.next_turn("note-2")
        self.assertEqual(self.call("minimoi_guest_requests", call_id="c4")["status"], "done")      # per turn
        self.assertEqual(self.one("SELECT count(*) FROM guild.mc_tool_admissions")[0], 4)       # refused: not admitted

    def test_a_policy_change_revokes_the_live_turn_and_closes_old_offers(self):
        r = self.call("minimoi_issue_comment", COMMENT)
        code, old = r["data"]["code"], self.version()
        self.install_policy(revision="test-2")
        self.assertNotEqual(self.version(), old)
        r = self.call("minimoi_guest_requests")                                    # activates the new version
        self.assertEqual((r["status"], r["code"]), ("refused", "tool_policy_changed"))
        self.assertEqual(self.one("SELECT state, closed_reason FROM guild.mc_tool_proposals WHERE code=%s", (code,)),
                         ("stale", "policy_changed"))
        self.next_turn("note-2")
        self.assertEqual(self.call("minimoi_guest_requests")["status"], "done")    # the next turn, new policy

    def test_an_approval_under_a_changed_policy_is_refused_before_activation_too(self):
        code = self.call("minimoi_issue_comment", COMMENT)["data"]["code"]
        self.install_policy(revision="test-2")                                     # no tool call has activated it yet
        posted = []
        p = self.props(posted)
        done = self.say(p, f"yes {code}", "note-yes")
        self.assertEqual((done["operation"]["state"], done["operation"]["code"]), ("stale", "policy_changed"))
        self.assertIn("propose it again", done["reply"])
        self.assertEqual(posted, [])

    def test_no_approval_without_a_usable_policy(self):
        code = self.call("minimoi_issue_comment", COMMENT)["data"]["code"]
        Path(self.tmp.name, "authority.json").write_text("{}")
        posted = []
        done = self.say(self.props(posted), f"yes {code}", "note-yes")
        self.assertEqual(done["operation"]["code"], "policy_unavailable")
        self.assertEqual(posted, [])

    def test_contexts_and_grants_carry_the_policy_version(self):
        code = self.call("minimoi_issue_comment", COMMENT)["data"]["code"]
        posted = []
        self.assertEqual(self.say(self.props(posted), f"yes {code}", "note-yes")["operation"]["state"], "verified")
        versions = {row[0] for row in self.all("SELECT definition->>'policy_version' FROM guild.mc_action_grants")}
        self.assertEqual(versions, {self.version()})
        self.assertEqual(self.one("SELECT policy_version FROM guild.mc_tool_proposals WHERE code=%s", (code,))[0],
                         self.version())

    def test_the_first_policy_activation_revokes_nothing(self):
        self.assertEqual(self.call("minimoi_guest_requests")["status"], "done")
        self.assertIsNone(self.one("SELECT revoked_at FROM guild.mc_tool_turns WHERE ledger_id=%s", (self.ledger_id,))[0])

    def test_startup_activates_new_policy_before_first_owner_tool_turn(self):
        from unittest import mock
        from types import SimpleNamespace
        import os
        from minimoi_portal.guild_ui import mc_tools_wiring as w
        from test_tool_path_postgres import EPOCH
        self.assertEqual(self.call("minimoi_guest_requests")["status"], "done")
        self.install_policy(revision="startup-new")
        app = mock.MagicMock()
        app.extensions = {"bp": {"feature_environ": {
            "MINIMOI_MC_TOOLS": "1", "MINIMOI_MC_TOOLS_ENV": "dev",
            "MINIMOI_MC_TOOLS_PRINCIPAL": OWNER, "MINIMOI_MC_TURN_LEDGER": "1",
            "MINIMOI_MC_TOOLS_TOKEN_SHA256": "a" * 64}}}
        expected = self.version()
        def listening(*args, **kwargs):
            self.assertEqual(self.one("SELECT policy_version FROM guild.mc_tool_policy WHERE environment='dev'")[0], expected)
            return "synthetic-listener"
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://unused"}), \
                mock.patch.object(w, "_connect", return_value=self.connect), \
                mock.patch.object(w, "build_service", return_value=self.service), \
                mock.patch.object(w, "PORTAL_EPOCH", EPOCH), \
                mock.patch("services.mc_tools.listener.serve", side_effect=listening):
            self.assertEqual(w.attach(app, "bp", SimpleNamespace(mc=None)), "synthetic-listener")
        self.assertEqual(self.one("SELECT revoked_reason FROM guild.mc_tool_turns WHERE ledger_id=%s", (self.ledger_id,))[0], "policy_changed")
        self.next_turn("first-after-startup")
        self.assertEqual(self.call("minimoi_guest_requests")["status"], "done")
        self.assertIsNone(self.one("SELECT revoked_at FROM guild.mc_tool_turns WHERE ledger_id=%s", (self.ledger_id,))[0])

    # helpers
    def props(self, posted):
        from core.mc_tools.proposals import ToolProposals
        return ToolProposals(self.connect, policy=self.policy, principal=OWNER, environment="dev",
                             comment_issue=lambda n, b: posted.append(n) or ("created", 1))

    def all(self, sql, params=()):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


if __name__ == "__main__":
    unittest.main()
