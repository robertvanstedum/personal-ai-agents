"""Reading the approved projection is authorized by membership, not by path.

Confinement under the subject's work directory only proves a path is inside
the tree. It does not prove Robert approved what is at the end of it. So the
read resolves the requested path against a freshly derived projection and
requires an exact eligible member whose pinned digest still matches. Knowing an
unapproved artifact's name buys nothing: the answer is ``not_found`` either
way.

The second half of this module fixes the identifier grammar, so a subject or a
root reference can never carry a separator, a traversal segment, a control
character or the reserved ``approved:`` prefix into a path or a root lookup.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domains.cos.work.confine import NotFound, PathDenied
from domains.cos.work.envelope import (
    IDENTIFIER_PATTERN,
    InvalidRequest,
    is_identifier,
)
from domains.cos.work.retrieval import Accumulation, approved_root_ref
from domains.cos.work.roots import (
    ENV_SOURCE_ROOTS,
    ENV_WORK_ROOT,
    load_root_configuration,
)

APPROVED = approved_root_ref("career")
WORK_DIR = Path("subjects") / "career" / "work"


def work_dir(root: Path, prefix: str) -> Path:
    return next(p for p in (root / WORK_DIR).iterdir() if p.name.startswith(prefix))


# -- membership is the authorization boundary --------------------------------


def test_earlier_unapproved_revision_is_not_found(
    career_accumulation: Accumulation, private_work_root: Path
):
    """revision 1 of approved work is not readable, though its path is real"""
    directory = work_dir(private_work_root, "approved-coauthored")
    superseded = f"{directory.name}/artifacts/0001-letter.md"
    assert (private_work_root / WORK_DIR / superseded).is_file()

    with pytest.raises(NotFound) as excinfo:
        career_accumulation.read_source("career", APPROVED, superseded)
    assert excinfo.value.code == "not_found"

    approved = f"{directory.name}/artifacts/0002-letter.md"
    assert career_accumulation.read_source("career", APPROVED, approved).content


def test_continuing_work_artifact_is_not_found(
    career_accumulation: Accumulation, private_work_root: Path
):
    """work still in progress is never readable through the approved view"""
    directory = work_dir(private_work_root, "continuing-draft")
    relative = f"{directory.name}/artifacts/0001-letter.md"
    assert (private_work_root / WORK_DIR / relative).is_file()
    with pytest.raises(NotFound):
        career_accumulation.read_source("career", APPROVED, relative)


def test_closed_without_approval_artifact_is_not_found(
    career_accumulation: Accumulation, private_work_root: Path
):
    """a draft for work Robert decided against is never readable"""
    directory = work_dir(private_work_root, "closed-do-not-apply")
    relative = f"{directory.name}/artifacts/0001-letter.md"
    assert (private_work_root / WORK_DIR / relative).is_file()
    with pytest.raises(NotFound):
        career_accumulation.read_source("career", APPROVED, relative)


def test_arbitrary_markdown_under_a_work_directory_is_not_found(
    career_accumulation: Accumulation, private_work_root: Path
):
    """a file that no record names at all is not readable"""
    directory = work_dir(private_work_root, "approved-coauthored")
    stray = directory / "artifacts" / "scratch.md"
    stray.write_text("a note nobody approved\n")
    notes = directory / "notes.md"
    notes.write_text("working notes\n")

    for relative in [f"{directory.name}/artifacts/scratch.md", f"{directory.name}/notes.md"]:
        with pytest.raises(NotFound):
            career_accumulation.read_source("career", APPROVED, relative)

    outcome = career_accumulation.search_sources("career", [APPROVED], "approved nobody notes")
    assert not any("scratch" in hit.relative_path for hit in outcome.hits)
    assert not any("notes.md" in hit.relative_path for hit in outcome.hits)


def test_valid_artifact_under_the_wrong_subject_is_refused(
    private_work_root: Path, career_sources: Path, decision_memo_sources: Path
):
    """an artifact approved under one subject is not readable under another"""
    document = {
        "career": {
            "resumes": {
                "path": str(career_sources / "resumes"),
                "context_class": "robert_source",
            }
        },
        "decision_memo": {
            "notes": {
                "path": str(decision_memo_sources / "notes"),
                "context_class": "robert_source",
            }
        },
    }
    accumulation = Accumulation(
        load_root_configuration(
            {ENV_WORK_ROOT: str(private_work_root), ENV_SOURCE_ROOTS: json.dumps(document)}
        )
    )
    directory = work_dir(private_work_root, "approved-coauthored")
    relative = f"{directory.name}/artifacts/0002-letter.md"

    assert accumulation.read_source("career", APPROVED, relative).content
    with pytest.raises(NotFound):
        accumulation.read_source(
            "decision_memo", approved_root_ref("decision_memo"), relative
        )
    with pytest.raises(InvalidRequest):
        accumulation.read_source("decision_memo", APPROVED, relative)


def test_approved_read_refuses_traversal_and_absolute_paths(
    career_accumulation: Accumulation,
):
    """the approved view is not a way around confinement"""
    for candidate in ["../../../etc/hosts", "a/../../b.md", "/etc/hosts"]:
        with pytest.raises(PathDenied):
            career_accumulation.read_source("career", APPROVED, candidate)


def test_approved_read_requires_the_pinned_digest(
    career_accumulation: Accumulation, private_work_root: Path
):
    """membership alone is not enough — the digest must still match"""
    from domains.cos.work.records import StaleContext

    directory = work_dir(private_work_root, "approved-agent-draft")
    relative = f"{directory.name}/artifacts/0001-letter.md"
    assert career_accumulation.read_source("career", APPROVED, relative).content

    target = private_work_root / WORK_DIR / relative
    target.write_text("swapped for something else entirely\n")
    with pytest.raises(StaleContext) as excinfo:
        career_accumulation.read_source("career", APPROVED, relative)
    assert excinfo.value.code == "stale_context"


# -- the closed identifier grammar -------------------------------------------


def test_identifier_grammar_shape():
    """the grammar is exactly the documented one"""
    assert IDENTIFIER_PATTERN.pattern == r"^[a-z0-9][a-z0-9_-]{0,63}$"
    for good in ["career", "decision_memo", "other-responses", "a", "0", "a" * 64]:
        assert is_identifier(good) is True
    for bad in [
        "",
        " ",
        "..",
        ".",
        "a/b",
        "a\\b",
        "/career",
        "career/",
        "Career",
        "career ",
        "care er",
        "-career",
        "_career",
        "approved:career",
        "caree\0r",
        "caree\nr",
        "a" * 65,
        None,
        7,
    ]:
        assert is_identifier(bad) is False


@pytest.mark.parametrize(
    "subject",
    ["", " ", "..", "a/b", "../career", "Career", "care\0er", "care\ner", "approved:career"],
)
def test_bad_subject_is_invalid_request(career_accumulation: Accumulation, subject):
    """a subject outside the grammar never reaches the filesystem"""
    with pytest.raises(InvalidRequest) as excinfo:
        career_accumulation.search_sources(subject, None, "reconciliation")
    assert excinfo.value.code == "invalid_request"
    with pytest.raises(InvalidRequest):
        career_accumulation.read_source(subject, "resumes", "current-resume.md")


@pytest.mark.parametrize(
    "root_ref", ["..", "a/b", "../resumes", "Resumes", "resu\0mes", "resu\nmes", "-resumes"]
)
def test_bad_root_ref_is_invalid_request(career_accumulation: Accumulation, root_ref):
    """a root reference outside the grammar never reaches the filesystem"""
    with pytest.raises(InvalidRequest):
        career_accumulation.search_sources("career", [root_ref], "reconciliation")
    with pytest.raises(InvalidRequest):
        career_accumulation.read_source("career", root_ref, "current-resume.md")


def test_approved_prefix_of_another_subject_is_invalid_request(
    career_accumulation: Accumulation,
):
    """approved:<other-subject> is a mismatch, not a lookup"""
    for ref in ["approved:decision_memo", "approved:", "approved:../career", "approved:Career"]:
        with pytest.raises(InvalidRequest):
            career_accumulation.search_sources("career", [ref], "reconciliation")
        with pytest.raises(InvalidRequest):
            career_accumulation.read_source("career", ref, "a/b.md")


def test_configured_root_may_not_claim_the_approved_prefix(career_sources: Path):
    """a deployment cannot declare a root that shadows the projection"""
    document = {
        "career": {
            "approved:career": {
                "path": str(career_sources / "resumes"),
                "context_class": "robert_source",
            }
        }
    }
    config = load_root_configuration({ENV_SOURCE_ROOTS: json.dumps(document)})
    assert config.root_refs("career") == ()
    assert [issue.code for issue in config.source_root_issues] == ["source_root_unavailable"]
    assert "approved prefix" in config.source_root_issues[0].reason


@pytest.mark.parametrize("ref", ["../escape", "a/b", "Resumes", "", "..", "resu mes"])
def test_configured_root_reference_outside_the_grammar_is_dropped(
    career_sources: Path, ref: str
):
    """a badly named configured root is dropped and reported"""
    document = {
        "career": {
            ref: {"path": str(career_sources / "resumes"), "context_class": "robert_source"}
        }
    }
    config = load_root_configuration({ENV_SOURCE_ROOTS: json.dumps(document)})
    assert config.root_refs("career") == ()
    assert config.source_root_issues
    assert "not a valid name" in config.source_root_issues[0].reason


def test_configured_subject_outside_the_grammar_is_dropped(career_sources: Path):
    """a badly named subject takes no roots with it into service"""
    document = {
        "Career/../etc": {
            "resumes": {
                "path": str(career_sources / "resumes"),
                "context_class": "robert_source",
            }
        }
    }
    config = load_root_configuration({ENV_SOURCE_ROOTS: json.dumps(document)})
    assert config.root_refs("Career/../etc") == ()
    assert config.source_root_issues
    assert "subject is not a valid name" in config.source_root_issues[0].reason
