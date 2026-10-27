"""Text copied from Word pastes as text, not as a picture MC cannot read (#407): runs tests/mc_paste_text_wins.mjs in
normal pytest/CI."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_paste_scenarios():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    done = subprocess.run([node, str(ROOT / "tests/mc_paste_text_wins.mjs")], capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "5 paste scenarios passed" in done.stdout
