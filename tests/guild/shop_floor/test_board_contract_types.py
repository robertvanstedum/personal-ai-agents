"""Board contracts can be used without importing portal/Flask machinery."""
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from domains.guild.board_contract import BoardContext, BoardRefused
from minimoi_portal.guild_ui import board_actions


def test_legacy_imports_refer_to_same_contract_types():
    # BoardActions uses exact type checks, so compatibility must retain identity.
    assert board_actions.BoardContext is BoardContext
    assert board_actions.BoardRefused is BoardRefused


def test_context_remains_immutable_and_does_not_print_owner_data():
    context = BoardContext("synthetic-owner", "dev", "guild", "mc", "synthetic-source", frozenset({"notes.read"}))
    assert context.owner_session is False
    assert "synthetic-owner" not in repr(context)
    assert "synthetic-source" not in repr(context)
    with pytest.raises(FrozenInstanceError):
        context.principal = "other"
    assert issubclass(BoardRefused, ValueError)


def test_contract_import_needs_only_standard_library(tmp_path):
    root = Path(__file__).resolve().parents[3]
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from domains.guild.board_contract import BoardContext, BoardRefused
assert not any(n == 'flask' or n.startswith('minimoi_portal') for n in sys.modules)
assert issubclass(BoardRefused, ValueError)
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(root)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
