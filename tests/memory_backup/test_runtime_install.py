"""Runtime preparation never activates jobs or changes capture approvals or Ask grants."""
import importlib.util
import json
from pathlib import Path
import plistlib
import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('runtime_install', REPO / 'scripts/vault/runtime_install.py')
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


def config(tmp_path):
    doc = json.loads((REPO / 'config/vault_runtime.example.json').read_text())
    doc['public_recipient'] = 'age1' + 'a' * 58
    for key in ['shelf_root','inbox_root','jobs_root','logs_root','ask_registry','launch_agents']:
        doc[key] = str(tmp_path / key)
    return doc


def test_prepare_has_complete_pin_candidates_and_preserves_old_jobs(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    agents = Path(cfg['launch_agents']); agents.mkdir()
    original = b'old operator plist bytes'
    old = agents / (R.LABELS['capture'] + '.plist'); old.write_bytes(original)
    monkeypatch.setattr(R, 'dependency_receipt', lambda py: {'python': 'test', 'packages': {}})
    # No service or installed job action is reachable: git is the only subprocess in preparation.
    out = tmp_path / 'prepared'
    result = R.prepare(REPO, cfg, out)
    assert result['installed'] is False and old.read_bytes() == original
    assert (out / 'previous-plists' / old.name).read_bytes() == original
    pin = Path(result['runtime'])
    assert not Path(cfg['jobs_root']).exists() and not Path(cfg['ask_registry']).exists()
    assert (pin / 'core/agent_turns/writer.py').exists()
    for script in ('query_suite.py', 'assets.py'):
        assert (pin / 'scripts/vault' / script).exists()
        assert result['runtime_file_hashes']['scripts/vault/' + script]
    assert result['runtime_file_hashes']['utils/credential_scrub.py']
    shelf = json.loads((pin / 'config/memory_shelf.json').read_text())
    assert shelf['never_copy'] == [] and set(shelf['sources']) == {'claude-code','codex'}
    assert 'rooms' not in shelf['sources']
    for job in R.LABELS:
        doc = plistlib.loads((out / 'candidate-plists' / (R.LABELS[job] + '.plist')).read_bytes())
        assert doc['WorkingDirectory'] == str(pin)
        if job == 'ask':
            assert doc['KeepAlive'] and doc['ProgramArguments'][doc['ProgramArguments'].index('--host') + 1] == '127.0.0.1'
        else:
            h,m = R.TIMES[job]; assert doc['StartCalendarInterval'] == {'Hour': h, 'Minute': m}
    with pytest.raises(ValueError, match='output_exists'):
        R.prepare(REPO, cfg, out)
    # Permit pytest to remove only its synthetic prepared pin afterward.
    for directory in [pin] + [p for p in pin.rglob('*') if p.is_dir()]:
        directory.chmod(0o700)


def test_profile_refuses_secret_or_unknown_fields_and_placeholder(tmp_path):
    p = tmp_path / 'config.json'; doc = config(tmp_path)
    doc['token'] = 'forbidden'; p.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='invalid_profile'): R.profile(p)
    del doc['token']; doc['public_recipient'] = 'age1REPLACE'; p.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='invalid_backup_settings'): R.profile(p)


def test_profile_and_commands_have_only_public_recipient(tmp_path):
    p = tmp_path / 'config.json'; p.write_text(json.dumps(config(tmp_path))); cfg = R.profile(p)
    env = R.environment(tmp_path, cfg)
    assert not any('TOKEN' in k or 'IDENTITY' in k for k in env)
    assert env['MOI_RESERVE_GIB'] == '5'
    assert R.commands(tmp_path, cfg, 'backup')[-2:] == ['run', '--execute']


def test_registry_input_change_produces_a_different_pin(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    monkeypatch.setattr(R, 'dependency_receipt', lambda py: {'python': 'test', 'packages': {}})
    original = Path.read_bytes
    registry = REPO / 'config/scheduled_jobs.json'
    doc = json.loads(registry.read_text())
    override = [json.dumps(doc).encode()]
    monkeypatch.setattr(Path, 'read_bytes', lambda p: override[0] if p == registry else original(p))
    a = R.prepare(REPO, cfg, tmp_path / 'first')
    doc['jobs'][0]['name'] = 'changed input name'
    override[0] = json.dumps(doc).encode()
    b = R.prepare(REPO, cfg, tmp_path / 'second')
    assert a['source_inputs'] != b['source_inputs'] and a['source_tree'] != b['source_tree']
    for manifest in (a,b):
        pin = Path(manifest['runtime'])
        for d in [pin] + [p for p in pin.rglob('*') if p.is_dir()]: d.chmod(0o700)
