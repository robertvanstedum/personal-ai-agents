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
    # no mc_pull_identity in the profile: nothing feeds the watch folder, so no minimoi-mc source (memory-watch is not turned yellow
    # every night by it, review LOW-MEDIUM-1) and no mc-pull agent; the manifest says so
    assert shelf['never_copy'] == [] and set(shelf['sources']) == {'claude-code','codex'}
    assert 'rooms' not in shelf['sources']
    assert result['runtime_file_hashes']['scripts/vault/vault_mc_pull.py'] and (pin / 'core/memory_sync/mc_pull.py').exists()
    assert not (out / 'candidate-plists' / (R.LABELS['mc_pull'] + '.plist')).exists()
    assert any('no mc_pull_identity' in line and 'minimoi-mc source not configured' in line for line in result['limitations'])
    assert not any('approve-source' in line for line in result['limitations'])
    assert 'sync' in R.LABELS                       # the vault-sync agent is pinned like the other Vault jobs
    for job in R.LABELS:
        if job == 'mc_pull':
            continue
        doc = plistlib.loads((out / 'candidate-plists' / (R.LABELS[job] + '.plist')).read_bytes())
        assert doc['WorkingDirectory'] == str(pin)
        if job == 'ask':
            assert doc['KeepAlive'] and doc['ProgramArguments'][doc['ProgramArguments'].index('--host') + 1] == '127.0.0.1'
        elif job == 'sync':                          # vault-sync: every 2 h and at load, from the pin, never the checkout
            assert R.LABELS['sync'] == 'com.vanstedum.minimoi-vault-sync'
            assert doc['StartInterval'] == 7200 and doc['RunAtLoad'] is True and 'StartCalendarInterval' not in doc
            assert doc['ProgramArguments'][1:] == [str(pin / 'scripts/vault/vault_sync.py'), 'run', '--execute']
            assert doc['EnvironmentVariables']['PYTHONPATH'] == str(pin)
            assert doc['EnvironmentVariables']['MOI_VAULT_PUBLISH_DEST'] == cfg['backup_dest']
            assert result['runtime_file_hashes']['core/memory_sync/sync.py'] and (pin / 'core/memory_sync/batch.py').exists()
        else:
            h,m = R.TIMES[job]; assert doc['StartCalendarInterval'] == {'Hour': h, 'Minute': m}
    with pytest.raises(ValueError, match='output_exists'):
        R.prepare(REPO, cfg, out)
    # Permit pytest to remove only its synthetic prepared pin afterward.
    for directory in [pin] + [p for p in pin.rglob('*') if p.is_dir()]:
        directory.chmod(0o700)


def test_an_mc_pull_identity_pins_the_mc_pull_agent(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg['mc_pull_identity'] = 'keychain:minimoi-vault-identity/age'
    p = tmp_path / 'profile.json'; p.write_text(json.dumps(cfg)); cfg = R.profile(p)
    monkeypatch.setattr(R, 'dependency_receipt', lambda py: {'python': 'test', 'packages': {}})
    out = tmp_path / 'prepared'
    result = R.prepare(REPO, cfg, out)
    pin = Path(result['runtime'])
    doc = plistlib.loads((out / 'candidate-plists' / 'com.vanstedum.minimoi-vault-mc-pull.plist').read_bytes())
    assert doc['Label'] == R.LABELS['mc_pull'] == 'com.vanstedum.minimoi-vault-mc-pull'
    assert doc['StartInterval'] == 7200 and doc['RunAtLoad'] is True and 'StartCalendarInterval' not in doc
    assert doc['ProgramArguments'][1:] == [str(pin / 'scripts/vault/vault_mc_pull.py'), 'run', '--execute',
                                           '--identity', 'keychain:minimoi-vault-identity/age']
    assert doc['WorkingDirectory'] == str(pin) and doc['EnvironmentVariables']['MOI_CONFIG'] == str(pin / 'config/memory_shelf.json')
    assert not any('IDENTITY' in k for k in doc['EnvironmentVariables'])     # the spec travels as an argument, never a variable
    assert (pin / 'scripts/vault/vault_mc_pull.py').exists()
    assert not any('no mc_pull_identity' in line for line in result['limitations'])
    # the production MC pull's watch folder is the minimoi-mc source root; capture waits for the owner's approval, and until then the
    # nightly memory-watch shows it yellow, which the manifest states
    shelf = json.loads((pin / 'config/memory_shelf.json').read_text())
    assert set(shelf['sources']) == {'claude-code', 'codex', 'minimoi-mc'}
    assert shelf['sources']['minimoi-mc'] == {'kind': 'mc', 'root': str(Path.home() / 'minimoi-staging/mc-export'), 'never_copy': []}
    assert any('memory-watch shows minimoi-mc yellow' in line and '`moi approve-source minimoi-mc`' in line
               for line in result['limitations'])
    for directory in [pin] + [p for p in pin.rglob('*') if p.is_dir()]:
        directory.chmod(0o700)


@pytest.mark.parametrize('bad', ['AGE-SECRET-KEY-1' + 'Q' * 58, 'file:relative/key', 'file:~/key', 'vault:x', 'keychain:', 'cmd: x',
                                 'file:/k\nAGE', 'file:/age-secret-key-1x'])
def test_mc_pull_identity_must_be_a_spec_never_key_text(tmp_path, bad):
    p = tmp_path / 'profile.json'; doc = config(tmp_path); doc['mc_pull_identity'] = bad; p.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='invalid_mc_pull_identity'): R.profile(p)


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
