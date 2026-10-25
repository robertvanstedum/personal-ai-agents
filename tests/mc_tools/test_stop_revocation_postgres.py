"""The owner's Stop revokes the turn's tool admission before the runtime is asked to abort (spec v1.2 §7.1, §7.4;
Codex M1 review P1-1, whose reproduction is the first test here). Real disposable PostgreSQL, the real StreamRun.stop
and the real binding. Run with tests/mc_tools/run_postgres.sh <pgbin>."""
import threading
import time
import unittest
from types import SimpleNamespace

from test_tool_path_postgres import Base, DSN, OWNER, session_key  # noqa: F401
from core.mc_tools import binding
from minimoi_portal.guild_ui.mc.streaming import StreamContext, StreamRun


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class StopRevocationTests(Base, unittest.TestCase):
    def revoke(self, ledger_id=None):
        with self.connect() as conn:
            return binding.revoke(conn, ledger_id=self.ledger_id if ledger_id is None else ledger_id, claim_generation=1)

    def run_for(self, runtime_stop):
        ctx = StreamContext(turn_id="review-stop", principal=OWNER,
                            backend=SimpleNamespace(kind="openclaw", stop=runtime_stop), events=iter(()), floor=None,
                            reply_key="review-reply", context={}, release=lambda: None, on_stop=self.revoke,
                            on_end=lambda *args: self.finish_turn(self.ledger_id))
        return StreamRun(ctx, record_usage=lambda **kw: None), ctx

    def ledger_state(self):
        return self.one("SELECT state FROM guild.mc_turn_ledger WHERE id=%s", (self.ledger_id,))[0]

    def test_a_call_arriving_while_the_runtime_aborts_is_refused(self):
        observed = []

        def runtime_stop(turn_id):           # a still-in-flight tool call lands while the abort is being handled
            observed.append(self.call("minimoi_guest_requests"))
            return True
        run, ctx = self.run_for(runtime_stop)
        self.assertTrue(run.stop())
        self.assertTrue(ctx.cancel.is_set())
        self.assertEqual((observed[0]["status"], observed[0]["code"]), ("refused", "tool_turn_stopped"))
        self.assertEqual(self.ledger_state(), "claimed")         # the turn still holds its place until it really ends

    def test_a_runtime_that_never_acknowledges_still_gets_no_new_tool_call(self):
        for runtime_stop in (lambda turn_id: False, lambda turn_id: (_ for _ in ()).throw(TimeoutError("relay"))):
            with self.subTest(runtime_stop=runtime_stop):
                self.setUp()
                run, _ = self.run_for(runtime_stop)
                self.assertFalse(run.stop())
                for name in ("minimoi_guest_requests", "minimoi_issue_comment"):
                    args = {"number": 69, "summary": "Text proposed after Robert pressed Stop.", "refs": []} \
                        if name == "minimoi_issue_comment" else None
                    self.assertEqual(self.call(name, args)["code"], "tool_turn_stopped")
                self.assertEqual(self.one("SELECT count(*) FROM guild.mc_tool_proposals")[0], 0)

    def test_revocation_waits_for_an_admission_already_in_progress_and_then_holds(self):
        admitted, release, revoked = threading.Event(), threading.Event(), threading.Event()

        def in_flight():
            with self.connect() as conn:
                binding.resolve(conn, session_key=session_key(), agent_id="mc-chat", allowed_agents={"mc-chat"},
                                principal=OWNER, environment="dev", run_id=self.run, tool_call_id="call_inflight",
                                instance_id=self.instance)
                admitted.set()
                release.wait(5)               # its work, then commit
        worker = threading.Thread(target=in_flight)
        worker.start()
        self.assertTrue(admitted.wait(5))
        stopper = threading.Thread(target=lambda: (self.revoke(), revoked.set()))
        stopper.start()
        time.sleep(0.3)
        self.assertFalse(revoked.is_set())                         # Stop waits for the admitted call to finish
        release.set()
        worker.join(5)
        stopper.join(5)
        self.assertTrue(revoked.is_set())
        self.assertEqual(self.call("minimoi_guest_requests")["code"], "tool_turn_stopped")

    def test_revocation_is_idempotent_and_a_turn_without_tools_has_nothing_to_revoke(self):
        self.assertTrue(self.revoke())
        first = self.one("SELECT revoked_at FROM guild.mc_tool_turns WHERE ledger_id=%s", (self.ledger_id,))[0]
        self.assertTrue(self.revoke())
        self.assertEqual(self.one("SELECT revoked_at FROM guild.mc_tool_turns WHERE ledger_id=%s", (self.ledger_id,))[0],
                         first)
        self.finish_turn(self.ledger_id)
        other = self.claim("note-no-tools")
        self.assertFalse(self.revoke(other))

    # Codex's two further edges (CODEX_RESPONSE_P1_1_2026-10-22.md), adopted on the current code.
    def test_stop_before_the_binding_exists_is_not_undone_by_a_late_dispatch(self):
        from test_tool_path_postgres import NOTE
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM guild.mc_tool_turns WHERE ledger_id=%s", (self.ledger_id,))
        observed = []

        def revoke():
            with self.connect() as conn:
                observed.append({"recorded_on_binding": binding.revoke(conn, ledger_id=self.ledger_id,
                                                                       claim_generation=1)})

        def delayed_dispatch(turn_id):                     # the worker's on_dispatch runs only after the Stop
            self.bind(self.ledger_id, NOTE)
            observed.append(self.call("minimoi_guest_requests"))
            return False
        self.run_for_with(revoke, delayed_dispatch).stop()
        self.assertEqual(observed[0], {"recorded_on_binding": False})
        self.assertEqual((observed[-1]["status"], observed[-1]["code"]), ("refused", "tool_turn_stopped"))
        self.assertIsNotNone(self.one("SELECT revoked_at FROM guild.mc_tool_turns WHERE ledger_id=%s",
                                      (self.ledger_id,))[0])           # the late binding starts revoked

    def test_a_failed_durable_revocation_still_fences_admission_and_is_reported(self):
        from minimoi_portal.guild_ui import mc_tools_wiring as w
        from types import SimpleNamespace as NS
        from unittest import mock
        turn = NS(claim=NS(ledger_id=self.ledger_id, generation=1))
        backend = mock.MagicMock(kind="openclaw", chat_model="openclaw/mc-chat")
        services = NS(mc=backend)

        def broken():
            raise ConnectionError("synthetic revocation persistence failure")
        on = {"MINIMOI_MC_TOOLS": "1", "MINIMOI_MC_TOOLS_ENV": "dev", "MINIMOI_MC_TOOLS_PRINCIPAL": OWNER,
              "MINIMOI_MC_TURN_LEDGER": "1"}
        with mock.patch.dict("os.environ", {"DATABASE_URL": "postgresql://unused"}), \
                mock.patch.object(w, "_connect", return_value=broken):
            tools = w.binder(services, turn, environ=on, principal=OWNER, conversation_id="c", note_id="n",
                             session_conversation_id="guild:robert:c")
        observed = []

        def runtime_stop(turn_id):
            observed.append(self.call("minimoi_guest_requests"))
            return False
        run = self.run_for_with(tools.revoke, runtime_stop)
        run.stop()
        self.assertEqual(run.tool_admission, "fenced")                    # honest: durable record failed
        self.assertEqual((observed[0]["status"], observed[0]["code"]), ("refused", "tool_turn_stopped"))
        self.assertIsNone(self.one("SELECT revoked_at FROM guild.mc_tool_turns WHERE ledger_id=%s",
                                   (self.ledger_id,))[0])                # nothing durable, yet nothing admitted

    def run_for_with(self, on_stop, runtime_stop):
        ctx = StreamContext(turn_id="review-stop-edge", principal=OWNER,
                            backend=SimpleNamespace(kind="openclaw", stop=runtime_stop), events=iter(()), floor=None,
                            reply_key="review-reply", context={}, release=lambda: None, on_stop=on_stop,
                            on_end=lambda *args: self.finish_turn(self.ledger_id))
        return StreamRun(ctx, record_usage=lambda **kw: None)

    def test_the_next_turn_is_not_affected(self):
        self.run_for(lambda turn_id: True)[0].stop()
        self.next_turn("note-after-stop")
        self.assertEqual(self.call("minimoi_guest_requests")["status"], "done")


if __name__ == "__main__":
    unittest.main()
