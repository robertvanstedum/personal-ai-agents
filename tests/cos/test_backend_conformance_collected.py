"""Collectable twin for the script-only backend conformance checks.

`tests/cos/test_backend_conformance.py` defines no ``test_``-prefixed
function: it runs under ``if __name__ == "__main__"`` and so contributes
nothing to CI, even though the repository's CI command collects this
directory. Rather than rewrite that file — it is the submitted record of how
the boundary was checked — this module imports its check functions and runs
them under names pytest will collect.

Only the offline half can run here. The scope-enforcement half needs a live
runtime and real credentials, so it is skipped with a reason rather than
quietly passing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).with_name("test_backend_conformance.py")


def load_script_module():
    """Import the script-style module without executing its __main__ block."""
    spec = importlib.util.spec_from_file_location("cos_backend_conformance_script", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_conformance_stub_mode_passes():
    """the boundary conformance run passes without any network call"""
    module = load_script_module()
    assert module.run_conformance(live=False) is True


def test_scope_check_requires_live_run():
    """the scope check needs a live runtime and is skipped here"""
    module = load_script_module()
    assert callable(module.run_scope_check)
    pytest.skip(
        "the observe/mutate scope check calls a live model runtime and real "
        "credentials; it is not run in CI"
    )


def test_acceptance_examples_are_declared():
    """the acceptance examples the script drives are present"""
    module = load_script_module()
    examples = module.ACCEPTANCE_EXAMPLES
    assert len(examples) == 5
    assert [example["id"] for example in examples] == [1, 2, 3, 4, 5]
    for example in examples:
        assert set(example) == {"id", "prompt", "expect_stored", "description"}
        assert isinstance(example["expect_stored"], bool)
        assert example["prompt"].strip()


def test_script_module_defines_no_collectable_tests_of_its_own():
    """the original file is left as it is, and still collects nothing

    If someone converts it later this test should be deleted along with this
    twin — it exists to make the gap visible rather than to preserve it.
    """
    module = load_script_module()
    assert [name for name in dir(module) if name.startswith("test_")] == []
