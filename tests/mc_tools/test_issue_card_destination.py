"""#585: a card for a GitHub issue or comment names the target repository and shows the full text before Robert's yes;
with no repository set nothing is proposed; and a yes never sends the write to a repository other than the one shown."""
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

from core.mc_tools import proposals as P
from core.mc_tools.service import ToolService

REPO = "example/minimoi-dev-issues"
CALLER = SimpleNamespace(agent_id="mc-chat")


def service(repo=REPO):
    return ToolService(lambda: None, principal="robert", environment="dev", tickets=True, comments=True,
                       issue_repo=repo)


def card(svc, method, args):
    """The words the platform would show Robert (``_pending_tool_proposal`` posts them verbatim) and the payload."""
    seen = {}

    def pending(conn, b, kind, payload, req, words):
        seen.update(kind=kind, payload=payload, words=words)
        return {"status": "needs_confirmation"}
    with mock.patch.object(svc, "_tx", lambda: nullcontext(None)), \
            mock.patch.object(svc, "_bind", lambda conn, caller: SimpleNamespace()), \
            mock.patch.object(svc, "_public_text_refusal", lambda conn, *t: None), \
            mock.patch.object(svc, "_pending_tool_proposal", pending):
        getattr(svc, method)(args, CALLER, None)
    return seen


class CardNamesTheDestination(unittest.TestCase):
    def test_new_issue_card_names_the_repo_and_shows_the_whole_body_with_footer(self):
        seen = card(service(), "_issue_create", {"title": "Keep chats live", "body": "Switching tabs loses the chat."})
        words = seen["words"]
        self.assertIn(f"Open this new issue in {REPO}?", words)
        self.assertIn(f"Destination: {REPO} (https://github.com/{REPO}/issues)", words)
        self.assertIn("Switching tabs loses the chat.", words)
        self.assertIn("Opened by MiniMoi's Master Craftsman after Robert approved this exact text", words)
        self.assertEqual(seen["payload"]["repo"], REPO)                  # the approval covers the destination

    def test_comment_card_names_the_repo_and_the_issue(self):
        seen = card(service(), "_issue_comment", {"number": 69, "summary": "Fixed on Dev.", "refs": []})
        self.assertIn(f"Add this comment to issue #69 in {REPO}?", seen["words"])
        self.assertIn(f"https://github.com/{REPO}/issues/69", seen["words"])
        self.assertIn("Fixed on Dev.", seen["words"])
        self.assertEqual(seen["payload"]["repo"], REPO)

    def test_issue_change_card_names_the_repo_and_shows_the_new_body_with_footer(self):
        seen = card(service(), "_issue_update", {"number": 388, "body": "Scope narrowed."})
        self.assertIn(f"Make this change to issue #388 in {REPO}?", seen["words"])
        self.assertIn("Scope narrowed.", seen["words"])
        self.assertIn("Edited by MiniMoi's", seen["words"])

    def test_no_repository_set_proposes_nothing(self):
        svc = service(repo=None)
        for method, args in (("_issue_create", {"title": "t", "body": "b"}),
                             ("_issue_comment", {"number": 1, "summary": "s", "refs": []}),
                             ("_issue_update", {"number": 1, "body": "b"}),
                             ("_data_repair_ticket", {"refs": ["guild:1"], "change": "reject"})):
            r = getattr(svc, method)(args, CALLER, None)
            self.assertEqual((r["status"], r["code"]), ("refused", "no_issue_repository"), method)
        self.assertIsNone(service(repo="not a repo").issue_repo)


class YesGoesOnlyWhereTheCardSaid(unittest.TestCase):
    def run_comment(self, writer_repo, approved=REPO):
        sent = []

        def comment_issue(number, body):
            sent.append(number)
            return "created", 7
        comment_issue.repo = writer_repo
        tp = P.ToolProposals(lambda: None, principal="robert", environment="dev", comment_issue=comment_issue)
        effect = SimpleNamespace(operation_id="op-1")
        payload = {"number": 69, "summary": "s", "refs": [], "environment": "dev", "agent": "mc-chat"}
        if approved:
            payload["repo"] = approved
        with mock.patch.object(tp, "_finish", lambda *a, **k: {"receipt_id": "r-1"}):
            return tp._run_comment(effect, {"payload": payload}), sent

    def test_same_repo_posts(self):
        reply, sent = self.run_comment(REPO)
        self.assertEqual((reply["operation"]["state"], sent), ("verified", [69]))

    def test_changed_repo_sends_nothing(self):
        reply, sent = self.run_comment("someone/else")
        self.assertEqual((reply["operation"]["state"], sent), ("failed", []))
        self.assertIn("not the one on the card you approved", reply["reply"])

    def test_an_offer_made_before_the_repo_was_recorded_is_left_to_the_writer(self):
        reply, sent = self.run_comment("someone/else", approved=None)
        self.assertEqual(sent, [69])


if __name__ == "__main__":
    unittest.main()
