"""The portal records a turn's tool binding only when tools are on, for the configured owner, on OpenClaw."""
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from minimoi_portal.guild_ui import mc_tools_wiring as w
from minimoi_portal.guild_ui.mc.openclaw import OpenClawMasterCraftsman

ON = {"MINIMOI_MC_TOOLS": "1", "MINIMOI_MC_TOOLS_ENV": "dev", "MINIMOI_MC_TOOLS_PRINCIPAL": "robert",
      "MINIMOI_MC_TURN_LEDGER": "1", "MINIMOI_MC_TOOLS_TOKEN_SHA256": "a" * 64}


class WiringTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://unused"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.backend = OpenClawMasterCraftsman("http://relay:1/v1", "x" * 40, http_get=lambda *a, **k: None,
                                               http_post=lambda *a, **k: None)
        self.services = SimpleNamespace(mc=self.backend)
        self.turn = SimpleNamespace(claim=SimpleNamespace(ledger_id=7, generation=1))

    def binder(self, environ=ON, principal="robert", services=None, turn="default"):
        return w.binder(services or self.services, self.turn if turn == "default" else turn, environ=environ,
                        principal=principal, conversation_id="c1", note_id="n1",
                        session_conversation_id="guild:robert:c1")

    def test_settings_need_every_switch(self):
        self.assertEqual(w.settings(ON)["agents"], ("mc-chat",))
        for missing in ("MINIMOI_MC_TOOLS", "MINIMOI_MC_TOOLS_ENV", "MINIMOI_MC_TOOLS_PRINCIPAL", "MINIMOI_MC_TURN_LEDGER"):
            self.assertIsNone(w.settings({k: v for k, v in ON.items() if k != missing}), missing)
        self.assertIsNone(w.settings(dict(ON, MINIMOI_MC_TOOLS_ENV="staging")))

    def test_binding_recorded_with_the_runtime_user_the_turn_is_sent_with(self):
        recorded = []
        conn = mock.MagicMock()
        with mock.patch.object(w, "_connect", return_value=lambda: conn), \
                mock.patch("core.mc_tools.binding.record", side_effect=lambda c, **kw: recorded.append(kw) or True):
            self.binder()()
        self.assertEqual(recorded, [{"ledger_id": 7, "claim_generation": 1, "principal": "robert", "conversation_id": "c1",
                                     "note_request_id": "n1", "session_user": self.backend._user("guild:robert:c1")}])
        conn.close.assert_called_once()

    def test_no_binding_when_off_or_not_ours(self):
        with mock.patch("core.mc_tools.binding.record") as record:
            for bind in (self.binder(environ={}), self.binder(principal="someone"), self.binder(turn=None),
                         self.binder(services=SimpleNamespace(mc=SimpleNamespace(kind="cos")))):
                bind()
        record.assert_not_called()

    def test_a_binding_failure_never_stops_the_turn(self):
        def broken():
            raise OSError("db down")
        with mock.patch.object(w, "_connect", return_value=broken):
            self.binder()()                                         # logs, returns; tools stay refused


if __name__ == "__main__":
    unittest.main()
