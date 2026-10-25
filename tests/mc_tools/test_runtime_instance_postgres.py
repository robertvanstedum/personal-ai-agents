"""Each turn is bound to the exact runtime instance (gateway process) and agent it was dispatched to (spec v1.2 §7.1
steps 2, 4, 5; §7.4; Codex M1 review P1-2). Real disposable PostgreSQL. Run with tests/mc_tools/run_postgres.sh."""
import unittest

from test_tool_path_postgres import Base, DSN, INSTANCE, OWNER, new_run, session_key  # noqa: F401
from core.mc_tools import binding

RESTARTED = "b" * 32


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class RuntimeInstanceTests(Base, unittest.TestCase):
    def announce(self, instance):
        return self.service.register_instance(instance)

    def test_a_restart_revokes_the_live_turn_and_old_process_callbacks_fail_while_the_lease_is_valid(self):
        self.assertEqual(self.call("minimoi_guest_requests")["status"], "done")
        self.assertTrue(self.announce(RESTARTED))
        lease_live = self.one("SELECT lease_until > clock_timestamp() FROM guild.mc_turn_ledger WHERE id=%s",
                              (self.ledger_id,))[0]
        self.assertTrue(lease_live)                                           # the owner's turn is still claimed
        self.assertEqual(self.call("minimoi_guest_requests")["code"], "tool_runtime_replaced")       # old process
        self.assertEqual(self.call("minimoi_guest_requests", instance=RESTARTED)["code"],
                         "tool_runtime_replaced")                             # the new process is not this turn's
        self.assertIsNotNone(self.one("SELECT revoked_at FROM guild.mc_tool_turns WHERE ledger_id=%s",
                                      (self.ledger_id,))[0])

    def test_after_a_restart_the_next_turn_binds_to_the_new_process(self):
        self.announce(RESTARTED)
        self.instance = RESTARTED
        self.next_turn("note-after-restart")
        self.assertEqual(self.one("SELECT runtime_instance FROM guild.mc_tool_turns WHERE ledger_id=%s",
                                  (self.ledger_id,))[0], RESTARTED)
        self.assertEqual(self.call("minimoi_guest_requests")["status"], "done")
        self.assertEqual(self.call("minimoi_guest_requests", instance=INSTANCE)["code"], "tool_runtime_replaced")

    def test_the_same_process_announcing_again_changes_nothing(self):
        self.assertFalse(self.announce(INSTANCE))
        self.assertEqual(self.call("minimoi_guest_requests")["status"], "done")

    def test_the_agent_is_the_one_the_turn_was_sent_to_not_any_allowed_agent(self):
        self.service.allowed_agents = frozenset({"mc-chat", "mc-agent"})
        r = self.call("minimoi_guest_requests", key=session_key(agent="mc-agent"), agent="mc-agent")
        self.assertEqual((r["status"], r["code"]), ("refused", "tool_session_invalid"))
        self.assertEqual(self.call("minimoi_guest_requests")["status"], "done")

    def test_a_call_without_its_process_identity_is_refused(self):
        for instance in (None, "", "A" * 32, "a" * 31, "x" * 32):
            self.assertEqual(self.call("minimoi_guest_requests", instance=instance)["code"], "tool_run_missing", instance)

    def test_no_announced_process_means_no_run_is_attached_and_nothing_is_admitted(self):
        self.finish_turn(self.ledger_id)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM guild.mc_tool_runtime")
        ledger_id = self.claim("note-2")
        self.bind(ledger_id, "note-2", attach=False)
        run = new_run()
        self.assertFalse(self.attach(ledger_id, run))
        self.ledger_id = ledger_id
        self.assertEqual(self.call("minimoi_guest_requests", run=run)["code"], "tool_run_not_bound")

    def test_a_turn_stopped_before_its_run_event_admits_nothing_and_says_stopped(self):
        self.finish_turn(self.ledger_id)
        ledger_id = self.claim("note-2")
        self.bind(ledger_id, "note-2", attach=False)
        with self.connect() as conn:
            binding.revoke(conn, ledger_id=ledger_id, claim_generation=1)
        run = new_run()
        self.assertTrue(self.attach(ledger_id, run))
        self.ledger_id = ledger_id
        self.assertEqual(self.call("minimoi_guest_requests", run=run)["code"], "tool_turn_stopped")

    def test_a_replayed_announcement_from_a_replaced_process_never_makes_it_current_again(self):
        self.assertTrue(self.announce(RESTARTED))
        with self.assertRaisesRegex(binding.ActionContractError, "tool_instance_retired"):
            self.announce(INSTANCE)                                          # the old process, late or replayed
        self.assertEqual(self.one("SELECT instance_id FROM guild.mc_tool_runtime WHERE environment='dev'")[0], RESTARTED)
        self.assertFalse(self.announce(RESTARTED))                           # the current one again: nothing changes
        self.assertTrue(self.announce("c" * 32))                             # a genuinely new process still can
        with self.assertRaisesRegex(binding.ActionContractError, "tool_instance_retired"):
            self.announce(RESTARTED)

    def test_the_instance_is_the_one_current_at_dispatch_not_at_the_first_call(self):
        self.finish_turn(self.ledger_id)
        ledger_id = self.claim("note-2")
        self.bind(ledger_id, "note-2", attach=False)                         # dispatched under INSTANCE
        self.assertTrue(self.announce(RESTARTED))                            # the gateway restarts before the run event
        run = new_run()
        self.assertFalse(self.attach(ledger_id, run))                        # its instance is no longer current
        self.ledger_id, self.instance = ledger_id, RESTARTED
        self.assertEqual(self.one("SELECT runtime_instance, revoked_reason FROM guild.mc_tool_turns WHERE ledger_id=%s",
                                  (ledger_id,)), (INSTANCE, "runtime_replaced"))
        self.assertEqual(self.call("minimoi_guest_requests", run=run)["status"], "refused")

    def test_a_turn_dispatched_before_any_announcement_is_never_admitted(self):
        self.finish_turn(self.ledger_id)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM guild.mc_tool_runtime")
        ledger_id = self.claim("note-2")
        self.bind(ledger_id, "note-2", attach=False)
        self.assertTrue(self.announce(RESTARTED))                            # announced only afterwards
        self.assertFalse(self.attach(ledger_id, new_run()))

    def test_announcements_are_strict(self):
        for bad in ("", "a" * 31, "A" * 32, None, 7):
            with self.assertRaises(binding.ActionContractError):
                self.announce(bad)


if __name__ == "__main__":
    unittest.main()
