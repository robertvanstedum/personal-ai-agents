"""Supervision test imports must not poison fresh multiprocessing interpreters."""
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('module,exercise',[
    ('test_supervision_relay.py', ''),
    ('test_supervision.py', "namespace['GrokAndExchanges']('test_the_mac_sync_sends_each_ledger_entry_once').debug()"),
])
def test_supervision_imports_preserve_stdlib_inspect(module,exercise):
    # A parent pytest already has stdlib inspect cached, masking path pollution.
    # Spawn reconstructs sys.path in a fresh interpreter, where the host reader's
    # inspect.py must never replace the stdlib module used by pytest/dataclasses.
    probe = f'''
import os, runpy, subprocess, sys
namespace = runpy.run_path({str(ROOT / 'tests/supervision')!r} + '/' + {module!r})
{exercise}
child = subprocess.run(
    [sys.executable, '-c', 'from inspect import Parameter; import dataclasses'],
    env={{**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)}},
    capture_output=True, text=True, timeout=15)
assert child.returncode == 0, child.stderr
'''
    result = subprocess.run([sys.executable, '-c', probe], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
