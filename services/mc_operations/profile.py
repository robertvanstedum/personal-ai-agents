"""Add a separate operational MC to the owner's existing OpenClaw gateway.

No credentials, channel bindings or model settings change. Main is marked default
to preserve its existing sole-agent routing when MC is added.
The plan is printable; the candidate config is private and never printed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


class Refused(ValueError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def candidate(original, state_root, workspace):
    result = deepcopy(original)
    entries = result.setdefault('agents', {}).setdefault('entries', {})
    if not isinstance(entries, dict) or set(entries) != {'main'} or result['agents'].get('ownership') == 'explicit':
        raise Refused('requires_personal_single_main_roster')
    if 'mc-agent' in entries:
        raise Refused('mc_agent_already_exists')
    # Preserve the existing sole-main fallback when the roster becomes multi-agent.
    entries['main']['default'] = True
    # No model override: use the same configured backend as the personal agent.
    entries['mc-agent'] = {
        'name': 'Master Craftsman',
        'workspace': str(Path(workspace).resolve()),
        'agentDir': str(Path(state_root).resolve() / 'agents/mc-agent/agent'),
        'tools': {'profile': 'full'},
        'sandbox': {'mode': 'off'},
        # Working-session continuity remains native. MiniMoi's capture/index is independent.
        # Do not inherit personal extra memory paths into the domain agent.
        'memory': {'search': {'enabled': False, 'rememberAcrossConversations': False}},
    }
    return result


def make_plan(path, workspace):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise Refused('config_must_be_regular_file')
    raw = path.read_bytes()
    doc = json.loads(raw)
    new = candidate(doc, path.parent, workspace)
    body = {'schema': 1, 'preserve_personal_default':'main', 'config_sha256': digest(raw),
            'config_path': str(path.resolve()), 'entry': new['agents']['entries']['mc-agent']}
    body['plan_id'] = digest(json.dumps(body, sort_keys=True).encode())
    return body, new


def rollback_candidate(current, previous, workspace, state_root):
    installed = candidate(previous, state_root, workspace)
    entries = current.get('agents', {}).get('entries', {})
    if set(entries) != {'main', 'mc-agent'}:
        raise Refused('roster_changed_since_install')
    if entries.get('mc-agent') != installed['agents']['entries']['mc-agent']:
        raise Refused('mc_changed_since_install')
    restored = deepcopy(current)
    del restored['agents']['entries']['mc-agent']
    old_main = previous['agents']['entries']['main']
    main = restored['agents']['entries']['main']
    if main.get('default') is not True:
        raise Refused('default_changed_since_install')
    if 'default' in old_main:
        main['default'] = old_main['default']
    else:
        main.pop('default', None)
    return restored


def validate_native(path, cli):
    env = dict(os.environ, OPENCLAW_CONFIG_PATH=str(path))
    proc = subprocess.run([cli, 'config', 'validate', '--json'], env=env,
                          capture_output=True, text=True, timeout=60)
    # OpenClaw can prepend a log line; never relay output (config errors may contain secrets).
    try:
        answer = json.loads(proc.stdout[proc.stdout.index('{'):])
    except (ValueError, TypeError):
        raise Refused('native_validation_unavailable') from None
    if proc.returncode or answer.get('valid') is not True:
        raise Refused('native_validation_failed')


def install(path, workspace, approved_id, cli='openclaw', validator=validate_native):
    path = Path(path)
    # Cooperating installers serialize; hash is checked again after native validation.
    import fcntl
    fd = os.open(str(path) + '.mc-install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan, new = make_plan(path, workspace)
        if plan['plan_id'] != approved_id:
            raise Refused('plan_changed')
        original = path.read_bytes()
        if digest(original) != plan['config_sha256']:
            raise Refused('config_changed')
        raw = (json.dumps(new, indent=2) + '\n').encode()
        tmp_fd, tmp_name = tempfile.mkstemp(prefix='.mc-candidate-', suffix='.json', dir=path.parent)
        try:
            with os.fdopen(tmp_fd, 'wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            validator(Path(tmp_name), cli)
            if path.is_symlink() or path.read_bytes() != original:
                raise Refused('config_changed')
            backup = path.with_name(path.name + '.before-mc-' + plan['config_sha256'][:16])
            backup_fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(backup_fd, 'wb') as handle:
                handle.write(original)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return {'state': 'installed', 'agent': 'mc-agent', 'backup': str(backup),
                'runtime_verified': False, 'dev_connected': False}
    finally:
        os.close(fd)


def rollback(path, backup, workspace, expected_hash, cli='openclaw', validator=validate_native):
    path, backup = Path(path), Path(backup)
    if path.is_symlink() or backup.is_symlink() or backup.parent.resolve() != path.parent.resolve():
        raise Refused('invalid_rollback_paths')
    import fcntl
    fd = os.open(str(path)+'.mc-install.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        current = path.read_bytes()
        if digest(current) != expected_hash:
            raise Refused('config_changed')
        restored = rollback_candidate(json.loads(current), json.loads(backup.read_bytes()), workspace, path.parent)
        tmp_fd, name = tempfile.mkstemp(prefix='.mc-rollback-',suffix='.json',dir=path.parent)
        try:
            with os.fdopen(tmp_fd,'w') as handle:
                json.dump(restored,handle,indent=2)
                handle.flush();os.fsync(handle.fileno())
            validator(Path(name),cli)
            if path.is_symlink() or path.read_bytes()!=current:
                raise Refused('config_changed')
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)
        return {'state':'mc_configuration_removed','workspace_preserved':True,'runtime_verified':False}
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--openclaw', default='openclaw')
    parser.add_argument('--apply-plan')
    parser.add_argument('--rollback-backup')
    parser.add_argument('--expected-config-hash')
    args = parser.parse_args()
    try:
        if args.rollback_backup:
            if args.apply_plan or not args.expected_config_hash:
                raise Refused('rollback_requires_current_hash')
            answer = rollback(args.config,args.rollback_backup,args.workspace,args.expected_config_hash,args.openclaw)
        elif args.apply_plan:
            answer = install(args.config, args.workspace, args.apply_plan, args.openclaw)
        else:
            answer = make_plan(args.config, args.workspace)[0]
        print(json.dumps(answer, indent=2))
    except (Refused, OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({'state': 'refused', 'reason': 'configuration_not_installed'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
