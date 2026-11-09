"""Fixes from the first Dev test run, 8 Nov: withdrawing a plain ask, "pull in" wording, code before prose in a repo lookup, and the
correction capture folded in chat."""
import tempfile
from pathlib import Path
from unittest import TestCase

from core import agent_requests as ar
from minimoi_portal.guild_ui import agent_ask as ask
from minimoi_portal.guild_ui.agent_ask import AskService
from minimoi_portal.guild_ui.markdown_render import render_markdown
from services.helper_seats import types as T

CONV, RID = "c-0123456789abcdef", ar.request_id_for("dev-test-fix")


def plain_ask(root, conversation=CONV):
    ar.create_request(root, request_id=RID, frm="mc", to="grok", action="Ask Grok to check the wording.", conversation_id=conversation,
                      origin="owner_message_via_mc")


class Withdraw(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.asks = AskService.__new__(AskService)
        self.asks.root = self.tmp.name

    def test_a_queued_plain_ask_can_be_withdrawn(self):
        plain_ask(self.tmp.name)
        self.assertEqual(self.asks.withdraw_typed(CONV, RID), "withdrawn")
        self.assertIsNotNone(ar.load(self.tmp.name, RID)["closed"])

    def test_not_from_another_conversation(self):
        plain_ask(self.tmp.name, "c-fedcba9876543210")
        self.assertEqual(self.asks.withdraw_typed(CONV, RID), "not_found")
        self.assertIsNone(ar.load(self.tmp.name, RID)["closed"])


class PullIn(TestCase):
    def _call(self, text, to):
        return ask.owner_ask_proposal(text, {"to": to, "title": "t", "files": None}, ())

    def test_pull_in_names_the_agents(self):
        for text in ("Pull in Claude and Codex.", "we need a war room on this, pull in Claude and Codex"):
            with self.subTest(text=text):
                self.assertIsNone(self._call(text, ["claude-code", "codex"])[1])

    def test_get_an_agent_to_do_something(self):
        self.assertIsNone(self._call("Get Grok to review #164.", ["grok"])[1])
        self.assertIsNone(self._call("Get Claude Code to look at this.", ["claude-code"])[1])
        self.assertIsNotNone(self._call("I need to get Grok to review this.", ["grok"])[1])
        self.assertIsNotNone(self._call("Get Grok's view.", ["grok"])[1])

    def test_a_negated_or_wrong_recipient_still_fails(self):
        self.assertIsNotNone(self._call("Don't pull in Codex.", ["codex"])[1])
        self.assertIsNotNone(self._call("Pull in Codex.", ["grok"])[1])
        self.assertIsNotNone(self._call("if this breaks, pull in Codex", ["codex"])[1])
        self.assertIsNotNone(self._call("we need a war room, ask Codex", ["codex"])[1])


class Lookup(TestCase):
    def test_the_implementing_code_outranks_tests_and_docs_that_mention_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, text in (("docs/aaa.md", "The build log item store is defined in many places. Build log item store."),
                              ("tests/test_store.py", "def test_build_log_item_store(): pass  # build log item store"),
                              ("domains/guild/queue_store.py", "import os\n\nclass QueueStore:\n    '''items of the build log'''\n")):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text)
            got = T.repo_lookup(T.Ctx(repo_root=tmp, ask_model=lambda p, purpose: "x"), {"question": "which file defines the Build Log item store"})
            self.assertEqual(got["sources"][0], "domains/guild/queue_store.py")


class Learning(TestCase):
    BLOCK = '```capture\nowner-said: "no"\n```\n\nCaptured 1 correction pairs'

    def test_a_complete_capture_is_folded_and_the_answer_stays(self):
        html = render_markdown("That changes my view.\n\n" + self.BLOCK)
        self.assertIn("<summary>Learning note</summary>", html)
        self.assertLess(html.index("That changes my view"), html.index("Learning note"))

    def test_other_code_is_left_alone(self):
        self.assertNotIn("Learning note", render_markdown("```python\nx = 1\n```"))
