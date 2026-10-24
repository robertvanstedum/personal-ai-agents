"""Dev deploy path (scripts/operations/dev_deploy.py): config check, gates, pinning, records, rollback.

Docker, git and HTTP are replaced by a fake host; nothing here touches a real container.
"""
from datetime import datetime, timezone
import json

import pytest

from scripts.operations import dev_deploy as dd

OLD = 'sha256:' + 'a' * 64
NEW = 'sha256:' + 'b' * 64
COMMIT = 'c' * 40
BEFORE = 'd' * 40


def container(image=OLD, env=None, name='minimoi-german', files=('/x/compose.yml',)):
    return {
        'Name': '/' + name, 'Image': image,
        'Config': {'Env': env or ['PORT=8767', 'SECRET=s3', 'PATH=/usr/bin'],
                   'Cmd': ['python', 'domains/german/html_server.py'], 'Entrypoint': None,
                   'Labels': {'com.docker.compose.project': 'minimoi-staging',
                              'com.docker.compose.project.working_dir': '/w',
                              'com.docker.compose.project.environment_file': '/w/.env',
                              'com.docker.compose.project.config_files': ','.join(files),
                              'com.docker.compose.service': 'german'}},
        'HostConfig': {'PortBindings': {'8767/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8767'}]},
                       'Memory': 402653184, 'NanoCpus': 500000000, 'RestartPolicy': {'Name': 'unless-stopped'}},
        'Mounts': [{'Type': 'bind', 'Source': '/data/german', 'Destination': '/app/domains/german/data', 'RW': True}],
        'NetworkSettings': {'Networks': {'minimoi-staging_default': {}}},
        'State': {'Running': True, 'Restarting': False},
    }


def image(image_id=OLD, commit=None, env=('PATH=/usr/bin',)):
    labels = {'minimoi.source_commit': commit} if commit else {}
    return {'Id': image_id, 'Config': {'Env': list(env), 'Cmd': ['python', 'domains/german/html_server.py'],
                                       'Entrypoint': None, 'Labels': labels, 'User': '', 'Volumes': None}}


def rendered(image_id, env=None, mounts=None, ports=None):
    return {'services': {'german': {
        'image': image_id, 'command': None, 'entrypoint': None,
        'environment': env if env is not None else {'PORT': '8767', 'SECRET': 's3'},
        'volumes': mounts if mounts is not None else [
            {'type': 'bind', 'source': '/data/german', 'target': '/app/domains/german/data'}],
        'ports': ports if ports is not None else [
            {'host_ip': '127.0.0.1', 'published': '8767', 'target': 8767, 'protocol': 'tcp'}],
        'networks': {'default': None}, 'mem_limit': '402653184', 'cpus': 0.5, 'restart': 'unless-stopped'}},
        'networks': {'default': {'name': 'minimoi-staging_default'}}}


class FakeHost:
    def __init__(self, tmp, *, current_commit=None, healthy=True, render_env=None, migrations=(), drops=''):
        self.tmp = tmp
        self.containers = {'minimoi-german': container()}
        self.images = {OLD: image(OLD, current_commit), NEW: image(NEW, COMMIT)}
        self.healthy = healthy
        self.render_env = render_env
        self.migrations = list(migrations)
        self.drops = drops
        self.calls = []
        self.gw = {'config_sha256': 'g', 'pids': ['1']}

    # git
    def git(self, *args):
        self.calls.append(('git',) + args)
        if args[0] == 'rev-list':
            return self.drops
        if args[0] == 'diff':
            return '\n'.join(self.migrations)
        if args[0] == 'ls-tree' and '--name-only' in args:
            return '\n'.join(self.migrations)
        if args[0] == 'ls-tree':
            return '100644 blob 1111\tdomains/german/html_server.py\n100644 blob 2222\tdomains/german/data/x.py\n'
        if args[0] == 'rev-parse':
            return COMMIT + '\n'
        raise AssertionError(args)

    def git_bytes(self, *args):
        return b'tar'

    def inspect(self, kind, name):
        return (self.images if kind == 'image' else self.containers).get(name)

    def docker(self, *args, stdin=None, env=None, log=None, timeout=900):
        self.calls.append(('docker',) + args)
        if args[0] == 'build':
            iid = args[args.index('--iidfile') + 1]
            open(iid, 'w').write(NEW)
            return ''
        if args[0] == 'exec':
            # source probe: report whatever blob the tree expects
            paths = json.loads(stdin)
            return json.dumps({p: '1111' for p in paths})
        return ''

    def run(self, args, *, stdin=None, env=None, timeout=900, log=None):
        self.calls.append(tuple(args))
        pin = args[[i for i, a in enumerate(args) if a == '-f'][-1] + 1]
        pinned = json.load(open(pin))['services']['german']['image']
        if 'config' in args:
            return json.dumps(rendered(pinned, env=self.render_env))
        if 'up' in args:
            ok = self.healthy or pinned == OLD
            self.containers['minimoi-german'] = dict(container(pinned), State={'Running': ok, 'Restarting': False})
            return ''
        raise AssertionError(args)

    def http_status(self, url):
        return 200 if self.containers['minimoi-german']['State']['Running'] else None

    def gateway(self):
        return dict(self.gw)

    def sleep(self, s):
        pass

    def now(self):
        return datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(dd, 'PINS', tmp_path / 'dev-pins')
    monkeypatch.setattr(dd, 'OPERATORS', tmp_path / 'operators')
    monkeypatch.setattr(dd, 'PASSTHROUGH', {})
    return tmp_path


def deploy(host, **kw):
    flags = dict({'accept_unknown_current': True}, **kw.pop('flags', {}))
    return dd.change(host, 'german', 'claude-code', action='deploy', commit=COMMIT, mode=kw.pop('mode', 'full'),
                     flags=flags, timeout=0, out=lambda *_: None, **kw)


# ---------- config comparison ----------

def views(rendered_doc, c=None):
    return dd.rendered_view(rendered_doc, 'german'), dd.running_view(c or container(), image())


def test_only_the_image_differs_is_clean():
    assert dd.config_diff(*views(rendered(NEW))) == []


def test_changed_env_value_is_named_without_its_value():
    diff = dd.config_diff(*views(rendered(NEW, env={'PORT': '8767', 'SECRET': 'other'})))
    assert diff == ['env:SECRET']


def test_env_dropped_from_chain_is_caught_but_image_defaults_are_not():
    diff = dd.config_diff(*views(rendered(NEW, env={'PORT': '8767'})))
    assert diff == ['env:SECRET']          # PATH comes from the image, not the chain


def test_mount_and_port_changes_are_caught():
    assert dd.config_diff(*views(rendered(NEW, mounts=[]))) == ['mounts']
    assert dd.config_diff(*views(rendered(NEW, ports=[]))) == ['ports']


def test_pin_goes_last_exactly_once():
    assert dd.with_pin(['a', 'b'], '/p/german.json') == ['a', 'b', '/p/german.json']
    assert dd.with_pin(['a', '/p/german.json', 'b'], '/p/german.json') == ['a', 'b', '/p/german.json']


# ---------- gates ----------

def test_unlabelled_current_image_needs_explicit_acceptance(runtime):
    with pytest.raises(dd.Refused, match='no source label'):
        deploy(FakeHost(runtime), flags={'accept_unknown_current': False})


def test_dropping_commits_on_dev_is_refused_and_listed(runtime):
    host = FakeHost(runtime, current_commit=BEFORE, drops='d1 trial work\nd2 more\n')
    with pytest.raises(dd.Refused, match='(?s)2 commit.*trial work'):
        deploy(host)
    assert not (runtime / 'operators').exists()       # refused before any record or change


def test_migrations_are_never_applied_and_block_until_acknowledged(runtime):
    host = FakeHost(runtime, current_commit=BEFORE, migrations=['domains/guild/db/migrations/012_x.sql'])
    with pytest.raises(dd.Refused, match='(?s)012_x.sql'):
        deploy(host)
    assert deploy(host, flags={'migrations_applied': True})


def test_overlay_refused_when_dependencies_changed(runtime):
    host = FakeHost(runtime, current_commit=BEFORE, migrations=['docker/requirements.german.txt'])
    with pytest.raises(dd.Refused, match='overlay not allowed'):
        deploy(host, mode='overlay', flags={'migrations_applied': True})


def test_hold_refuses_other_operators(runtime):
    (runtime / 'dev-pins').mkdir()
    (runtime / 'dev-pins' / 'HOLD-german').write_text(json.dumps({'owner': 'codex', 'reason': 'trial'}))
    with pytest.raises(dd.Refused, match='held by codex'):
        deploy(FakeHost(runtime))
    assert dd.change(FakeHost(runtime), 'german', 'codex', action='deploy', commit=COMMIT, mode='full',
                     flags={'accept_unknown_current': True}, timeout=0, out=lambda *_: None)


def test_dry_run_reports_every_gate_and_changes_nothing(runtime):
    host = FakeHost(runtime, migrations=['domains/guild/db/migrations/009.sql'])
    lines = []
    assert dd.change(host, 'german', 'claude-code', action='deploy', commit=COMMIT, mode='full',
                     dry_run=True, out=lines.append) is None
    plan = json.loads(lines[0])
    assert plan['config_diff'] == [] and len(plan['would_refuse']) == 2
    assert not any(c[0] == 'docker' and c[1] == 'build' for c in host.calls)
    assert not any('up' in c for c in host.calls)
    assert not (runtime / 'operators').exists()


def test_config_drift_refuses_after_build_without_touching_the_container(runtime):
    host = FakeHost(runtime, render_env={'PORT': '9999', 'SECRET': 's3'})
    with pytest.raises(dd.Refused, match='env:PORT'):
        deploy(host)
    assert host.containers['minimoi-german']['Image'] == OLD
    record = next((runtime / 'operators').iterdir())
    assert json.loads((record / 'REFUSED.json').read_text())['dev_changed'] is False
    assert 's3' not in (record / 'config-after.json').read_text()


# ---------- deploy, record, rollback ----------

def test_deploy_pins_records_and_verifies(runtime):
    host = FakeHost(runtime)
    record = deploy(host)
    assert host.containers['minimoi-german']['Image'] == NEW
    pin = json.loads((runtime / 'dev-pins' / 'german.json').read_text())
    assert pin == {'services': {'german': {'image': NEW}}}
    for name in ('REQUEST.json', 'pin-before.json', 'pin-after.json', 'running-before.json', 'running-after.json',
                 'config-before.json', 'config-after.json', 'health.json', 'RESULT.json', 'rollback.sh'):
        assert (record / name).exists(), name
    result = json.loads((record / 'RESULT.json').read_text())
    assert result['ok'] and result['commit'] == COMMIT and result['production_changed'] is False
    health = json.loads((record / 'health.json').read_text())
    assert health['source']['ok'] and health['source']['files'] == 1   # the mounted data path is skipped
    assert health['gateway_untouched'] is True
    build = next(c for c in host.calls if c[:2] == ('docker', 'build'))
    assert f'minimoi.source_commit={COMMIT}' in build and 'docker/Dockerfile.german' in build
    up = next(c for c in host.calls if 'up' in c)
    files = [up[i + 1] for i, a in enumerate(up) if a == '-f']
    assert up[-3:] == ('-d', '--no-deps', 'german') and files[-1].endswith('dev-pins/german.json')
    assert dd.history('german')[-1]['image'] == NEW
    assert 's3' not in ''.join(p.read_text() for p in record.iterdir() if p.is_file())


def test_failed_health_rolls_back_automatically(runtime):
    host = FakeHost(runtime, healthy=False)
    with pytest.raises(RuntimeError, match='rolled back: True'):
        deploy(host)
    assert host.containers['minimoi-german']['Image'] == OLD
    record = next((runtime / 'operators').iterdir())
    failure = json.loads((record / 'FAILURE.json').read_text())
    assert failure['rolled_back'] is True
    assert json.loads((runtime / 'dev-pins' / 'german.json').read_text())['services']['german']['image'] == OLD


def test_gateway_change_during_deploy_fails_verification(runtime):
    host = FakeHost(runtime)
    real_up = host.run

    def up_and_touch_gateway(args, **kw):
        if 'up' in args:
            host.gw = {'config_sha256': 'changed', 'pids': ['2']}
        return real_up(args, **kw)
    host.run = up_and_touch_gateway
    with pytest.raises(RuntimeError):
        deploy(host)


def test_rollback_returns_to_the_previous_image_and_writes_its_own_record(runtime):
    host = FakeHost(runtime)
    first = deploy(host)
    target, source = dd.rollback_target('german')
    assert target == OLD and source == first
    record = dd.change(host, 'german', 'claude-code', action='rollback', target_image=target, timeout=0,
                       out=lambda *_: None)
    assert host.containers['minimoi-german']['Image'] == OLD
    assert record != first and json.loads((record / 'RESULT.json').read_text())['action'] == 'rollback'
    assert dd.rollback_target('german', str(first))[0] == OLD
    assert [r['action'] for r in dd.history('german')] == ['deploy', 'rollback']


def test_main_refuses_a_real_run_outside_the_wrapper(runtime, monkeypatch, capsys):
    monkeypatch.delenv('MINIMOI_HEAVY_JOB', raising=False)
    assert dd.main(['deploy', 'german', 'HEAD', '--operator', 'x'], host=FakeHost(runtime)) == 2
    assert 'deploy_dev.sh' in capsys.readouterr().err


def test_every_supported_service_has_a_dockerfile_and_health_url():
    from pathlib import Path
    root = Path(dd.__file__).resolve().parents[2]
    for name, row in dd.SERVICES.items():
        assert (root / row['dockerfile']).exists(), name
        assert all((root / d).exists() for d in row['deps']), name
        assert row['health'], name
    assert 'model-gateway' not in dd.SERVICES and 'postgres' not in dd.SERVICES
