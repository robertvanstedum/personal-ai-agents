"""Version-1 envelope, closed vocabularies, identifiers."""

from __future__ import annotations

import uuid

import pytest

from domains.cos.work.envelope import (
    DATA_CLASSES,
    EFFECTS,
    EGRESS_VALUES,
    ERROR_CODES,
    W0A_ERROR_CODES,
    WORK_CONTRACT_VERSION,
    InvalidRequest,
    WorkError,
    build_request,
    error_response,
    is_uuid4,
    make_receipt,
    new_operation_id,
    require_uuid4,
    success_response,
)

OPERATION_ID = "6f2b8ea1-4d73-4c59-a018-3be7d2f9c456"


def test_effect_vocabulary_is_the_closed_eight():
    """the operation set is exactly the eight effects"""
    assert EFFECTS == frozenset(
        {
            "open_work",
            "attach_source",
            "search_sources",
            "read_source",
            "write_artifact",
            "request_disposition",
            "record_disposition",
            "use_robert_edit",
        }
    )
    for absent in ["list_work", "delete_work", "send", "export", "schedule", "delegate"]:
        assert absent not in EFFECTS
    with pytest.raises(InvalidRequest):
        build_request("list_work", {})


def test_error_vocabulary_is_closed():
    """an unknown error code cannot be raised"""
    assert isinstance(ERROR_CODES, frozenset)
    assert W0A_ERROR_CODES <= ERROR_CODES
    with pytest.raises(ValueError):
        WorkError("something_went_wrong", "plain language")


def test_required_w0a_error_codes_present():
    """every error code this gate needs exists"""
    required = {
        "invalid_request",
        "work_root_unavailable",
        "source_root_unavailable",
        "path_rejected",
        "not_found",
        "unsupported_file",
        "too_large",
        "stale_hash",
        "egress_denied",
        "runtime_profile_unavailable",
    }
    assert required == W0A_ERROR_CODES
    assert required <= ERROR_CODES


def test_unknown_error_code_refused():
    """constructing an error outside the vocabulary fails"""
    for code in ["", "PATH_REJECTED", "path-rejected", "model_refused"]:
        with pytest.raises(ValueError):
            WorkError(code, "plain language")


def test_uuid4_validation():
    """operation identifiers must be UUID4"""
    assert is_uuid4(OPERATION_ID) is True
    assert is_uuid4(new_operation_id()) is True
    assert is_uuid4(str(uuid.uuid1())) is False
    assert is_uuid4("01M1Q6CCR74M9Z9YKN7B0ED6F1") is False
    assert is_uuid4(None) is False
    assert is_uuid4(OPERATION_ID.replace("-", "")) is False
    assert require_uuid4(OPERATION_ID, "operation_id") == OPERATION_ID
    with pytest.raises(InvalidRequest):
        require_uuid4("not-a-uuid", "operation_id")


def test_request_envelope_shape():
    """requests carry version, operation id, effect and params"""
    request = build_request(
        "search_sources", {"subject": "career", "query": "reconciliation"},
        operation_id=OPERATION_ID,
        grant_ref="opaque",
    )
    assert request == {
        "work_contract_version": WORK_CONTRACT_VERSION,
        "operation_id": OPERATION_ID,
        "grant_ref": "opaque",
        "effect": "search_sources",
        "params": {"subject": "career", "query": "reconciliation"},
    }
    assert WORK_CONTRACT_VERSION == 1
    with pytest.raises(InvalidRequest):
        build_request("search_sources", "not an object")


def test_success_response_shape():
    """responses carry version, ok, result, receipt and error"""
    receipt = make_receipt(
        OPERATION_ID, "read_source", "committed", relative_path="current-resume.md", bytes=1228
    )
    response = success_response("read_source", OPERATION_ID, {"result_count": 1}, receipt)
    assert response["work_contract_version"] == 1
    assert response["ok"] is True
    assert response["error"] is None
    assert response["receipt"]["relative_path"] == "current-resume.md"
    assert set(response) == {
        "work_contract_version",
        "operation_id",
        "effect",
        "ok",
        "result",
        "receipt",
        "error",
    }


def test_error_response_is_content_free():
    """errors carry no body and no absolute path"""
    error = WorkError("path_rejected", "that file name is not allowed", relative_path="a/b.md")
    response = error_response("read_source", OPERATION_ID, error)
    assert response["ok"] is False
    assert response["result"] is None
    assert response["error"] == {
        "code": "path_rejected",
        "message": "that file name is not allowed",
        "relative_path": "a/b.md",
    }
    assert "content" not in response["error"]


def test_receipt_rejects_unknown_key():
    """receipts cannot smuggle content"""
    with pytest.raises(InvalidRequest) as excinfo:
        make_receipt(OPERATION_ID, "read_source", "committed", content="the whole letter")
    assert "content" in excinfo.value.message
    with pytest.raises(InvalidRequest):
        make_receipt(OPERATION_ID, "read_source", "committed", absolute_path="/private/x")
    with pytest.raises(InvalidRequest):
        make_receipt("not-a-uuid", "read_source", "committed")


def test_egress_vocabulary_only_none():
    """'none' is the only permitted egress value"""
    assert EGRESS_VALUES == frozenset({"none"})
    assert "allowed" not in EGRESS_VALUES
    assert "egress_denied" in ERROR_CODES


def test_data_classes_are_generic_not_career():
    """the privacy vocabulary is private_personal and external_public"""
    assert DATA_CLASSES == frozenset({"private_personal", "external_public"})
    assert not any("career" in value for value in DATA_CLASSES)
