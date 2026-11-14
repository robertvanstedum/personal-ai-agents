"""TEMPORARY review reproductions on real PostgreSQL (not to be committed)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "minimoi_actions"))
from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402
from core.mc_domain_actions import guest_commands as gc  # noqa: E402
from core.mc_delegation_dispatcher.store import DispatcherStore  # noqa: E402
from core.mc_delegation_dispatcher.relay import DelegationRelay  # noqa: E402
from test_guest_actions_postgres import GUESTS_DDL  # noqa: E402


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class GuestRepro(PostgresFixture, unittest.TestCase):
    migrations = ("core/minimoi_actions/sql/004_action_receipts.sql",
                  "core/minimoi_actions/sql/005_action_grants.sql",
                  "core/mc_domain_actions/sql/001_guest_action_evidence.sql",
                  "core/mc_domain_actions/sql/002_guest_proposals.sql",
                  "core/mc_delegation_dispatcher/sql/001_delegations.sql")

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE guild.mc_guest_proposals, guild.mc_guest_action_evidence,"
                        "guild.mc_action_grant_consumptions, guild.mc_action_grants, guild.mc_action_events,"
                        "guild.mc_action_receipts, guild.mc_delegations CASCADE")
            cur.execute(GUESTS_DDL)
            cur.execute("INSERT INTO guild.guest_requests (name,email) VALUES "
                        "('Ana Souza','ana@example.test'),('Bruno Costa','bruno@example.test')")
        self.cmds = gc.GuestCommands(self.connect, principal="owner", environment="dev")

    def say(self, text, turn, conversation="conv-1"):
        return self.cmds.handle(gc.parse(text), conversation=conversation, turn_key=turn)

    def status(self, email):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM guild.guest_requests WHERE email=%s", (email,))
            return cur.fetchone()[0]

    def test_R1_retried_confirm_turn_confirms_a_newer_proposal(self):
        self.say("Reject the guest request from Bruno", "t1")
        r2 = self.say("Confirm", "t2")
        print("\nR1 first confirm:", r2["operation"]["state"], r2["operation"].get("outcome"))
        self.say("Reject the guest request from Ana", "t3")          # new proposal; owner has NOT confirmed it
        retry = self.say("Confirm", "t2")                             # the t2 turn retried (same turn key)
        print("R1 retried t2 ->", retry["operation"], "| ana:", self.status("ana@example.test"))
        self.assertEqual(self.status("ana@example.test"), "rejected")

    def test_R2_confirmed_guest_keeps_claiming_confirm_for_30_minutes(self):
        self.say("Reject the guest request from Bruno", "t1")
        self.say("Confirm", "t2")
        print("\nR2 has_pending after done:", self.cmds.has_pending("conv-1"))
        r = self.say("Confirm", "t9")      # meant for a lesson proposal in the same conversation
        print("R2 later Confirm ->", r["reply"])
        self.assertTrue(self.cmds.has_pending("conv-1"))
        self.assertEqual(r["operation"]["state"], "nothing_pending")

    def test_R3_lapsed_lease_is_never_reclaimed_and_stays_running(self):
        store = DispatcherStore(self.connect, principal="robert", environment="dev")
        relay = DelegationRelay(store, ref_for=lambda r: "a" * 40)
        relay.start({"job_id": "j-1", "agent": "claude-code", "task": "research x", "deadline_s": 3600})
        caps = {"cost_enforced_actors": ["claude-code"], "scopes": ["research"], "repositories": ["personal-ai-agents"]}
        env = store.claim("mac-1", caps)
        store.progress(env["request_id"], env["generation"], "executing")
        with self.connect() as conn, conn.cursor() as cur:   # runner crashes; lease lapses
            cur.execute("UPDATE guild.mc_delegations SET lease_until=clock_timestamp() - interval '1 hour'")
        again = store.claim("mac-1", caps)
        stop = store.stop("j-1")
        print("\nR3 reclaim:", again, "| relay:", relay.status("j-1")[1]["state"], "| stop:", stop,
              "| cancel_requested seen by old gen:", store.cancel_requested(env["request_id"], env["generation"]))
        self.assertIsNone(again)
        self.assertEqual(stop, "requested")


if __name__ == "__main__":
    unittest.main()
