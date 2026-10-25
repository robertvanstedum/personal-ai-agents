"""Regression tests for the 21 Oct review of the MC tool path (mc_tool_path_review.md), built from its
reproductions on real PostgreSQL. Run with tests/mc_tools/run_postgres.sh <pgbin>."""
import hashlib
import secrets
import unittest

from test_tool_path_postgres import Base, CONV, DSN, OWNER  # noqa: F401
from core.mc_tools.proposals import ToolProposals, parse  # noqa: F401
from services.mc_tools.listener import create_app


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class ReviewFixes(Base, unittest.TestCase):
    def props(self, **kw):
        return ToolProposals(self.connect, policy=self.policy, principal=OWNER, environment="dev", **kw)

    def test_p2_1_a_retried_yes_never_confirms_a_newer_proposal(self):
        posted = []
        p = self.props(comment_issue=lambda n, b: posted.append(n) or ("created", len(posted)))
        self.call("minimoi_issue_comment", {"number": 69, "summary": "First comment, approved by Robert.", "refs": ["guild:1"]})
        self.say(p, "yes", "note-yes-1")
        self.call("minimoi_issue_comment", {"number": 70, "summary": "Second comment, not approved yet.", "refs": ["guild:2"]})
        retry = self.say(p, "yes", "note-yes-1")         # the first yes, retried
        self.assertTrue(retry["operation"].get("repeated"))
        self.assertEqual(posted, [69])
        self.assertEqual(self.one("SELECT state FROM guild.mc_tool_proposals WHERE payload->>'number'='70'")[0], "pending")

    def test_p2_2_short_words_are_not_captured_after_a_proposal_is_done(self):
        p = self.props(comment_issue=lambda n, b: ("created", 1))
        self.call("minimoi_issue_comment", {"number": 69, "summary": "Workaround noted for the record here.", "refs": ["guild:1"]})
        self.assertTrue(p.has_pending(CONV, "note-yes-1"))
        self.say(p, "yes", "note-yes-1")
        self.assertFalse(p.has_pending(CONV, "note-later"))       # "ok" / "no" later go to MC, not the platform
        self.assertTrue(p.has_pending(CONV, "note-yes-1"))        # only the same note, retried

    def test_p1_1_the_platform_shows_the_exact_proposal_and_a_plain_ok_only_answers_it_next(self):
        summary = "Text Robert sees verbatim on screen from the platform."
        r = self.call("minimoi_issue_comment", {"number": 69, "summary": summary, "refs": ["guild:1"]})
        code = r["data"]["code"]
        (conv, _key, card), = self.shown                                      # the platform's own note, verbatim
        self.assertEqual(conv, CONV)
        self.assertIn(summary, card)
        self.assertIn(code, card)
        posted = []
        p = self.props(comment_issue=lambda n, b: posted.append(b) or ("created", 1))
        self.next_turn("note-other")                                           # Robert talks about something else
        self.assertEqual(self.say(p, "ok", "note-ok")["operation"]["state"], "needs_code")   # not his next turn
        self.assertEqual(posted, [])
        done = self.say(p, f"yes {code.lower()}", "note-named")                # naming it always works
        self.assertEqual(done["operation"]["state"], "verified")
        self.assertTrue(posted[0].startswith(summary))

    def test_p1_1_after_reading_github_only_a_named_yes_approves(self):
        self.call("minimoi_github_issues", {"number": 69})                    # text anyone can write on GitHub
        r = self.call("minimoi_issue_comment", {"number": 69, "summary": "Steered by a public comment perhaps.",
                                                "refs": []})
        code = r["data"]["code"]
        self.assertIn(f"yes {code}", self.shown[-1][2])
        posted = []
        p = self.props(comment_issue=lambda n, b: posted.append(n) or ("created", 1))
        self.assertEqual(self.say(p, "yes", "note-yes")["operation"]["state"], "needs_code")
        self.assertEqual(posted, [])
        self.assertEqual(self.say(p, f"yes {code}", "note-yes-named")["operation"]["state"], "verified")

    def test_p1_1_one_proposal_per_turn_and_a_proposal_that_cannot_be_shown_is_withdrawn(self):
        self.call("minimoi_issue_comment", {"number": 69, "summary": "The one change offered in this turn.", "refs": []})
        r = self.call("minimoi_issue_comment", {"number": 70, "summary": "A second change in the same turn.", "refs": []})
        self.assertEqual(r["code"], "one_proposal_per_turn")
        self.say(self.props(), "no", "note-no")
        self.next_turn("note-2")
        self.service.show = lambda *a: False
        r = self.call("minimoi_issue_comment", {"number": 71, "summary": "This one could not be displayed.", "refs": []})
        self.assertEqual(r["code"], "proposal_not_shown")
        self.assertEqual(self.one("SELECT count(*) FROM guild.mc_tool_proposals WHERE state='pending'")[0], 0)

    def test_p1_2_a_forged_caller_sees_no_personal_data_and_cannot_ride_on_a_plain_ok(self):
        self.service.isolated_runtime = False                  # the gateway's OS user also runs exec-capable agents
        token = secrets.token_urlsafe(32)
        client = create_app(self.service, token_sha256=hashlib.sha256(token.encode()).hexdigest()).test_client()
        forged = "agent:mc-chat:openai-user:guild-mc:" + hashlib.sha256(f"guild:{OWNER}:{CONV}".encode()).hexdigest()[:32]
        headers = {"Authorization": "Bearer " + token, "X-MC-Session": forged, "X-MC-Agent": "mc-chat"}
        refused = client.post("/internal/mc/v1/tools/minimoi_guest_requests", json={"arguments": {}}, headers=headers).get_json()
        self.assertEqual((refused["status"], refused["code"]), ("refused", "tool_run_missing"))   # the session alone: nothing
        headers |= {"X-MC-Run": self.run, "X-MC-Tool-Call": "call_forged",     # worst case: it also learned the live run
                    "X-MC-Instance": self.instance}                            # and the gateway process id
        listed = client.post("/internal/mc/v1/tools/minimoi_guest_requests", json={"arguments": {}}, headers=headers).get_json()
        self.assertEqual(listed["status"], "done")
        for private in ("Ana", "Bruno", "example.test", "@"):
            self.assertNotIn(private, listed["message"] + str(listed["data"]))
        proposed = client.post("/internal/mc/v1/tools/minimoi_issue_comment", headers=headers, json={
            "arguments": {"number": 69, "summary": "Spam text approved by nobody, posted as MiniMoi.", "refs": []}}).get_json()
        code = proposed["data"]["code"]
        posted = []
        p = self.props(comment_issue=lambda n, b: posted.append(n) or ("created", 1))
        self.assertEqual(self.say(p, "ok", "note-next")["operation"]["state"], "needs_code")    # his next plain "ok"
        self.assertEqual(posted, [])
        self.assertIn(f"yes {code}", self.shown[-1][2])        # only a deliberate, named yes could approve it

    def test_p2_3_one_register_a_yes_for_a_lesson_adopts_the_lesson_and_nothing_else(self):
        from core.mc_lessons import commands as lessons
        posted = []
        self.call("minimoi_issue_comment", {"number": 69, "summary": "An earlier offer Robert moved past.", "refs": []})
        self.assertEqual(self.call("minimoi_lesson_propose", {"text": "Lead with the answer, then the detail."})["code"],
                         "one_proposal_per_turn")                                   # same turn: refused
        self.next_turn("note-2")
        waiting = self.call("minimoi_lesson_propose", {"text": "Lead with the answer, then the detail."})
        self.assertEqual((waiting["status"], waiting["code"]), ("needs_clarification", "offer_pending"))  # never replaced
        p = self.props(comment_issue=lambda n, b: posted.append(n) or ("created", 1), lessons=self.ledger)
        self.assertEqual(self.say(p, "no", "note-no")["operation"]["state"], "cancelled")   # Robert drops the comment
        self.next_turn("note-3")
        self.assertEqual(self.call("minimoi_lesson_propose", {"text": "Lead with the answer, then the detail."})["status"],
                         "needs_confirmation")
        done = self.say(p, "yes", "note-yes")
        self.assertEqual(done["operation"]["state"], "verified")
        self.assertEqual(posted, [])
        self.assertEqual([x["text"] for _, x in self.ledger.read().active()], ["Lead with the answer, then the detail."])
        # A lesson the owner-command phrase left waiting before the tool path took over is a leftover nothing can
        # answer (A15): drained as history when MC next offers, never adopted, never two kinds pending (spec §9.1).
        lessons.LessonCommands(self.ledger).handle(lessons.Command("remember", text="Use metric units."),
                                                   conversation=CONV, turn_key="note-phrase")
        self.next_turn("note-4")
        r = self.call("minimoi_issue_comment", {"number": 70, "summary": "A newer offer from the tool path.", "refs": []})
        self.assertEqual(r["status"], "needs_confirmation")
        self.assertFalse(lessons.LessonCommands(self.ledger).has_pending(CONV))
        self.assertNotIn("Use metric units.", [x["text"] for _, x in self.ledger.read().active()])

    def test_p2_4_public_text_filter_catches_generic_first_names_lookalikes_spacing_and_guest_logins(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO guild.guest_requests (name,email) VALUES ('Robert Ng','rng@example.test')")
        self.service.people = lambda: ["Sofia Lima sofia.lima@example.test"]           # a guest login, not a request
        for turn, summary in enumerate(("Rejected Robert's request on the backend for now.",   # 'robert' not exempt
                                        "Rejected Аna's request on the backend today.",         # Cyrillic А
                                        "Rejected B r u n o's request on the backend today.",
                                        "Rejected B.r.u.n.o on the backend today.",
                                        "Extended Sofia's login by a week on the backend.",     # guest account
                                        "Ｂｒｕｎｏ was rejected on the backend.")):             # full-width letters
            self.next_turn(f"note-p24-{turn}")
            r = self.call("minimoi_issue_comment", {"number": 69, "summary": summary, "refs": ["guild:1"]})
            self.assertEqual(r["status"], "refused", summary)
        self.next_turn("note-p24-ok")
        ok = self.call("minimoi_issue_comment", {"number": 69, "summary": "Rejected a test guest request on the "
                                                 "backend; the approval path is still missing.", "refs": ["guild:1"]})
        self.assertEqual(ok["status"], "needs_confirmation")

    def test_p2_6_prod_refuses_data_and_access_changes_without_a_standard_change(self):
        self.service.environment = "prod"
        r = self.call("minimoi_guest_request_reject", {"refs": ["guild:2"]})
        self.assertEqual((r["status"], r["code"]), ("refused", "needs_change_record"))
        r = self.call("minimoi_access_invite", {"name": "Sofia", "email": "sofia@example.test", "days": 7})
        self.assertEqual(r["code"], "needs_change_record")
        self.assertEqual(self.receipts(), [])

    def test_p2_6_prod_standard_change_is_offered_named_and_each_use_logged_on_its_ticket(self):
        self.service.environment = "prod"
        self.service.standard_changes = {"guest_reject": {"id": "SC-guest-reject-1", "ticket": 371}}
        r = self.call("minimoi_guest_request_reject", {"refs": ["guild:2"]})
        self.assertEqual(r["status"], "needs_confirmation")
        self.assertIn("Standard change SC-guest-reject-1 (ticket #371)", self.shown[-1][2])
        logged = []
        p = ToolProposals(self.connect, policy=self.policy, principal=OWNER, environment="prod", guest_rejects=True,
                          comment_issue=lambda n, body: logged.append((n, body)) or ("created", 5))
        done = self.say(p, "yes", "note-yes")
        self.assertEqual(done["operation"]["state"], "verified", done)
        self.assertIn("Logged on ticket #371", done["reply"])
        (number, body), = logged
        self.assertEqual(number, 371)
        self.assertIn("guild:2", body)
        for private in ("Bruno", "example.test", "@"):
            self.assertNotIn(private, body)

    def test_p2_6_the_data_repair_ticket_is_a_draft_change_record(self):
        from core.mc_tools.proposals import issue_text
        title, body = issue_text({"refs": ["guild:3"], "change": "reject", "environment": "prod"}, "op-1")
        self.assertTrue(title.startswith("RFC (draft, prod)"))
        self.assertIn("Not approved", body)
        for field in ("**What:**", "**Why:**", "**Risk and impact:**", "**Steps:**", "**Pre-checks:**", "**Verification:**",
                      "**Rollback:**", "**Test evidence:**", "**Review:**", "**Approval:**", "**Outcome:**"):
            self.assertIn(field, body)

    def test_p3_3_only_actors_with_a_budget_enforcer_are_offered(self):
        r = self.call("minimoi_delegate_research", {"agent": "codex", "title": "Look at the relay"})
        self.assertEqual(r["status"], "refused")

    def test_p3_4_an_invite_never_rewrites_a_guests_rejected_request(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.guest_requests SET status='rejected', reason='their own words' WHERE id=1")
        self.call("minimoi_access_invite", {"name": "Someone Else", "email": "ana@example.test", "days": 7})
        p = self.props(invite=lambda prop, ref: ("done", {"username": "guest_x"}, None))
        done = self.say(p, "yes", "note-yes")
        self.assertEqual(done["operation"]["state"], "failed")
        self.assertEqual(tuple(self.one("SELECT name, status, reason FROM guild.guest_requests WHERE id=1")),
                         ("Ana Souza", "rejected", "their own words"))

    def test_r1_github_text_read_in_an_earlier_turn_still_needs_the_code(self):
        self.call("minimoi_github_issues", {"number": 69})                 # turn N: MC reads public text
        self.next_turn("note-n1")                                          # turn N+1: no read, but the session remembers it
        r = self.call("minimoi_issue_comment", {"number": 69, "summary": "Steered by last turn's public comment.", "refs": []})
        code = r["data"]["code"]
        posted = []
        p = self.props(comment_issue=lambda n, b: posted.append(n) or ("created", 1))
        self.assertEqual(self.say(p, "yes", "note-yes")["operation"]["state"], "needs_code")
        self.assertEqual(posted, [])
        self.assertIn(f"yes {code}", self.shown[-1][2])
        self.assertIn("This conversation read external pages", self.shown[-1][2])       # the reason, in one line
        # Sticky for the conversation, later turns too.
        self.assertEqual(self.say(p, f"no {code}", "note-n2")["operation"]["state"], "cancelled")
        self.next_turn("note-n3")
        r = self.call("minimoi_lesson_propose", {"text": "Lead with the answer, then the detail."})
        self.assertIn("This conversation read external pages", self.shown[-1][2])
        self.assertEqual(self.say(self.props(lessons=self.ledger), "yes", "note-n4")["operation"]["state"], "needs_code")
        self.assertEqual(self.say(self.props(lessons=self.ledger), f"yes {r['data']['code']}", "note-n5")["operation"]
                         ["state"], "verified")

    def test_r2_cards_say_when_the_platform_cannot_prove_the_proposal_came_from_mc(self):
        self.service.isolated_runtime = False
        r = self.call("minimoi_issue_comment", {"number": 69, "summary": "Forged text that Robert did not ask for.", "refs": []})
        card = self.shown[-1][2]
        self.assertIn("MiniMoi cannot yet prove this card came from Master Craftsman", card)
        self.assertIn(f"no {r['data']['code']}", card)
        self.assertEqual(self.say(self.props(), f"no {r['data']['code']}", "note-no")["operation"]["state"], "cancelled")
        self.service.isolated_runtime = True                                # isolated and untainted: no warning
        self.next_turn("note-2")
        self.call("minimoi_issue_comment", {"number": 70, "summary": "A normal proposal in an isolated runtime.", "refs": []})
        self.assertNotIn("cannot yet prove", self.shown[-1][2])
        self.assertNotIn("public GitHub text", self.shown[-1][2])


if __name__ == "__main__":
    unittest.main()
