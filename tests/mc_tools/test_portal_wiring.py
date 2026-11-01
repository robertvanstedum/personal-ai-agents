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

    def test_off_reason_names_the_first_missing_switch_and_no_value(self):
        self.assertIsNone(w.off_reason(ON))
        self.assertEqual(w.off_reason({}), "MINIMOI_MC_TOOLS is not 1")
        self.assertEqual(w.off_reason(dict(ON, MINIMOI_MC_TOOLS="0")), "MINIMOI_MC_TOOLS is not 1")
        self.assertEqual(w.off_reason(dict(ON, MINIMOI_MC_TOOLS_ENV="staging")), "MINIMOI_MC_TOOLS_ENV is not dev or prod")
        self.assertEqual(w.off_reason(dict(ON, MINIMOI_MC_TURN_LEDGER="0")), "MINIMOI_MC_TURN_LEDGER is not 1")
        for missing in ("MINIMOI_MC_TOOLS", "MINIMOI_MC_TOOLS_ENV", "MINIMOI_MC_TOOLS_PRINCIPAL", "MINIMOI_MC_TURN_LEDGER"):
            self.assertIsNotNone(w.off_reason({k: v for k, v in ON.items() if k != missing}), missing)

    def test_mc_chat_on_with_tools_off_warns_at_start(self):
        def attach(environ):
            app = SimpleNamespace(extensions={"guild": {"feature_environ": environ}})
            with mock.patch.object(w._log, "warning") as warn:
                self.assertIsNone(w.attach(app, "guild", self.services))
            return warn
        warn = attach({"MINIMOI_GUILD_MC": "openclaw", "MINIMOI_MC_TOOLS": "0", "MINIMOI_MC_TOOLS_ENV": "prod"})
        warn.assert_called_once()
        self.assertIn("MINIMOI_MC_TOOLS is not 1", warn.call_args.args)
        attach({"MINIMOI_GUILD_MC": "cos"}).assert_not_called()       # MC chat off: nothing to warn about
        attach({}).assert_not_called()

    def test_standard_changes_parse_strictly(self):
        self.assertEqual(w._standard_changes("guest_reject=SC-3@371, access_invite=SC-4@372"),
                         {"guest_reject": {"id": "SC-3", "ticket": 371}, "access_invite": {"id": "SC-4", "ticket": 372}})
        self.assertEqual(w._standard_changes("delete_everything=SC-1@1,guest_reject=SC 3@371,guest_reject=SC-3@0"), {})

    def test_binding_recorded_with_the_runtime_user_the_turn_is_sent_with(self):
        recorded = []
        conn = mock.MagicMock()
        with mock.patch.object(w, "_connect", return_value=lambda: conn), \
                mock.patch("core.mc_tools.binding.record", side_effect=lambda c, **kw: recorded.append(kw) or True):
            self.binder()()
        self.assertEqual(recorded, [{"ledger_id": 7, "claim_generation": 1, "principal": "robert", "conversation_id": "c1",
                                     "note_request_id": "n1", "session_user": self.backend._user("guild:robert:c1"),
                                     "runtime_agent": "mc-agent",                # the agent this backend sends to
                                     "environment": "dev", "portal_epoch": w.PORTAL_EPOCH}])
        conn.close.assert_called_once()

    def test_no_binding_when_off_or_not_ours(self):
        with mock.patch("core.mc_tools.binding.record") as record:
            for bind in (self.binder(environ={}), self.binder(principal="someone"), self.binder(turn=None),
                         self.binder(services=SimpleNamespace(mc=SimpleNamespace(kind="cos")))):
                bind()
        record.assert_not_called()

    def test_the_streamed_run_is_attached_to_this_claim(self):
        attached = []
        conn = mock.MagicMock()
        with mock.patch.object(w, "_connect", return_value=lambda: conn), \
                mock.patch("core.mc_tools.binding.attach_run", side_effect=lambda c, **kw: attached.append(kw) or True):
            self.binder().attach_run("chatcmpl_12345678-1234-4234-8234-123456789abc")
        self.assertEqual(attached, [{"ledger_id": 7, "claim_generation": 1,
                                     "run_id": "chatcmpl_12345678-1234-4234-8234-123456789abc", "environment": "dev"}])
        conn.close.assert_called_once()

    def test_a_run_not_attached_is_logged_and_tools_stay_refused(self):
        conn = mock.MagicMock()
        with mock.patch.object(w, "_connect", return_value=lambda: conn), \
                mock.patch("core.mc_tools.binding.attach_run", return_value=False), \
                mock.patch.object(w._log, "warning") as warn:
            self.binder().attach_run("chatcmpl_12345678-1234-4234-8234-123456789abc")
        warn.assert_called_once()

    def test_no_run_attached_when_off_or_not_ours(self):
        with mock.patch("core.mc_tools.binding.attach_run") as attach:
            for binding in (self.binder(environ={}), self.binder(principal="someone"), self.binder(turn=None)):
                binding.attach_run("chatcmpl_12345678-1234-4234-8234-123456789abc")
        attach.assert_not_called()
        self.assertIs(self.binder(environ={}), w.NO_BINDING)

    def test_the_reader_returns_only_the_platforms_own_note_in_the_owners_conversation(self):
        notes = {"k1": {"who": "guild_platform", "text": "card"}, "k2": {"who": "master_craftsman", "text": "card"}}
        floor = SimpleNamespace(get_note=notes.get)
        read = w.proposal_reader(self.services, "robert")
        with mock.patch.object(w, "_conversation_floor", return_value=floor):
            self.assertEqual(read("c1", "k1")["text"], "card")
            self.assertIsNone(read("c1", "k2"))                     # not the platform's note: never an offer's card
            self.assertIsNone(read("c1", "missing"))
        with mock.patch.object(w, "_conversation_floor", return_value=None):
            self.assertIsNone(read("c-not-his", "k1"))

    def test_the_reader_raises_when_the_store_cannot_answer(self):
        def down(key):
            raise OSError("floor store unavailable")
        with mock.patch.object(w, "_conversation_floor", return_value=SimpleNamespace(get_note=down)):
            with self.assertRaises(OSError):
                w.proposal_reader(self.services, "robert")("c1", "k1")

    def test_stop_fences_then_revokes_this_claim_and_reports_a_failed_record(self):
        revoked = []
        conn = mock.MagicMock()
        with mock.patch.object(w, "_connect", return_value=lambda: conn), \
                mock.patch("core.mc_tools.binding.revoke", side_effect=lambda c, **kw: revoked.append(kw) or True):
            self.binder().revoke()
        self.assertEqual(revoked, [{"ledger_id": 7, "claim_generation": 1}])
        conn.close.assert_called_once()

        def broken():
            raise OSError("db down")
        from core.mc_tools import fence
        with mock.patch.object(w, "_connect", return_value=broken):
            self.assertEqual(self.binder().revoke(), "fenced")      # durable record failed: fenced in process, said so
        self.assertTrue(fence.fenced(7, 1))
        self.assertEqual(self.binder(environ={}).revoke(), "none")

    def test_a_handoff_is_delivered_into_its_own_conversation_under_a_fixed_note_key(self):
        from core.mc_tools import handoffs
        store = mock.MagicMock()
        store.create.side_effect = [({"id": "c-new"}, False), ({"id": "c-new"}, True)]
        store.rename.return_value = {"id": "c-new"}
        floor = mock.MagicMock()
        floor.add_note.return_value = SimpleNamespace(outcome="kept")
        cfg = {"principal": "robert", "environment": "dev", "dsn": "postgresql://unused"}
        captured = {}

        def fake_receive(connect, body, *, principal, environment, deliver, read_back, link_for):
            captured["first"] = deliver("tid-1", "From Chief of Staff: hello", "text")
            captured["again"] = deliver("tid-1", "From Chief of Staff: hello", "text")
            return {"ok": True}
        with mock.patch("minimoi_portal.guild_ui.conversations.conversations_of", return_value=store), \
                mock.patch.object(w, "_conversation_floor", return_value=floor), \
                mock.patch.object(handoffs, "receive", fake_receive):
            w.handoff_receiver(self.services, cfg)({})
        self.assertEqual(captured["first"], ("c-new", "mc-handoff-tid-1"))
        store.create.assert_called_with("robert", key="mc-handoff-tid-1")
        store.rename.assert_called_once_with("c-new", "robert", "From Chief of Staff: hello")   # once, not on the retry
        floor.add_note.return_value = SimpleNamespace(outcome="idempotency_mismatch")
        with mock.patch("minimoi_portal.guild_ui.conversations.conversations_of", return_value=store), \
                mock.patch.object(w, "_conversation_floor", return_value=floor):
            store.create.side_effect = [({"id": "c-new"}, True)]
            def receive_again(connect, body, *, principal, environment, deliver, read_back, link_for):
                return deliver("tid-1", "t", "changed text")
            with mock.patch.object(handoffs, "receive", receive_again), self.assertRaises(handoffs.HandoffUnknown):
                w.handoff_receiver(self.services, cfg)({})

    def test_a_binding_failure_never_stops_the_turn(self):
        def broken():
            raise OSError("db down")
        with mock.patch.object(w, "_connect", return_value=broken):
            self.binder()()                                         # logs, returns; tools stay refused
            self.binder().attach_run("chatcmpl_12345678-1234-4234-8234-123456789abc")


class IssueFilerTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.token = os.path.join(self.dir.name, "issues.token")
        with open(self.token, "w") as f:
            f.write("ghp_" + "x" * 36 + "\n")
        os.chmod(self.token, 0o600)
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://unused"}):
            self.cfg = w.settings(dict(ON, MINIMOI_MC_TOOLS_TICKETS="1", MINIMOI_MC_ISSUES_TOKEN_FILE=self.token,
                                       MINIMOI_GITHUB_REPO="robertvanstedum/personal-ai-agents"))

    def filer(self, status, body=None, raises=False):
        sent = []

        def post(url, **kw):
            sent.append((url, kw))
            if raises:
                raise TimeoutError()
            return SimpleNamespace(status_code=status, json=lambda: body or {})
        return w.issue_filer(self.cfg, http_post=post), sent

    def test_created_refused_unknown(self):
        file_issue, sent = self.filer(201, {"number": 123})
        self.assertEqual(file_issue("t", "b"), ("created", 123))
        url, kw = sent[0]
        self.assertEqual(url, "https://api.github.com/repos/robertvanstedum/personal-ai-agents/issues")
        self.assertEqual(kw["json"], {"title": "t", "body": "b"})
        self.assertFalse(kw["allow_redirects"])
        self.assertEqual(self.filer(422)[0]("t", "b"), ("refused", None))
        self.assertEqual(self.filer(500)[0]("t", "b"), ("unknown", None))
        self.assertEqual(self.filer(201, {"number": "123"})[0]("t", "b"), ("unknown", None))
        self.assertEqual(self.filer(0, raises=True)[0]("t", "b"), ("unknown", None))

    def test_comment_goes_to_that_issue_only(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://unused"}):
            cfg = dict(self.cfg, comments=True)
        sent = []
        comment = w.issue_commenter(cfg, http_post=lambda url, **kw: sent.append((url, kw)) or
                                    SimpleNamespace(status_code=201, json=lambda: {"id": 77}))
        self.assertEqual(comment(69, "text"), ("created", 77))
        self.assertEqual(sent[0][0], "https://api.github.com/repos/robertvanstedum/personal-ai-agents/issues/69/comments")
        self.assertEqual(comment(0, "text"), ("refused", None))
        self.assertIsNone(w.issue_commenter(self.cfg))                    # comments not switched on

    def test_new_issue_carries_its_labels(self):
        file_issue, sent = self.filer(201, {"number": 5})
        self.assertEqual(file_issue("t", "b", ["ui"]), ("created", 5))
        self.assertEqual(sent[0][1]["json"], {"title": "t", "body": "b", "labels": ["ui"]})

    def test_update_patches_that_issue_with_only_known_fields(self):
        cfg = dict(self.cfg, comments=True)
        sent = []
        update = w.issue_updater(cfg, http_patch=lambda url, **kw: sent.append((url, kw)) or
                                 SimpleNamespace(status_code=200, json=lambda: {"number": 388}))
        self.assertEqual(update(388, {"state": "closed", "labels": ["ui"]}), ("created", 388))
        url, kw = sent[0]
        self.assertEqual(url, "https://api.github.com/repos/robertvanstedum/personal-ai-agents/issues/388")
        self.assertEqual(kw["json"], {"state": "closed", "labels": ["ui"]})
        self.assertEqual(update(388, {"assignees": ["x"]}), ("refused", None))      # nothing beyond the four fields
        self.assertEqual(update(388, {}), ("refused", None))
        self.assertEqual(len(sent), 1)
        self.assertIsNone(w.issue_updater(self.cfg))                     # issue changes not switched on

    def test_off_without_a_private_token_file(self):
        os.chmod(self.token, 0o644)
        self.assertIsNone(w.issue_filer(self.cfg))
        os.chmod(self.token, 0o600)
        link = self.token + ".link"
        os.symlink(self.token, link)
        self.assertIsNone(w.issue_filer(dict(self.cfg, issues_token_file=link)))
        self.assertIsNone(w.issue_filer(dict(self.cfg, tickets=False)))


if __name__ == "__main__":
    unittest.main()


class StartupPolicyTests(unittest.TestCase):
    def test_invalid_policy_keeps_listener_off_without_changing_database(self):
        app = mock.MagicMock()
        app.extensions = {"bp": {"feature_environ": ON}}
        service = SimpleNamespace(policy=SimpleNamespace(current=lambda: (None, "policy_unapproved")))
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://unused"}), \
                mock.patch.object(w, "build_service", return_value=service), \
                mock.patch.object(w, "_connect") as connect, \
                mock.patch("services.mc_tools.listener.serve") as serve:
            self.assertIsNone(w.attach(app, "bp", SimpleNamespace(mc=None)))
        connect.assert_not_called()
        serve.assert_not_called()

    def test_the_front_door_reaches_the_tool_service_only_when_it_is_attached(self):
        for asks, wired in ((None, False), (object(), True)):
            app = mock.MagicMock()
            app.extensions = {"bp": {"feature_environ": ON, "agent_asks": asks}}
            service = SimpleNamespace(policy=SimpleNamespace(current=lambda: (None, "policy_unapproved")))
            with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://unused"}), \
                    mock.patch.object(w, "build_service", return_value=service) as build:
                w.attach(app, "bp", SimpleNamespace(mc=None, store=None, floor=None))
            self.assertEqual(build.call_args.kwargs["agent_ask"] is not None, wired)


class AgentAskerTests(unittest.TestCase):
    def test_asks_through_the_front_door_with_the_owner_note_and_returns_its_words(self):
        import tempfile
        from minimoi_portal.guild_ui.agent_ask import AskService
        self.assertIsNone(w.agent_asker(None))
        ask = w.agent_asker(AskService(tempfile.mkdtemp()))
        made, words = ask("c-0123456789abcdef", "Robert's words.", "n-1", ("grok",))
        self.assertEqual([(m["to"], m["ok"]) for m in made], [("grok", True)])
        self.assertTrue(words.startswith("Asked Grok:"))
