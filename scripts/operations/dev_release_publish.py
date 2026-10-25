"""Publish fresh dev image observations to one private Docker metadata volume.

Host operator only: no portal restart, gateway change or model call. Publication
uses the pinned portal image with no network, capabilities, credentials or host
binds. The portal mount is prepared separately, read-only.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.operations.dev_release_status import DIGEST, dev_observation, inventory
from scripts.operations.release_status import need
from minimoi_portal.guild_ui.release_context import validated

VOLUME = 'minimoi-dev-release-observation'
TARGET = '/app/runtime/release'
LABELS = {'minimoi.environment': 'dev', 'minimoi.purpose': 'release-observation'}

# Executed in the pinned image; imports the image's strict versioned reader. The
# envelope includes the previous file hash, so concurrent writers fail closed.
WORKER = r'''
import hashlib,json,os,pathlib,stat,sys,tempfile
from datetime import datetime,timezone
from minimoi_portal.guild_ui.release_context import read,validated
root=pathlib.Path('/app/runtime/release'); path=root/'status.json'
assert os.geteuid()==0
for p in (root,*root.parents):
 s=p.lstat(); assert stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022
import fcntl
fd=os.open(root/'.publisher.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
s=os.fstat(fd); assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o077
fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
try:
 try:
  prior=read(path); validated(prior)
  assert prior['environment']=='dev' and prior['manifest'] is None
  prior_hash=hashlib.sha256(json.dumps(prior,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 except FileNotFoundError: prior=None; prior_hash=None
 if sys.argv[1]=='read':
  print(json.dumps({'document':prior,'sha256':prior_hash})); sys.exit(0)
 raw=sys.stdin.buffer.read(32769); assert len(raw)<=32768
 envelope=json.loads(raw); assert set(envelope)=={'document','previous_sha256'}
 assert envelope['previous_sha256']==prior_hash
 doc=envelope['document']; observed=validated(doc)
 assert doc['environment']=='dev' and doc['manifest'] is None
 assert 0<=(datetime.now(timezone.utc)-observed).total_seconds()<=60
 assert prior is None or validated(prior)<=observed
 raw=json.dumps(doc,sort_keys=True,separators=(',',':')).encode(); assert len(raw)<=16384
 tmp_fd,tmp=tempfile.mkstemp(prefix='.status-',dir=root)
 try:
  os.fchmod(tmp_fd,0o644)
  with os.fdopen(tmp_fd,'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
  os.replace(tmp,path)
  d=os.open(root,os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW)
  try: os.fsync(d)
  finally: os.close(d)
 finally:
  if os.path.lexists(tmp):os.unlink(tmp)
 assert read(path)==doc
 print(json.dumps({'published':True,'sha256':hashlib.sha256(raw).hexdigest(),'observed_at':doc['observed_at'],'status':doc['status']}))
finally: os.close(fd)
'''


def fresh(doc, now=None):
    observed = validated(doc)
    need(doc['environment'] == 'dev' and doc['manifest'] is None)
    need(0 <= ((now or datetime.now(timezone.utc)) - observed).total_seconds() <= 60)
    return doc


def single_mount_delta(before, after):
    """Compare resolved Compose documents without logging their environment."""
    import copy
    expected = copy.deepcopy(before)
    need('portal' in expected.get('services', {}))
    mounts = expected['services']['portal'].setdefault('volumes', [])
    need(not any(m.get('target') == TARGET for m in mounts))
    need(VOLUME not in expected.get('volumes', {}))
    mounts.append({'type': 'volume', 'source': VOLUME, 'target': TARGET, 'read_only': True})
    expected.setdefault('volumes', {})[VOLUME] = {'name': VOLUME, 'external': True}
    # Compose may reorder mounts. No other service, environment, image, port,
    # mount option or top-level change is allowed.
    candidate = copy.deepcopy(after)
    for doc in (expected, candidate):
        doc['services']['portal']['volumes'].sort(key=lambda m: m['target'])
    need(candidate == expected)
    return True


def volume_valid(row):
    need(row.get('Name') == VOLUME and row.get('Driver') == 'local')
    need(not row.get('Options'))  # Reject a local driver configured as a host bind.
    need(all((row.get('Labels') or {}).get(k) == v for k, v in LABELS.items()))


def container_command(image, mode):
    need(isinstance(image, str) and DIGEST.fullmatch(image))
    need(mode in ('read', 'publish'))
    return ['docker', 'run', '--rm', '-i', '--pull=never', '--network=none',
            '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--user=0:0', '--pids-limit=32', '--memory=128m',
            '--mount', f'type=volume,source={VOLUME},target={TARGET},volume-nocopy',
            '--entrypoint=python', image, '-B', '-c', WORKER, mode]


def run_json(args, *, payload=None):
    r = subprocess.run(args, input=payload, text=True, capture_output=True, timeout=30)
    need(r.returncode == 0 and len(r.stdout) <= 32768)
    return json.loads(r.stdout)


def ensure_volume():
    # Listing exact names avoids treating an unavailable daemon/permission error
    # as permission to create a replacement volume.
    r = subprocess.run(['docker', 'volume', 'ls', '--format', '{{.Name}}'], capture_output=True, text=True, timeout=10)
    need(r.returncode == 0)
    if VOLUME not in r.stdout.splitlines():
        args = ['docker', 'volume', 'create', '--driver=local']
        for k, v in LABELS.items():
            args += ['--label', k+'='+v]
        r = subprocess.run(args+[VOLUME], capture_output=True, text=True, timeout=10)
        need(r.returncode == 0 and r.stdout.strip() == VOLUME)
    rows = run_json(['docker', 'volume', 'inspect', VOLUME])
    need(type(rows) is list and len(rows) == 1)
    volume_valid(rows[0])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=['begin', 'finish'])
    p.add_argument('commit')
    p.add_argument('--image', required=True, help='Exact expected portal image SHA-256')
    args = p.parse_args(argv)
    # Validate operator inputs before creating a volume or launching a process.
    import re
    need(re.fullmatch(r'[a-f0-9]{40}', args.commit) and DIGEST.fullmatch(args.image))
    ensure_volume()
    previous = run_json(container_command(args.image, 'read'))
    doc = fresh(dev_observation(args.phase, args.commit, {'portal': args.image}, inventory(), previous['document']))
    receipt = run_json(container_command(args.image, 'publish'), payload=json.dumps({'document':doc, 'previous_sha256':previous['sha256']}))
    print(json.dumps(receipt, sort_keys=True))
    # A failed image check is published honestly, then fails the operator command.
    return 1 if doc['status'] == 'selected_image_check_failed' else 0


if __name__ == '__main__':
    sys.exit(main())
