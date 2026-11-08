"""The owner-evidence engine (MC front door spec §8). Synthetic cases only: the real corpus is private."""
import json

import pytest

from core.owner_evidence import Bundle, Case, Run, gate, grade, load, run, sample_sheet
from core.owner_evidence.suite import SCHEMA, only_paraphrases

IDEA = "Could we add a weekly digest of unfinished items to the shop floor page?"
SRC = {"kind": "synthetic", "ref": "test", "date": "2026-11-08"}
TAKE = "I'd put it on the shop floor, because a separate page gets forgotten. The risk is noise; I recommend a cap of five. #41 on the Build Log."
BUNDLE = Bundle("m", "sha256:p", "rev", "v1", "dev")


def corpus(tmp_path, **over):
    row = {"id": "C1", "words": IDEA, "source": SRC, "modes": ["cold", "warm", "variant"], "must_call": ["minimoi_build_log_add"],
           "must_not_call": ["minimoi_agent_request"], "must_be_substantive": True, "memory": ["#212 weekly digest, 2 Nov"], "earlier_ref": "#212",
           "variant_words": "Make the digest daily instead.", **over}
    p = tmp_path / "cases.json"
    p.write_text(json.dumps({"schema": SCHEMA, "version": "v1", "cases": [row]}))
    return p


def case(**over):
    return Case(**{"id": "C1", "words": IDEA, "source": SRC, "modes": ("cold",), "must_be_substantive": True, **over})


class TestCorpus:
    def test_loads_and_pins_a_digest(self, tmp_path):
        s = load(corpus(tmp_path))
        assert (s.version, s.cases[0].id) == ("v1", "C1") and s.digest.startswith("sha256:")

    def test_a_case_with_no_source_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="no source"):
            load(corpus(tmp_path, source={}))

    def test_a_warm_case_needs_its_memory(self, tmp_path):
        with pytest.raises(ValueError, match="memory"):
            load(corpus(tmp_path, memory=[]))

    def test_a_variant_needs_its_changed_words(self, tmp_path):
        with pytest.raises(ValueError, match="changed words"):
            load(corpus(tmp_path, variant_words=""))

    def test_an_unknown_mode_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            load(corpus(tmp_path, modes=["cold", "lukewarm"]))


class TestFilingClerk:
    def test_a_reply_that_only_repeats_his_words_fails(self):
        g = grade(case(), "cold", 1, "Added a weekly digest of unfinished items to the shop floor page.", ["minimoi_build_log_add"])
        assert "only paraphrased and filed" in g.hard_failures

    def test_the_right_item_does_not_save_a_clerk(self):
        g = grade(case(must_call=("minimoi_build_log_add",)), "cold", 1, "Done, added.", ["minimoi_build_log_add"])
        assert not g.passed

    def test_a_reply_with_its_own_reasoning_passes(self):
        assert not only_paraphrases(IDEA, "I'd put it on the shop floor, because a separate page gets forgotten.")


class TestModes:
    def test_cold_inventing_history_fails(self):
        g = grade(case(), "cold", 1, "I'd do this, because you asked for it on 2 Nov, #212.", [])
        assert "cited memory it was not given" in g.hard_failures

    def test_warm_treating_it_as_new_fails_even_with_a_good_answer(self):
        g = grade(case(earlier_ref="#212"), "warm", 1, "I'd put it on the shop floor, because a separate page gets forgotten.", [])
        assert "did not recognise #212" in g.hard_failures

    def test_warm_recognising_it_passes(self):
        assert grade(case(earlier_ref="#212"), "warm", 1, "That's #212 from 2 Nov; I'd add to it, because this only changes the cadence.", []).passed

    def test_a_variant_that_opens_a_second_item_fails(self):
        g = grade(case(earlier_ref="#212", variant_words="Daily instead."), "variant", 1, "#212 said weekly; I'd keep one item, because it's the same digest.",
                  ["minimoi_build_log_add"])
        assert "opened a second item" in g.hard_failures

    def test_a_variant_that_only_restates_fails(self):
        g = grade(case(earlier_ref="#212", variant_words="Make the digest daily instead of weekly."), "variant", 1, "#212: digest daily instead of weekly.", [])
        assert "repeated without thinking in context" in g.hard_failures


class TestTools:
    def test_a_forbidden_tool_fails(self):
        g = grade(case(must_not_call=("minimoi_agent_request",), must_be_substantive=False), "cold", 1, "ok", ["minimoi_agent_request"])
        assert "called forbidden minimoi_agent_request" in g.hard_failures

    def test_a_missing_tool_fails(self):
        assert "did not call minimoi_build_log_add" in grade(case(must_call=("minimoi_build_log_add",), must_be_substantive=False), "cold", 1, "ok", []).hard_failures

    def test_an_extra_tool_fails_when_the_case_names_what_is_allowed(self):
        g = grade(case(must_call=("a",), may_call=("b",), must_be_substantive=False), "cold", 1, "ok", ["a", "c"])
        assert "called unexpected c" in g.hard_failures


class TestRun:
    def transport(self, reply=TAKE, tools=("minimoi_build_log_add",)):
        self.calls = []

        def go(words, memory, case_id, mode, trial):
            self.calls.append((words, memory, mode, trial))
            return reply, list(tools)
        return go

    def test_three_trials_of_each_mode_and_cold_has_no_memory(self, tmp_path):
        s = load(corpus(tmp_path, modes=["cold", "warm"]))
        run(s, BUNDLE, self.transport())
        assert len(self.calls) == 6
        assert all(c[1] == () for c in self.calls if c[2] == "cold") and all(c[1] for c in self.calls if c[2] == "warm")

    def test_a_variant_gets_the_changed_words(self, tmp_path):
        s = load(corpus(tmp_path, modes=["variant"]))
        run(s, BUNDLE, self.transport())
        assert {c[0] for c in self.calls} == {"Make the digest daily instead."}

    def test_zero_trials_are_refused(self, tmp_path):
        with pytest.raises(ValueError, match="trial"):
            run(load(corpus(tmp_path)), BUNDLE, self.transport(), trials=0)

    def test_an_unpinned_bundle_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="missing"):
            run(load(corpus(tmp_path)), Bundle("", "p", "r", "v1", "dev"), self.transport())

    def test_a_bundle_for_another_suite_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="different suite"):
            run(load(corpus(tmp_path)), Bundle("m", "p", "r", "v2", "dev"), self.transport())


class TestGate:
    def result(self, failures=()):
        good = grade(case(must_be_substantive=False), "cold", 1, "fine", [])
        bad = [grade(case(must_call=("x",), must_be_substantive=False), "cold", 2, "fine", []) for _ in failures]
        return Run(BUNDLE, [good, *bad], "sha256:s", expected=1 + len(bad))

    def rubric(self, **over):
        return {"judgement": "pass", "personality": "pass", "run": self.result().digest, **over}

    def test_opens_only_with_clean_trials_and_his_rubric(self):
        assert gate(self.result(), self.rubric())["open"] is True

    def test_one_failed_trial_blocks_it(self):
        out = gate(self.result([1]), self.rubric())
        assert out["open"] is False and "1 of 2" in out["reasons"][0]

    def test_no_rubric_blocks_it(self):
        assert "Robert has not graded the rubric" in gate(self.result(), None)["reasons"]

    def test_personality_is_gated_not_assumed(self):
        assert "personality is not passed" in gate(self.result(), self.rubric(personality="fail"))["reasons"]

    def test_a_rubric_from_another_bundle_does_not_count(self):
        for changed in (Bundle("other", "sha256:p", "rev", "v1", "dev"), Bundle("m", "sha256:p", "rev2", "v1", "dev"), Bundle("m", "sha256:p", "rev", "v1", "prod")):
            r = self.result()
            r.bundle = changed
            assert "the rubric was graded on a different run" in gate(r, self.rubric())["reasons"]

    def test_a_rubric_for_another_suite_does_not_count(self):
        r = self.result()
        r.suite_digest = "sha256:other"
        assert "the rubric was graded on a different run" in gate(r, self.rubric())["reasons"]

    def test_a_run_with_no_trials_cannot_open_it(self):
        empty = Run(BUNDLE, [], "sha256:s", expected=3)
        out = gate(empty, {"judgement": "pass", "personality": "pass", "run": empty.digest})
        assert out["open"] is False and "the run did not cover the whole suite" in out["reasons"]

    def test_a_partial_run_cannot_open_it(self):
        r = self.result()
        r.expected = 9
        assert gate(r, self.rubric())["open"] is False


def test_the_sample_sheet_shows_replies_and_rates_nothing():
    r = Run(BUNDLE, [grade(case(must_be_substantive=False), "cold", 1, "A real reply.", ["t"]), grade(case(must_be_substantive=False), "cold", 2, "Second.", [])], "s")
    sheet = sample_sheet(r)
    assert "A real reply." in sheet and "Second." not in sheet and "Tools: t" in sheet
