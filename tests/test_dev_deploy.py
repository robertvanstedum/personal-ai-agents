"""Dev deploy path (scripts/operations/dev_deploy.py): config check, gates, pinning, records, rollback.

Docker, git and HTTP are replaced by a fake host; nothing here touches a real container.
"""
from datetime import datetime, timezone
import json
import subprocess

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
    with pytest.raises(dd.Refused, match='no known source commit'):
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


def test_registry_units_point_at_real_files_and_name_what_is_out_of_bounds():
    from pathlib import Path
    root = Path(dd.__file__).resolve().parents[2]
    for name, row in dd.UNITS.items():
        assert all((root / d).exists() for d in row['deps']), name
        assert all((root / repo).exists() for repo, _ in dd.source_paths(row)), name
        assert row['health'] or row.get('docker_healthcheck'), name
        if row['kind'] == 'launchd':
            assert (root / row['script']).exists(), name
        else:
            assert (root / row['dockerfile']).exists(), name
    assert {'portal', 'guild-ui', 'german', 'portuguese', 'cos-scheduler', 'curator', 'records',
            'operations', 'dev-agent'} <= set(dd.UNITS)
    assert dd.UNITS['guild-ui']['part_of'] == 'portal'
    assert 'guild_ui' in dd.UNITS['portal']['includes'] and 'MC chat' in dd.UNITS['portal']['includes']
    assert set(dd.OUT_OF_BOUNDS) == {'model-gateway', 'postgres'}
    assert dd.source_paths(dd.UNITS['records']) == [('prototype-lab/projects/project-records-room-poc', '/app')]


def test_registry_rejects_malformed_entries(tmp_path):
    def load(units, oob=None):
        path = tmp_path / 'units.json'
        path.write_text(json.dumps({'units': units, 'out_of_bounds': oob or {}}))
        return dd.load_registry(path)
    good = {'domain': 'x', 'runtime': 'docker', 'container': 'c', 'dockerfile': 'd', 'deps': [], 'source': ['s'],
            'health': ['http://h']}
    assert load({'x': good})[0]['x']['kind'] == 'container'
    with pytest.raises(ValueError, match='missing dockerfile'):
        load({'x': {k: v for k, v in good.items() if k != 'dockerfile'}})
    with pytest.raises(ValueError, match='health'):
        load({'x': dict(good, health=[])})
    with pytest.raises(ValueError, match='runtime'):
        load({'x': dict(good, runtime='k8s')})
    with pytest.raises(ValueError, match='part_of'):
        load({'x': dict(good, part_of='nope')})
    with pytest.raises(ValueError, match='out of bounds'):
        load({'postgres': good}, {'postgres': 'data'})


def test_out_of_bounds_unknown_and_unavailable_units_are_refused(runtime, capsys):
    host = FakeHost(runtime)
    assert dd.main(['deploy', 'model-gateway', 'HEAD', '--operator', 'x', '--dry-run'], host=host) == 2
    assert 'provider keys' in capsys.readouterr().err
    assert dd.main(['deploy', 'nope', 'HEAD', '--operator', 'x', '--dry-run'], host=host) == 2
    assert 'units are:' in capsys.readouterr().err
    assert dd.main(['deploy', 'dev-agent', 'HEAD', '--operator', 'x', '--dry-run'], host=host) == 2
    assert 'not deployable today' in capsys.readouterr().err


def test_a_part_deploys_as_an_overlay_with_its_own_label(runtime, monkeypatch):
    import io
    import tarfile
    part = dict(dd.UNITS['german'], name='german-words', part_of='german', source=('domains/german',))
    monkeypatch.setitem(dd.UNITS, 'german-words', part)
    host = FakeHost(runtime, current_commit=BEFORE)
    host.images[NEW] = image(NEW)
    host.images[NEW]['Config']['Labels'] = {'minimoi.source_commit': BEFORE, 'minimoi.part.german-words': COMMIT}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as t:
        info = tarfile.TarInfo('domains/german/html_server.py')
        t.addfile(info, io.BytesIO(b''))
    host.git_bytes = lambda *args: buf.getvalue()
    record = dd.change(host, 'german-words', 'claude-code', action='deploy', commit=COMMIT, mode='full',
                       timeout=0, out=lambda *_: None)
    build = next(c for c in host.calls if c[:2] == ('docker', 'build'))
    assert 'Dockerfile.dev-overlay' in build                         # a part is always an overlay
    assert f'minimoi.part.german-words={COMMIT}' in build
    assert not any(str(a).startswith('minimoi.source_commit=') for a in build)   # the domain label is kept
    assert json.loads((record / 'RESULT.json').read_text())['commit_before'] == BEFORE
    assert (runtime / 'dev-pins' / 'german.json').exists()          # the part shares its domain's pin
    (runtime / 'dev-pins' / 'HOLD-german').write_text(json.dumps({'owner': 'codex', 'reason': 'trial'}))
    with pytest.raises(dd.Refused, match='held by codex'):
        dd.change(host, 'german-words', 'claude-code', action='deploy', commit=COMMIT, timeout=0,
                  out=lambda *_: None)

def test_overlay_context_replaces_source_dirs_from_a_named_base():
    import io
    import tarfile
    src = io.BytesIO()
    with tarfile.open(fileobj=src, mode='w', format=tarfile.PAX_FORMAT) as t:
        body = b'print(1)\n'
        info = tarfile.TarInfo('domains/german/html_server.py')
        info.size = len(body)
        t.addfile(info, io.BytesIO(body))

    class Git:
        def git_bytes(self, *args):
            assert args[:3] == ('archive', '--format=tar', COMMIT)
            return src.getvalue()

    ctx, dockerfile = dd.overlay_context(Git(), dd.UNITS['german'], COMMIT, image(), 'minimoi-german:dev-overlay-base')
    assert ctx[257:265] == b'ustar  \x00'           # GNU header: what Docker accepts as a stdin context
    with tarfile.open(fileobj=io.BytesIO(ctx)) as t:
        text = t.extractfile(dockerfile).read().decode()
        assert 'domains/german/html_server.py' in t.getnames()
    assert text.startswith('FROM minimoi-german:dev-overlay-base\nUSER root\n')
    assert 'RUN rm -rf /app/domains/german /app/core /app/utils' in text
    assert 'COPY domains/german/ /app/domains/german/' in text and text.rstrip().endswith('USER root')


# ---------- launchd agents ----------

LAUNCHCTL_PRINT = """gui/501/com.user.operations = {
\tactive count = 1
\tpath = {path}
\tstate = running

\tprogram = /venv/bin/python3
\targuments = {
\t\t/venv/bin/python3
\t\t{location}/domains/guild/agents/operations.py
\t}

\tworking directory = {location}
\tenvironment = {
\t\tPORT => 8768
\t}
\tpid = {pid}
}
"""


def test_parse_launchd_reads_top_level_fields_and_arguments():
    job = dd.parse_launchd(LAUNCHCTL_PRINT.replace('{path}', '/p.plist').replace('{location}', '/ops')
                           .replace('{pid}', '819'))
    assert job['state'] == 'running' and job['pid'] == 819 and job['path'] == '/p.plist'
    assert job['working_directory'] == '/ops'
    assert job['arguments'] == ['/venv/bin/python3', '/ops/domains/guild/agents/operations.py']


def test_plist_repoint_changes_only_the_code_location():
    import plistlib
    old = {'Label': 'x', 'KeepAlive': True, 'WorkingDirectory': '/ops', 'EnvironmentVariables': {'PORT': '8768'},
           'ProgramArguments': ['/venv/bin/python3', '/ops/domains/guild/agents/operations.py']}
    new = dd.repoint(old, '/code/abc', 'domains/guild/agents/operations.py')
    assert dd.plist_diff(old, new) == []
    assert new['ProgramArguments'] == ['/venv/bin/python3', '/code/abc/domains/guild/agents/operations.py']
    assert plistlib.loads(plistlib.dumps(new))['ProgramArguments'][0] == '/venv/bin/python3'   # still an array
    assert dd.plist_diff(old, dict(new, KeepAlive=False)) == ['KeepAlive']
    assert dd.plist_diff(old, dict(new, ProgramArguments=['/other/python', 'x'])) == ['ProgramArguments[interpreter]']


class FakeLaunchd(FakeHost):
    """A launchd agent whose plist and code directories live under tmp."""

    def __init__(self, tmp, *, healthy=True):
        super().__init__(tmp)
        import plistlib
        self.home = tmp / 'ops-runtime'
        (self.home / 'domains/guild/agents').mkdir(parents=True)
        (self.home / 'domains/guild/agents/operations.py').write_bytes(b'old\n')
        (self.home / 'logs').mkdir()
        (self.home / 'logs/operations.log').write_text('history\n')
        self.plist = tmp / 'com.user.operations.plist'
        self.plist.write_bytes(plistlib.dumps({
            'Label': 'com.user.operations', 'KeepAlive': True, 'WorkingDirectory': str(self.home),
            'ProgramArguments': ['/venv/bin/python3', f'{self.home}/domains/guild/agents/operations.py']}))
        self.loaded, self.pid, self.healthy = True, 819, healthy

    def git_at(self, location, *args):
        return None                       # the fake home is not a git checkout: unknown current code

    def git(self, *args):
        if args[0] == 'ls-tree' and '--name-only' not in args:
            body = b'new\n'
            blob = __import__('hashlib').sha1(b'blob %d\0' % len(body) + body).hexdigest()
            return f'100644 blob {blob}\tdomains/guild/agents/operations.py\n'
        return super().git(*args)

    def extract(self, commit, dest):
        (dest / 'domains/guild/agents').mkdir(parents=True)
        (dest / 'domains/guild/agents/operations.py').write_bytes(b'new\n')
        (dest / 'data/guild/memory').mkdir(parents=True)
        (dest / 'data/guild/memory/ops_memory.md').write_text('repo copy\n')

    def _location(self):
        import plistlib
        return plistlib.loads(self.plist.read_bytes())['WorkingDirectory']

    def launchd(self, label):
        if not self.loaded:
            return None
        text = LAUNCHCTL_PRINT.replace('{path}', str(self.plist)).replace('{location}', self._location())
        running = self.healthy or self._location() == str(self.home)
        text = text.replace('{pid}', str(self.pid))
        return dict(dd.parse_launchd(text), state='running' if running else 'waiting')

    def launchctl(self, *args, check=True):
        self.calls.append(('launchctl',) + args)
        if args[0] == 'bootout':
            self.loaded = False
        elif args[0] == 'bootstrap':
            self.loaded, self.pid = True, self.pid + 1
        return 0

    def process_args(self, pid):
        return f'/venv/bin/python3 {self._location()}/domains/guild/agents/operations.py'

    def http_status(self, url):
        return 200 if self.healthy or self._location() == str(self.home) else None


def deploy_ops(host, **kw):
    return dd.change(host, 'operations', 'claude-code', action='deploy', commit=COMMIT, mode='full',
                     flags={'accept_unknown_current': True}, timeout=0, out=lambda *_: None, **kw)


def test_launchd_deploy_pins_code_keeps_state_and_rolls_back(runtime, monkeypatch):
    monkeypatch.setattr(dd, 'CODE', runtime / 'dev-code')
    host = FakeLaunchd(runtime)
    before = host.plist.read_bytes()
    record = deploy_ops(host)
    code = runtime / 'dev-code' / 'operations' / COMMIT
    assert host._location() == str(code)
    assert (code / 'logs').resolve() == (host.home / 'logs').resolve()
    assert (code / 'logs' / 'operations.log').read_text() == 'history\n'          # state carried over
    assert (code / 'data/guild/memory').is_symlink()                                 # repo copy replaced by link
    assert (code / '.minimoi-source-commit').read_text().strip() == COMMIT
    health = json.loads((record / 'health.json').read_text())
    assert health['runs_pinned_code'] and health['new_process'] and health['source']['ok']
    assert (record / 'plist-before.plist').read_bytes() == before
    assert [c[1] for c in host.calls if c[0] == 'launchctl'] == ['bootout', 'bootstrap', 'kickstart']

    target, source = dd.rollback_target('operations', str(record))
    assert target == str(host.home)
    dd.change(host, 'operations', 'claude-code', action='rollback', target_image=target, timeout=0,
              out=lambda *_: None)
    assert host._location() == str(host.home)
    assert json.loads((runtime / 'dev-pins' / 'operations.json').read_text())['location'] == str(host.home)
    # Re-deploying the same commit reuses the extracted directory.
    deploy_ops(host)
    assert host._location() == str(code)


def test_launchd_failed_health_restores_the_old_plist(runtime, monkeypatch):
    monkeypatch.setattr(dd, 'CODE', runtime / 'dev-code')
    host = FakeLaunchd(runtime, healthy=False)
    before = host.plist.read_bytes()
    with pytest.raises(RuntimeError, match='rolled back: True'):
        deploy_ops(host)
    assert host.plist.read_bytes() == before
    record = next((runtime / 'operators').iterdir())
    assert json.loads((record / 'FAILURE.json').read_text())['rolled_back'] is True


def test_launchd_refuses_a_plist_edited_without_reload(runtime, monkeypatch):
    import plistlib
    host = FakeLaunchd(runtime)
    doc = plistlib.loads(host.plist.read_bytes())
    host.launchd = lambda label: dict(dd.parse_launchd(LAUNCHCTL_PRINT.replace('{path}', str(host.plist))
                                                       .replace('{location}', '/elsewhere').replace('{pid}', '1')))
    with pytest.raises(dd.Refused, match='without reloading'):
        deploy_ops(host)
    assert plistlib.loads(host.plist.read_bytes()) == doc


def test_launchd_overlay_is_refused(runtime):
    with pytest.raises(dd.Refused, match='container images only'):
        dd.change(FakeLaunchd(runtime), 'operations', 'claude-code', action='deploy', commit=COMMIT,
                  mode='overlay', flags={'accept_unknown_current': True}, timeout=0, out=lambda *_: None)


def test_launchd_refuses_when_requirements_changed(runtime, monkeypatch):
    host = FakeLaunchd(runtime)
    host.git_at = lambda location, *args: '' if args[0] == 'status' else BEFORE + '\n'
    host.migrations = ['requirements.txt']
    with pytest.raises(dd.Refused, match='shared venv'):
        deploy_ops(host)


# ---------- sidecar lifecycle (portal + board-api) and full source coverage ----------

def portal_container(image_id, cid):
    c = container(image_id, name='minimoi-portal')
    c['Id'] = cid
    c['Config']['Labels'] = dict(c['Config']['Labels'], **{'com.docker.compose.service': 'portal'})
    return c


def board_container(image_id, portal_id):
    c = container(image_id, env=['MINIMOI_BOARD_ENV=dev', 'BOARD_API_PORT=5013', 'PATH=/usr/bin'], name='minimoi-board-api-dev')
    c['Id'] = 'board-' + portal_id
    c['Config']['Labels'] = dict(c['Config']['Labels'], **{'com.docker.compose.service': 'board-api'})
    c['Config']['Cmd'] = None
    c['HostConfig'] = {'NetworkMode': f'container:{portal_id}', 'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
                       'SecurityOpt': ['no-new-privileges:true'], 'Tmpfs': {'/tmp': ''}, 'PortBindings': {},
                       'RestartPolicy': {'Name': 'unless-stopped'}}
    c['Mounts'] = [{'Type': 'bind', 'Source': '/staging/secrets/board-api', 'Destination': '/run/board-api', 'RW': False},
                   {'Type': 'bind', 'Source': '/staging/data/guild', 'Destination': '/app/runtime/guild', 'RW': False}]
    c['NetworkSettings'] = {'Networks': {}}
    return c


def board_rendered(image_id):
    return {'image': image_id, 'command': [], 'entrypoint': None, 'network_mode': 'service:portal',
            'environment': {'MINIMOI_BOARD_ENV': 'dev', 'BOARD_API_PORT': '5013'}, 'read_only': True,
            'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'], 'tmpfs': ['/tmp'],
            'restart': 'unless-stopped',
            'volumes': [{'type': 'bind', 'source': '/staging/secrets/board-api', 'target': '/run/board-api',
                         'read_only': True},
                        {'type': 'bind', 'source': '/staging/data/guild', 'target': '/app/runtime/guild',
                         'read_only': True}]}


class FakePortal(FakeHost):
    """portal + its board-api sidecar. `stale_join` makes the sidecar keep the old namespace, `rotate` changes
    the credential files during the change, `ready` is the signed readiness answer, `bad_asset` a JS file
    whose deployed bytes differ from the commit."""

    def __init__(self, tmp, *, stale_join=False, rotate=False, ready='pass', bad_asset=False, healthy=True):
        super().__init__(tmp, current_commit=BEFORE, healthy=healthy)
        self.containers = {'minimoi-portal': portal_container(OLD, 'p-old'),
                           'minimoi-board-api-dev': board_container(OLD, 'p-old')}
        self.stale_join, self.rotate, self.ready, self.bad_asset = stale_join, rotate, ready, bad_asset
        self.cred = 'cred-1'
        self.order = []

    def git(self, *args):
        if args[0] == 'ls-tree' and '--name-only' not in args:
            return ('100644 blob 1111\tminimoi_portal/guild_ui/templates/board.html\n'
                    '100644 blob 2222\tminimoi_portal/guild_ui/static/board.js\n'
                    '100644 blob 3333\tminimoi_portal/guild_ui/static/board.css\n'
                    '120000 blob 4444\tminimoi_portal/guild_ui/static/link\n')
        if args[0] == 'show':
            return 'docs/\n*.md\n'
        return super().git(*args)

    def docker(self, *args, stdin=None, env=None, log=None, timeout=900):
        self.calls.append(('docker',) + args)
        if args[0] == 'build':
            open(args[args.index('--iidfile') + 1], 'w').write(NEW)
            return ''
        if args[:2] == ('rm', '-f'):
            self.order.append('stop:' + args[2])
            self.containers.pop(args[2], None)
            return ''
        if args[0] == 'exec' and dd.MC_TURN_PROBE in args:
            return '0\n'
        if args[0] == 'exec' and args[-1] == '-':
            assert b'capabilities.read' in stdin
            return json.dumps({'signed_readiness': self.ready, 'mutations': 0}) + '\n'
        if args[0] == 'exec':
            blobs = {'/app/minimoi_portal/guild_ui/templates/board.html': '1111',
                     '/app/minimoi_portal/guild_ui/static/board.js': '2222',
                     '/app/minimoi_portal/guild_ui/static/board.css': '3333'}
            if self.bad_asset and self.containers['minimoi-portal']['Image'] == NEW:
                blobs['/app/minimoi_portal/guild_ui/static/board.js'] = 'ffff'
            self.probed = json.loads(stdin)
            return json.dumps({p: blobs.get(p) for p in self.probed})
        return ''

    def run(self, args, *, stdin=None, env=None, timeout=900, log=None):
        self.calls.append(tuple(args))
        pin = json.load(open(args[[i for i, a in enumerate(args) if a == '-f'][-1] + 1]))
        service = 'board-api' if 'board-api' in args else 'portal'
        pinned = pin['services'][service]['image']
        if 'config' in args:
            if service == 'board-api':
                return json.dumps({'services': {'board-api': board_rendered(pinned)}})
            doc = rendered(pinned)
            doc['services']['portal'] = doc['services'].pop('german')
            return json.dumps(doc)
        self.order.append('up:' + service)
        if service == 'portal':
            ok = self.healthy or pinned == OLD
            self.containers['minimoi-portal'] = dict(portal_container(pinned, 'p-' + pinned[7:10]),
                                                     State={'Running': ok, 'Restarting': False})
            if self.rotate:
                self.cred = 'cred-2'
        else:
            portal_id = 'p-old' if self.stale_join else self.containers['minimoi-portal']['Id']
            self.containers['minimoi-board-api-dev'] = board_container(pinned, portal_id)
        return ''

    def http_status(self, url):
        return 200 if self.containers['minimoi-portal']['State']['Running'] else None

    def git_bytes(self, *args):
        import io
        import tarfile
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w') as t:
            t.addfile(tarfile.TarInfo('minimoi_portal/guild_ui/static/board.js'), io.BytesIO(b''))
        return buf.getvalue()

    def dir_digest(self, paths):
        assert paths == ['/staging/secrets/board-api']
        return self.cred


def deploy_portal(host, unit='portal'):
    return dd.change(host, unit, 'codex', action='deploy', commit=COMMIT, mode='full', timeout=0,
                     out=lambda *_: None)


def test_portal_deploy_stops_board_api_first_then_rejoins_it_to_the_new_portal(runtime):
    host = FakePortal(runtime)
    record = deploy_portal(host)
    assert host.order == ['stop:minimoi-board-api-dev', 'up:portal', 'up:board-api']
    board = host.containers['minimoi-board-api-dev']
    assert board['HostConfig']['NetworkMode'] == 'container:' + host.containers['minimoi-portal']['Id'] != 'container:p-old'
    side = json.loads((record / 'health.json').read_text())['sidecar:board-api']
    assert side == {'running': True, 'image': True, 'network_mode': True, 'invariants_changed': [],
                    'credentials_unchanged': True, 'readiness': {'signed_readiness': 'pass', 'mutations': 0}}
    # Only a digest of the credential files is recorded (the fake's digest is the string 'cred-1').
    assert json.loads((record / 'sidecars-before.json').read_text())['board-api']['credentials_sha256'] == 'cred-1'
    assert json.loads((runtime / 'dev-pins' / 'portal.json').read_text())['services'] == {
        'portal': {'image': NEW}, 'board-api': {'image': NEW}}


@pytest.mark.parametrize('fault, field', [('stale_join', 'network_mode'), ('rotate', 'credentials_unchanged'),
                                          ('ready', 'readiness')])
def test_board_api_faults_fail_verification_and_roll_both_back(runtime, fault, field):
    host = FakePortal(runtime, **{fault: 'timeout' if fault == 'ready' else True})
    with pytest.raises(RuntimeError, match='failed verification'):
        deploy_portal(host)
    record = next((runtime / 'operators').iterdir())
    failure = json.loads((record / 'FAILURE.json').read_text())
    side = failure['checks']['sidecar:board-api']
    assert side[field] in (False, {'signed_readiness': 'timeout', 'mutations': 0})
    assert host.containers['minimoi-portal']['Image'] == OLD
    # The rollback ran the same lifecycle: sidecar stopped again, portal restored, sidecar rejoined.
    assert host.order[3:] == ['stop:minimoi-board-api-dev', 'up:portal', 'up:board-api']


def test_sidecar_on_a_stale_namespace_is_refused_before_anything_changes(runtime):
    host = FakePortal(runtime)
    host.containers['minimoi-board-api-dev']['HostConfig']['NetworkMode'] = 'container:gone'
    with pytest.raises(dd.Refused, match='not attached'):
        deploy_portal(host)
    assert host.order == []


@pytest.mark.parametrize('unit', ['portal', 'guild-ui'])
def test_missing_required_sidecar_is_refused_before_deployment(runtime, unit):
    host = FakePortal(runtime)
    del host.containers['minimoi-board-api-dev']
    with pytest.raises(dd.Refused, match='required sidecar missing: board-api'):
        deploy_portal(host, unit=unit)
    assert host.order == []


@pytest.mark.parametrize('changed', ['rw,exec,size=64m', 'rw,noexec,size=4g'])
def test_tmpfs_options_are_compared_before_and_after_deployment(changed):
    before = board_container(OLD, 'p-old')
    before['HostConfig']['Tmpfs'] = {'/tmp': 'rw,noexec,size=64m'}
    original = dd.running_view(before, image(OLD))
    spec = board_rendered(OLD)
    spec['tmpfs'] = ['/tmp:rw,noexec,size=64m']
    assert 'tmpfs' not in dd.config_diff(dd.rendered_view({'services': {'board-api': spec}}, 'board-api'), original)
    spec['tmpfs'] = ['/tmp:' + changed]
    proposed = dd.rendered_view({'services': {'board-api': spec}}, 'board-api')
    assert 'tmpfs' in dd.config_diff(proposed, original)
    before['HostConfig']['Tmpfs']['/tmp'] = changed
    after = dd.running_view(before, image(OLD))
    assert after['tmpfs'] != original['tmpfs']
    assert 'tmpfs' in dd.SIDECAR_INVARIANTS


def test_sidecar_security_drift_in_the_chain_is_refused(runtime):
    host = FakePortal(runtime)
    real = host.run

    def drifted(args, **kw):
        out = real(args, **kw)
        if 'config' in args and 'board-api' in args:
            doc = json.loads(out)
            doc['services']['board-api']['read_only'] = False
            return json.dumps(doc)
        return out
    host.run = drifted
    with pytest.raises(dd.Refused, match='read_only'):
        deploy_portal(host)
    assert host.order == []


def test_source_check_covers_html_js_css_and_catches_a_changed_asset(runtime):
    host = FakePortal(runtime, bad_asset=True)
    with pytest.raises(RuntimeError, match='failed verification'):
        deploy_portal(host, unit='guild-ui')
    record = next((runtime / 'operators').iterdir())
    source = json.loads((record / 'FAILURE.json').read_text())['checks']['source']
    assert source['mismatched'] == ['/app/minimoi_portal/guild_ui/static/board.js']
    assert source['by_type'] == {'.html': 1, '.js': 1, '.css': 1}         # the symlink is not a file to compare


def test_dockerignore_rules():
    patterns = ['_working/', '*.pyc', 'domains/curator/research/data/', '**/__pycache__', 'docs', '!docs/keep.md']
    assert dd.dockerignored('_working/x/y.py', patterns)
    assert dd.dockerignored('core/a.pyc', patterns) is False          # `*.pyc` is root-only in Docker
    assert dd.dockerignored('domains/curator/research/data/f.json', patterns)
    assert dd.dockerignored('core/x/__pycache__/a.py', patterns)
    assert dd.dockerignored('docs/a.md', patterns) and not dd.dockerignored('docs/keep.md', patterns)
    assert not dd.dockerignored('minimoi_portal/guild_ui/static/board.js', patterns)


@pytest.mark.parametrize('field,value', [
    ('Env', ['PATH=/different']), ('Cmd', ['different-server']),
    ('Entrypoint', ['different-entrypoint']), ('Volumes', {'/new-data': {}}),
    ('User', 'root'), ('WorkingDir', '/different'),
])
def test_candidate_image_defaults_cannot_change_effective_config(runtime, field, value):
    host = FakeHost(runtime)
    host.images[NEW]['Config'][field] = value
    with pytest.raises(dd.Refused, match='rendered config differs'):
        deploy(host)
    assert not any('up' in call for call in host.calls)
    assert host.containers['minimoi-german']['Image'] == OLD


def test_stale_rollback_pin_refused_before_build(runtime):
    pins = runtime / 'dev-pins'
    pins.mkdir()
    (pins / 'german.json').write_text(json.dumps({'services': {'german': {'image': NEW}}}))
    host = FakeHost(runtime)
    with pytest.raises(dd.Refused, match='existing pin differs'):
        deploy(host)
    assert not any(call[:2] == ('docker', 'build') for call in host.calls)


def test_mc_turn_started_during_build_refuses_before_mutation(runtime):
    host = FakePortal(runtime)
    original = host.docker
    probes = []

    def docker(*args, **kwargs):
        if dd.MC_TURN_PROBE in args:
            probes.append(True)
            return '0' if len(probes) == 1 else '1'
        return original(*args, **kwargs)

    host.docker = docker
    with pytest.raises(dd.Refused, match='MC turn started'):
        deploy_portal(host)
    assert len(probes) == 2
    assert host.order == []
    assert not (runtime / 'dev-pins' / 'portal.json').exists()


def test_apply_timeout_is_uncertain_without_competing_rollback(runtime):
    host = FakePortal(runtime)
    original = host.run

    def run(args, **kwargs):
        if 'up' in args:
            raise subprocess.TimeoutExpired('docker compose up', 300)
        return original(args, **kwargs)

    host.run = run
    with pytest.raises(RuntimeError, match='outcome uncertain'):
        deploy_portal(host)
    assert host.order == ['stop:minimoi-board-api-dev']
    record = next((runtime / 'operators').iterdir())
    assert json.loads((record / 'UNCERTAIN.json').read_text())['reconciliation_required']
    assert not (record / 'RESULT.json').exists()
    assert not (record / 'FAILURE.json').exists()


def test_runtime_config_drift_triggers_verified_rollback(runtime):
    host = FakeHost(runtime)
    original = host.run

    def run(args, **kwargs):
        result = original(args, **kwargs)
        if 'up' in args and host.containers['minimoi-german']['Image'] == NEW:
            host.containers['minimoi-german']['Config']['Env'].append('UNEXPECTED=value')
        return result

    host.run = run
    with pytest.raises(RuntimeError, match='rolled back: True'):
        deploy(host)
    record = next((runtime / 'operators').iterdir())
    failure = json.loads((record / 'FAILURE.json').read_text())
    assert failure['checks']['config_changed'] == ['env']
    assert failure['rolled_back']


@pytest.mark.parametrize('field', ['user', 'working_dir'])
def test_compose_identity_and_directory_drift_are_refused(field):
    doc = rendered(NEW)
    doc['services']['german'][field] = 'unexpected'
    assert dd.config_diff(*views(doc)) == [field]


def test_explicit_entrypoint_suppresses_image_command():
    doc = rendered(NEW)
    doc['services']['german']['entrypoint'] = ['custom-server']
    c = container()
    c['Config']['Entrypoint'] = ['custom-server']
    c['Config']['Cmd'] = None
    assert dd.config_diff(*views(doc, c)) == []
