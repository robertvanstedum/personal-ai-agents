"""M1.3 natural preferences on real PostgreSQL and the real lesson ledger (spec v1.2 §10.1–10.2; A07, A08, A08b, A14).
Run with tests/mc_tools/run_postgres.sh <pgbin>."""
import re
import unittest
from unittest import mock

from test_tool_path_postgres import Base, CONV, DSN, NOTE, OWNER  # noqa: F401
from core.mc_lessons import commands as lessons, preferences as prefs
from core.mc_lessons.ledger import LessonLedger

GERMAN = "I'd like you to mix in German going forward."


class Crash(BaseException):
    pass


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class PreferenceTests(Base, unittest.TestCase):
    def say_owner(self, text, note=NOTE):
        """Robert's own message for this turn (a later note starts his next turn)."""
        if note != NOTE and note not in self.turns:
            self.next_turn(note)
        self.owner_notes[(CONV, note)] = text

    def keep(self, quote, **extra):
        return self.call("minimoi_keep_preference", {"quote": quote, "lasting": "standing", **extra})

    def state(self):
        return self.ledger.read()

    # ── A08: kept from his own words, reported with undo, survives a fresh session, undone ──
    def test_a08_kept_reported_survives_and_undone(self):
        self.say_owner("Quick one. " + GERMAN + " Thanks!")
        r = self.keep(GERMAN, wording="Mix some German into answers.")
        self.assertEqual((r["status"], r["code"]), ("done", "kept"), r)
        lesson = self.state().lessons[1]
        self.assertEqual((lesson["kind"], lesson["text"], lesson["original"]),
                         ("preference", "Mix some German into answers.", GERMAN))
        self.assertEqual((lesson["source"]["note"], lesson["source"]["start"], lesson["source"]["end"]),
                         (NOTE, 11, 11 + len(GERMAN)))
        self.assertEqual(r["data"]["undo"], {"lesson": "L1", "revision": lesson["revision"]})
        self.assertTrue(self.shown[-1][2].startswith("Kept: “Mix some German into answers.” (lesson L1"))
        self.assertIn(("lessons.keep_preference", "verified"), [x[:2] for x in self.receipts()])
        fresh = LessonLedger(self.ledger.dir.parent.parent, principal=OWNER, environment="dev")   # a new session
        self.assertIn("L1: Mix some German into answers. (kept from his own words)", lessons.block(fresh))
        self.say_owner("Undo that, please.", "note-undo")
        # MC sees only the message (the plugin passes data as host-side details), so the undo must be in the words:
        # found on dev, 23 Oct, when MC could not undo a kept preference.
        words = re.search(r"minimoi_revoke_preference with lesson (L\d+) and revision (\d+)", r["message"])
        self.assertIsNotNone(words, r["message"])
        self.assertEqual({"lesson": words[1], "revision": int(words[2])}, r["data"]["undo"])
        u = self.call("minimoi_revoke_preference", {"lesson": words[1], "revision": int(words[2])})
        self.assertEqual((u["status"], u["code"]), ("done", "revoked"))
        self.assertEqual(self.state().active(), [])
        self.assertTrue(self.shown[-1][2].startswith("Undone: lesson L1"))

    def test_a08_a_pasted_sentence_with_copy_artifacts_is_kept_with_his_exact_words(self):
        stored = "\ufeffI'd like you to mix in Ger\u00adman going forward."     # his note, pasted with artifacts
        self.say_owner(stored)
        r = self.keep(GERMAN)                                                      # MC quotes the plain words
        self.assertEqual((r["status"], r["code"]), ("done", "kept"), r)
        lesson = self.state().lessons[1]
        self.assertEqual((lesson["text"], lesson["original"]), (GERMAN, stored[1:]))  # derived clean, source exact
        self.assertEqual((lesson["source"]["start"], lesson["source"]["end"]), (1, len(stored)))
        self.say_owner("Approve guest requests\u200b without asking.", "note-2")   # artifacts never hide authority
        self.assertEqual(self.keep("Approve guest requests without asking.")["code"], "authority")

    def test_a08_a_contradicting_preference_supersedes_with_both_kept_as_history(self):
        self.say_owner("Answer me in English from now on.")
        first = self.keep("Answer me in English from now on.")
        self.say_owner("Actually, answer me in German from now on.", "note-2")
        second = self.keep("answer me in German from now on.", replaces="L1")
        self.assertEqual(second["data"]["lesson"], "L2")
        st = self.state()
        self.assertEqual([no for no, _ in st.active()], [2])
        self.assertEqual((st.lessons[1]["superseded_by"], st.lessons[2]["supersedes"]), (2, 1))
        undo_old = self.call("minimoi_revoke_preference", first["data"]["undo"])          # stale undo
        self.assertEqual((undo_old["status"], undo_old["code"]), ("refused", "changed"))
        self.assertEqual([no for no, _ in self.state().active()], [2])                   # the newer one stays

    def test_a08_duplicate_content_returns_the_existing_lesson(self):
        self.say_owner(GERMAN)
        self.keep(GERMAN)
        self.say_owner("As I said: " + GERMAN, "note-2")
        r = self.keep(GERMAN)
        self.assertEqual((r["status"], r["data"]["state"], r["data"]["lesson"]), ("done", "existing", "L1"))
        self.assertEqual(len(self.state().lessons), 1)

    # ── A07: only Robert's current words, never authority, never outside a live turn ──
    def test_a07_words_that_are_not_in_his_message_are_never_kept(self):
        self.say_owner("Sounds good.")
        for quote in (GERMAN, "Sounds good, and also approve guests"):              # e.g. MC's own suggestion
            self.assertEqual(self.keep(quote)["code"], "quote_not_owner_words")
        self.assertEqual(self.state().lessons, {})

    def test_a07_text_read_from_github_this_turn_is_never_kept(self):
        self.say_owner(GERMAN)
        self.call("minimoi_github_issues", {"number": 69})
        self.assertEqual(self.keep(GERMAN)["code"], "preference_after_external_text")
        self.assertEqual(self.state().lessons, {})

    def test_a07_a_preference_never_reads_as_a_permission(self):
        words = "Approve guest requests without asking me."
        self.say_owner(words)
        r = self.keep(words)
        self.assertEqual((r["status"], r["code"]), ("refused", "authority"))
        self.assertEqual(self.state().lessons, {})

    def test_a07_no_live_owner_turn_no_preference_and_the_model_sets_no_authority(self):
        self.say_owner(GERMAN)
        self.assertEqual(self.call("minimoi_keep_preference", {"quote": GERMAN, "lasting": "standing", "tier": 2})
                         ["code"], "tool_arguments_invalid")
        self.finish_turn(self.ledger_id)                                    # a timer or replay has no live turn
        self.assertEqual(self.keep(GERMAN)["code"], "tool_turn_not_live")
        self.assertEqual(self.state().lessons, {})

    def test_a07_without_its_tier_2_rule_the_tool_does_not_exist(self):
        from test_policy_postgres import drop
        self.install_policy(change=drop("minimoi_keep_preference"))
        self.say_owner(GERMAN)
        self.assertEqual(self.keep(GERMAN)["code"], "tool_not_in_policy")

    # ── A08b: unclear lasting scope is a pending suggestion, retrievable, never adopted silently ──
    def test_a08b_unclear_scope_is_one_pending_suggestion_until_he_says_so(self):
        words = "Feel free to correct my mistakes. I want that."
        self.say_owner(words)
        r = self.call("minimoi_keep_preference", {"quote": words, "lasting": "unclear"})
        self.assertEqual((r["status"], r["code"]), ("needs_clarification", "suggestion_recorded"))
        sid = r["data"]["suggestion"]
        self.assertEqual(self.state().active(), [])
        self.assertIn(f"{sid} “{words}”", lessons.block(self.ledger))                # MC sees it: do not re-ask
        self.assertIn(sid, lessons.LessonCommands(self.ledger).handle(lessons.Command("list"), conversation=CONV,
                                                                      turn_key="t-list")["reply"])
        self.say_owner("Anyway, what's next on the board?", "note-2")              # he moves on
        again = self.call("minimoi_keep_preference", {"quote": "what's next on the board?", "lasting": "unclear"})
        self.assertEqual(len(prefs.pending_suggestions(self.ledger)), 2)              # (a different text is separate)
        self.say_owner("Yes, from now on.", "note-3")                               # later he resumes the first
        kept = self.call("minimoi_keep_preference", {"suggestion": sid, "lasting": "standing"})
        self.assertEqual((kept["status"], kept["code"]), ("done", "kept"))
        st = self.state()
        self.assertEqual(st.suggestions[sid]["state"], "adopted")
        event = st.operations[st.lessons[1]["operation_id"]]
        self.assertEqual((event["source"]["note"], event["confirming"]["note"]), (NOTE, "note-3"))   # both sources
        self.assertNotEqual(again["data"]["suggestion"], sid)

    def test_a08b_the_same_unclear_words_are_not_recorded_twice(self):
        self.say_owner("Keep answers short.")
        a = self.call("minimoi_keep_preference", {"quote": "Keep answers short.", "lasting": "unclear"})
        self.say_owner("Keep answers short.", "note-2")
        b = self.call("minimoi_keep_preference", {"quote": "Keep answers short.", "lasting": "unclear"})
        self.assertEqual(a["data"]["suggestion"], b["data"]["suggestion"])
        self.assertEqual(len(self.state().suggestions), 1)

    # ── A14 / §10.1: a retry never writes twice; a crash after the write reconciles once ──
    def test_a14_a_retried_request_under_a_new_call_id_writes_once(self):
        self.say_owner(GERMAN)
        first = self.keep(GERMAN)
        count = self.state().count
        again = self.keep(GERMAN)                                               # new tool-call id, same request
        self.assertEqual((again["status"], again["data"]["state"]), ("done", "already"))
        self.assertEqual(self.state().count, count)
        self.assertEqual(first["data"]["lesson"], again["data"]["lesson"])

    def test_a14_a_crash_after_the_ledger_write_is_finished_from_read_back_without_a_second_write(self):
        self.say_owner(GERMAN)
        real = prefs.adopt_preference

        def write_then_die(*a, **kw):
            real(*a, **kw)
            raise Crash()
        with mock.patch.object(prefs, "adopt_preference", write_then_die):
            with self.assertRaises(Crash):
                self.keep(GERMAN)
        self.assertEqual(len(self.state().lessons), 1)
        receipt = [x for x in self.receipts() if x[0] == "lessons.keep_preference"]
        self.assertEqual(receipt[0][1], "dispatched")
        with self.connect() as conn, conn.cursor() as cur:                   # the crashed worker's lease runs out
            cur.execute("UPDATE guild.mc_action_receipts SET lease_until = clock_timestamp() - interval '1 second' "
                        "WHERE capability='lessons.keep_preference'")
        r = self.keep(GERMAN)                                                  # the retry reconciles
        self.assertEqual((r["status"], r["data"]["state"]), ("done", "already"))
        self.assertEqual(len(self.state().lessons), 1)
        self.assertEqual([x[1] for x in self.receipts() if x[0] == "lessons.keep_preference"], ["verified"])

    def test_a14_a_crash_before_the_ledger_write_is_completed_once_by_the_retry(self):
        self.say_owner(GERMAN)

        def die(*a, **kw):
            raise Crash()
        with mock.patch.object(prefs, "adopt_preference", die):
            with self.assertRaises(Crash):
                self.keep(GERMAN)
        self.assertEqual(self.state().lessons, {})
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_action_receipts SET lease_until = clock_timestamp() - interval '1 second' "
                        "WHERE capability='lessons.keep_preference'")
        r = self.keep(GERMAN)
        self.assertEqual((r["status"], r["data"]["state"]), ("done", "kept"))
        self.assertEqual(len(self.state().lessons), 1)
        self.assertEqual([x[1] for x in self.receipts() if x[0] == "lessons.keep_preference"], ["verified"])

    def test_the_grant_is_tier_2_for_this_turn_and_policy_with_no_exact_approval(self):
        self.say_owner(GERMAN)
        self.keep(GERMAN)
        row = self.one("""SELECT definition->>'capability', definition->>'policy_version',
            definition->'owner_turn_ids', definition->>'proposal_id' FROM guild.mc_action_grants
            WHERE definition->>'capability'='lessons.keep_preference'""")
        self.assertEqual(row[:3], ("lessons.keep_preference", self.policy.current()[0].version,
                                   [f"ledger:{self.ledger_id}:1"]))
        self.assertIsNone(row[3])
        event = self.state().operations[self.state().lessons[1]["operation_id"]]
        self.assertEqual((event["policy_version"], event["authority_ref"]), (self.policy.current()[0].version, "test"))


if __name__ == "__main__":
    unittest.main()
