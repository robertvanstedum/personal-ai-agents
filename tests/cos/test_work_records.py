"""The canonical record shapes, read side.

Checkpoint A: the full test list, not yet implemented.
"""
import pytest

def test_load_work_record_accepts_fixture_records():
    """every synthetic work.json validates"""
    pytest.skip("checkpoint A")

def test_record_rejects_subject_extension():
    """the removed extension container is refused by name"""
    pytest.skip("checkpoint A")

def test_record_rejects_adapter_binding():
    """adapter metadata is refused by name"""
    pytest.skip("checkpoint A")

def test_record_rejects_unknown_top_level_field():
    """unknown top-level fields are refused"""
    pytest.skip("checkpoint A")

def test_record_rejects_unknown_source_field():
    """unknown source fields are refused"""
    pytest.skip("checkpoint A")

def test_record_rejects_unknown_artifact_field():
    """unknown artifact fields are refused"""
    pytest.skip("checkpoint A")

def test_record_requires_uuid4_work_id():
    """work_id must be UUID4"""
    pytest.skip("checkpoint A")

def test_record_rejects_unknown_state():
    """the lifecycle vocabulary is closed"""
    pytest.skip("checkpoint A")

def test_record_rejects_provider_or_model_field():
    """no provider, model or runtime field is accepted"""
    pytest.skip("checkpoint A")

def test_source_context_class_closed_set():
    """sources are robert_source or external_source"""
    pytest.skip("checkpoint A")

def test_artifact_context_class_closed_set():
    """artifacts are agent_draft or coauthored_output"""
    pytest.skip("checkpoint A")

def test_disposition_shape_and_closed_state():
    """disposition carries state, at, artifact_ref, reason, operation_id"""
    pytest.skip("checkpoint A")

def test_approved_artifact_ref_only_for_approved_text():
    """only an approved_text disposition pins an artifact for reuse"""
    pytest.skip("checkpoint A")

def test_conversation_binding_names_active_work_only():
    """the binding carries the active work id and nothing else"""
    pytest.skip("checkpoint A")

def test_conversation_binding_rejects_unknown_field():
    """the binding schema is closed"""
    pytest.skip("checkpoint A")

def test_verify_sha256_detects_changed_bytes():
    """a changed file no longer matches"""
    pytest.skip("checkpoint A")

def test_require_sha256_raises_stale_hash():
    """a mismatch raises stale_hash naming the relative path"""
    pytest.skip("checkpoint A")

