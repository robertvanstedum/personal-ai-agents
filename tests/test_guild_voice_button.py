"""Bounded synthetic host/presentation checks; no browser/provider startup."""
import os
from pathlib import Path
import shutil
import subprocess
import pytest


def test_guild_voice_button_states_and_fallback():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js unavailable for voice control fixture')
    script = Path(__file__).with_name('guild_voice_button.mjs')
    result = subprocess.run([node, str(script)], capture_output=True, text=True,
                            timeout=20, env=dict(os.environ), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '11 functional voice-button' in result.stdout
