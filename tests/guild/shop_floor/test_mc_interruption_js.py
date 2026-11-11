"""Run the real request-scoped recovery reducer; no network/provider."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[3]

def test_interruption_recovery():
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    result = subprocess.run(["node", "--test", str(ROOT / "tests/mc_interruption.test.mjs")], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
