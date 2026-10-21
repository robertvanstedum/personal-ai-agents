"""Regression tests for the 21 Oct review of the MC tool path (mc_tool_path_review.md), built from its
reproductions on real PostgreSQL. Run with tests/mc_tools/run_postgres.sh <pgbin>."""
import unittest

from test_tool_path_postgres import Base, CONV, DSN, OWNER  # noqa: F401
from core.mc_tools.proposals import ToolProposals, parse  # noqa: F401


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class ReviewFixes(Base, unittest.TestCase):
    def props(self, **kw):
        return ToolProposals(self.connect, principal=OWNER, environment="dev", **kw)

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
        self.next_turn("note-2")
        self.service.show = lambda *a: False
        r = self.call("minimoi_issue_comment", {"number": 71, "summary": "This one could not be displayed.", "refs": []})
        self.assertEqual(r["code"], "proposal_not_shown")
        self.assertEqual(self.one("SELECT count(*) FROM guild.mc_tool_proposals WHERE state='pending'")[0], 0)


if __name__ == "__main__":
    unittest.main()
