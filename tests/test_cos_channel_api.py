"""The public channel API delegates once and loads no runtime at import time."""
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from domains.cos import channel_api


@pytest.fixture
def runtime(monkeypatch):
    module = ModuleType("domains.cos.chief_of_staff")
    module._chat = Mock(return_value="synthetic reply")
    module._read_memory = Mock(return_value="Grüße\n世界")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


@pytest.mark.parametrize("channel", ["telegram_text", "telegram_voice"])
@pytest.mark.parametrize("text", ["hello", "/note remember this", "/remind tomorrow test"])
def test_chat_delegates_exactly_once(runtime, channel, text):
    assert channel_api.chat(text, channel=channel) == "synthetic reply"
    runtime._chat.assert_called_once_with(text, channel=channel)
    runtime._read_memory.assert_not_called()


def test_separate_calls_are_not_cached_or_retried(runtime):
    runtime._chat.side_effect = ["one", RuntimeError("synthetic backend failure")]
    assert channel_api.chat("first", channel="telegram_text") == "one"
    with pytest.raises(RuntimeError, match="synthetic backend failure"):
        channel_api.chat("second", channel="telegram_text")
    assert runtime._chat.call_count == 2


def test_status_reports_character_count_without_chat_or_mutable_state(runtime):
    assert channel_api.memory_character_count() == len("Grüße\n世界")
    runtime._read_memory.assert_called_once_with()
    runtime._chat.assert_not_called()
    assert not hasattr(runtime, "_state")


def test_status_errors_propagate(runtime):
    runtime._read_memory.side_effect = OSError("synthetic read failure")
    with pytest.raises(OSError, match="synthetic read failure"):
        channel_api.memory_character_count()
    runtime._read_memory.assert_called_once_with()


def test_import_is_stdlib_only_and_does_not_start_runtime(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from domains.cos import channel_api
assert 'domains.cos.chief_of_staff' not in sys.modules
assert 'openai' not in sys.modules
assert 'telegram' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(root)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
