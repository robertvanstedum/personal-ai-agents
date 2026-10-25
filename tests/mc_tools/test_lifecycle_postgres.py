"""Codex's lifecycle edges on 29a5ae85 (29a5ae85-review/REVIEW.md R1, R2), adopted on real PostgreSQL. Spec v1.2 §7.1,
§7.4. Run with tests/mc_tools/run_postgres.sh <pgbin>."""
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from test_tool_path_postgres import (Base, CONV, DSN, EPOCH, INSTANCE, NOTE, OWNER, new_run, session_key,  # noqa: F401
                                     session_user)
from core.mc_tools import binding
from minimoi_portal.guild_ui import mc_tools_wiring as wiring

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class LifecycleEdges(Base, unittest.TestCase):
    def test_r1_a_dispatch_racing_a_runtime_replacement_never_leaves_the_old_turn_usable(self):
        """Codex's exact interleaving: the dispatch's record is uncommitted while the replacement is announced."""
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM guild.mc_tool_turns WHERE ledger_id=%s", (self.ledger_id,))
        dispatch = self.connect()
        replaced = threading.Event()

        def replace():
            with self.connect() as conn:                              # waits for the dispatch's share lock
                binding.register_instance(conn, environment="dev", instance_id="b" * 32)
            replaced.set()
        try:
            self.assertTrue(binding.record(dispatch, ledger_id=self.ledger_id, claim_generation=1, principal=OWNER,
                                           conversation_id=CONV, note_request_id=NOTE, session_user=session_user(),
                                           runtime_agent="mc-chat", environment="dev", portal_epoch=EPOCH))
            t = threading.Thread(target=replace)
            t.start()
            time.sleep(0.3)
            self.assertFalse(replaced.is_set())                          # serialized behind the uncommitted dispatch
            dispatch.commit()
        finally:
            dispatch.close()
        t.join(10)
        self.assertTrue(replaced.is_set())
        self.run = new_run()
        self.assertFalse(self.attach(self.ledger_id, self.run))          # its instance is no longer current
        r = self.call("minimoi_guest_requests")
        self.assertEqual(self.one("SELECT instance_id FROM guild.mc_tool_runtime WHERE environment='dev'")[0], "b" * 32)
        self.assertEqual(r["status"], "refused")
        self.assertEqual(self.one("SELECT revoked_reason FROM guild.mc_tool_turns WHERE ledger_id=%s",
                                  (self.ledger_id,))[0], "runtime_replaced")

    def test_r1_an_admission_checks_the_current_instance_not_only_its_own_row(self):
        with self.connect() as conn, conn.cursor() as cur:               # simulate a stale row the update missed
            cur.execute("UPDATE guild.mc_tool_runtime SET instance_id=%s WHERE environment='dev'", ("c" * 32,))
        self.assertEqual(self.call("minimoi_guest_requests")["code"], "tool_runtime_replaced")

    def test_r2_a_failed_durable_stop_is_not_forgotten_by_a_restarted_portal(self):
        def broken():
            raise ConnectionError("synthetic Stop persistence failure")
        cfg = {"MINIMOI_MC_TOOLS": "1", "MINIMOI_MC_TOOLS_ENV": "dev", "MINIMOI_MC_TOOLS_PRINCIPAL": OWNER,
               "MINIMOI_MC_TURN_LEDGER": "1"}
        backend = mock.MagicMock(kind="openclaw", chat_model="openclaw/mc-chat")
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://unused"}), \
                mock.patch.object(wiring, "_connect", return_value=broken):
            tools = wiring.binder(NS(mc=backend), NS(claim=NS(ledger_id=self.ledger_id, generation=1)), environ=cfg,
                                  principal=OWNER, conversation_id=CONV, note_id=NOTE,
                                  session_conversation_id="guild:robert:" + CONV)
        self.assertEqual(tools.revoke(), "fenced")
        self.assertEqual(self.call("minimoi_guest_requests")["code"], "tool_turn_stopped")   # this process: fenced
        # A fresh portal process: its listener starts only after recording its own epoch, then serves the late call.
        args = {"session_key": session_key(), "agent_id": "mc-chat", "allowed_agents": ["mc-chat"], "principal": OWNER,
                "environment": "dev", "run_id": self.run, "tool_call_id": "late_after_restart", "instance_id": INSTANCE}
        code = """import os, json, sys, uuid, psycopg2
from core.mc_tools import binding
with psycopg2.connect(os.environ['MC_BINDING_TEST_DSN']) as conn:
    binding.start_portal_epoch(conn, environment='dev', epoch=str(uuid.uuid4()))
with psycopg2.connect(os.environ['MC_BINDING_TEST_DSN']) as conn:
    try:
        binding.resolve(conn, **json.loads(sys.argv[1])); print('admitted')
    except binding.NotBound as e:
        print('refused:' + str(e))
"""
        out = subprocess.run([sys.executable, "-c", code, json.dumps(args)], cwd=ROOT, text=True, capture_output=True,
                             timeout=20, env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "refused:tool_portal_restarted")
        self.assertEqual(self.one("SELECT revoked_reason FROM guild.mc_tool_turns WHERE ledger_id=%s",
                                  (self.ledger_id,))[0], "portal_restarted")

    def test_r2_a_listener_does_not_start_when_its_epoch_cannot_be_recorded(self):
        def broken():
            raise ConnectionError("database unavailable at startup")
        app = mock.MagicMock()
        app.extensions = {"bp": {"feature_environ": {"MINIMOI_MC_TOOLS": "1", "MINIMOI_MC_TOOLS_ENV": "dev",
                                                     "MINIMOI_MC_TOOLS_PRINCIPAL": OWNER, "MINIMOI_MC_TURN_LEDGER": "1",
                                                     "MINIMOI_MC_TOOLS_TOKEN_SHA256": "a" * 64}}}
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://unused"}), \
                mock.patch.object(wiring, "_connect", return_value=broken), \
                mock.patch.object(wiring, "build_service", return_value=self.service), \
                mock.patch("services.mc_tools.listener.serve") as serve:
            self.assertIsNone(wiring.attach(app, "bp", NS(mc=None)))
        serve.assert_not_called()

    def test_r2_turns_of_the_current_epoch_survive_its_own_start(self):
        with self.connect() as conn:
            binding.start_portal_epoch(conn, environment="dev", epoch=EPOCH)      # the same process, re-recorded
        self.assertEqual(self.call("minimoi_guest_requests")["status"], "done")


if __name__ == "__main__":
    unittest.main()
