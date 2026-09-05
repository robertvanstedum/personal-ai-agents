"""Anti-overfitting: a second subject needs no code.

The first real slice is Career, but the foundation has to stay fit for a
decision memo written from two local text files. If that requires a branch
anywhere in the common package, the boundary is in the wrong place.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from domains.cos.work.envelope import InvalidRequest
from domains.cos.work.records import FORBIDDEN_FIELDS
from domains.cos.work.retrieval import Accumulation, approved_root_ref
from domains.cos.work.roots import (
    ENV_SOURCE_ROOTS,
    ENV_WORK_ROOT,
    load_root_configuration,
)


def declare(mapping: dict, default_class: str = "robert_source") -> str:
    """Render a deployment declaration; every root carries a provenance class.

    A value may be a plain path — which takes ``default_class`` — or a
    ``(path, context_class)`` pair, so a test can declare any of the four
    classes without a second helper.
    """
    document: dict[str, dict[str, dict[str, str]]] = {}
    for subject, refs in mapping.items():
        entries: dict[str, dict[str, str]] = {}
        for ref, value in refs.items():
            path, context_class = value if isinstance(value, tuple) else (value, default_class)
            entries[ref] = {"path": str(path), "context_class": context_class}
        document[subject] = entries
    return json.dumps(document)


@pytest.fixture
def both_subjects(
    private_work_root: Path, career_sources: Path, decision_memo_sources: Path
) -> Accumulation:
    """One configuration, two subjects, no code between them."""
    declared = declare(
        {
            "career": {
                "resumes": career_sources / "resumes",
                "other-responses": career_sources / "other-responses",
                "base-letters": career_sources / "base-letters",
            },
            "decision_memo": {"notes": decision_memo_sources / "notes"},
        }
    )
    configuration = load_root_configuration(
        {ENV_WORK_ROOT: str(private_work_root), ENV_SOURCE_ROOTS: declared}
    )
    assert configuration.source_root_issues == ()
    return Accumulation(configuration)


def test_decision_memo_uses_same_search_with_different_roots(both_subjects: Accumulation):
    """the same service serves a different subject"""
    memo = both_subjects.search_sources("decision_memo", ["notes"], "migration support")
    assert memo.hits
    assert {hit.root_ref for hit in memo.hits} == {"notes"}
    assert {hit.context_class for hit in memo.hits} == {"robert_source"}

    read = both_subjects.read_source("decision_memo", "notes", "vendor-notes.txt")
    assert "Candidate A" in read.content
    assert read.sha256

    career = both_subjects.search_sources("career", ["resumes"], "throughput")
    assert career.hits
    assert type(memo) is type(career)


def test_decision_memo_guidance_words_differ_from_career(
    career_sources: Path, decision_memo_sources: Path
):
    """the fixtures share no guidance vocabulary"""
    def vocabulary(root: Path) -> set[str]:
        words: set[str] = set()
        for path in sorted(root.rglob("*")):
            if path.is_file():
                words.update(re.findall(r"[a-z]{4,}", path.read_text("utf-8").casefold()))
        return words

    career_words = vocabulary(career_sources)
    memo_words = vocabulary(decision_memo_sources)

    career_guidance = {"throughput", "warehouse", "carriers", "freight"}
    memo_guidance = {"budget", "migration", "renewal", "incumbent"}
    assert career_guidance <= career_words
    assert memo_guidance <= memo_words
    assert career_guidance & memo_words == set()
    assert memo_guidance & career_words == set()


def test_decision_memo_needs_no_subject_extension(both_subjects: Accumulation):
    """no per-subject record container is required"""
    assert "subject_extension" in FORBIDDEN_FIELDS
    outcome = both_subjects.search_sources("decision_memo", None, "defer")
    assert outcome.hits
    assert outcome.issues == ()

    from domains.cos.work import records

    for module_source in [records.__doc__ or ""]:
        assert "namespace" not in module_source


def test_decision_memo_subject_has_no_dedicated_code_path(both_subjects: Accumulation):
    """no branch anywhere depends on the subject name"""
    package = Path(__file__).resolve().parents[2] / "domains" / "cos" / "work"
    for path in sorted(package.rglob("*.py")):
        source = path.read_text("utf-8")
        assert "decision_memo" not in source
        assert '"career"' not in source
        assert "'career'" not in source

    for subject, root_ref, term in [
        ("career", "resumes", "throughput"),
        ("decision_memo", "notes", "budget"),
    ]:
        outcome = both_subjects.search_sources(subject, [root_ref], term)
        assert outcome.hits
        assert all(hit.subject == subject for hit in outcome.hits)


def test_decision_memo_projection_empty_without_approved_work(both_subjects: Accumulation):
    """a subject with no approved work projects nothing"""
    items, issues = both_subjects.approved_artifacts("decision_memo")
    assert items == ()
    assert issues == ()

    outcome = both_subjects.search_sources(
        "decision_memo", [approved_root_ref("decision_memo")], "budget"
    )
    assert outcome.hits == ()

    with pytest.raises(InvalidRequest):
        both_subjects.search_sources("decision_memo", [approved_root_ref("career")], "budget")
