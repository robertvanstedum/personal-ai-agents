"""Search costs are bounded, and a partial answer says so.

Result and excerpt ceilings bound what comes back. They say nothing about the
work done to produce it: a loose root with tens of thousands of files would
still be walked to the end. So the traversal itself has a budget — files
examined and bytes examined — alongside limits on the query.

A budget that runs out does not raise. The search succeeds with whatever it
found and reports a content-free ``search_truncated`` issue against the root it
could not finish, so a partial answer is never mistaken for a complete one.
That is a deliberate choice: a caller asking about their own writing is better
served by "here is what I found, and I did not get to the end" than by an
error. Every caller-supplied limit, in contrast, is validated and refused when
it is out of range — never silently clamped.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from domains.cos.work.envelope import SEARCH_TRUNCATED, InvalidRequest
from domains.cos.work.retrieval import (
    DEFAULT_MAX_BYTES_EXAMINED,
    DEFAULT_MAX_FILES_EXAMINED,
    MAX_BYTES_EXAMINED_CEILING,
    MAX_FILES_EXAMINED_CEILING,
    MAX_QUERY_CHARS,
    MAX_QUERY_TOKENS,
    Accumulation,
    approved_root_ref,
)
from domains.cos.work.roots import ENV_SOURCE_ROOTS, ENV_WORK_ROOT, load_root_configuration

APPROVED = approved_root_ref("career")


@pytest.fixture
def wide_root(workspace: Path, private_work_root: Path) -> Accumulation:
    """A root with many small files, all matching the same word."""
    directory = workspace / "wide"
    directory.mkdir()
    os.chmod(directory, 0o700)
    for index in range(40):
        note = directory / f"note-{index:03d}.md"
        note.write_text(f"reconciliation appears in note {index}\n")
        os.chmod(note, 0o600)
    declaration = (
        '{"career": {"wide": {"path": "%s", "context_class": "robert_source"}}}' % directory
    )
    return Accumulation(
        load_root_configuration(
            {ENV_WORK_ROOT: str(private_work_root), ENV_SOURCE_ROOTS: declaration}
        )
    )


# -- defaults ----------------------------------------------------------------


def test_budget_defaults_are_deterministic():
    """the budget is a stated number, not a heuristic"""
    assert DEFAULT_MAX_FILES_EXAMINED == 2_000
    assert DEFAULT_MAX_BYTES_EXAMINED == 32 * 1024 * 1024
    assert MAX_FILES_EXAMINED_CEILING == 20_000
    assert MAX_BYTES_EXAMINED_CEILING == 256 * 1024 * 1024
    assert MAX_QUERY_CHARS == 256
    assert MAX_QUERY_TOKENS == 16


def test_an_ordinary_search_is_not_truncated(career_accumulation: Accumulation):
    """the budget is invisible when it is not reached"""
    outcome = career_accumulation.search_sources("career", None, "reconciliation")
    assert outcome.hits
    assert outcome.truncated is False
    assert not any(issue.code == SEARCH_TRUNCATED for issue in outcome.issues)


# -- the query itself --------------------------------------------------------


def test_query_longer_than_the_limit_is_refused(career_accumulation: Accumulation):
    """an oversized query never starts a walk"""
    query = "reconciliation " * 40
    assert len(query) > MAX_QUERY_CHARS
    with pytest.raises(InvalidRequest) as excinfo:
        career_accumulation.search_sources("career", None, query)
    assert excinfo.value.code == "invalid_request"
    assert career_accumulation.search_sources("career", None, "a" * MAX_QUERY_CHARS) is not None


def test_query_with_too_many_distinct_words_is_refused(career_accumulation: Accumulation):
    """token count is bounded, so match work per line is bounded"""
    too_many = " ".join(f"word{index}" for index in range(MAX_QUERY_TOKENS + 1))
    with pytest.raises(InvalidRequest):
        career_accumulation.search_sources("career", None, too_many)

    just_enough = " ".join(f"word{index}" for index in range(MAX_QUERY_TOKENS))
    assert career_accumulation.search_sources("career", None, just_enough).hits == ()


def test_repeated_words_do_not_count_twice(career_accumulation: Accumulation):
    """the limit is on distinct tokens, which is what costs anything"""
    repeated = " ".join(["throughput"] * (MAX_QUERY_TOKENS + 5))
    assert len(repeated) <= MAX_QUERY_CHARS
    assert career_accumulation.search_sources("career", None, repeated).hits


def test_non_text_query_is_refused(career_accumulation: Accumulation):
    """a query must be text"""
    for bad in [None, 7, ["reconciliation"]]:
        with pytest.raises(InvalidRequest):
            career_accumulation.search_sources("career", None, bad)


# -- caller-supplied limits are validated, never clamped ---------------------


@pytest.mark.parametrize("name", ["max_files_examined", "max_bytes_examined"])
@pytest.mark.parametrize("value", [0, -1, 1.5, True, False, "2000", None])
def test_budget_limits_must_be_positive_whole_numbers(
    career_accumulation: Accumulation, name: str, value
):
    """a limit that is not a positive integer is refused"""
    with pytest.raises(InvalidRequest) as excinfo:
        career_accumulation.search_sources("career", None, "reconciliation", **{name: value})
    assert excinfo.value.code == "invalid_request"


def test_budget_limits_above_the_ceiling_are_refused_not_clamped(
    career_accumulation: Accumulation,
):
    """asking for more than the ceiling fails rather than quietly shrinking"""
    with pytest.raises(InvalidRequest):
        career_accumulation.search_sources(
            "career", None, "reconciliation", max_files_examined=MAX_FILES_EXAMINED_CEILING + 1
        )
    with pytest.raises(InvalidRequest):
        career_accumulation.search_sources(
            "career", None, "reconciliation", max_bytes_examined=MAX_BYTES_EXAMINED_CEILING + 1
        )


# -- reaching a budget -------------------------------------------------------


def test_file_budget_reports_a_content_free_truncation(wide_root: Accumulation):
    """a walk cut short says so, and says nothing else"""
    outcome = wide_root.search_sources(
        "career", ["wide"], "reconciliation", max_files_examined=5
    )
    assert outcome.hits
    assert outcome.truncated is True
    truncations = [issue for issue in outcome.issues if issue.code == SEARCH_TRUNCATED]
    assert len(truncations) == 1
    assert truncations[0].root_ref == "wide"
    assert truncations[0].relative_path == ""
    assert "reconciliation" not in truncations[0].message
    assert "note-" not in truncations[0].message


def test_byte_budget_reports_a_content_free_truncation(wide_root: Accumulation):
    """the bytes ceiling stops the walk the same way"""
    outcome = wide_root.search_sources(
        "career", ["wide"], "reconciliation", max_bytes_examined=60
    )
    assert outcome.truncated is True
    assert [issue.code for issue in outcome.issues] == [SEARCH_TRUNCATED]


def test_truncation_is_reported_per_root(wide_root: Accumulation, workspace: Path):
    """each root gets its own allowance, and its own notice"""
    outcome = wide_root.search_sources(
        "career", ["wide", APPROVED], "reconciliation", max_files_examined=1
    )
    truncated_refs = {
        issue.root_ref for issue in outcome.issues if issue.code == SEARCH_TRUNCATED
    }
    assert "wide" in truncated_refs


def test_truncated_results_remain_deterministic(wide_root: Accumulation):
    """a cut-short walk returns the same thing every time"""
    def run():
        outcome = wide_root.search_sources(
            "career", ["wide"], "reconciliation", max_files_examined=7
        )
        return [(hit.relative_path, hit.line_start) for hit in outcome.hits]

    first = run()
    assert first
    for _ in range(4):
        assert run() == first


def test_search_truncated_is_a_notice_not_an_error_code():
    """the operation succeeded; only its completeness is in question"""
    from domains.cos.work.envelope import ERROR_CODES, ISSUE_CODES

    assert SEARCH_TRUNCATED == "search_truncated"
    assert SEARCH_TRUNCATED not in ERROR_CODES
    assert SEARCH_TRUNCATED in ISSUE_CODES
    assert ERROR_CODES < ISSUE_CODES


def test_every_issue_code_belongs_to_the_closed_vocabulary(
    career_accumulation: Accumulation, private_work_root: Path, wide_root: Accumulation
):
    """issues carry codes from one closed set, like errors do"""
    from domains.cos.work.envelope import ISSUE_CODES

    target = next(
        (private_work_root / "subjects" / "career" / "work").glob(
            "approved-coauthored*/artifacts/0002-letter.md"
        )
    )
    target.write_text(target.read_text() + "\nedited outside the system\n")

    outcomes = [
        career_accumulation.search_sources("career", [APPROVED], "quayside"),
        wide_root.search_sources("career", ["wide"], "reconciliation", max_files_examined=2),
    ]
    codes = {issue.code for outcome in outcomes for issue in outcome.issues}
    assert codes
    assert codes <= ISSUE_CODES
