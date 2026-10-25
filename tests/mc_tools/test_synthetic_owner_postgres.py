"""A second synthetic owner through the real listener, binding and lesson stores.

No network server or model is started. Both owners, including the existing fixture's
`robert`, are fictional records in disposable PostgreSQL and temporary directories.
This proves the storage/admission mechanics, not isolation of a deployed gateway.
"""
import hashlib
import secrets
import unittest
from pathlib import Path
from unittest import mock

from test_tool_path_postgres import Base, CONV, DSN, INSTANCE, NOTE, OWNER, session_user
from core.mc_lessons import commands as lessons, preferences
from core.mc_lessons.ledger import LessonLedger
from core.mc_tools.service import ToolService
from services.mc_tools.listener import create_app


class SimulatedCrash(BaseException):
    pass


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class SyntheticOwnerTests(Base, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # Seed the *other fictional owner's* ledger so an accidental write or undo
        # cannot hide behind an empty file. Never open any operator lesson path.
        words = "Use plain English in future answers."
        self.owner_notes[(CONV, NOTE)] = words
        self.assertEqual(self.call("minimoi_keep_preference", {"quote": words, "lasting": "standing"})["code"], "kept")
        self.other_before = self.other_files()
        self.other_run = self.run
        self.synthetic = "synthetic-regression-" + secrets.token_hex(6)
        self.synthetic_root = Path(self.tmp.name) / "synthetic-lessons"
        self.synthetic_ledger = LessonLedger(self.synthetic_root, principal=self.synthetic, environment="dev")
        self.synthetic_notes, self.synthetic_cards = {}, {}
        self.token = secrets.token_urlsafe(32)
        self.start_service()
        self.synthetic_turn("synthetic-1", "Mix German into future answers.")

    def other_files(self):
        return {p.name: p.read_bytes() for p in self.ledger.dir.iterdir() if p.is_file()}

    def tearDown(self):
        try:
            if hasattr(self, "other_before"):
                self.assertEqual(self.other_files(), self.other_before,
                                 "synthetic journey modified the other owner's lesson files")
        finally:
            super().tearDown()

    def start_service(self):
        # Explicit adapters only: no GitHub, delegation, invites, guest mutation,
        # or external executor. New service/ledger objects support restart tests.
        self.synthetic_ledger = LessonLedger(self.synthetic_root, principal=self.synthetic, environment="dev")
        self.synthetic_service = ToolService(
            self.connect, principal=self.synthetic, environment="dev", policy=self.policy,
            lessons=self.synthetic_ledger, show=self.show_synthetic, read_shown=self.read_synthetic,
            read_owner_note=lambda conv, note: self.synthetic_notes.get((conv, note)))
        self.client = create_app(self.synthetic_service,
                                 token_sha256=hashlib.sha256(self.token.encode()).hexdigest()).test_client()

    def show_synthetic(self, conv, key, text):
        self.synthetic_cards.setdefault((conv, key), {"text": text, "who": "guild_platform"})
        return self.synthetic_cards[(conv, key)]["text"] == text

    def read_synthetic(self, conv, key):
        return self.synthetic_cards.get((conv, key))

    def synthetic_turn(self, note, text):
        self.synthetic_notes[(CONV, note)] = {"text": text, "who": self.synthetic}
        self.synthetic_id = self.claim(note, principal=self.synthetic)
        self.assertTrue(self.bind(self.synthetic_id, note, principal=self.synthetic))
        self.synthetic_run = self.runs[self.synthetic_id]

    def post(self, name, args, *, principal=None, run=None, token=None):
        principal = self.synthetic if principal is None else principal
        headers = {"Authorization": "Bearer " + (self.token if token is None else token),
                   "X-MC-Agent": "mc-chat", "X-MC-Instance": INSTANCE,
                   "X-MC-Session": "agent:mc-chat:openai-user:" + session_user(CONV, principal),
                   "X-MC-Run": self.synthetic_run if run is None else run,
                   "X-MC-Tool-Call": "synthetic_" + secrets.token_hex(8)}
        return self.client.post("/internal/mc/v1/tools/" + name, json={"arguments": args}, headers=headers)

    def keep(self):
        response = self.post("minimoi_keep_preference", {
            "quote": "Mix German into future answers.", "lasting": "standing"})
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_keep_restart_and_undo_touch_only_the_synthetic_store(self):
        kept = self.keep()
        self.assertEqual((kept["status"], kept["code"]), ("done", "kept"))
        self.assertNotEqual(self.synthetic_ledger.dir, self.ledger.dir)
        self.start_service()
        self.assertIn("Mix German into future answers.", lessons.block(self.synthetic_ledger))
        self.synthetic_turn("synthetic-undo", "Undo that preference.")
        undone = self.post("minimoi_revoke_preference", kept["data"]["undo"]).get_json()
        self.assertEqual((undone["status"], undone["code"]), ("done", "revoked"))
        self.assertEqual(self.synthetic_ledger.read().active(), [])
        self.assertEqual(len(self.synthetic_cards), 2)

    def test_real_binding_for_the_other_owner_cannot_enter_the_synthetic_service(self):
        args = {"quote": "Use plain English in future answers.", "lasting": "standing"}
        # A valid live binding and valid listener token still cannot cross owners.
        denied = self.post("minimoi_keep_preference", args, principal=OWNER, run=self.other_run).get_json()
        self.assertEqual(denied["status"], "refused", denied)
        self.assertEqual(self.synthetic_ledger.read().count, 0)
        self.assertEqual(self.synthetic_cards, {})
        reverse = self.service.call("minimoi_keep_preference", args,
                                    session_key="agent:mc-chat:openai-user:" + session_user(CONV, self.synthetic),
                                    agent_id="mc-chat", run_id=self.synthetic_run,
                                    tool_call_id="cross_owner", instance_id=INSTANCE)
        self.assertEqual(reverse["status"], "refused", reverse)

    def test_wrong_executor_credential_is_refused_before_any_lesson_write(self):
        response = self.post("minimoi_keep_preference", {
            "quote": "Mix German into future answers.", "lasting": "standing"}, token="wrong" * 8)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.synthetic_ledger.read().count, 0)
        self.assertEqual(self.synthetic_cards, {})

    def test_crash_after_write_recovers_once_in_a_new_service(self):
        adopt = preferences.adopt_preference

        def write_then_crash(*args, **kwargs):
            adopt(*args, **kwargs)
            raise SimulatedCrash()

        with mock.patch.object(preferences, "adopt_preference", write_then_crash):
            with self.assertRaises(SimulatedCrash):
                self.keep()
        count = self.synthetic_ledger.read().count
        self.assertEqual(len(self.synthetic_ledger.read().lessons), 1)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_action_receipts SET lease_until = clock_timestamp() - interval '1 second' "
                        "WHERE principal_id=%s AND capability='lessons.keep_preference'", (self.synthetic,))
        self.start_service()
        recovered = self.keep()
        self.assertEqual((recovered["status"], recovered["data"]["state"]), ("done", "already"))
        self.assertEqual(recovered["data"]["display"], "recorded")
        self.assertEqual(self.synthetic_ledger.read().count, count)
        self.assertEqual(len(self.synthetic_cards), 1)

    def test_failed_display_is_honest_and_retry_repairs_only_the_notice(self):
        with mock.patch.object(self.synthetic_service, "show", return_value=False):
            kept = self.keep()
        self.assertEqual((kept["status"], kept["data"]["display"]), ("done", "unconfirmed"))
        self.assertIn("could not confirm its notice", kept["message"])
        self.assertNotIn("showed Robert", kept["message"])
        count = self.synthetic_ledger.read().count
        self.assertEqual(self.synthetic_cards, {})
        recovered = self.keep()
        self.assertEqual(recovered["data"]["display"], "recorded")
        with mock.patch.object(self.synthetic_service, "show", side_effect=AssertionError("must not repost")):
            self.assertEqual(self.keep()["data"]["display"], "recorded")
        self.assertEqual(self.synthetic_ledger.read().count, count)
        self.assertEqual(len(self.synthetic_cards), 1)

    def test_false_display_ack_and_read_failure_never_claim_delivery(self):
        with mock.patch.object(self.synthetic_service, "show", return_value=True):
            self.assertEqual(self.keep()["data"]["display"], "unconfirmed")
        with mock.patch.object(self.synthetic_service, "read_shown", side_effect=OSError("store unavailable")), \
                mock.patch.object(self.synthetic_service, "show") as show:
            self.assertEqual(self.keep()["data"]["display"], "unconfirmed")
            show.assert_not_called()
        self.assertEqual(len(self.synthetic_ledger.read().lessons), 1)
        self.assertEqual(self.keep()["data"]["display"], "recorded")

    def test_conflicting_note_is_not_overwritten_or_accepted_as_platform_evidence(self):
        self.keep()
        key = next(iter(self.synthetic_cards))
        for wrong_note in ({"who": "master_craftsman", "text": self.synthetic_cards[key]["text"]},
                           {"who": "guild_platform", "text": "different notice"}):
            self.synthetic_cards[key] = wrong_note
            with mock.patch.object(self.synthetic_service, "show") as show:
                self.assertEqual(self.keep()["data"]["display"], "unconfirmed")
                show.assert_not_called()

    def test_undo_display_recovers_without_a_second_revocation(self):
        kept = self.keep()
        self.synthetic_turn("synthetic-undo", "Undo that preference.")
        with mock.patch.object(self.synthetic_service, "show", return_value=False):
            undone = self.post("minimoi_revoke_preference", kept["data"]["undo"]).get_json()
        self.assertEqual((undone["status"], undone["data"]["display"]), ("done", "unconfirmed"))
        count = self.synthetic_ledger.read().count
        self.start_service()
        recovered = self.post("minimoi_revoke_preference", kept["data"]["undo"]).get_json()
        self.assertEqual((recovered["status"], recovered["data"]["display"]), ("done", "recorded"))
        self.assertEqual(self.synthetic_ledger.read().count, count)
        self.assertEqual(len(self.synthetic_cards), 2)

