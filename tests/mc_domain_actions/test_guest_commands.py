"""Owner guest commands in the MC conversation (journey link 4), grammar and full flows."""
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "minimoi_actions"))
from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

from core.mc_domain_actions import guest_commands as gc  # noqa: E402
from test_guest_actions_postgres import GUESTS_DDL  # noqa: E402


class GrammarTests(unittest.TestCase):
    def test_owner_commands(self):
        self.assertEqual(gc.parse("Reject the guest request from Bruno"), gc.Command("reject", "Bruno"))
        self.assertEqual(gc.parse("please decline the access request from ana@example.test."),
                         gc.Command("reject", "ana@example.test"))
        for t in ("Confirm", "yes, confirm", "Approve it.", "Confirm the rejection"):
            self.assertEqual(gc.parse(t), gc.Command("confirm"), t)
        for t in ("Cancel", "No, cancel", "Don't", "do not"):
            self.assertEqual(gc.parse(t), gc.Command("cancel"), t)

    def test_not_commands(self):
        for t in ('He said "Reject the guest request from Bruno"', "> Reject the guest request from Bruno",
                  "Should I reject the guest request from Bruno?", "Reject the guest request",
                  "Confirm that Bruno asked for access", "Reject the guest request from Bruno\nand Ana",
                  "Can you tell me about guest requests?", "", None, "x" * 300):
            self.assertIsNone(gc.parse(t), t)


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class FlowTests(PostgresFixture, unittest.TestCase):
    migrations = ("core/minimoi_actions/sql/004_action_receipts.sql",
                  "core/minimoi_actions/sql/005_action_grants.sql",
                  "core/mc_domain_actions/sql/001_guest_action_evidence.sql",
                  "core/mc_domain_actions/sql/002_guest_proposals.sql")

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE guild.mc_guest_proposals, guild.mc_guest_action_evidence,"
                        "guild.mc_action_grant_consumptions, guild.mc_action_grants, guild.mc_action_events,"
                        "guild.mc_action_receipts CASCADE")
            cur.execute(GUESTS_DDL)
            cur.execute("INSERT INTO guild.guest_requests (name,email) VALUES "
                        "('Ana Souza','ana@example.test'),('Ana Lima','ana.lima@example.test'),"
                        "('Bruno Costa','bruno@example.test')")
        self.cmds = gc.GuestCommands(self.connect, principal="owner", environment="dev")

    def say(self, text, turn, conversation="conv-1"):
        return self.cmds.handle(gc.parse(text), conversation=conversation, turn_key=turn)

    def status(self, email):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM guild.guest_requests WHERE email=%s", (email,))
            return cur.fetchone()[0]

    def test_reject_confirm_is_done_and_verified(self):
        r1 = self.say("Reject the guest request from Bruno", "t1")
        self.assertEqual(r1["operation"]["state"], "proposed")
        self.assertIn("Reply Confirm", r1["reply"])
        self.assertEqual(self.status("bruno@example.test"), "requested")        # nothing before confirmation
        r2 = self.say("Confirm", "t2")
        self.assertEqual((r2["operation"]["state"], r2["operation"]["outcome"]), ("verified", "rejected"))
        self.assertIn("verified by read-back", r2["reply"])
        self.assertEqual(self.status("bruno@example.test"), "rejected")

    def test_ambiguous_name_asks_which_and_proposes_nothing(self):
        r = self.say("Reject the guest request from Ana", "t1")
        self.assertEqual(r["operation"]["state"], "clarify")
        self.assertIn("ana.lima@example.test", r["reply"])
        self.assertEqual(self.say("Confirm", "t2")["operation"]["state"], "nothing_pending")
        r = self.say("Reject the guest request from ana.lima@example.test", "t3")
        self.assertEqual(r["operation"]["state"], "proposed")
        self.assertEqual(self.say("Confirm", "t4")["operation"]["outcome"], "rejected")
        self.assertEqual(self.status("ana@example.test"), "requested")

    def test_no_match_says_so(self):
        r = self.say("Reject the guest request from Zed", "t1")
        self.assertEqual(r["operation"]["state"], "no_match")
        self.assertIn("3 open request", r["reply"])

    def test_cancel_drops_the_proposal(self):
        self.say("Reject the guest request from Bruno", "t1")
        self.assertEqual(self.say("Cancel", "t2")["operation"]["state"], "cancelled")
        self.assertEqual(self.say("Confirm", "t3")["operation"]["state"], "nothing_pending")
        self.assertEqual(self.status("bruno@example.test"), "requested")

    def test_confirmation_only_counts_in_the_same_conversation(self):
        self.say("Reject the guest request from Bruno", "t1", conversation="conv-1")
        self.assertEqual(self.say("Confirm", "t2", conversation="conv-2")["operation"]["state"], "nothing_pending")
        self.assertEqual(self.status("bruno@example.test"), "requested")

    def test_expired_proposal_does_nothing(self):
        self.say("Reject the guest request from Bruno", "t1")
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_guest_proposals SET expires_at=clock_timestamp()-interval '1 minute'")
        self.assertEqual(self.say("Confirm", "t2")["operation"]["state"], "expired")
        self.assertEqual(self.status("bruno@example.test"), "requested")

    def test_request_changed_before_confirmation_does_nothing(self):
        self.say("Reject the guest request from Bruno", "t1")
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.guest_requests SET status='granted' WHERE email='bruno@example.test'")
        r = self.say("Confirm", "t2")
        self.assertEqual(r["operation"]["state"], "stale")
        self.assertEqual(self.status("bruno@example.test"), "granted")

    def test_repeated_confirm_turn_never_acts_twice(self):
        self.say("Reject the guest request from Bruno", "t1")
        first = self.say("Confirm", "t2")
        again = self.say("Confirm", "t2")            # the same turn retried (network retry)
        self.assertEqual(first["operation"]["outcome"], "rejected")
        self.assertTrue(again["operation"].get("repeated"))
        later = self.say("Confirm", "t3")            # a new confirmation with nothing pending
        self.assertEqual(later["operation"]["state"], "nothing_pending")
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM guild.mc_guest_action_evidence")
            self.assertEqual(cur.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
