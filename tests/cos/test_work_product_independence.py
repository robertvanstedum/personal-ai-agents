"""The reference carries no product, vendor or subject identity.

Checkpoint A: the full test list, not yet implemented.
"""
import pytest

def test_no_product_or_vendor_identifier_in_package():
    """no vendor or product name appears anywhere in the package"""
    pytest.skip("checkpoint A")

def test_package_imports_no_provider_sdk_or_http_client():
    """the package imports nothing that could reach a network"""
    pytest.skip("checkpoint A")

def test_no_career_vocabulary_in_executable_code():
    """subject names appear in prose only, never in code"""
    pytest.skip("checkpoint A")

