"""The canonical record shapes, read side."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from domains.cos.work.records import (
    ARTIFACT_CONTEXT_CLASSES,
    FORBIDDEN_FIELDS,
    SOURCE_CONTEXT_CLASSES,
    WORK_STATES,
    RecordInvalid,
    StaleContext,
    load_conversation_binding,
    load_work_record,
    parse_conversation_binding,
    parse_work_record,
    require_sha256,
    verify_sha256,
)

WORK_DIR = Path("subjects") / "career" / "work"


def work_records(root: Path):
    return sorted((root / WORK_DIR).iterdir())


@pytest.fixture
def approved_record_data(private_work_root: Path) -> dict:
    path = next(
        p / "work.json"
        for p in work_records(private_work_root)
        if p.name.startswith("approved-coauthored")
    )
    return json.loads(path.read_text())


def test_load_work_record_accepts_fixture_records(private_work_root: Path):
    """every synthetic work.json validates"""
    directories = work_records(private_work_root)
    assert len(directories) == 5
    states = set()
    for directory in directories:
        record = load_work_record(directory / "work.json")
        assert record.subject == "career"
        assert record.schema_version == 1
        states.add(record.state)
    assert states == {"approved_text", "continuing", "closed"}


@pytest.mark.parametrize("field", sorted(FORBIDDEN_FIELDS))
def test_record_rejects_removed_contract_fields(approved_record_data: dict, field: str):
    """the fields review removed are refused by name"""
    data = copy.deepcopy(approved_record_data)
    data[field] = {"namespace": "career", "data": {}}
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert field in excinfo.value.message
    assert "removed from the contract" in excinfo.value.message


def test_record_rejects_subject_extension(approved_record_data: dict):
    """the removed extension container is refused by name"""
    data = copy.deepcopy(approved_record_data)
    data["subject_extension"] = {"namespace": "career", "data": {"role_family": "operations"}}
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert "subject_extension" in excinfo.value.message


def test_record_rejects_adapter_binding(approved_record_data: dict):
    """adapter metadata is refused by name"""
    data = copy.deepcopy(approved_record_data)
    data["adapter_binding"] = {"adapter": "local_inprocess"}
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert "adapter_binding" in excinfo.value.message


def test_record_rejects_unknown_top_level_field(approved_record_data: dict):
    """unknown top-level fields are refused"""
    data = copy.deepcopy(approved_record_data)
    data["role_family"] = "operations"
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert "role_family" in excinfo.value.message


def test_record_rejects_unknown_source_field(approved_record_data: dict):
    """unknown source fields are refused"""
    data = copy.deepcopy(approved_record_data)
    data["sources"][0]["confidence"] = 0.8
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert "confidence" in excinfo.value.message


def test_record_rejects_unknown_artifact_field(approved_record_data: dict):
    """unknown artifact fields are refused"""
    data = copy.deepcopy(approved_record_data)
    data["artifacts"][0]["score"] = 7
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert "score" in excinfo.value.message


def test_record_requires_uuid4_work_id(approved_record_data: dict):
    """work_id must be UUID4"""
    for bad in ["work-1", "01M1Q6CCR74M9Z9YKN7B0ED6F1", "3f1c9a2e7b644d5a9c318ea20b45d701"]:
        data = copy.deepcopy(approved_record_data)
        data["work_id"] = bad
        with pytest.raises(RecordInvalid):
            parse_work_record(data)


def test_record_rejects_unknown_state(approved_record_data: dict):
    """the lifecycle vocabulary is closed"""
    assert WORK_STATES == frozenset({"continuing", "approved_text", "closed", "unresolved"})
    data = copy.deepcopy(approved_record_data)
    data["state"] = "do_not_apply"
    with pytest.raises(RecordInvalid):
        parse_work_record(data)


@pytest.mark.parametrize("field", ["model", "provider", "runtime", "backend", "adapter"])
def test_record_rejects_provider_or_model_field(approved_record_data: dict, field: str):
    """no provider, model or runtime field is accepted"""
    data = copy.deepcopy(approved_record_data)
    data[field] = "whatever-was-running"
    with pytest.raises(RecordInvalid):
        parse_work_record(data)


def test_source_context_class_closed_set(approved_record_data: dict):
    """sources are robert_source or external_source"""
    assert SOURCE_CONTEXT_CLASSES == frozenset({"robert_source", "external_source"})
    data = copy.deepcopy(approved_record_data)
    data["sources"][0]["context_class"] = "agent_draft"
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert "provenance class" in excinfo.value.message


def test_artifact_context_class_closed_set(approved_record_data: dict):
    """artifacts are agent_draft or coauthored_output"""
    assert ARTIFACT_CONTEXT_CLASSES == frozenset({"agent_draft", "coauthored_output"})
    data = copy.deepcopy(approved_record_data)
    data["artifacts"][0]["context_class"] = "robert_source"
    with pytest.raises(RecordInvalid):
        parse_work_record(data)


def test_disposition_shape_and_closed_state(approved_record_data: dict, private_work_root: Path):
    """disposition carries state, at, artifact_ref, reason, operation_id"""
    record = parse_work_record(approved_record_data)
    assert record.disposition is not None
    assert record.disposition.state == "approved_text"
    assert record.disposition.artifact_ref == "art-0002"
    assert record.disposition.decided_at
    assert record.disposition.operation_id

    closed = next(
        load_work_record(p / "work.json")
        for p in work_records(private_work_root)
        if p.name.startswith("closed-do-not-apply")
    )
    assert closed.disposition is not None
    assert closed.disposition.state == "closed"
    assert closed.disposition.reason == "do not apply"

    data = copy.deepcopy(approved_record_data)
    data["disposition"]["state"] = "rejected"
    with pytest.raises(RecordInvalid):
        parse_work_record(data)

    data = copy.deepcopy(approved_record_data)
    data["disposition"]["decided_by_model"] = True
    with pytest.raises(RecordInvalid):
        parse_work_record(data)


def test_approved_artifact_ref_only_for_approved_text(private_work_root: Path):
    """only an approved_text disposition pins an artifact for reuse"""
    pinned = {}
    for directory in work_records(private_work_root):
        record = load_work_record(directory / "work.json")
        pinned[directory.name.split("--")[0]] = record.approved_artifact_ref
    assert pinned["approved-coauthored"] == "art-0002"
    assert pinned["approved-agent-draft"] == "art-0001"
    assert pinned["continuing-draft"] is None
    assert pinned["closed-do-not-apply"] is None


def test_record_refuses_approved_ref_it_does_not_have(approved_record_data: dict):
    """an approval that names a missing artifact is not trusted"""
    data = copy.deepcopy(approved_record_data)
    data["disposition"]["artifact_ref"] = "art-9999"
    with pytest.raises(RecordInvalid) as excinfo:
        parse_work_record(data)
    assert "does not have" in excinfo.value.message


def test_conversation_binding_names_active_work_only(private_work_root: Path):
    """the binding carries the active work id and nothing else"""
    binding = load_conversation_binding(private_work_root / "conversations" / "owner.json")
    assert binding.conversation_id == "owner"
    assert binding.subject == "career"
    assert binding.work_id
    assert not hasattr(binding, "summary")
    assert not hasattr(binding, "history")


def test_conversation_binding_rejects_unknown_field(private_work_root: Path):
    """the binding schema is closed"""
    data = json.loads((private_work_root / "conversations" / "owner.json").read_text())
    data["model_summary"] = "what the assistant thought about the work"
    with pytest.raises(RecordInvalid) as excinfo:
        parse_conversation_binding(data)
    assert "model_summary" in excinfo.value.message


def test_verify_sha256_detects_changed_bytes(private_work_root: Path, approved_record_data: dict):
    """a changed file no longer matches"""
    record = parse_work_record(approved_record_data)
    artifact = record.artifact("art-0002")
    directory = next(
        p for p in work_records(private_work_root) if p.name.startswith("approved-coauthored")
    )
    target = directory / artifact.path
    assert verify_sha256(target, artifact.sha256) is True
    target.write_text(target.read_text() + "\nan edit made outside the system\n")
    assert verify_sha256(target, artifact.sha256) is False


def test_require_sha256_raises_stale_context(private_work_root: Path, approved_record_data: dict):
    """a mismatch raises stale_context naming the relative path"""
    record = parse_work_record(approved_record_data)
    artifact = record.artifact("art-0002")
    directory = next(
        p for p in work_records(private_work_root) if p.name.startswith("approved-coauthored")
    )
    target = directory / artifact.path
    require_sha256(target, artifact.sha256, artifact.path)
    target.write_text("replaced\n")
    with pytest.raises(StaleContext) as excinfo:
        require_sha256(target, artifact.sha256, artifact.path)
    assert excinfo.value.code == "stale_context"
    assert excinfo.value.relative_path == artifact.path


def test_load_work_record_rejects_broken_json(workspace: Path):
    """an unreadable or malformed record is refused, not guessed at"""
    broken = workspace / "work.json"
    broken.write_text("{ not json")
    with pytest.raises(RecordInvalid):
        load_work_record(broken)
    with pytest.raises(RecordInvalid):
        load_work_record(workspace / "absent.json")
