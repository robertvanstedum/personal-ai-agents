"""M1.2a on real PostgreSQL (spec v1.2 §9.1–9.2): an offer is approvable only once its exact card is on the page and
read back (A04), at most one pending offer per conversation, reported and never replaced (A03), and the two clocks
(A02+, single offer; the batch cases come with the batch tool). Run with tests/mc_tools/run_postgres.sh <pgbin>."""
import threading
import unittest
from datetime import datetime, timedelta

from test_tool_path_postgres import Base, CONV, DSN, OWNER  # noqa: F401
from core.mc_tools import offers
from core.mc_tools.proposals import ToolProposals, parse


class Crash(BaseException):
    """The portal process dies here: nothing after this line runs, nothing catches it."""


COMMENT = {"number": 69, "summary": "Workaround noted for the record here.", "refs": ["guild:1"]}


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class OfferTests(Base, unittest.TestCase):
    def props(self, **kw):
        self.posted = getattr(self, "posted", [])
        kw.setdefault("comment_issue", lambda n, b: self.posted.append(n) or ("created", len(self.posted)))
        kw.setdefault("read_shown", self.read_shown)
        return ToolProposals(self.connect, policy=self.policy, principal=OWNER, environment="dev", **kw)

    def offer(self, args=COMMENT):
        return self.call("minimoi_issue_comment", args)

    def row(self, code=None):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""SELECT state, display_state, closed_reason, published_at, approved_at, execution_deadline,
                expires_at, display_note_ref FROM guild.mc_tool_proposals
                WHERE (%s::text IS NULL OR code=%s) ORDER BY created_at DESC LIMIT 1""", (code, code))
            return dict(zip(("state", "display", "reason", "published_at", "approved_at", "deadline", "expires_at",
                             "key"), cur.fetchone()))

    def db_now(self):
        return self.one("SELECT clock_timestamp()")[0]

    # ── A04: display crash windows, on the implemented admission and approval paths ──────────────────────────────

    def test_a04_crash_after_commit_before_the_note_leaves_an_offer_nobody_can_approve(self):
        def crash(*a):
            raise Crash()
        self.service.show = crash
        with self.assertRaises(Crash):
            self.offer()
        code = self.one("SELECT code FROM guild.mc_tool_proposals")[0]
        self.assertEqual(self.row()["display"], "prepared")
        self.assertEqual(self.page, {})
        for text, turn in (("yes", "note-yes"), (f"yes {code}", "note-named"), (f"ja {code}", "note-ja")):
            r = self.say(self.props(), text, turn)
            self.assertEqual(r["operation"]["state"], "not_shown", text)
        self.assertEqual(self.posted, [])
        self.assertEqual(self.receipts()[-1][1], "verified")                 # only the proposal's own receipt
        # It never reaches the page later, and once it is clearly not mid-publication it frees the conversation.
        self.next_turn("note-later")
        self.assertEqual(self.offer()["code"], "offer_pending")              # still within the grace: not cut off
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_tool_proposals SET created_at = created_at - interval '2 minutes'")
        self.service.show = self.show
        self.next_turn("note-again")
        self.assertEqual(self.offer()["status"], "needs_confirmation")
        self.assertEqual(self.one("SELECT closed_reason FROM guild.mc_tool_proposals WHERE code=%s", (code,))[0],
                         "display_failed")

    def test_a04_crash_after_the_note_before_marking_recovers_exactly_that_one_note(self):
        real = self.read_shown
        calls = []

        def crash_once(conv, key):
            calls.append(key)
            if len(calls) == 1:
                raise Crash()
            return real(conv, key)
        self.service.read_shown = crash_once
        with self.assertRaises(Crash):
            self.offer()
        self.assertEqual(self.row()["display"], "prepared")
        self.assertEqual(len(self.page), 1)                                  # the card is on the page
        done = self.say(self.props(read_shown=crash_once), "yes", "note-yes")  # the very next owner turn
        self.assertEqual(done["operation"]["state"], "verified")
        self.assertEqual(self.posted, [69])
        r = self.row()
        self.assertEqual((r["display"], r["published_at"]), ("published", self.page[(CONV, r["key"])]["created_at"]))
        self.assertEqual(len(self.page), 1)                                  # recovery never posts again

    def test_a04_crash_before_the_failed_display_is_cancelled_stays_unapprovable(self):
        def crash(conv, key):
            raise Crash()
        self.service.show = lambda *a: False
        self.service.read_shown = crash
        with self.assertRaises(Crash):
            self.offer()
        self.assertEqual(self.row()["display"], "prepared")
        code = self.one("SELECT code FROM guild.mc_tool_proposals")[0]
        self.assertEqual(self.say(self.props(), f"yes {code}", "note-yes")["operation"]["state"], "not_shown")
        self.assertEqual(self.posted, [])

    def test_a04_a_changed_card_on_the_page_fails_the_offer_for_good(self):
        def crash(conv, key):
            raise Crash()
        self.service.read_shown = crash
        with self.assertRaises(Crash):
            self.offer()
        key = self.row()["key"]
        self.page[(CONV, key)]["text"] += " (edited)"
        code = self.one("SELECT code FROM guild.mc_tool_proposals")[0]
        self.assertEqual(self.say(self.props(), f"yes {code}", "note-yes")["operation"]["state"], "not_shown")
        self.assertEqual(self.row()["reason"], "display_changed")
        self.assertEqual(self.posted, [])

    def test_a04_a_store_that_cannot_answer_withdraws_the_offer_at_admission(self):
        def down(conv, key):
            raise OSError("floor store unavailable")
        self.service.read_shown = down
        r = self.offer()
        self.assertEqual(r["code"], "proposal_not_shown")
        self.assertEqual(self.row()["reason"], "display_failed")

    def test_a04_a_transport_retry_never_mints_a_second_offer(self):
        self.assertEqual(self.offer()["status"], "needs_confirmation")
        self.assertEqual(self.offer()["code"], "one_proposal_per_turn")
        self.assertEqual(self.one("SELECT count(*) FROM guild.mc_tool_proposals")[0], 1)
        self.assertEqual(len(self.page), 1)

    def test_a21_a_legacy_offer_is_never_approvable(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO guild.mc_tool_proposals (proposal_id, principal_id, environment, conversation_id,
                owner_turn_id, proposing_ledger_id, code, shown_text, kind, payload, operation_id, expires_at)
                VALUES (gen_random_uuid(), %s, 'dev', %s, %s, %s, 'P-LGCY', 'old card', 'issue_comment',
                '{"number": 69, "summary": "Old text from before the deploy.", "refs": [], "environment": "dev"}',
                gen_random_uuid(), clock_timestamp() + interval '5 minutes')""",
                (OWNER, CONV, "note-ask-1", self.ledger_id))
        self.assertEqual(self.say(self.props(), "yes P-LGCY", "note-yes")["operation"]["state"], "not_shown")
        self.assertEqual(self.posted, [])

    # ── A03: one pending offer per conversation ──────────────────────────────────────────────────────────────────

    def test_a03_a_second_offer_is_reported_and_the_first_kept(self):
        first = self.offer()["data"]["code"]
        self.next_turn("note-2")
        r = self.offer({"number": 70, "summary": "A different change, unrelated to the first.", "refs": []})
        self.assertEqual((r["status"], r["code"], r["data"]["pending"]), ("needs_clarification", "offer_pending", first))
        self.assertIn(f"no {first}", r["message"])
        self.assertEqual(self.one("SELECT count(*) FROM guild.mc_tool_proposals")[0], 1)
        # The yes that follows is not the immediate reply to the card any more: it needs the code.
        self.assertEqual(self.say(self.props(), "yes", "note-yes")["operation"]["state"], "needs_code")
        self.assertEqual(self.say(self.props(), f"yes {first}", "note-named")["operation"]["state"], "verified")
        self.assertEqual(self.posted, [69])

    def test_a03_explicit_cancellation_then_the_new_offer(self):
        first = self.offer()["data"]["code"]
        self.assertEqual(self.say(self.props(), f"nein {first}", "note-no")["operation"]["state"], "cancelled")
        self.next_turn("note-2")
        second = self.offer({"number": 70, "summary": "The change Robert wants now.", "refs": []})
        self.assertEqual(second["status"], "needs_confirmation")
        self.assertEqual(self.say(self.props(), f"yes {first}", "note-old")["operation"]["state"], "nothing_pending")
        self.assertEqual(self.posted, [])

    def test_a03_concurrent_admissions_in_one_turn_make_one_offer(self):
        barrier, results = threading.Barrier(4), []

        def go(n):
            barrier.wait()
            results.append(self.offer({"number": 70 + n, "summary": f"Concurrent change number {n} here.", "refs": []}))
        threads = [threading.Thread(target=go, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(r["status"] for r in results), ["needs_confirmation", "refused", "refused", "refused"])
        self.assertEqual(self.one("SELECT count(*) FROM guild.mc_tool_proposals WHERE state='pending'")[0], 1)
        self.assertEqual(len(self.page), 1)

    def test_a03_the_database_also_refuses_a_second_pending_offer(self):
        self.offer()
        with self.assertRaises(Exception):
            with self.connect() as conn, conn.cursor() as cur:
                cur.execute("""INSERT INTO guild.mc_tool_proposals (proposal_id, principal_id, environment,
                    conversation_id, owner_turn_id, proposing_ledger_id, code, shown_text, kind, payload, operation_id,
                    expires_at) SELECT gen_random_uuid(), principal_id, environment, conversation_id, owner_turn_id,
                    proposing_ledger_id, 'P-TWO2', shown_text, kind, payload, gen_random_uuid(), expires_at
                    FROM guild.mc_tool_proposals""")

    def test_a03_a_leftover_guest_offer_from_the_command_path_is_drained_never_approved(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO guild.mc_guest_proposals (proposal_id, principal_id, environment, conversation_id,
                owner_turn_id, ref, target_version, operation_id, expires_at) VALUES (gen_random_uuid(), %s, 'dev', %s,
                'note-old', 'guild:1', 'v1', gen_random_uuid(), clock_timestamp() + interval '5 minutes')""", (OWNER, CONV))
        self.assertEqual(self.offer()["status"], "needs_confirmation")
        self.assertEqual(self.one("SELECT state FROM guild.mc_guest_proposals")[0], "cancelled")
        self.assertEqual(self.one("SELECT status FROM guild.guest_requests WHERE id=1")[0], "requested")

    def test_a03_an_expired_offer_frees_the_conversation(self):
        first = self.offer()["data"]["code"]
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_tool_proposals SET expires_at = clock_timestamp() - interval '1 second'")
        self.next_turn("note-2")
        self.assertEqual(self.offer({"number": 70, "summary": "A fresh change after the lapse.", "refs": []})["status"],
                         "needs_confirmation")
        self.assertEqual(self.one("SELECT closed_reason FROM guild.mc_tool_proposals WHERE code=%s", (first,))[0],
                         "offer_expired")

    # ── A02+: the two clocks, on one offer ────────────────────────────────────────────────────────────────────────

    def at_offer_expiry(self, delta):
        """Approve by code with the approval clock exactly at the offer's expiry plus ``delta``. The offer's expiry is
        set to the database's now, so the grant (checked on the database clock) sees the same moment."""
        code = self.offer()["data"]["code"]
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE guild.mc_tool_proposals SET expires_at = clock_timestamp(),
                created_at = clock_timestamp() - interval '10 minutes' RETURNING expires_at""")
            expires = cur.fetchone()[0]
        now = expires + delta
        return code, self.say(self.props(clock=lambda cur: now), f"yes {code}", "note-yes"), now

    def test_a02plus_approval_just_before_the_offer_expiry_fixes_the_execution_deadline(self):
        code, done, now = self.at_offer_expiry(-timedelta(microseconds=1))
        self.assertEqual(done["operation"]["state"], "verified")
        r = self.row(code)
        self.assertEqual(r["approved_at"], now)
        self.assertEqual(r["deadline"], now + offers.EXECUTION_GRACE)
        expires, approval = self.one("""SELECT expires_at, definition->>'approval_expires_at'
            FROM guild.mc_action_grants ORDER BY created_at DESC LIMIT 1""")
        self.assertEqual(expires, r["deadline"])                                   # lifetime = execution deadline
        self.assertEqual(datetime.fromisoformat(approval), r["deadline"])          # and the approval ends there too

    def assert_expired(self, code, done):
        self.assertEqual(done["operation"]["state"], "expired")
        self.assertIn("expired at", done["reply"])
        self.assertIn("(America/Chicago)", done["reply"])
        self.assertIn("propose it again", done["reply"])
        self.assertEqual(self.row(code)["reason"], "offer_expired")
        self.assertIsNone(self.row(code)["approved_at"])
        self.assertEqual(self.posted, [])

    def test_a02plus_approval_exactly_at_the_offer_expiry_does_nothing_and_offers_to_repropose(self):
        code, done, _ = self.at_offer_expiry(timedelta(0))
        self.assert_expired(code, done)

    def test_a02plus_approval_just_after_the_offer_expiry_does_nothing(self):
        code, done, _ = self.at_offer_expiry(timedelta(microseconds=1))
        self.assert_expired(code, done)

    def test_a02plus_a_reproposal_gets_a_new_code_and_needs_its_own_approval(self):
        code, done, _ = self.at_offer_expiry(timedelta(seconds=1))
        self.assertEqual(done["operation"]["state"], "expired")
        self.next_turn("note-again")
        new = self.offer()["data"]["code"]
        self.assertNotEqual(new, code)
        self.assertEqual(self.say(self.props(), f"yes {code}", "note-old-yes")["operation"]["state"], "nothing_pending")
        self.assertEqual(self.posted, [])
        self.assertEqual(self.say(self.props(), f"yes {new}", "note-new-yes")["operation"]["state"], "verified")

    def test_a02plus_the_database_refuses_an_approval_outside_the_window(self):
        self.offer()
        with self.assertRaises(Exception):
            with self.connect() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE guild.mc_tool_proposals SET approved_at = expires_at,
                    execution_deadline = expires_at + interval '5 minutes'""")
        with self.assertRaises(Exception):
            with self.connect() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE guild.mc_tool_proposals SET approved_at = created_at,
                    execution_deadline = created_at + interval '6 minutes'""")

    # ── A02 / A02+: one offer for several guest requests, and the execution deadline mid-batch ──────────────────

    def guests(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO guild.guest_requests (name,email) VALUES ('Carla Dias','carla@example.test')")
        return ["guild:1", "guild:2", "guild:3"]

    def status(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, status FROM guild.guest_requests ORDER BY id")
            return dict(cur.fetchall())

    def batch_props(self, clock=None):
        return self.props(guest_rejects=True, **({"clock": clock} if clock else {}))

    def test_a02_one_card_one_ja_each_item_verified_and_a_later_request_untouched(self):
        self.guests()
        r = self.call("minimoi_guest_request_reject", {"refs": ["guild:1", "guild:2"]})
        self.assertEqual(r["status"], "needs_confirmation")
        card = self.shown[-1][2]
        self.assertIn("Reject these 2 guest requests?", card)
        self.assertIn("1. Ana Souza <ana@example.test>", card)
        self.assertIn("2. Bruno Costa <bruno@example.test>", card)
        with self.connect() as conn, conn.cursor() as cur:            # arrives after the snapshot
            cur.execute("INSERT INTO guild.guest_requests (name,email) VALUES ('Dora Lima','dora@example.test')")
        done = self.say(self.batch_props(), "ja", "note-ja")
        self.assertEqual(done["operation"]["state"], "verified")
        self.assertIn("2 of 2 rejected, verified by read-back: Ana Souza (guild:1), Bruno Costa (guild:2).", done["reply"])
        self.assertEqual(self.status(), {1: "rejected", 2: "rejected", 3: "requested", 4: "requested"})
        self.assertEqual([x[:2] for x in self.receipts()].count(("guest.reject_request", "verified")), 2)

    def test_a02_a_changed_item_gives_an_accurate_partial_result(self):
        refs = self.guests()
        self.call("minimoi_guest_request_reject", {"refs": refs})
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.guest_requests SET status='granted' WHERE id=2")
        done = self.say(self.batch_props(), "yes", "note-yes")
        self.assertEqual(done["operation"]["state"], "partial")
        self.assertIn("2 of 3 rejected", done["reply"])
        self.assertIn("1 changed since you approved and was not rejected: guild:2.", done["reply"])
        self.assertEqual(self.status(), {1: "rejected", 2: "granted", 3: "rejected"})

    def crossing(self, second_check):
        """Approve a three-item batch at the database's now; the deadline check before item 1 passes, the one before
        item 2 reads ``second_check(deadline)``."""
        refs = self.guests()
        self.call("minimoi_guest_request_reject", {"refs": refs})
        t = self.db_now()
        ticks = iter([t, t, second_check(t + offers.EXECUTION_GRACE), second_check(t + offers.EXECUTION_GRACE)])
        return self.say(self.batch_props(clock=lambda cur: next(ticks)), "yes", "note-yes"), t

    def test_a02plus_deadline_just_before_lets_the_next_item_run(self):
        done, _ = self.crossing(lambda d: d - timedelta(microseconds=1))
        self.assertEqual(done["operation"]["state"], "verified")
        self.assertEqual(self.status(), {1: "rejected", 2: "rejected", 3: "rejected"})

    def test_a02plus_deadline_reached_mid_batch_keeps_completed_items_and_dispatches_nothing_more(self):
        for label, second in (("exactly at", lambda d: d), ("just after", lambda d: d + timedelta(microseconds=1))):
            with self.subTest(label):
                if label != "exactly at":
                    self.tearDown(); self.setUp()                                  # noqa: E702 (fresh fixture)
                done, t = self.crossing(second)
                self.assertEqual(done["operation"]["state"], "partial")
                self.assertIn("1 of 3 rejected, verified by read-back: Ana Souza (guild:1).", done["reply"])
                self.assertIn("2 not done: the five-minute window to carry out your approval ended at", done["reply"])
                self.assertIn("guild:2, guild:3", done["reply"])
                self.assertEqual(self.status(), {1: "rejected", 2: "requested", 3: "requested"})
                # Replay of the same yes reports and dispatches nothing; the remainder is never revived.
                again = self.say(self.batch_props(), "yes", "note-yes")
                self.assertTrue(again["operation"]["repeated"])
                self.assertIn("That was already handled", again["reply"])
                self.assertIn("2 not done, nothing changed", again["reply"])
                self.assertEqual(self.status(), {1: "rejected", 2: "requested", 3: "requested"})
                # A re-proposal cannot include the completed item: it is not open any more.
                self.next_turn("note-again")
                r = self.call("minimoi_guest_request_reject", {"refs": ["guild:1", "guild:2", "guild:3"]})
                self.assertEqual((r["code"], r["data"]["missing"]), ("no_open_request", ["guild:1"]))

    def test_a02plus_an_unknown_item_stops_the_batch_and_replay_never_retries(self):
        from unittest import mock
        from core.mc_domain_actions import guest
        refs = self.guests()
        self.call("minimoi_guest_request_reject", {"refs": refs})
        real, calls = guest.GuestActions.execute, []

        def flaky(actions, conn, context, intent, worker, **kw):
            calls.append(intent.request.arguments["ref"])
            if len(calls) == 2:
                raise ConnectionError("database connection lost at commit")
            return real(actions, conn, context, intent, worker, **kw)
        with mock.patch.object(guest.GuestActions, "execute", flaky):
            done = self.say(self.batch_props(), "yes", "note-yes")
        self.assertEqual(done["operation"]["state"], "unknown")
        self.assertIn("Could not confirm the outcome for guild:2", done["reply"])
        self.assertIn("Not attempted after that: guild:3.", done["reply"])
        self.assertEqual(calls, ["guild:1", "guild:2"])
        self.assertEqual(self.status()[3], "requested")
        self.say(self.batch_props(), "yes", "note-yes")                       # replay
        self.assertEqual(self.status()[3], "requested")

    def test_a02_the_batch_schema_is_bounded_and_strict(self):
        for refs in ([], ["guild:1", "guild:1"], [f"guild:{n}" for n in range(1, 22)], ["Ana Souza"]):
            self.assertEqual(self.call("minimoi_guest_request_reject", {"refs": refs})["status"], "refused", refs)
        self.assertEqual(self.call("minimoi_guest_request_reject", {"ref": "guild:1"})["status"], "refused")

    # ── A23: English card, absolute expiry with its zone, ja ──────────────────────────────────────────────────────

    def test_a23_the_card_is_english_with_an_absolute_expiry_and_zone(self):
        r = self.offer()
        card = self.shown[-1][2]
        until = offers.expiry_words(self.row()["expires_at"])
        self.assertIn(f"This offer expires at {until}", card)
        self.assertRegex(until, r"^\d\d:\d\d C[DS]T \(America/Chicago\) on \d{1,2} [A-Z][a-z]{2} \d{4}$")
        self.assertIn(COMMENT["summary"], card)
        self.assertIn(r["data"]["code"], card)
        self.assertIn(until, r["message"])

    def test_a23_ja_approves_under_the_same_rules_as_yes(self):
        self.assertEqual(parse("ja"), ("confirm", None))
        self.assertEqual(parse("Ja, bitte."), ("confirm", None))
        self.assertEqual(parse("ja P-7K3M"), ("confirm", "P-7K3M"))
        self.assertEqual(parse("nein P-7K3M"), ("cancel", "P-7K3M"))
        self.assertIsNone(parse("ja, aber morgen"))
        self.offer()
        self.assertEqual(self.say(self.props(), "ja", "note-ja")["operation"]["state"], "verified")
        self.assertEqual(self.posted, [69])


if __name__ == "__main__":
    unittest.main()
