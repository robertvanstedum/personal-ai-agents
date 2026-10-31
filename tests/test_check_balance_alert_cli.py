"""Run the balance-alert command against a fake checkout and no account access."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def cli(tmp_path):
    root = tmp_path / 'checkout'
    scripts = root / 'scripts'
    scripts.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / 'scripts/check_balance_alert.py'
    command = scripts / source.name
    command.write_bytes(source.read_bytes())
    (root / 'core').mkdir()
    (root / 'core/__init__.py').write_text('')
    env = dict(os.environ, HOME=str(tmp_path / 'isolated-home'))
    return root, command, env


def run_command(cli, result='(None, None)', *, raises=False):
    root, command, env = cli
    behavior = 'raise RuntimeError("unavailable")' if raises else f'return {result}'
    (root / 'core/track_usage.py').write_text(
        'BALANCE_THRESHOLDS = {"getting_low": 10.0, "critically_low": 5.0, "severely_low": 1.0}\n'
        f'def check_balance():\n    {behavior}\n'
    )
    return subprocess.run([sys.executable, '-I', '-S', str(command)], cwd=root.parent,
                          env=env, text=True, capture_output=True)


@pytest.mark.parametrize('level,amount,code,message', [
    ('WARNING', 8.5, 1, '⚡ Getting Low Balance: $8.50 (threshold: $10.0)\n💡 Consider topping up: https://console.anthropic.com/settings/billing\n'),
    ('CRITICAL', 2.5, 2, '⚠️⚠️ CRITICALLY LOW BALANCE: $2.50 (threshold: $5.0)\n🔗 Top up: https://console.anthropic.com/settings/billing\n'),
    ('SEVERE', 0.5, 3, '🚨🚨🚨 SEVERELY LOW BALANCE: $0.50 (threshold: $1.0)\n🔗 Top up: https://console.anthropic.com/settings/billing\n'),
    ('OTHER', 1.2, 1, 'Low balance: $1.20\n'),
])
def test_warning_uses_own_checkout_and_preserves_output(cli, level, amount, code, message):
    result = run_command(cli, repr((amount, level)))
    assert (result.returncode, result.stdout, result.stderr) == (code, message, '')


@pytest.mark.parametrize('value,raises', [('(None, None)', False), ('(100, None)', False), ('(0, None)', True)])
def test_ok_unavailable_and_error_remain_silent(cli, value, raises):
    result = run_command(cli, value, raises=raises)
    assert (result.returncode, result.stdout, result.stderr) == (0, '', '')


def test_import_does_not_check_an_account_or_exit(cli):
    root, command, env = cli
    (root / 'core/track_usage.py').write_text('raise AssertionError("account module imported")\n')
    code = 'import runpy, sys; module = runpy.run_path(sys.argv[1]); assert callable(module["main"]); print("import OK")'
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code, str(command)],
                            cwd=root.parent, env=env, text=True, capture_output=True)
    assert (result.returncode, result.stdout, result.stderr) == (0, 'import OK\n', '')
