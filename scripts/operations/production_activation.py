"""Read the reviewed host-only production activation configuration; never writes.

No file means no override. Output is six validated nonsecret shell assignments,
for read/export by deployment scripts, never shell evaluation. Provisioning and
activation require separate reviewed operator actions.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import stat
import sys

PATH = Path('/etc/minimoi/production-activation.json')
KEYS = ('MINIMOI_MC_BOARD_OUTCOMES', 'MINIMOI_GUILD_MANAGED_VOICE_PRODUCTION',
        'MINIMOI_MC_MANAGED_REALTIME', 'MINIMOI_COS_MANAGED_REALTIME',
        'MINIMOI_COS_WRITE_GUARD', 'MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO')

class Refused(ValueError):
    pass

def need(ok):
    if not ok:
        raise Refused('production_activation_refused')

def unique(pairs):
    out = {}
    for key, value in pairs:
        need(key not in out)
        out[key] = value
    return out

def validate(doc):
    need(isinstance(doc, dict) and set(doc) == {'schema', 'environment'})
    need(doc['schema'] == 'minimoi.production-activation.v1')
    env = doc['environment']
    need(isinstance(env, dict) and set(env) == set(KEYS))
    need(all(type(env[k]) is str for k in KEYS))
    need(all(env[k] in ('0', '1') for k in KEYS[:4]))
    need(env[KEYS[4]] in ('', 'on'))
    amount = env[KEYS[5]]
    need(amount == '' or (amount.isascii() and amount.isdecimal() and len(amount) <= 8 and 1 <= int(amount) <= 20_000_000))
    if env['MINIMOI_MC_MANAGED_REALTIME'] == '1':
        need(env['MINIMOI_GUILD_MANAGED_VOICE_PRODUCTION'] == '1' and amount != '')
    if env['MINIMOI_COS_MANAGED_REALTIME'] == '1':
        need(env['MINIMOI_COS_WRITE_GUARD'] == 'on' and amount != '')
    return env

def read(path=PATH, *, trusted_uid=0):
    """Host-only root-owned private directory/file; test paths may be synthetic."""
    path = Path(path)
    try:
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            return None
        try:
            # Absence means no override even under an existing public directory.
            # A present configuration still requires the private trusted parent.
            info = os.fstat(parent)
            need(info.st_uid == trusted_uid and stat.S_IMODE(info.st_mode) == 0o700)
            before = os.fstat(fd)
            need(stat.S_ISREG(before.st_mode) and before.st_uid == trusted_uid and
                 stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1 and before.st_size <= 8192)
            raw = os.read(fd, 8193)
            after = os.fstat(fd)
            need(len(raw) == before.st_size and len(raw) <= 8192 and
                 (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                 (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns))
            return validate(json.loads(raw.decode('utf-8'), object_pairs_hook=unique))
        finally:
            os.close(fd)
    finally:
        os.close(parent)

def main():
    try:
        env = read()
        if env is not None:
            for key in KEYS:
                print(key + '=' + env[key])
        return 0
    except (OSError, ValueError, UnicodeError):
        print('production_activation_refused', file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
