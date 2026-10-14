#!/usr/bin/env python3
"""Prepare a pinned local Vault runtime and candidate plists; never installs jobs or grants.

prepare --config PROFILE --out NEW_DIRECTORY
run-job --config PROFILE --job capture|index|backup|watchdog (operator activation only)
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ROOTS = ('core/memory_shelf', 'core/memory_index', 'core/memory_ask', 'core/memory_backup', 'core/vault_t1', 'core/jobs')
FILES = ('core/__init__.py', 'core/agent_turns/__init__.py', 'core/agent_turns/writer.py',
         'utils/__init__.py', 'utils/credential_scrub.py', 'utils/payment_scrub.py',
         'scripts/moi', 'scripts/jobs/watchdog.py', 'scripts/vault/backup.py',
         'scripts/vault/runtime_install.py', 'requirements-vault.txt')
LABELS = {'capture': 'com.vanstedum.minimoi-memory-watch', 'index': 'com.vanstedum.minimoi-memory-index',
          'backup': 'com.vanstedum.minimoi-vault-backup', 'watchdog': 'com.vanstedum.minimoi-jobs-watchdog',
          'ask': 'com.vanstedum.minimoi-vault-ask'}
TIMES = {'capture': (0, 15), 'index': (2, 30), 'backup': (4, 30), 'watchdog': (9, 0)}
KEYS = {'python', 'shelf_root', 'inbox_root', 'jobs_root', 'logs_root', 'ask_registry',
        'age_bin', 'age_version', 'backup_dest', 'public_recipient', 'launch_agents'}


def profile(path):
    doc = json.loads(Path(path).read_text())
    if not isinstance(doc, dict) or set(doc) != KEYS or not all(isinstance(v, str) and v for v in doc.values()):
        raise ValueError('invalid_profile')
    for k in KEYS - {'age_version', 'backup_dest', 'public_recipient'}:
        doc[k] = str(Path(doc[k]).expanduser())
        if not Path(doc[k]).is_absolute():
            raise ValueError('path_not_absolute')
    if not doc['backup_dest'].startswith('s3://') or not re.fullmatch(r'age1[0-9a-z]{58}', doc['public_recipient']):
        raise ValueError('invalid_backup_settings')
    return doc


def runtime_files(repo):
    files = [repo / r for r in FILES]
    for r in ROOTS + ('scripts/memory',):
        files.extend((repo / r).glob('*.py'))
    return sorted(set(files))


def commands(pin, cfg, job):
    py = cfg['python']
    if job == 'ask':
        return [py, '-m', 'core.memory_ask.service', '--host', '127.0.0.1', '--port', '8771',
                '--shelf-root', cfg['shelf_root'], '--registry', cfg['ask_registry']]
    paths = {'capture': 'scripts/memory/daily_watch.py', 'index': 'scripts/memory/index_job.py',
             'watchdog': 'scripts/jobs/watchdog.py', 'backup': 'scripts/vault/backup.py'}
    result = [py, str(pin / paths[job])]
    if job == 'backup':
        result += ['run', '--execute']
    return result


def environment(pin, cfg):
    return {'PYTHONPATH': str(pin), 'PYTHONDONTWRITEBYTECODE': '1', 'MOI_PYTHON': cfg['python'],
            'MOI_CONFIG': str(pin / 'config/memory_shelf.json'), 'MINIMOI_JOBS_ROOT': cfg['jobs_root'],
            'MINIMOI_JOBS_REGISTRY': str(pin / 'config/scheduled_jobs.json'),
            'MOI_SHELF_ROOT': cfg['shelf_root'], 'MOI_VAULT_BACKUP_DEST': cfg['backup_dest'],
            'VAULT_BACKUP_AGE_RECIPIENT': cfg['public_recipient'], 'MOI_AGE_BIN': cfg['age_bin'],
            'MOI_AGE_VERSION': cfg['age_version'], 'MOI_RESERVE_GIB': '5',
            'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'}


def dependency_receipt(python):
    code = "import sys,sqlite3,json,importlib.metadata as m; import yaml,sqlite_vec,flask; print(json.dumps({'python':sys.version.split()[0],'sqlite':sqlite3.sqlite_version,'packages':{p:m.version(p) for p in ['Flask','PyYAML','sqlite-vec']}}))"
    done = subprocess.run([python, '-I', '-B', '-c', code], capture_output=True, text=True, timeout=30, check=True,
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    result = json.loads(done.stdout)
    if result['packages'] != {'Flask': '3.1.3', 'PyYAML': '6.0.3', 'sqlite-vec': '0.1.9'}:
        raise ValueError('dependency_pin_mismatch')
    return result


def prepare(repo, cfg, out):
    repo, out = Path(repo).resolve(), Path(out).expanduser().absolute()
    if '_working' in out.parts or out.is_relative_to(repo):
        raise ValueError('runtime_must_be_outside_repository_and_working')
    if out.exists() or out.is_symlink():
        raise ValueError('output_exists')
    receipt = dependency_receipt(cfg['python'])
    files = runtime_files(repo)
    if any(not p.is_file() or p.is_symlink() for p in files):
        raise ValueError('source_missing_or_link')
    hashes = {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    registry_bytes = (repo / 'config/scheduled_jobs.json').read_bytes()
    input_hashes = {'config/scheduled_jobs.json': hashlib.sha256(registry_bytes).hexdigest()}
    head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
    tree = hashlib.sha256(json.dumps({'files': hashes, 'profile': cfg, 'inputs': input_hashes, 'dependencies': receipt}, sort_keys=True).encode()).hexdigest()
    out.mkdir(mode=0o700, parents=True)
    pin = out / 'runtime' / tree
    pin.mkdir(mode=0o700, parents=True)
    for src in files:
        dst = pin / src.relative_to(repo)
        dst.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        if hashlib.sha256(dst.read_bytes()).hexdigest() != hashes[str(src.relative_to(repo))]:
            raise ValueError('source_changed')
        dst.chmod(0o500 if src.name == 'moi' else 0o400)
    config = pin / 'config'
    config.mkdir(mode=0o700)
    shelf = {'schema_version': 1, 'shelf_root': cfg['shelf_root'], 'inbox_root': cfg['inbox_root'],
             'repo_root': str(pin), 'headroom': {'min_free_bytes': 5 << 30}, 'never_copy': [],
             'sources': {'claude-code': {'kind': 'claude-code', 'root': str(Path.home() / '.claude/projects'), 'never_copy': []},
                         'codex': {'kind': 'codex', 'root': str(Path.home() / '.codex/sessions'), 'never_copy': []}}}
    (config / 'memory_shelf.json').write_text(json.dumps(shelf, indent=2) + '\n')
    (config / 'runtime.json').write_text(json.dumps(cfg, indent=2) + '\n')
    registry = json.loads(registry_bytes)
    for job in ('capture', 'index', 'backup', 'watchdog'):
        job_id = {'capture': 'memory-watch', 'index': 'memory-index', 'backup': 'vault-backup', 'watchdog': 'jobs-watchdog'}[job]
        h, minute = TIMES[job]
        row = next((r for r in registry['jobs'] if r['id'] == job_id), None)
        if row is None:
            row = {'id': job_id, 'name': job_id, 'host': 'mac', 'expected_every_s': 86400,
                   'missed_after_s': 180000, 'stuck_after_s': 7200, 'status_file': job_id + '.json',
                   'owner': 'robert', 'runbook': 'docs/vault/RECOVERY.md'}
            registry['jobs'].append(row)
        row['schedule'] = {'text': f'daily {h:02}:{minute:02} Chicago', 'kind': 'daily',
                           'at': f'{h:02}:{minute:02}', 'tz': 'America/Chicago'}
    (config / 'scheduled_jobs.json').write_text(json.dumps(registry, indent=2) + '\n')
    candidates, previous = out / 'candidate-plists', out / 'previous-plists'
    candidates.mkdir(mode=0o700); previous.mkdir(mode=0o700)
    originals = {}
    for job, label in LABELS.items():
        old = Path(cfg['launch_agents']) / (label + '.plist')
        originals[label] = old.is_file()
        if old.is_symlink():
            raise ValueError('existing_plist_is_link')
        if old.is_file():
            shutil.copyfile(old, previous / old.name)
            (previous / old.name).chmod(0o600)
        args = commands(pin, cfg, job) if job == 'ask' else [cfg['python'], str(pin / 'scripts/vault/runtime_install.py'),
                    'run-job', '--config', str(config / 'runtime.json'), '--job', job]
        doc = {'Label': label, 'ProgramArguments': args, 'WorkingDirectory': str(pin),
               'EnvironmentVariables': environment(pin, cfg),
               'StandardOutPath': str(Path(cfg['logs_root']) / (label + '.log')),
               'StandardErrorPath': str(Path(cfg['logs_root']) / (label + '.log'))}
        if job == 'ask':
            doc.update({'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 15})
        else:
            h, minute = TIMES[job]; doc.update({'RunAtLoad': False, 'StartCalendarInterval': {'Hour': h, 'Minute': minute}})
        (candidates / (label + '.plist')).write_bytes(plistlib.dumps(doc))
    manifest = {'format': 'vault-runtime/1', 'source_commit': head, 'source_files': hashes, 'source_inputs': input_hashes, 'source_tree': tree,
                'runtime': str(pin), 'dependency_receipt': receipt,
                'runtime_file_hashes': {str(p.relative_to(pin)): hashlib.sha256(p.read_bytes()).hexdigest() for p in pin.rglob('*') if p.is_file()},
                'previous_plists_present': originals, 'installed': False,
                'limitations': ['launchd calendar uses Mac timezone; verify America/Chicago before installation',
                                'busy batch skips that scheduled run; no automatic retry',
                                'Rooms source absent pending verified mapping', 'consumer retrieval curated only',
                                'existing shared venv is not immutable; retain dependency receipt',
                                'source approvals, Ask grants/tokens and installed jobs unchanged']}
    (out / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for p in config.iterdir():
        p.chmod(0o400)
    for d in sorted((p for p in pin.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        d.chmod(0o500)
    pin.chmod(0o500)
    return manifest


def run_job(cfg, job, pin=None):
    pin = Path(pin or Path(__file__).resolve().parents[2])
    if job != 'watchdog' and datetime.now(ZoneInfo('America/Chicago')).hour >= 6:
        print(json.dumps({'job': job, 'state': 'skipped', 'reason': 'outside_window'})); return 1
    jobs = Path(cfg['jobs_root']); jobs.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(jobs / 'vault-batch.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if job != 'watchdog':
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                result = {'job': job, 'state': 'skipped', 'reason': 'batch_busy', 'retry': False, 'at': datetime.now(ZoneInfo('America/Chicago')).isoformat()}
                temp = jobs / f'.{job}-admission-{os.getpid()}.tmp'
                temp.write_text(json.dumps(result) + '\n'); temp.chmod(0o600)
                os.replace(temp, jobs / f'{job}-admission.json')
                print(json.dumps(result)); return 1
        env = os.environ.copy(); env.update(environment(pin, cfg))
        return subprocess.run(commands(pin, cfg, job), cwd=pin, env=env, pass_fds=(fd,) if job != 'watchdog' else ()).returncode
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run-job')); parser.add_argument('--config', required=True)
    parser.add_argument('--out'); parser.add_argument('--job', choices=tuple(TIMES))
    args = parser.parse_args(); cfg = profile(args.config)
    if args.action == 'prepare':
        if not args.out: parser.error('--out required')
        result = prepare(Path(__file__).resolve().parents[2], cfg, args.out)
        print(json.dumps({'prepared': True, 'installed': False, 'source_commit': result['source_commit'], 'source_tree': result['source_tree']}))
        return 0
    if not args.job: parser.error('--job required')
    return run_job(cfg, args.job)

if __name__ == '__main__':
    sys.exit(main())
