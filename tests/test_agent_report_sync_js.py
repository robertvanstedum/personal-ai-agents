"""Run the synthetic agent-report browser module fixtures in normal pytest/CI."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_agent_report_sync_browser_fixtures():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    fixture = Path(__file__).with_name("agent_report_sync.mjs")
    done = subprocess.run([node, str(fixture)], capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "2 agent-report sync browser fixtures passed" in done.stdout
