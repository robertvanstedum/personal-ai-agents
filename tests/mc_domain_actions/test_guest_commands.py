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

    def test_natural_owner_wording_inside_a_longer_message(self):
        robert = ("Guten Morgen.  I have a guest request but can't accept or reject it.  Can you do if for me?  "
                  "Just reject the guest requests we have.")
        self.assertEqual(gc.parse(robert), gc.Command("reject", None))
        for t in ("Reject the guest request", "Please reject all the guest requests.", "Could you decline the pending guest request?",
                  "Morning!\nReject the open guest requests now"):
            self.assertEqual(gc.parse(t), gc.Command("reject", None), t)
        self.assertEqual(gc.parse("Hi. Can you reject the guest request from Bruno Costa please."),
                         gc.Command("reject", "Bruno Costa"))

    def test_roberts_real_phrasing_on_dev_22_oct(self):
        for t in ("Guten Morgen.  Can you reject guest requests we currently have",
                  "Hey MC, please reject all the pending guest requests.",
                  "Could you decline the guest requests that are still open?",
                  "Reject the outstanding guest requests for me", "Go ahead and reject these guest requests."):
            self.assertEqual(gc.parse(t), gc.Command("reject", None), t)
        for t in ("Do you want me to reject guest requests?", "I don't want you to reject guest requests.",
                  "Don't reject the guest requests yet.", "Why did you reject the guest request?"):
            self.assertIsNone(gc.parse(t), t)

    def test_not_commands(self):
        for t in ('He said "Reject the guest request from Bruno"', "> Reject the guest request from Bruno",
                  "Should I reject the guest request from Bruno?", "I can't accept or reject it.",
                  "Confirm that Bruno asked for access",
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

    def test_without_a_name_several_open_requests_are_listed_and_nothing_is_proposed(self):
        r = self.say("Just reject the guest requests we have.", "t1")
        self.assertEqual(r["operation"]["state"], "clarify")
        self.assertIn("Bruno Costa", r["reply"])
        self.assertFalse(self.cmds.has_pending("conv-1"))

    def test_without_a_name_the_only_open_request_is_proposed_then_confirmed(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.guest_requests SET status='granted' WHERE email LIKE 'ana%'")
        r = self.say("Guten Morgen. Just reject the guest requests we have.", "t1")
        self.assertEqual(r["operation"]["state"], "proposed")
        self.assertEqual(self.say("Confirm", "t2")["operation"]["state"], "verified")
        self.assertEqual(self.status("bruno@example.test"), "rejected")
        self.assertEqual(self.say("Reject the guest requests", "t3")["operation"]["state"], "no_match")

    def test_after_a_confirmed_reject_a_later_confirm_is_not_claimed_except_as_a_retry(self):
        self.say("Reject the guest request from Bruno", "t1")
        self.say("Confirm", "t2")
        self.assertFalse(self.cmds.has_pending("conv-1", "t3"))        # e.g. confirming a lesson next
        self.assertTrue(self.cmds.has_pending("conv-1", "t2"))         # the same turn retried

    def test_a_retried_confirm_never_approves_a_newer_proposal(self):          # review P1-1
        self.say("Reject the guest request from Bruno", "t1")
        self.say("Confirm", "t2")
        self.say("Reject the guest request from ana@example.test", "t3")
        again = self.say("Confirm", "t2")                                     # the lost response is retried
        self.assertTrue(again["operation"].get("repeated"))
        self.assertEqual(self.status("ana@example.test"), "requested")
        self.assertTrue(self.cmds.has_pending("conv-1", "t4"))                 # Ana still waits for her own Confirm

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
