"""MC's delegated card brief under the fakework executor profile (Blue Lantern, 5 Nov 2026).

Robert approved a fakework card whose brief was MC's plain words; the Mac runner refused it
``fakework_program_invalid``, because fakework/1 takes its program from the brief. Under a fakework profile the brief
must be a valid program that still carries MC's words; under the real profiles it stays the plain words."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.mc_delegation_dispatcher import cards as card_mod  # noqa: E402
from core.mc_delegation_runner.fakework import parse_program  # noqa: E402
from core.mc_tools.tasks_adapter import TasksAdapter  # noqa: E402

FAKE = json.loads((ROOT / "config/partnership/executor_profiles.fakework.json").read_text())
REAL = json.loads((ROOT / "config/partnership/executor_profiles.json").read_text())
SET = "sha256:" + "5" * 64
CANONICAL = {"outcome": "Draft a three-bullet checklist labeled “Blue Lantern 1105”.\n\nKeep it short.",
             "done_when": "Codex submits the draft, ready for Claude Code review."}


def adapter(doc):
    return TasksAdapter(client=None, environment="dev", card_ref="a" * 40, profiles_doc=doc)


class FakeworkBriefTests(unittest.TestCase):
    def test_a_fakework_build_brief_is_a_program_that_keeps_mcs_words(self):
        brief = adapter(FAKE)._brief("build", "codex", CANONICAL)
        steps = parse_program(brief)                                     # the runner's own parser: no refusal
        self.assertEqual([s[0] for s in steps], ["checkpoint", "result"])
        self.assertIn("# Draft a three-bullet checklist", brief)
        self.assertIn("# Done when: Codex submits the draft", brief)

    def test_a_fakework_review_brief_records_findings_and_approves_the_exact_reviewed_set(self):
        steps = parse_program(adapter(FAKE)._brief("review", "claude-code", CANONICAL, SET))
        self.assertEqual([s[0] for s in steps], ["findings", "verdict", "result"])
        self.assertEqual(steps[1][1], {"verdict": "approve", "set": SET})

    def test_a_fakework_review_without_the_reviewed_set_is_refused(self):
        with self.assertRaises(ValueError):
            adapter(FAKE)._brief("review", "claude-code", CANONICAL)

    def test_the_fakework_card_compiles(self):
        a = adapter(FAKE)
        for kind, holder in (("build", "codex"), ("review", "claude-code")):
            inputs = [{"ref": "C-x.patch", "sha256": "0" * 64}] if kind == "review" else []
            card = a._card(kind, holder, "TK-mc-" + "b" * 24, a._brief(kind, holder, CANONICAL, SET), inputs)
            card_mod.Catalogue.load().compile(card)

    def test_real_profiles_keep_the_plain_words(self):
        brief = adapter(REAL)._brief("build", "codex", CANONICAL)
        self.assertEqual(brief, f"{CANONICAL['outcome']}\nDone when: {CANONICAL['done_when']}")


if __name__ == "__main__":
    unittest.main()
