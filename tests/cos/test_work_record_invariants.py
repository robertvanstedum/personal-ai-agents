"""Cross-field invariants a canonical record must satisfy.

Field-by-field validation is not enough. What matters is whether the record's
parts agree with each other: an approval must name an artifact that exists on a
record whose own state says approved; a decision not to apply must be
recordable before anything was written; references, revisions and paths must be
unambiguous; and digests must have the exact shape a pin requires.

A record that fails any of these is reported and skipped. It never becomes
readable through the approved projection — a malformed record is not a reason
to trust its claims, it is a reason to trust none of them.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from domains.cos.work.records import (
    ARTIFACTS_DIRNAME,
    SHA256_PATTERN,
    SOURCES_DIRNAME,
    RecordInvalid,
    load_work_record,
    parse_work_record,
)
from domains.cos.work.retrieval import Accumulation, approved_root_ref

WORK_DIR = Path("subjects") / "career" / "work"
APPROVED = approved_root_ref("career")


def work_dir(root: Path, prefix: str) -> Path:
    return next(p for p in (root / WORK_DIR).iterdir() if p.name.startswith(prefix))


@pytest.fixture
def approved_data(private_work_root: Path) -> dict:
    return json.loads((work_dir(private_work_root, "approved-coauthored") / "work.json").read_text())


@pytest.fixture
def continuing_data(private_work_root: Path) -> dict:
    return json.loads((work_dir(private_work_root, "continuing-draft") / "work.json").read_text())


def rewrite(directory: Path, data: dict) -> None:
    (directory / "work.json").write_text(json.dumps(data))


# -- the disposition timestamp is decided_at ---------------------------------


def test_disposition_uses_decided_at(approved_data: dict):
    """the field is decided_at, and the old name is not accepted"""
    record = parse_work_record(approved_data)
    assert record.disposition.decided_at == "2026-08-13T17:02:41Z"
    assert not hasattr(record.disposition, "at")

    data = copy.deepcopy(approved_data)
    data["disposition"]["at"] = data["disposition"].pop("decided_at")
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert "not part of the contract: at" in excinfo.value.message


# -- approval requires an artifact and an agreeing state ---------------------


def test_approved_disposition_requires_a_named_artifact(approved_data: dict):
    """an approval with no artifact reference is refused"""
    for value in [None, ""]:
        data = copy.deepcopy(approved_data)
        data["disposition"]["artifact_ref"] = value
        with pytest.raises(RecordInvalid) as excinfo:
            parse_work_record(data)
        assert "exact artifact" in excinfo.value.message


def test_approved_disposition_requires_an_artifact_that_exists(approved_data: dict):
    """an approval naming an absent artifact is refused"""
    data = copy.deepcopy(approved_data)
    data["disposition"]["artifact_ref"] = "art-9999"
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert "does not have" in excinfo.value.message


def test_approved_disposition_requires_a_matching_top_level_state(approved_data: dict):
    """a record that is not approved cannot carry an approval"""
    for state in ["continuing", "closed", "unresolved"]:
        data = copy.deepcopy(approved_data)
        data["state"] = state
        with pytest.raises(RecordInvalid) as excinfo:
            parse_work_record(data)
        assert "denies" in excinfo.value.message


def test_continuing_work_never_projects_an_approval(
    career_accumulation: Accumulation, private_work_root: Path, continuing_data: dict
):
    """a disposition cannot approve work the record says is still in progress"""
    directory = work_dir(private_work_root, "continuing-draft")
    data = copy.deepcopy(continuing_data)
    data["disposition"] = {
        "state": "approved_text",
        "decided_at": "2026-08-27T15:00:00Z",
        "artifact_ref": "art-0001",
        "reason": "a claim the record's own state contradicts",
        "operation_id": "5d9a4b31-8c07-4e62-9f14-6b2d7ac30e58",
    }
    rewrite(directory, data)

    items, issues = career_accumulation.approved_artifacts("career")
    assert not any(item.relative_path.startswith("continuing-") for item in items)
    assert [issue.code for issue in issues] == ["invalid_request"]

    relative = f"{directory.name}/artifacts/0001-letter.md"
    from domains.cos.work.confine import NotFound

    with pytest.raises(NotFound):
        career_accumulation.read_source("career", APPROVED, relative)


# -- closed work may have no artifact at all ---------------------------------


def test_closed_work_may_record_a_null_artifact_ref(private_work_root: Path):
    """'do not apply' can happen before a single letter is drafted"""
    record = load_work_record(work_dir(private_work_root, "closed-before-any-draft") / "work.json")
    assert record.state == "closed"
    assert record.artifacts == ()
    assert record.disposition.artifact_ref is None
    assert record.disposition.reason == "do not apply, nothing drafted"
    assert record.approved_artifact_ref is None


def test_closed_work_without_a_draft_projects_nothing(career_accumulation: Accumulation):
    """it is a valid record that simply has nothing to offer"""
    items, issues = career_accumulation.approved_artifacts("career")
    assert issues == ()
    assert not any(item.relative_path.startswith("closed-") for item in items)


def test_non_approval_disposition_still_needs_a_real_artifact(approved_data: dict):
    """a closed disposition may name nothing, but not something absent"""
    data = copy.deepcopy(approved_data)
    data["state"] = "closed"
    data["disposition"]["state"] = "closed"
    data["disposition"]["artifact_ref"] = None
    assert parse_work_record(data).disposition.artifact_ref is None

    data["disposition"]["artifact_ref"] = "art-9999"
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert "does not have" in excinfo.value.message


# -- uniqueness --------------------------------------------------------------


def test_artifact_references_must_be_unique(approved_data: dict):
    """two artifacts cannot answer to the same reference"""
    data = copy.deepcopy(approved_data)
    data["artifacts"][1]["ref"] = "art-0001"
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert "share a reference" in excinfo.value.message


def test_artifact_revisions_must_be_unique(approved_data: dict):
    """two artifacts cannot claim the same revision number"""
    data = copy.deepcopy(approved_data)
    data["artifacts"][1]["revision"] = 1
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert "share a revision" in excinfo.value.message


def test_artifact_paths_must_be_unique(approved_data: dict):
    """two artifacts cannot point at the same file"""
    data = copy.deepcopy(approved_data)
    data["artifacts"][1]["path"] = data["artifacts"][0]["path"]
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert "share a path" in excinfo.value.message


def test_source_references_and_paths_must_be_unique(approved_data: dict):
    """the same rule holds for captured sources"""
    data = copy.deepcopy(approved_data)
    data["sources"].append(copy.deepcopy(data["sources"][0]))
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert "share a" in excinfo.value.message


# -- subtree discipline ------------------------------------------------------


def test_artifact_paths_must_live_under_artifacts(approved_data: dict):
    """an artifact recorded outside artifacts/ is refused"""
    assert ARTIFACTS_DIRNAME == "artifacts"
    for bad in ["sources/0002-letter.md", "0002-letter.md", "notes/0002-letter.md", "../x.md"]:
        data = copy.deepcopy(approved_data)
        data["artifacts"][1]["path"] = bad
        with pytest.raises(RecordInvalid):
            parse_work_record(data)


def test_source_paths_must_live_under_sources(approved_data: dict):
    """a source recorded outside sources/ is refused"""
    assert SOURCES_DIRNAME == "sources"
    for bad in ["artifacts/0001-posting.txt", "0001-posting.txt", "/etc/hosts", "../x.txt"]:
        data = copy.deepcopy(approved_data)
        data["sources"][0]["path"] = bad
        with pytest.raises(RecordInvalid):
            parse_work_record(data)


def test_valid_subtree_paths_are_accepted(approved_data: dict):
    """the positive case: nested paths inside the right subtree are fine"""
    data = copy.deepcopy(approved_data)
    data["artifacts"][1]["path"] = "artifacts/revised/0002-letter.md"
    record = parse_work_record(data)
    assert record.artifact("art-0002").path == "artifacts/revised/0002-letter.md"


# -- digest shape ------------------------------------------------------------


def test_sha256_must_have_the_exact_shape(approved_data: dict):
    """a pin is 64 lowercase hex characters, and nothing else"""
    assert SHA256_PATTERN.pattern == r"^[0-9a-f]{64}$"
    good = approved_data["artifacts"][1]["sha256"]
    assert SHA256_PATTERN.fullmatch(good)
    for bad in [
        good.upper(),
        good[:-1],
        good + "a",
        good[:-1] + "g",
        f"sha256:{good}",
        f"{good}\n",
        "",
    ]:
        data = copy.deepcopy(approved_data)
        data["artifacts"][1]["sha256"] = bad
        with pytest.raises(RecordInvalid) as excinfo:
            parse_work_record(data)
        assert "sha256" in excinfo.value.message


# -- declared byte counts ----------------------------------------------------


def test_declared_bytes_must_match_the_stored_file(
    career_accumulation: Accumulation, private_work_root: Path, approved_data: dict
):
    """a record that misstates a file's size is reported, not trusted"""
    directory = work_dir(private_work_root, "approved-coauthored")
    data = copy.deepcopy(approved_data)
    data["artifacts"][1]["bytes"] = data["artifacts"][1]["bytes"] + 7
    rewrite(directory, data)

    items, issues = career_accumulation.approved_artifacts("career")
    assert not any(item.relative_path.startswith("approved-coauthored") for item in items)
    assert [issue.code for issue in issues] == ["invalid_request"]
    assert "recorded size" in issues[0].message


def test_declared_bytes_may_be_omitted(
    career_accumulation: Accumulation, private_work_root: Path, approved_data: dict
):
    """the field is optional; when absent nothing is claimed about size"""
    directory = work_dir(private_work_root, "approved-coauthored")
    data = copy.deepcopy(approved_data)
    data["artifacts"][1].pop("bytes")
    rewrite(directory, data)

    items, issues = career_accumulation.approved_artifacts("career")
    assert issues == ()
    assert any(item.relative_path.startswith("approved-coauthored") for item in items)


def test_negative_byte_count_is_refused(approved_data: dict):
    """a byte count below zero is not a byte count"""
    data = copy.deepcopy(approved_data)
    data["artifacts"][1]["bytes"] = -1
    with pytest.raises(RecordInvalid):
        parse_work_record(data)


# -- pending approval --------------------------------------------------------


def test_pending_approval_must_name_an_artifact_that_exists(approved_data: dict):
    """a pending request cannot pin something the record does not hold"""
    data = copy.deepcopy(approved_data)
    data["pending_approval"] = {
        "pending_id": "3b7f19c4-0e58-4a26-9d31-7c05af62be80",
        "proposed_state": "approved_text",
        "artifact_ref": "art-9999",
        "issued_at": "2026-08-13T16:50:00Z",
        "expires_at": "2026-08-13T17:50:00Z",
    }
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert "pending approval names an artifact" in excinfo.value.message


# -- malformed records are reported and skipped, never readable --------------

def test_malformed_record_is_reported_and_never_readable(
    career_accumulation: Accumulation, private_work_root: Path, approved_data: dict
):
    """a broken record loses its approval entirely"""
    from domains.cos.work.confine import NotFound

    directory = work_dir(private_work_root, "approved-coauthored")
    data = copy.deepcopy(approved_data)
    data["artifacts"][1]["revision"] = 1  # duplicate revision
    rewrite(directory, data)

    items, issues = career_accumulation.approved_artifacts("career")
    assert [issue.code for issue in issues] == ["invalid_request"]
    assert not any(item.relative_path.startswith("approved-coauthored") for item in items)

    relative = f"{directory.name}/artifacts/0002-letter.md"
    with pytest.raises(NotFound):
        career_accumulation.read_source("career", APPROVED, relative)
    assert career_accumulation.search_sources("career", [APPROVED], "quayside").hits == ()
