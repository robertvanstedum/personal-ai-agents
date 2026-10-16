"""Legacy language connect cleanup uses the actual shared JS controller, no providers."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_legacy_openai_and_xai_connect_failure_cleanup_and_success():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for shared controller functional checks")
    script = Path(__file__).with_name("realtime_voice_legacy_connect.mjs")
    result = subprocess.run(
        [node, str(script)], capture_output=True, text=True, timeout=20, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 legacy OpenAI/XAI connect-failure/success functional scenarios passed" in result.stdout
