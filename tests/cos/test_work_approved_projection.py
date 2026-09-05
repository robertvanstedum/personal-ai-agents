"""The subject-scoped approved-output projection.

This is where the n+1 promise is either kept or quietly broken: approved and
co-authored work must be retrievable for the next related work, and an
unreviewed draft must never be presented as though Robert stood behind it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domains.cos.work.records import StaleContext
from domains.cos.work.retrieval import (
    APPROVED_ROOT_PREFIX,
    Accumulation,
    approved_root_ref,
)
from domains.cos.work.roots import RootUnavailable, load_root_configuration

APPROVED = approved_root_ref("career")
WORK_DIR = Path("subjects") / "career" / "work"


def work_dir(root: Path, prefix: str) -> Path:
    return next(p for p in (root / WORK_DIR).iterdir() if p.name.startswith(prefix))


def test_projection_returns_only_disposition_pinned_artifacts(
    career_accumulation: Accumulation,
):
    """exactly the artifacts an approved_text disposition names"""
    items, issues = career_accumulation.approved_artifacts("career")
    assert issues == ()
    assert len(items) == 2
    assert {item.relative_path.rsplit("/", 1)[-1] for item in items} == {
        "0001-letter.md",
        "0002-letter.md",
    }
    assert all(item.disposition.work_id == item.work_id for item in items)


def test_projection_preserves_coauthored_output_class(career_accumulation: Accumulation):
    """an approved co-authored output keeps its class"""
    items, _ = career_accumulation.approved_artifacts("career")
    coauthored = [item for item in items if item.relative_path.startswith("approved-coauthored")]
    assert len(coauthored) == 1
    assert coauthored[0].context_class == "coauthored_output"


def test_projection_preserves_agent_draft_class_when_approved(
    career_accumulation: Accumulation,
):
    """an approved agent draft stays an agent draft

    Approval changes reuse eligibility, not authorship.
    """
    items, _ = career_accumulation.approved_artifacts("career")
    drafts = [item for item in items if item.relative_path.startswith("approved-agent-draft")]
    assert len(drafts) == 1
    assert drafts[0].context_class == "agent_draft"


def test_projection_excludes_unapproved_artifact_of_approved_work(
    career_accumulation: Accumulation,
):
    """earlier revisions of approved work are not exposed"""
    items, _ = career_accumulation.approved_artifacts("career")
    paths = {item.relative_path for item in items}
    assert not any(
        path.startswith("approved-coauthored") and path.endswith("0001-letter.md")
        for path in paths
    )
    # Both revisions open "Dear Quayside team" and the superseded draft names
    # the same employer, so a query that matches revision 1 as readily as
    # revision 2 proves the projection pins one artifact rather than a folder.
    outcome = career_accumulation.search_sources("career", [APPROVED], "quayside")
    assert outcome.hits
    assert all(hit.relative_path.endswith("0002-letter.md") for hit in outcome.hits)
    assert not any("0001-letter.md" in hit.relative_path for hit in outcome.hits)


def test_projection_excludes_continuing_work(career_accumulation: Accumulation):
    """continuing work is never projected"""
    items, _ = career_accumulation.approved_artifacts("career")
    assert not any(item.relative_path.startswith("continuing-") for item in items)


def test_projection_excludes_closed_work_without_approval(career_accumulation: Accumulation):
    """a closed 'do not apply' work is never projected"""
    items, _ = career_accumulation.approved_artifacts("career")
    assert not any(item.relative_path.startswith("closed-") for item in items)
    assert career_accumulation.search_sources("career", [APPROVED], "withdrawn").hits == ()


def test_projection_carries_disposition_reference(career_accumulation: Accumulation):
    """each item names work_id, operation_id and decision time"""
    items, _ = career_accumulation.approved_artifacts("career")
    for item in items:
        assert item.disposition.work_id
        assert item.disposition.operation_id
        assert item.disposition.decided_at.endswith("Z")


def test_search_approved_root_finds_approved_language(career_accumulation: Accumulation):
    """approved language is retrievable for the next work"""
    outcome = career_accumulation.search_sources("career", [APPROVED], "quayside reconciliation")
    assert outcome.hits
    assert all(hit.root_ref.startswith(APPROVED_ROOT_PREFIX) for hit in outcome.hits)
    assert all(hit.disposition is not None for hit in outcome.hits)
    assert {hit.context_class for hit in outcome.hits} <= {"coauthored_output", "agent_draft"}


def test_search_approved_root_never_returns_provisional_draft(
    career_accumulation: Accumulation,
):
    """an unreviewed draft never masquerades as approved"""
    assert career_accumulation.search_sources("career", [APPROVED], "provisional").hits == ()
    everywhere = career_accumulation.search_sources("career", None, "provisional")
    assert everywhere.hits == ()


def test_read_approved_artifact_returns_original_class(career_accumulation: Accumulation):
    """reading preserves authorship"""
    items, _ = career_accumulation.approved_artifacts("career")
    for item in items:
        outcome = career_accumulation.read_source("career", APPROVED, item.relative_path)
        assert outcome.context_class == item.context_class
        assert outcome.sha256 == item.sha256
        assert outcome.disposition == item.disposition
        assert outcome.content


def test_stale_artifact_hash_is_reported_not_silently_returned(
    career_accumulation: Accumulation, private_work_root: Path
):
    """a changed artifact is reported as stale_context in search"""
    directory = work_dir(private_work_root, "approved-coauthored")
    target = directory / "artifacts" / "0002-letter.md"
    target.write_text(target.read_text() + "\nEdited outside the system.\n")

    items, issues = career_accumulation.approved_artifacts("career")
    assert [issue.code for issue in issues] == ["stale_context"]
    assert issues[0].relative_path.startswith("approved-coauthored")
    assert not any(item.relative_path.startswith("approved-coauthored") for item in items)

    outcome = career_accumulation.search_sources("career", [APPROVED], "quayside")
    assert outcome.hits == ()
    assert [issue.code for issue in outcome.issues] == ["stale_context"]


def test_stale_artifact_hash_on_read_raises_stale_context(
    career_accumulation: Accumulation, private_work_root: Path
):
    """a changed artifact is refused on read"""
    directory = work_dir(private_work_root, "approved-coauthored")
    relative = f"{directory.name}/artifacts/0002-letter.md"
    (directory / "artifacts" / "0002-letter.md").write_text("replaced entirely\n")
    with pytest.raises(StaleContext) as excinfo:
        career_accumulation.read_source("career", APPROVED, relative)
    assert excinfo.value.code == "stale_context"
    assert excinfo.value.relative_path == relative


def test_projection_exposes_no_list_all_work_operation(career_accumulation: Accumulation):
    """there is no list operation on the public surface"""
    surface = {name for name in dir(career_accumulation) if not name.startswith("_")}
    assert surface == {
        "approved_artifacts",
        "available_root_refs",
        "configuration",
        "read_source",
        "search_sources",
    }
    assert not any("list" in name for name in surface)


def test_projection_unavailable_without_work_root(career_env, career_sources: Path):
    """no write root means no projection, and sources still read"""
    from domains.cos.work.roots import ENV_WORK_ROOT

    env = dict(career_env)
    env.pop(ENV_WORK_ROOT)
    accumulation = Accumulation(load_root_configuration(env))

    assert APPROVED not in accumulation.available_root_refs("career")
    with pytest.raises(RootUnavailable):
        accumulation.approved_artifacts("career")
    with pytest.raises(RootUnavailable):
        accumulation.search_sources("career", [APPROVED], "quayside")

    assert accumulation.search_sources("career", None, "reconciliation").hits


def test_projection_ignores_malformed_work_record(
    career_accumulation: Accumulation, private_work_root: Path
):
    """an invalid record is reported, not trusted"""
    directory = work_dir(private_work_root, "approved-agent-draft")
    data = json.loads((directory / "work.json").read_text())
    data["subject_extension"] = {"namespace": "career", "data": {}}
    (directory / "work.json").write_text(json.dumps(data))

    items, issues = career_accumulation.approved_artifacts("career")
    assert [issue.code for issue in issues] == ["invalid_request"]
    assert issues[0].relative_path.endswith("work.json")
    assert not any(item.relative_path.startswith("approved-agent-draft") for item in items)
    assert len(items) == 1


def test_approved_root_of_another_subject_is_refused(career_accumulation: Accumulation):
    """the approved view is scoped to one subject"""
    from domains.cos.work.envelope import InvalidRequest

    with pytest.raises(InvalidRequest):
        career_accumulation.search_sources("career", ["approved:decision_memo"], "budget")
    with pytest.raises(InvalidRequest):
        career_accumulation.read_source("career", "approved:decision_memo", "a/b.md")
