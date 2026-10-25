import subprocess
from pathlib import Path
import pytest
from services.mc_operations.portal_environment import run


def test_passes_only_caller_and_propagates_failure(tmp_path, monkeypatch):
    token = tmp_path / 'caller'
    token.write_text('x' * 40 + '\n')
    token.chmod(0o600)
    def child(command, env, check, stdout, stderr):
        assert command == ['reviewed-wrapper', 'config']
        assert env['MC_NATIVE_RELAY_TOKEN'] == 'x' * 40
        assert check is False
        assert stdout == stderr == subprocess.DEVNULL
        return subprocess.CompletedProcess(command, 9)
    monkeypatch.setattr(subprocess, 'run', child)
    assert run(token, ['reviewed-wrapper', 'config']) == 9


@pytest.mark.parametrize('case', ['public', 'symlink', 'short', 'multiline', 'oversized', 'oversized_whitespace'])
def test_rejects_unsafe_tokens(tmp_path, monkeypatch, case):
    token = tmp_path / 'caller'
    token.write_text('x' * 40)
    token.chmod(0o600)
    if case == 'public': token.chmod(0o644)
    if case == 'symlink':
        link = tmp_path / 'link'
        link.symlink_to(token)
        token = link
    if case == 'short': token.write_text('short')
    if case == 'multiline': token.write_text('x' * 40 + '\ny')
    if case == 'oversized': token.write_text('x' * 4097)
    if case == 'oversized_whitespace': token.write_text('x' * 40 + ' ' * 5000)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: pytest.fail('must not launch'))
    with pytest.raises((ValueError, OSError)):
        run(token, ['wrapper'])
