"""The subject-scoped approved-output projection.

Checkpoint A: the full test list, not yet implemented.
"""
import pytest

def test_projection_returns_only_disposition_pinned_artifacts():
    """exactly the artifacts an approved_text disposition names"""
    pytest.skip("checkpoint A")

def test_projection_preserves_coauthored_output_class():
    """an approved co-authored output keeps its class"""
    pytest.skip("checkpoint A")

def test_projection_preserves_agent_draft_class_when_approved():
    """an approved agent draft stays an agent draft"""
    pytest.skip("checkpoint A")

def test_projection_excludes_unapproved_artifact_of_approved_work():
    """earlier revisions of approved work are not exposed"""
    pytest.skip("checkpoint A")

def test_projection_excludes_continuing_work():
    """continuing work is never projected"""
    pytest.skip("checkpoint A")

def test_projection_excludes_closed_work_without_approval():
    """a closed 'do not apply' work is never projected"""
    pytest.skip("checkpoint A")

def test_projection_carries_disposition_reference():
    """each item names work_id, operation_id and decision time"""
    pytest.skip("checkpoint A")

def test_search_approved_root_finds_approved_language():
    """approved language is retrievable for the next work"""
    pytest.skip("checkpoint A")

def test_search_approved_root_never_returns_provisional_draft():
    """an unreviewed draft never masquerades as approved"""
    pytest.skip("checkpoint A")

def test_read_approved_artifact_returns_original_class():
    """reading preserves authorship"""
    pytest.skip("checkpoint A")

def test_stale_artifact_hash_is_reported_not_silently_returned():
    """a changed artifact is reported as stale_hash in search"""
    pytest.skip("checkpoint A")

def test_stale_artifact_hash_on_read_raises_stale_hash():
    """a changed artifact is refused on read"""
    pytest.skip("checkpoint A")

def test_projection_exposes_no_list_all_work_operation():
    """there is no list operation on the public surface"""
    pytest.skip("checkpoint A")

def test_projection_unavailable_without_work_root():
    """no write root means no projection, and sources still read"""
    pytest.skip("checkpoint A")

def test_projection_ignores_malformed_work_record():
    """an invalid record is reported, not trusted"""
    pytest.skip("checkpoint A")

