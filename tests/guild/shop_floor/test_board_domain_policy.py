"""Domain Board policy can be used without portal transport dependencies."""
import subprocess
import sys
from pathlib import Path

import pytest

from domains.guild import board_contract, board_operations, board_text, board_wire
from minimoi_portal.guild_ui import stores  # initializes the existing store/mixin pair
from minimoi_portal.guild_ui import board, board_text as legacy


def test_portal_policy_exports_preserve_identity():
    for name in ("CleanText", "TextRefused", "clean", "normalized", "LIMIT", "CONVENTION", "WHITESPACE"):
        assert getattr(legacy, name) is getattr(board_text, name)
    assert board.LABELS is board_contract.LABELS
    assert board_operations.LABELS is board_contract.LABELS


@pytest.mark.parametrize("text, expected", [(" hello\r\nworld ", "hello\nworld"),
    ("😀" * 280, "😀" * 280), ("card 4242 4242 4242 4242", "card [payment detail removed]")])
def test_text_policy_preserved(text, expected):
    result = board_text.clean(text)
    assert result.text == expected
    assert result.count == len(expected)


@pytest.mark.parametrize("text, code, count", [(" ", "text_empty", 0),
    ("😀" * 281, "text_too_long", 281), ("\ud800", "invalid_text", None), (None, "invalid_text", None)])
def test_refusal_contract(text, code, count):
    with pytest.raises(legacy.TextRefused) as error:
        board_text.clean(text)
    assert error.value.code == code
    assert error.value.count == count


def test_domain_wire_has_no_portal_dependency(tmp_path):
    root = Path(__file__).resolve().parents[3]
    code = """
import json, sys
sys.path.insert(0, sys.argv[1])
from domains.guild.board_wire import payload, digest
p = payload('guild.board.note.update', {'expected_revision': 3, 'changes': {'text': ' hello\\r\\nworld ', 'done': True}}, '7')
assert p == {'changes': {'text': 'hello\\nworld', 'done': True}, 'text_scrubbed': False, 'target': 7, 'expected_revision': 3}
assert len(digest('guild.board.note.update', p)) == 64
assert not any(n == 'flask' or n.startswith('minimoi_portal') for n in sys.modules)
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(root)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
