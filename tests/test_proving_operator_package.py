"""No Docker, credentials, network or model calls: render and exercise the guard."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

MODULE = Path(__file__).resolve().parents[1] / 'scripts/staging/mc_probe/render_operators.py'
spec = importlib.util.spec_from_file_location('operator_renderer', MODULE)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


@pytest.fixture
def config(tmp_path):
    c = {k: str(tmp_path / k) for k in renderer.PATH_KEYS}
    c.update(day=datetime.now(ZoneInfo('America/Chicago')).date().isoformat(), run_id='sample-review-1',
             base_calls=2, base_micro=6_000_000, window_micro=10_000_000,
             max_new_calls=20, input_rate=8, output_rate=20,
             expected_model='anthropic/example-reviewed-model', key_ref='test-key-reference',
             original_guard_sha256='c' * 64,
             test_image='sha256:' + 'a' * 64, revisions={'sample': 'b' * 40}, fixtures={})
    return c


def test_render_is_private_compilable_and_does_not_execute(tmp_path, config):
    dest = tmp_path / 'generated'
    manifest = renderer.render(config, dest)
    assert len(manifest['scripts']) == 5
    assert manifest['executed'] is False
    assert not Path(config['state_root']).exists()
    assert dest.stat().st_mode & 0o777 == 0o700
    for name in manifest['scripts']:
        assert (dest / name).stat().st_mode & 0o777 == 0o600
        compile((dest / name).read_text(), name, 'exec')
    with pytest.raises(ValueError, match='new_output'):
        renderer.render(config, dest)


@pytest.mark.parametrize('change', [
    {'window_micro': 20_000_000}, {'window_micro': 0}, {'input_rate': 0},
    {'base_calls': True}, {'run_id': "x');__import__('os')"},
    {'day': 'tomorrow'}, {'revisions': {'main': 'main'}},
    {'upstream_key': 'must-not-be-in-config'}, {'brief': '/private/sealed/key.md'},
])
def test_bad_configuration_refused_without_output(tmp_path, config, change):
    config.update(change)
    with pytest.raises((ValueError, TypeError)):
        renderer.render(config, tmp_path / 'generated')
    assert not (tmp_path / 'generated').exists()


def test_render_refuses_repository_destination(tmp_path, config):
    config['repo_root'] = str(tmp_path)
    with pytest.raises(ValueError, match='outside_repository'):
        renderer.render(config, tmp_path / 'generated')


def test_path_literal_cannot_inject_code(tmp_path, config):
    config['brief'] = str(tmp_path / "a');raise RuntimeError('injected")
    dest = tmp_path / 'generated'
    renderer.render(config, dest)
    compile((dest / 'runner.py').read_text(), 'runner', 'exec')


def test_guard_retains_history_and_refuses_uncounted_calls(tmp_path, config):
    dest = tmp_path / 'generated'
    renderer.render(config, dest)
    state = tmp_path / 'state'
    state.mkdir()
    source = (dest / 'guard.py').read_text().replace('/state/', str(state) + '/')
    env = {'__name__': 'guard_test'}
    exec(compile(source, 'guard', 'exec'), env)
    db = sqlite3.connect(state / 'model-admission.sqlite')
    db.execute('CREATE TABLE calls(id INTEGER PRIMARY KEY,day TEXT,at TEXT)')
    # Historical calls from another day remain counted, never reset.
    db.executemany('INSERT INTO calls(day,at) VALUES (?,?)', [('2000-01-01', 'old')] * 2)
    db.execute('CREATE TABLE bounds_sample_review_1(call_id INTEGER PRIMARY KEY,micro INTEGER)')
    db.commit()
    with pytest.raises(ValueError, match='not_armed'):
        env['admit'](100000)
    (state / 'armed.json').write_text(json.dumps({'expires_epoch': time.time() + 600}))
    for _ in range(11):
        env['admit'](100000)
    assert db.execute('SELECT SUM(micro) FROM bounds_sample_review_1').fetchone()[0] == 9205504
    with pytest.raises(ValueError, match='mixed_price_budget_limit'):
        env['admit'](100000)
    assert db.execute('SELECT COUNT(*) FROM calls').fetchone()[0] == 13
    env['BASE_DAY'] = '2000-01-01'
    with pytest.raises(ValueError, match='review_new_day'):
        env['admit'](1)
    env['BASE_DAY'] = config['day']
    env['BASE_MICRO'] = 24_999_999
    with pytest.raises(ValueError, match='mixed_price_budget_limit'):
        env['admit'](1)
    env['BASE_MICRO'] = config['base_micro']
    db.execute('INSERT INTO calls(day,at) VALUES (?,?)', (config['day'], 'unknown'))
    db.commit()
    with pytest.raises(ValueError, match='unpriced_admission'):
        env['admit'](1)
    db.close()


def test_resource_abort_restores_dev_without_starting_model(tmp_path, config, monkeypatch):
    import hashlib
    import subprocess
    import shutil
    import types
    state = Path(config['state_root'])
    state.mkdir()
    (state / 'guard-state').mkdir()
    (state / 'tool-queue').mkdir()
    original = b'# inert original guard\n'
    (state / 'model-guard.py').write_bytes(original)
    config.update(base_calls=0, base_micro=0, original_guard_sha256=hashlib.sha256(original).hexdigest())
    # Empty initial ledgers are valid; the abort must preserve their empty history.
    with sqlite3.connect(state / 'budget-v2.sqlite') as db:
        db.execute('CREATE TABLE stop_flags(reason TEXT)')
        db.execute('CREATE TABLE reservations(day TEXT,charged INTEGER,state TEXT)')
    with sqlite3.connect(state / 'guard-state/model-admission.sqlite') as db:
        db.execute('CREATE TABLE calls(id INTEGER PRIMARY KEY,day TEXT,at TEXT)')
    Path(config['voice_probe']).write_text('# reviewed idle probe')
    Path(config['model_receipt']).write_text('{"verified":true}')
    dest = tmp_path / 'generated'
    renderer.render(config, dest)
    calls = []
    paused = False
    def fake_output(args, **kwargs):
        nonlocal paused
        calls.append(args)
        if args[:2] == ['docker', 'inspect']:
            return json.dumps([{'Id': args[2], 'State': {'Running': args[2] == 'minimoi-cos-agent-a'}}])
        if args[:2] == ['sysctl', '-n']:
            return '4' if paused else '1'
        if args == ['sysctl', 'vm.swapusage']:
            return 'used = 100.00M'
        if args[:2] == ['docker', 'stop']:
            if args[-1] == 'minimoi-cos-agent-a':
                paused = True
            return ''
        if args[:2] == ['docker', 'start']:
            return ''
        if '/app/config.yaml' in args:
            return json.dumps({'model_list': [{'model_name': n, 'litellm_params': {'model': config['expected_model']}} for n in ('minimoi-cos-agent', 'minimoi-mc-agent')]})
        if args[:3] == ['docker', 'exec', '-i']:
            return '{"unresolved_tool_calls":0,"session_states":[]}'
        if args[:3] == ['docker', 'exec', 'minimoi-portal']:
            return 'idle'
        raise AssertionError(args)
    monkeypatch.setattr(subprocess, 'check_output', fake_output)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('No process may start before resource gate'))
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: types.SimpleNamespace(free=12 * 1024**3))
    monkeypatch.setattr(time, 'sleep', lambda _: None)
    # A deterministic valid Chicago time, independent of CI scheduling.
    import datetime as dt
    class Morning(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat(config['day'] + 'T10:00:00').replace(tzinfo=tz)
    monkeypatch.setattr(dt, 'datetime', Morning)
    with pytest.raises(AssertionError, match='pressure_critical'):
        exec(compile((dest / 'window.py').read_text(), 'window', 'exec'), {'__file__': str(dest / 'window.py')})
    record = json.loads((state / 'SAMPLE_REVIEW_1_WINDOW.json').read_text())
    assert record['restored_healthy'] is True and record['cleanup_errors'] == []
    assert ['docker', 'start', 'minimoi-cos-agent-a'] in calls
    assert not any(a[:2] == ['docker', 'start'] and a[-1] == 'minimoi-proving-agent' for a in calls)
    assert (state / 'model-guard.py').read_bytes() == original
    assert not (state / 'guard-state/armed.json').exists()
    with sqlite3.connect(state / 'guard-state/model-admission.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM calls').fetchone()[0] == 0
