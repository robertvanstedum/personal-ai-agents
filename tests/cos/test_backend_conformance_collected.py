"""Collectable twin for the script-only backend conformance checks.

Checkpoint A: the full test list, not yet implemented.
"""
import pytest

def test_conformance_stub_mode_passes():
    """the boundary conformance run passes without any network call"""
    pytest.skip("checkpoint A")

def test_scope_check_requires_live_run():
    """the scope check needs a live runtime and is skipped here"""
    pytest.skip("checkpoint A")

def test_acceptance_examples_are_declared():
    """the acceptance examples the script drives are present"""
    pytest.skip("checkpoint A")

