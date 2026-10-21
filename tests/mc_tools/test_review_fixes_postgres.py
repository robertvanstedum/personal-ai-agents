"""Regression tests for the 21 Oct review of the MC tool path (mc_tool_path_review.md), built from its
reproductions on real PostgreSQL. Run with tests/mc_tools/run_postgres.sh <pgbin>."""
import unittest

from test_tool_path_postgres import Base, CONV, DSN, OWNER  # noqa: F401
from core.mc_tools.proposals import ToolProposals


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class ReviewFixes(Base, unittest.TestCase):
    def props(self, **kw):
        return ToolProposals(self.connect, principal=OWNER, environment="dev", **kw)

    def test_p2_1_a_retried_yes_never_confirms_a_newer_proposal(self):
        posted = []
        p = self.props(comment_issue=lambda n, b: posted.append(n) or ("created", len(posted)))
        self.call("minimoi_issue_comment", {"number": 69, "summary": "First comment, approved by Robert.", "refs": ["guild:1"]})
        p.handle("confirm", conversation=CONV, turn_key="note-yes-1")
        self.call("minimoi_issue_comment", {"number": 70, "summary": "Second comment, not approved yet.", "refs": ["guild:2"]})
        retry = p.handle("confirm", conversation=CONV, turn_key="note-yes-1")         # the first yes, retried
        self.assertTrue(retry["operation"].get("repeated"))
        self.assertEqual(posted, [69])
        self.assertEqual(self.one("SELECT state FROM guild.mc_tool_proposals WHERE payload->>'number'='70'")[0], "pending")


if __name__ == "__main__":
    unittest.main()
