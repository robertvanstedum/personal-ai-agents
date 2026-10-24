"""Deploy one named service at a pinned commit to dev (the Mac), with a dated record and rollback.

Design: docs/DEV_DEPLOY_DESIGN_v0.1_2026-10-24.md. Dev only: this never talks to EC2, ECR or
production. Source comes from `git archive <commit>`; no checkout is ever touched. The running
container's recorded compose chain is re-applied with one stable per-service pin file at its end,
and the only difference allowed between the running and the rendered service is the image.

Use scripts/operations/deploy_dev.sh, which takes the host-wide heavy-job lock first.
"""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import fcntl
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = Path(os.environ.get('MINIMOI_DEV_RUNTIME', Path.home() / 'minimoi-runtime'))
PINS = RUNTIME / 'dev-pins'
CODE = RUNTIME / 'dev-code'
OPERATORS = RUNTIME / 'operators'
GATEWAY_CONFIG = Path.home() / '.openclaw' / 'openclaw.json'
SHA = re.compile(r'[0-9a-f]{40}\Z')
IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}\Z')
MIGRATIONS = re.compile(r'(^|/)(db/)?migrations/')
DEP_FILES = ('requirements.txt', 'requirements.test.txt', 'package.json', 'package-lock.json')

# Values the dev chain interpolates that no env file holds: read from a running container's env,
# handed to `docker compose` through the process environment only, never printed or written.
PASSTHROUGH = {'MC_NATIVE_RELAY_TOKEN': ('minimoi-portal', 'MC_RUNTIME_TOKEN')}

# One row per supported service. `sidecars` share the image and move with it; `source` is what an
# overlay replaces and what the post-deploy source check hashes.
SERVICES = {
    'portal': dict(container='minimoi-portal', dockerfile='docker/Dockerfile.portal',
                   deps=('docker/requirements.portal.txt',),
                   source=('minimoi_portal', 'core', 'services', 'domains', 'utils'),
                   health=('http://127.0.0.1:5001/health', 'https://dev.minimoi.ai/health'),
                   sidecars=(('board-api', 'minimoi-board-api-dev'),), mc_turn_probe=True),
    'german': dict(container='minimoi-german', dockerfile='docker/Dockerfile.german',
                   deps=('docker/requirements.german.txt',), source=('domains/german', 'core', 'utils'),
                   health=('http://127.0.0.1:8767/health',)),
    'portuguese': dict(container='minimoi-portuguese', dockerfile='docker/Dockerfile.portuguese',
                       deps=('docker/requirements.portuguese.txt',), source=('domains/portuguese', 'core', 'utils'),
                       health=('http://127.0.0.1:8770/health',)),
    'cos-scheduler': dict(container='minimoi-cos-scheduler', dockerfile='docker/Dockerfile.cos-scheduler',
                          deps=('docker/requirements.cos-agent.txt',),
                          source=('core', 'utils', 'domains/guild', 'domains/cos', 'services/model_gateway',
                                  'services/usage'),
                          health=('http://127.0.0.1:8769/health',)),
}
# Mac-only launchd agents: pinned code directories instead of images (see change_launchd).
SERVICES['operations'] = dict(
    kind='launchd', label='com.user.operations', script='domains/guild/agents/operations.py',
    deps=('requirements.txt',), source=('domains/guild', 'core', 'utils'),
    # Paths the agent writes inside its code directory: they stay in their current home and every pinned
    # code directory links to them, so a deploy or rollback never loses logs or memory.
    state=('logs', 'data/guild/memory'),
    health=('http://127.0.0.1:8768/health',))
for row in SERVICES.values():
    row.setdefault('kind', 'container')
    row.setdefault('sidecars', ())
    row.setdefault('mc_turn_probe', False)
    row.setdefault('app_root', '/app')

MC_TURN_PROBE = ("import os,psycopg2; c=psycopg2.connect(os.environ['DATABASE_URL']); cur=c.cursor(); "
                 "cur.execute(\"SELECT count(*) FROM guild.mc_turn_ledger WHERE state = %s AND lease_until > "
                 "clock_timestamp()\", ('claimed',)); print(cur.fetchone()[0]); c.close()")

# Runs inside the container: git blob ids of the listed files, so they compare with `git ls-tree`.
BLOB_PROBE = ("import hashlib,json,sys\nout={}\nfor p in json.load(sys.stdin):\n try: b=open(p,'rb').read()\n"
              " except OSError: out[p]=None; continue\n out[p]=hashlib.sha1(b'blob %d\\0'%len(b)+b).hexdigest()\n"
              "print(json.dumps(out))")


class Refused(Exception):
    """A precondition failed before anything on dev changed."""


def utc_stamp(now):
    return now.strftime('%Y%m%dT%H%M%SZ')


def digest(pairs):
    return hashlib.sha256('\n'.join(sorted(pairs)).encode()).hexdigest()


def env_dict(items):
    return dict(x.split('=', 1) for x in items or [])


# ---------- host access (replaced by a fake in tests) ----------

class Host:
    def run(self, args, *, stdin=None, env=None, timeout=900, log=None):
        if log is not None:
            with open(log, 'ab') as f:
                r = subprocess.run(args, input=stdin, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
            if r.returncode:
                raise RuntimeError(f'{args[0]} {args[1]} failed ({r.returncode}); see {log}')
            return ''
        r = subprocess.run(args, input=stdin, env=env, capture_output=True, timeout=timeout)
        if r.returncode:
            raise RuntimeError(f'{" ".join(args[:3])} failed: {r.stderr.decode(errors="replace")[-600:]}')
        return r.stdout.decode()

    def git(self, *args):
        return self.run(['git', '-C', str(ROOT), *args], timeout=120)

    def git_bytes(self, *args):
        return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, check=True).stdout

    def inspect(self, kind, name):
        r = subprocess.run(['docker', kind, 'inspect', name] if kind == 'image' else ['docker', 'inspect', name],
                           capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout)[0] if r.returncode == 0 else None

    def docker(self, *args, stdin=None, env=None, log=None, timeout=900):
        return self.run(['docker', *args], stdin=stdin, env=env, log=log, timeout=timeout)

    def http_status(self, url):
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return None

    def gateway(self):
        sha = hashlib.sha256(GATEWAY_CONFIG.read_bytes()).hexdigest() if GATEWAY_CONFIG.exists() else None
        r = subprocess.run(['pgrep', '-f', 'openclaw.* gateway'], capture_output=True, text=True)
        return {'config_sha256': sha, 'pids': sorted(r.stdout.split())}

    def sleep(self, seconds):
        time.sleep(seconds)

    def launchd(self, label):
        """The loaded launchd job as launchd itself reports it, or None when it is not loaded."""
        r = subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/{label}'], capture_output=True, text=True)
        return parse_launchd(r.stdout) if r.returncode == 0 else None

    def launchctl(self, *args, check=True):
        r = subprocess.run(['launchctl', *[a.replace('{uid}', str(os.getuid())) for a in args]],
                           capture_output=True, text=True, timeout=60)
        if check and r.returncode:
            raise RuntimeError(f'launchctl {args[0]} failed: {(r.stderr or r.stdout)[-400:]}')
        return r.returncode

    def process_args(self, pid):
        r = subprocess.run(['ps', '-o', 'command=', '-p', str(pid)], capture_output=True, text=True)
        return r.stdout.strip()

    def git_at(self, location, *args):
        r = subprocess.run(['git', '-C', str(location), *args], capture_output=True, text=True, timeout=60)
        return r.stdout if r.returncode == 0 else None

    def extract(self, commit, dest):
        archive = self.git_bytes('archive', '--format=tar', commit)
        subprocess.run(['tar', '-x', '-C', str(dest)], input=archive, check=True)

    def now(self):
        return datetime.now(timezone.utc)


# ---------- compose chain ----------

def chain_of(container):
    labels = container['Config']['Labels']
    return {'project': labels['com.docker.compose.project'],
            'working_dir': labels['com.docker.compose.project.working_dir'],
            'env_files': [f for f in labels.get('com.docker.compose.project.environment_file', '').split(',') if f],
            'files': labels['com.docker.compose.project.config_files'].split(','),
            'service': labels['com.docker.compose.service']}


def with_pin(files, pin):
    """The recorded chain with the pin file last, exactly once. Re-running never grows the chain."""
    pin = str(pin)
    return [f for f in files if f != pin] + [pin]


def compose_cmd(chain, files):
    cmd = ['docker', 'compose', '-p', chain['project'], '--project-directory', chain['working_dir']]
    for f in chain['env_files']:
        cmd += ['--env-file', f]
    for f in files:
        cmd += ['-f', f]
    return cmd


def compose_env(host):
    env = dict(os.environ)
    for var, (container, key) in PASSTHROUGH.items():
        if var in env:
            continue
        c = host.inspect('container', container)
        if c:
            value = env_dict(c['Config']['Env']).get(key)
            if value:
                env[var] = value
    return env


def render(host, chain, files, env):
    cmd = compose_cmd(chain, files) + ['config', '--format', 'json', chain['service']]
    return json.loads(host.run(cmd, env=env, timeout=120))


# ---------- config comparison: only the image may change ----------

def rendered_view(doc, service):
    s = doc['services'][service]
    networks = doc.get('networks') or {}
    vols = doc.get('volumes') or {}

    def mount(v):
        source = v.get('source')
        if v['type'] == 'volume':
            source = (vols.get(source) or {}).get('name', source)
        return [v['type'], source, v['target'], not v.get('read_only', False)]

    ports = sorted([p.get('host_ip', ''), str(p.get('published', '')), int(p['target']), p.get('protocol', 'tcp')]
                   for p in s.get('ports') or [])
    return {'image': s.get('image'),
            'env': {k: '' if v is None else str(v) for k, v in (s.get('environment') or {}).items()},
            'mounts': sorted(mount(v) for v in s.get('volumes') or []),
            'ports': ports,
            'command': s.get('command'), 'entrypoint': s.get('entrypoint'),
            'networks': sorted((networks.get(n) or {}).get('name', n) for n in (s.get('networks') or {})),
            'memory': int(s['mem_limit']) if s.get('mem_limit') else 0,
            'nano_cpus': int(round(float(s['cpus']) * 1e9)) if s.get('cpus') else 0,
            'restart': s.get('restart') or 'no'}


def running_view(container, image):
    hc = container['HostConfig']
    image_volumes = set((image['Config'].get('Volumes') or {}))
    mounts = sorted([m['Type'], m.get('Name') if m['Type'] == 'volume' else m['Source'], m['Destination'], m['RW']]
                    for m in container['Mounts'] if not (m['Type'] == 'volume' and m['Destination'] in image_volumes))
    ports = sorted([b.get('HostIp', ''), b.get('HostPort', ''), int(k.split('/')[0]), k.split('/')[1]]
                   for k, binds in (hc.get('PortBindings') or {}).items() for b in binds or [])
    return {'image': container['Image'], 'env': env_dict(container['Config']['Env']),
            'image_env': env_dict(image['Config'].get('Env')),
            'mounts': mounts, 'ports': ports,
            'command': container['Config'].get('Cmd'), 'entrypoint': container['Config'].get('Entrypoint'),
            'image_command': image['Config'].get('Cmd'), 'image_entrypoint': image['Config'].get('Entrypoint'),
            'networks': sorted(container['NetworkSettings']['Networks']),
            'memory': hc.get('Memory') or 0, 'nano_cpus': hc.get('NanoCpus') or 0,
            'restart': (hc.get('RestartPolicy') or {}).get('Name') or 'no'}


def config_diff(rendered, running):
    """Differences other than the image, as names only (never values)."""
    diff = []
    env, live = rendered['env'], running['env']
    for k in sorted(set(env) | set(live)):
        if k in env:
            if live.get(k) != env[k]:
                diff.append(f'env:{k}')
        elif running['image_env'].get(k) != live[k]:
            diff.append(f'env:{k}')        # set on the container, but by nothing in the chain any more
    for key in ('command', 'entrypoint'):
        want = rendered[key] if rendered[key] is not None else running['image_' + key]
        if want != running[key]:
            diff.append(key)
    for key in ('mounts', 'ports', 'networks', 'memory', 'nano_cpus', 'restart'):
        if rendered[key] != running[key]:
            diff.append(key)
    return diff


def config_summary(view):
    return {'image': view['image'], 'env_keys': sorted(view['env']),
            'env_sha256': digest(f'{k}={v}' for k, v in view['env'].items()),
            'mounts': view['mounts'], 'ports': view['ports'], 'networks': view['networks']}


# ---------- preconditions ----------

def read_hold(service):
    path = PINS / f'HOLD-{service}'
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return {'owner': '?', 'reason': path.read_text()[:200]}


def check_hold(service, operator):
    hold = read_hold(service)
    if hold and hold.get('owner') != operator:
        raise Refused(f'{service} is held by {hold.get("owner")}: {hold.get("reason", "")} '
                      f'(remove {PINS}/HOLD-{service} only with the holder\'s agreement)')
    return hold


def source_commit(image):
    sha = (image['Config'].get('Labels') or {}).get('minimoi.source_commit')
    return sha if sha and SHA.fullmatch(sha) else None


def dropped_commits(host, current, target):
    """Commits on dev now that the target does not contain."""
    if current == target:
        return []
    out = host.git('rev-list', '--oneline', f'{target}..{current}')
    return [line for line in out.splitlines() if line]


def changed_paths(host, current, target):
    return [p for p in host.git('diff', '--name-only', current, target).splitlines() if p]


def migration_paths(host, current, target):
    if current:
        return [p for p in changed_paths(host, current, target) if MIGRATIONS.search(p)]
    return [p for p in host.git('ls-tree', '-r', '--name-only', target).splitlines() if MIGRATIONS.search(p)]


def overlay_blockers(host, row, current, target):
    if not current:
        return ['the running image has no minimoi.source_commit label']
    watched = {row['dockerfile'], *row['deps']}
    return [p for p in changed_paths(host, current, target) if p in watched or Path(p).name in DEP_FILES]


# ---------- build ----------

def build(host, service, row, commit, mode, base_image, record, labels):
    tag = f'minimoi-{service}:dev-{commit[:12]}'
    label_args = [a for k, v in sorted(labels.items()) for a in ('--label', f'{k}={v}')]
    log = record / 'build.log'
    iid = record / 'IMAGE'
    if mode == 'full':
        context = host.git_bytes('archive', '--format=tar', commit)
        dockerfile = row['dockerfile']
    else:
        # BuildKit cannot start FROM a bare image ID, so name the exact running image first.
        base_tag = f'minimoi-{service}:dev-overlay-base'
        host.docker('tag', base_image['Id'], base_tag)
        context, dockerfile = overlay_context(host, row, commit, base_image, base_tag)
    (record / 'build-context.sha256').write_text(hashlib.sha256(context).hexdigest() + '\n')
    host.docker('build', '-f', dockerfile, '--iidfile', str(iid), '-t', tag, *label_args, '-',
                stdin=context, log=str(log), timeout=1800)
    image = iid.read_text().strip()
    if not IMAGE_ID.fullmatch(image):
        raise RuntimeError(f'build produced no image id: {image!r}')
    return image, tag


def overlay_context(host, row, commit, base_image, base_tag):
    """A tar of the service's source directories at the commit plus a Dockerfile that replaces them
    wholesale on top of the running image (so deletions are real)."""
    import io
    import tarfile
    archive = host.git_bytes('archive', '--format=tar', commit, '--', *row['source'])
    user = base_image['Config'].get('User') or 'root'
    root = row['app_root']
    lines = [f'FROM {base_tag}', 'USER root',
             'RUN rm -rf ' + ' '.join(f'{root}/{d}' for d in row['source'])]
    lines += [f'COPY {d}/ {root}/{d}/' for d in row['source']]
    lines.append(f'USER {user}')
    dockerfile = ('\n'.join(lines) + '\n').encode()
    buf = io.BytesIO()
    # GNU format: Docker does not recognise a stdin context that opens with Python's pax header.
    with tarfile.open(fileobj=io.BytesIO(archive)) as src, \
            tarfile.open(fileobj=buf, mode='w', format=tarfile.GNU_FORMAT) as out:
        for member in src.getmembers():
            out.addfile(member, src.extractfile(member) if member.isfile() else None)
        info = tarfile.TarInfo('Dockerfile.dev-overlay')
        info.size = len(dockerfile)
        out.addfile(info, io.BytesIO(dockerfile))
    return buf.getvalue(), 'Dockerfile.dev-overlay'


# ---------- apply and verify ----------

def write_pin(path, service, image, sidecars):
    doc = {'services': {name: {'image': image} for name in (service, *[s for s, _ in sidecars])}}
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(doc, indent=2) + '\n')
    os.replace(tmp, path)
    return doc


def up(host, chain, files, env, log):
    host.run(compose_cmd(chain, files) + ['up', '-d', '--no-deps', chain['service']], env=env, log=str(log), timeout=300)


def source_check(host, row, container_name, commit, container):
    tree = host.git('ls-tree', '-r', commit, '--', *row['source'])
    mounted = [m['Destination'] for m in container['Mounts']]
    expected = {}
    for line in tree.splitlines():
        meta, path = line.split('\t', 1)
        _, kind, blob = meta.split()
        target = f'{row["app_root"]}/{path}'
        if kind == 'blob' and path.endswith('.py') and not any(target.startswith(m + '/') for m in mounted):
            expected[target] = blob
    got = json.loads(host.docker('exec', '-i', container_name, 'python3', '-c', BLOB_PROBE,
                                 stdin=json.dumps(sorted(expected)).encode(), timeout=120))
    bad = sorted(p for p, blob in expected.items() if got.get(p) != blob)
    return {'files': len(expected), 'mismatched': bad[:20], 'ok': not bad}


def health(host, row, container_name, image, commit, timeout):
    """Wait for the container to be up on the expected image, then run every check once."""
    deadline = time.monotonic() + timeout
    checks = {}
    while True:
        c = host.inspect('container', container_name)
        state = (c or {}).get('State') or {}
        hc = (state.get('Health') or {}).get('Status')
        urls = {u: host.http_status(u) for u in row['health']}
        ready = (state.get('Running') and not state.get('Restarting') and hc in (None, 'healthy')
                 and all(s == 200 for s in urls.values()))
        if ready or time.monotonic() >= deadline:
            break
        host.sleep(3)
    image_doc = host.inspect('image', image) or {}
    checks['running'] = bool(state.get('Running')) and not state.get('Restarting')
    checks['image'] = (c or {}).get('Image') == image
    checks['docker_health'] = hc or 'none defined'
    checks['urls'] = urls
    if commit:
        checks['label'] = source_commit(image_doc) == commit
        checks['source'] = source_check(host, row, container_name, commit, c) if checks['running'] else {'ok': False}
    else:
        checks['label'] = checks['source'] = 'skipped: target image has no source label'
    ok = (checks['running'] and checks['image'] and hc in (None, 'healthy')
          and all(s == 200 for s in urls.values())
          and (not commit or (checks['label'] and checks['source']['ok'])))
    return ok, checks


# ---------- records ----------

def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def new_record(name):
    """A fresh record directory; a second change in the same second gets a -2 suffix, never a shared one."""
    OPERATORS.mkdir(parents=True, exist_ok=True)
    for n in range(1, 100):
        record = OPERATORS / (name if n == 1 else f'{name}-{n}')
        try:
            record.mkdir()
            return record
        except FileExistsError:
            continue
    raise RuntimeError(f'cannot create a record directory for {name}')


def history(service=None):
    path = PINS / 'HISTORY.jsonl'
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if service is None or r['service'] == service]


def append_history(row):
    PINS.mkdir(parents=True, exist_ok=True)
    with open(PINS / 'HISTORY.jsonl', 'a') as f:
        f.write(json.dumps(row, sort_keys=True) + '\n')


class Lock:
    def __enter__(self):
        PINS.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(PINS / '.deploy.lock', os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            raise Refused('another dev deploy is running')
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def deploy_gates(host, row, current, commit, mode, flags, gate, plan):
    """Refusals shared by every kind of service: dropped commits, unknown current code, migrations, overlay."""
    if current:
        dropped = dropped_commits(host, current, commit)
        plan['drops'] = dropped
        if dropped and not flags.get('replace_unrelated'):
            gate(f'{len(dropped)} commit(s) on dev now are not in {commit[:12]}:\n  '
                 + '\n  '.join(dropped[:15]) + '\nre-run with --replace-unrelated to drop them')
    elif not flags.get('accept_unknown_current'):
        gate(f'what dev runs now for this service has no known source commit; '
             're-run with --accept-unknown-current (recorded) for this first labelled deploy')
    migrations = migration_paths(host, current, commit)
    plan['migrations'] = migrations
    if migrations and not flags.get('migrations_applied'):
        gate('migrations are involved; apply them explicitly as their own recorded step, then re-run '
             'with --with-migrations-applied (code rollback never undoes schema):\n  '
             + '\n  '.join(migrations[:20]))
    if row['kind'] == 'launchd' and current:
        deps = [p for p in changed_paths(host, current, commit) if p in row['deps']]
        if deps:
            gate('the shared venv is not pinned and ' + ', '.join(deps) + ' changed; update the venv as its own '
                 'recorded step first')
    if mode == 'overlay':
        blockers = overlay_blockers(host, row, current, commit) if row['kind'] == 'container' \
            else ['overlay applies to container images only']
        if blockers:
            gate('overlay not allowed (' + ', '.join(blockers[:10]) + '); use a full build')


# ---------- the change itself (deploy and rollback share it) ----------

def change(host, service, operator, *, action, commit=None, target_image=None, mode=None, flags=None,
           dry_run=False, timeout=90, out=print):
    """Move `service` to a new image. `action` is 'deploy' (build from `commit`) or 'rollback'
    (re-pin `target_image`). Returns the record directory (None for a dry run)."""
    row = SERVICES[service]
    if row['kind'] == 'launchd':
        return change_launchd(host, service, operator, action=action, commit=commit, target=target_image,
                              mode=mode, flags=flags, dry_run=dry_run, timeout=timeout, out=out)
    flags = flags or {}
    check_hold(service, operator)
    container = host.inspect('container', row['container'])
    if not container:
        raise Refused(f'{row["container"]} is not running on this host')
    chain = chain_of(container)
    before_image = host.inspect('image', container['Image'])
    current = source_commit(before_image)
    sidecars = [(name, host.inspect('container', cname)) for name, cname in row['sidecars']]
    sidecars = [(name, c) for name, c in sidecars if c]
    for name, c in sidecars:
        if c['Image'] != container['Image']:
            raise Refused(f'sidecar {name} runs {c["Image"][:19]}, not the {service} image; not moving them together')

    plan = {'service': service, 'action': action, 'operator': operator, 'container': row['container'],
            'image_before': container['Image'], 'commit_before': current, 'commit': commit, 'mode': mode,
            'flags': flags, 'sidecars': [n for n, _ in sidecars], 'dry_run': dry_run}

    def gate(message):
        # A dry run reports every refusal it would hit; a real run stops at the first.
        if not dry_run:
            raise Refused(message)
        plan.setdefault('would_refuse', []).append(message)
    if action == 'deploy':
        deploy_gates(host, row, current, commit, mode, flags, gate, plan)
    if row['mc_turn_probe']:
        busy = host.docker('exec', row['container'], 'python', '-c', MC_TURN_PROBE, timeout=30).strip()
        if busy != '0':
            gate('an MC turn is in progress on the portal; try again when it ends')

    env = compose_env(host)
    PINS.mkdir(parents=True, exist_ok=True)
    pin = PINS / f'{service}.json'
    files = with_pin(chain['files'], pin)
    running = running_view(container, before_image)
    if dry_run:
        probe = PINS / f'.{service}.dry-run.json'
        write_pin(probe, service, container['Image'], sidecars)
        try:
            rendered = rendered_view(render(host, chain, with_pin(chain['files'], probe), env), chain['service'])
        finally:
            probe.unlink()
        plan['config_diff'] = config_diff(rendered, running)
        plan['chain_files'] = len(files)
        plan['pin_in_chain_already'] = str(pin) in chain['files']
        out(json.dumps(plan, indent=2, sort_keys=True))
        return None

    now = host.now()
    name = f'dev-{action}-{service}-{(commit or target_image[7:])[:12]}-{utc_stamp(now)}'
    record = new_record(name)
    plan['started'] = now.isoformat()
    plan['host_production_changed'] = False
    write_json(record / 'REQUEST.json', plan)
    write_json(record / 'running-before.json', config_summary(running))
    write_json(record / 'pin-before.json', json.loads(pin.read_text()) if pin.exists()
               else {'services': {n: {'image': container['Image']} for n in (service, *[s for s, _ in sidecars])}})
    gateway_before = host.gateway()
    out(f'record: {record}')

    try:
        if action == 'deploy':
            out(f'building {service} at {commit[:12]} ({mode})…')
            # No per-record label: the same commit and mode build the same image, so a re-deploy is identical.
            labels = {'minimoi.source_commit': commit, 'minimoi.environment': 'dev', 'minimoi.service': service}
            image, tag = build(host, service, row, commit, mode, before_image, record, labels)
        else:
            image, tag = target_image, None
            commit = source_commit(host.inspect('image', image) or {'Config': {}})
        candidate = record / 'pin-after.json'
        write_pin(candidate, service, image, sidecars)
        target_image_doc = host.inspect('image', image)
        rendered = rendered_view(render(host, chain, with_pin(chain['files'], candidate), env), chain['service'])
        diff = config_diff(rendered, running)
        write_json(record / 'config-before.json', config_summary(running))
        write_json(record / 'config-after.json', dict(config_summary(rendered), diff=diff))
        if rendered['image'] != image:
            raise Refused(f'a file after the pin overrides the image ({rendered["image"]}); nothing changed')
        if diff:
            raise Refused('the rendered config differs from the running one in more than the image: '
                          + ', '.join(diff) + '; nothing changed (see config-after.json)')
    except Exception as exc:
        # Nothing on dev changed yet: the container still runs the old image.
        write_json(record / 'REFUSED.json', {'stage': 'build/config check', 'reason': f'{type(exc).__name__}: {exc}'[:1500],
                                             'dev_changed': False, 'at': host.now().isoformat()})
        raise

    host.docker('tag', container['Image'], f'minimoi-{service}:dev-prev')   # keeps the rollback target from a prune
    pin_before = json.loads((record / 'pin-before.json').read_text())
    (record / 'rollback.sh').write_text(
        f'#!/bin/sh\n# Put {service} back on the image it ran before this record. Usage: rollback.sh <operator>\n'
        f'script="{ROOT}/scripts/operations/deploy_dev.sh"\n'
        f'[ -x "$script" ] || script="$HOME/Projects/personal-ai-agents/scripts/operations/deploy_dev.sh"\n'
        f'exec "$script" rollback {service} --to "{record}" --operator "${{1:?operator name}}"\n')
    os.chmod(record / 'rollback.sh', 0o755)

    def apply(pin_doc, log):
        write_pin(pin, service, pin_doc['services'][service]['image'], sidecars)
        up(host, chain, files, env, record / log)
        for name, c in sidecars:
            up(host, chain_of(c), with_pin(chain_of(c)['files'], pin), env, record / log)

    out(f'applying {image[:19]} to {row["container"]}…')
    try:
        apply({'services': {service: {'image': image}}}, 'up.log')
        ok, checks = health(host, row, row['container'], image, commit, timeout)
        for name, c in sidecars:
            sc = host.inspect('container', c['Name'].lstrip('/')) or {}
            checks[f'sidecar:{name}'] = sc.get('Image') == image and (sc.get('State') or {}).get('Running', False)
            ok = ok and checks[f'sidecar:{name}']
    except Exception as exc:
        ok, checks = False, {'error': f'{type(exc).__name__}: {exc}'[:500]}
    gateway_after = host.gateway()
    checks['gateway_untouched'] = gateway_after == gateway_before
    ok = ok and checks['gateway_untouched']
    write_json(record / 'health.json', checks)
    after = host.inspect('container', row['container'])
    if after:
        write_json(record / 'running-after.json',
                   config_summary(running_view(after, host.inspect('image', after['Image']) or target_image_doc)))

    entry = {'service': service, 'action': action, 'operator': operator, 'record': str(record),
             'image_before': container['Image'], 'image': image, 'commit_before': current, 'commit': commit,
             'at': host.now().isoformat()}
    if ok:
        write_json(record / 'RESULT.json', dict(entry, ok=True, tag=tag, production_changed=False,
                                                gateway_restarted=False))
        append_history(dict(entry, ok=True))
        out(f'ok: {service} runs {image[:19]} ({(commit or "unlabelled")[:12]}); rollback: {record}/rollback.sh')
        return record

    out('verification failed; rolling back…')
    try:
        apply(pin_before, 'rollback.log')
        back_ok, back = health(host, row, row['container'], container['Image'], current, timeout)
    except Exception as exc:
        back_ok, back = False, {'error': f'{type(exc).__name__}: {exc}'[:500]}
    write_json(record / 'FAILURE.json', dict(entry, ok=False, checks=checks, rolled_back=back_ok, rollback=back,
                                             production_changed=False))
    append_history(dict(entry, ok=False, rolled_back=back_ok,
                        image=container['Image'] if back_ok else image))
    raise RuntimeError(f'{action} of {service} failed verification; rolled back: {back_ok}. See {record}')


# ---------- launchd agents (Mac only): pinned code directories ----------
#
# A launchd agent has no image. Each deploy extracts `git archive <commit>` into an immutable directory
# CODE/<service>/<commit>/, links the agent's state paths back to their current home, and re-points the
# plist (plistlib; only the script argument and WorkingDirectory change) before reloading the job.
# The "image" in records and history is that code directory, so rollback is a re-point.

def parse_launchd(text):
    job, args, in_args = {}, [], False
    for raw in text.splitlines():
        line = raw.strip()
        if in_args:
            if line == '}':
                in_args = False
            else:
                args.append(line)
            continue
        if line == 'arguments = {':
            in_args = True
        elif ' = ' in line and raw.startswith('\t') and not raw.startswith('\t\t'):
            key, value = line.split(' = ', 1)
            job.setdefault(key.replace(' ', '_'), value)
    job['arguments'] = args
    if 'pid' in job:
        job['pid'] = int(job['pid'])
    return job


def location_commit(host, location):
    """The commit a code directory holds: our marker, or a clean git checkout's HEAD; None if unknown."""
    marker = Path(location) / '.minimoi-source-commit'
    if marker.exists():
        sha = marker.read_text().strip()
        return sha if SHA.fullmatch(sha) else None
    head = host.git_at(location, 'rev-parse', 'HEAD')
    dirty = host.git_at(location, 'status', '--porcelain', '--untracked-files=no')
    return head.strip() if head and dirty == '' and SHA.fullmatch(head.strip()) else None


def repoint(plist, location, script):
    new = json.loads(json.dumps(plist))
    new['ProgramArguments'] = [plist['ProgramArguments'][0], f'{location}/{script}']
    new['WorkingDirectory'] = str(location)
    return new


def plist_diff(old, new):
    """Plist keys that differ beyond the code location (names only)."""
    keys = sorted(set(old) | set(new))
    diff = [k for k in keys if k not in ('ProgramArguments', 'WorkingDirectory') and old.get(k) != new.get(k)]
    if old.get('ProgramArguments', [None])[:1] != new.get('ProgramArguments', [None])[:1] \
            or len(old.get('ProgramArguments', [])) != len(new.get('ProgramArguments', [])):
        diff.append('ProgramArguments[interpreter]')
    return diff


def prepare_code(host, row, commit, state_home):
    """CODE/<service>/<commit>, extracted once and reused; state paths link to `state_home`."""
    service = next(k for k, v in SERVICES.items() if v is row)
    target = CODE / service / commit
    if (target / '.minimoi-source-commit').exists():
        if (target / '.minimoi-source-commit').read_text().strip() != commit:
            raise Refused(f'{target} holds a different commit')
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f'.{commit}.partial'
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    host.extract(commit, tmp)
    for rel in row['state']:
        home = Path(state_home) / rel
        home.mkdir(parents=True, exist_ok=True)
        path = tmp / rel
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(home)
    (tmp / '.minimoi-source-commit').write_text(commit + '\n')
    os.replace(tmp, target)
    return target


def local_source_check(host, row, location, commit):
    tree = host.git('ls-tree', '-r', commit, '--', *row['source'])
    bad, files = [], 0
    for line in tree.splitlines():
        meta, path = line.split('\t', 1)
        _, kind, blob = meta.split()
        if kind != 'blob' or not path.endswith('.py') or any(path.startswith(s + '/') for s in row['state']):
            continue
        files += 1
        try:
            b = (Path(location) / path).read_bytes()
        except OSError:
            bad.append(path)
            continue
        if hashlib.sha1(b'blob %d\0' % len(b) + b).hexdigest() != blob:
            bad.append(path)
    return {'files': files, 'mismatched': bad[:20], 'ok': files > 0 and not bad}


def launchd_health(host, row, location, pid_before, commit, timeout):
    deadline = time.monotonic() + timeout
    script = f'{location}/{row["script"]}'
    while True:
        job = host.launchd(row['label']) or {}
        pid = job.get('pid')
        command = host.process_args(pid) if pid else ''
        urls = {u: host.http_status(u) for u in row['health']}
        ready = (job.get('state') == 'running' and pid and pid != pid_before and script in command
                 and all(v == 200 for v in urls.values()))
        if ready or time.monotonic() >= deadline:
            break
        host.sleep(2)
    checks = {'running': job.get('state') == 'running', 'new_process': bool(pid) and pid != pid_before,
              'runs_pinned_code': script in command, 'urls': urls}
    checks['source'] = local_source_check(host, row, location, commit) if commit \
        else 'skipped: the code location has no known commit'
    ok = (checks['running'] and checks['new_process'] and checks['runs_pinned_code']
          and all(v == 200 for v in urls.values()) and (not commit or checks['source']['ok']))
    return ok, checks


def reload_job(host, label, plist_path, plist_bytes):
    tmp = plist_path.with_suffix('.plist.tmp')
    tmp.write_bytes(plist_bytes)
    os.replace(tmp, plist_path)
    host.launchctl('bootout', f'gui/{{uid}}/{label}', check=False)
    for _ in range(20):
        if host.launchd(label) is None:
            break
        host.sleep(0.5)
    host.launchctl('bootstrap', 'gui/{uid}', str(plist_path))
    host.launchctl('kickstart', f'gui/{{uid}}/{label}', check=False)


def change_launchd(host, service, operator, *, action, commit=None, target=None, mode=None, flags=None,
                   dry_run=False, timeout=90, out=print):
    row = SERVICES[service]
    flags = flags or {}
    check_hold(service, operator)
    job = host.launchd(row['label'])
    if not job:
        raise Refused(f'launchd job {row["label"]} is not loaded on this host')
    plist_path = Path(job['path'])
    plist_bytes = plist_path.read_bytes()
    plist = plistlib.loads(plist_bytes)
    location = plist.get('WorkingDirectory', '')
    args = plist.get('ProgramArguments', [])
    if len(args) != 2 or args[1] != f'{location}/{row["script"]}':
        raise Refused(f'{plist_path} does not run {row["script"]} from its WorkingDirectory; not guessing')
    if job.get('arguments') != args:
        raise Refused(f'the loaded {row["label"]} job differs from {plist_path}; someone edited it without reloading')
    current = location_commit(host, location)
    PINS.mkdir(parents=True, exist_ok=True)
    pin = PINS / f'{service}.json'
    pinned = json.loads(pin.read_text()) if pin.exists() else {}
    state_home = pinned.get('state_home') or location

    plan = {'service': service, 'action': action, 'operator': operator, 'label': row['label'],
            'image_before': location, 'commit_before': current, 'commit': commit, 'mode': mode or 'code',
            'flags': flags, 'state_home': state_home, 'dry_run': dry_run}

    def gate(message):
        if not dry_run:
            raise Refused(message)
        plan.setdefault('would_refuse', []).append(message)
    if action == 'deploy':
        deploy_gates(host, row, current, commit, mode, flags, gate, plan)
    if dry_run:
        plan['config_diff'] = plist_diff(plist, repoint(plist, location, row['script']))
        out(json.dumps(plan, indent=2, sort_keys=True))
        return None

    now = host.now()
    tag = commit[:12] if commit else Path(target).name[:12]
    record = new_record(f'dev-{action}-{service}-{tag}-{utc_stamp(now)}')
    plan['started'] = now.isoformat()
    plan['host_production_changed'] = False
    write_json(record / 'REQUEST.json', plan)
    (record / 'plist-before.plist').write_bytes(plist_bytes)
    write_json(record / 'pin-before.json', {'location': location, 'state_home': state_home, 'commit': current})
    gateway_before = host.gateway()
    out(f'record: {record}')
    try:
        if action == 'deploy':
            out(f'extracting {service} at {commit[:12]}…')
            new_location = prepare_code(host, row, commit, state_home)
        else:
            new_location = Path(target)
            if not (new_location / row['script']).exists():
                raise Refused(f'{new_location} has no {row["script"]}')
            commit = location_commit(host, new_location)
        new_plist = repoint(plist, new_location, row['script'])
        diff = plist_diff(plist, new_plist)
        new_bytes = plistlib.dumps(new_plist)
        (record / 'plist-after.plist').write_bytes(new_bytes)
        write_json(record / 'pin-after.json', {'location': str(new_location), 'state_home': state_home,
                                               'commit': commit})
        if diff:
            raise Refused('the re-pointed plist differs in more than the code location: ' + ', '.join(diff))
    except Exception as exc:
        write_json(record / 'REFUSED.json', {'stage': 'code/config check', 'reason': f'{type(exc).__name__}: {exc}'[:1500],
                                             'dev_changed': False, 'at': host.now().isoformat()})
        raise
    (record / 'rollback.sh').write_text(
        f'#!/bin/sh\n# Put {service} back on the code it ran before this record. Usage: rollback.sh <operator>\n'
        f'script="{ROOT}/scripts/operations/deploy_dev.sh"\n'
        f'[ -x "$script" ] || script="$HOME/Projects/personal-ai-agents/scripts/operations/deploy_dev.sh"\n'
        f'exec "$script" rollback {service} --to "{record}" --operator "${{1:?operator name}}"\n')
    os.chmod(record / 'rollback.sh', 0o755)

    out(f'pointing {row["label"]} at {new_location}…')
    pid_before = job.get('pid')
    try:
        reload_job(host, row['label'], plist_path, new_bytes)
        ok, checks = launchd_health(host, row, str(new_location), pid_before, commit, timeout)
    except Exception as exc:
        ok, checks = False, {'error': f'{type(exc).__name__}: {exc}'[:500]}
    checks['gateway_untouched'] = host.gateway() == gateway_before
    ok = ok and checks['gateway_untouched']
    write_json(record / 'health.json', checks)
    entry = {'service': service, 'action': action, 'operator': operator, 'record': str(record),
             'image_before': location, 'image': str(new_location), 'commit_before': current, 'commit': commit,
             'at': host.now().isoformat()}
    if ok:
        write_json(pin, {'location': str(new_location), 'state_home': state_home, 'commit': commit})
        write_json(record / 'RESULT.json', dict(entry, ok=True, production_changed=False, gateway_restarted=False))
        append_history(dict(entry, ok=True))
        out(f'ok: {service} runs {new_location} ({(commit or "unknown")[:12]}); rollback: {record}/rollback.sh')
        return record

    out('verification failed; rolling back…')
    try:
        pid_now = (host.launchd(row['label']) or {}).get('pid')
        reload_job(host, row['label'], plist_path, plist_bytes)
        back_ok, back = launchd_health(host, row, location, pid_now, current, timeout)
    except Exception as exc:
        back_ok, back = False, {'error': f'{type(exc).__name__}: {exc}'[:500]}
    write_json(record / 'FAILURE.json', dict(entry, ok=False, checks=checks, rolled_back=back_ok, rollback=back,
                                             production_changed=False))
    append_history(dict(entry, ok=False, rolled_back=back_ok, image=location if back_ok else str(new_location)))
    raise RuntimeError(f'{action} of {service} failed verification; rolled back: {back_ok}. See {record}')


def rollback_target(service, to=None):
    if to:
        record = Path(to)
        doc = json.loads((record / 'pin-before.json').read_text())
        return (doc['location'] if 'location' in doc else doc['services'][service]['image']), record
    rows = [r for r in history(service) if r.get('ok')]
    if not rows:
        raise Refused(f'no recorded change for {service} to roll back')
    last = rows[-1]
    return last['image_before'], Path(last['record'])


def status(host, service=None, out=print):
    names = [service] if service else list(SERVICES)
    rows = []
    for name in names:
        if SERVICES[name]['kind'] == 'launchd':
            job = host.launchd(SERVICES[name]['label'])
            location = job and job.get('working_directory')
            rows.append({'service': name, 'running': bool(job and job.get('state') == 'running'),
                         'location': location, 'commit': location and location_commit(host, location),
                         'hold': read_hold(name), 'last_change': ([r for r in history(name)] or [None])[-1]})
            continue
        c = host.inspect('container', SERVICES[name]['container'])
        image = host.inspect('image', c['Image']) if c else None
        last = ([r for r in history(name)] or [None])[-1]
        rows.append({'service': name, 'running': bool(c and c['State'].get('Running')),
                     'image': c and c['Image'], 'commit': image and source_commit(image),
                     'hold': read_hold(name), 'last_change': last})
    out(json.dumps(rows, indent=2, sort_keys=True))
    return rows


def main(argv=None, host=None):
    host = host or Host()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    d = sub.add_parser('deploy')
    d.add_argument('service', choices=sorted(SERVICES))
    d.add_argument('commit')
    d.add_argument('--operator', required=True)
    d.add_argument('--overlay', action='store_true')
    d.add_argument('--with-migrations-applied', action='store_true')
    d.add_argument('--accept-unknown-current', action='store_true')
    d.add_argument('--replace-unrelated', action='store_true')
    d.add_argument('--dry-run', action='store_true')
    d.add_argument('--timeout', type=int, default=90)
    r = sub.add_parser('rollback')
    r.add_argument('service', choices=sorted(SERVICES))
    r.add_argument('--operator', required=True)
    r.add_argument('--to')
    r.add_argument('--dry-run', action='store_true')
    r.add_argument('--timeout', type=int, default=90)
    s = sub.add_parser('status')
    s.add_argument('service', nargs='?', choices=sorted(SERVICES))
    a = p.parse_args(argv)
    try:
        if a.cmd == 'status':
            status(host, a.service)
            return 0
        if not a.dry_run and os.environ.get('MINIMOI_HEAVY_JOB') != '1':
            raise Refused('run through scripts/operations/deploy_dev.sh (it takes the heavy-job lock)')
        with Lock():
            if a.cmd == 'deploy':
                commit = host.git('rev-parse', '--verify', f'{a.commit}^{{commit}}').strip()
                flags = {'migrations_applied': a.with_migrations_applied,
                         'accept_unknown_current': a.accept_unknown_current,
                         'replace_unrelated': a.replace_unrelated}
                change(host, a.service, a.operator, action='deploy', commit=commit,
                       mode='overlay' if a.overlay else 'full', flags=flags, dry_run=a.dry_run, timeout=a.timeout)
            else:
                image, source = rollback_target(a.service, a.to)
                print(f'rolling {a.service} back to {image[:19]} (from {source})')
                change(host, a.service, a.operator, action='rollback', target_image=image,
                       flags={'from_record': str(source)}, dry_run=a.dry_run, timeout=a.timeout)
        return 0
    except Refused as e:
        print(f'refused: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
