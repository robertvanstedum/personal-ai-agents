"""Run the real adapter/controller/MC-host synthetic browser regression in CI."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


def test_real_adapter_managed_admission_messages():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js unavailable for managed voice browser fixture')
    script = Path(__file__).with_name('managed_admission_real_adapter.mjs')
    env = dict(os.environ)
    env.setdefault('MANAGED_ADMISSION_REPO', str(script.parents[1]))
    result = subprocess.run([node, str(script)], capture_output=True, text=True,
                            timeout=20, check=False, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '4 actual adapter/controller/MC-host' in result.stdout
