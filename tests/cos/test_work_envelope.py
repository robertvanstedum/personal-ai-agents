"""Version-1 envelope, closed vocabularies, identifiers.

Checkpoint A: the full test list, not yet implemented.
"""
import pytest

def test_effect_vocabulary_is_the_closed_eight():
    """the operation set is exactly the eight effects"""
    pytest.skip("checkpoint A")

def test_error_vocabulary_is_closed():
    """an unknown error code cannot be raised"""
    pytest.skip("checkpoint A")

def test_required_w0a_error_codes_present():
    """every error code this gate needs exists"""
    pytest.skip("checkpoint A")

def test_unknown_error_code_refused():
    """constructing an error outside the vocabulary fails"""
    pytest.skip("checkpoint A")

def test_uuid4_validation():
    """operation identifiers must be UUID4"""
    pytest.skip("checkpoint A")

def test_request_envelope_shape():
    """requests carry version, operation id, effect and params"""
    pytest.skip("checkpoint A")

def test_success_response_shape():
    """responses carry version, ok, result, receipt and error"""
    pytest.skip("checkpoint A")

def test_error_response_is_content_free():
    """errors carry no body and no absolute path"""
    pytest.skip("checkpoint A")

def test_receipt_rejects_unknown_key():
    """receipts cannot smuggle content"""
    pytest.skip("checkpoint A")

def test_egress_vocabulary_only_none():
    """'none' is the only permitted egress value"""
    pytest.skip("checkpoint A")

def test_data_classes_are_generic_not_career():
    """the privacy vocabulary is private_personal and external_public"""
    pytest.skip("checkpoint A")

