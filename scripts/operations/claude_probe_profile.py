"""Operator-only, one bounded synthetic receiver; never imports the ledger manager.

No automatic restart/restoration. A loaded prior job is booted out once and left
unloaded for an explicit operator restoration decision after this child exits.
The SessionStart hook records metadata only; it does not grant authority.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid

LABEL = 'ai.minimoi.connection.claude-code'
BINARY = '/Users/vanstedum/Library/Application Support/Claude/claude-code/2.1.304/6bbba6adc37f/claude.app/Contents/MacOS/claude'
BINARY_SHA = '21d484aa3737c3f2e665dbd849797caafb36675f8e26e11cb47e19220bdf6c15'
AUTHORITY_END = 1792904400  # 2026-10-25 05:00:00 UTC
READY = 'MC-WORKER-READY'
PROMPT = ('Synthetic MC connectivity receiver initialization. Reply exactly MC-WORKER-READY. '
          'Do not use tools, inspect files, change anything or start work. '
          'For the subsequent fixed synthetic connectivity probe, reply only with its requested token.')


def require(value, reason):
    if not value: raise ValueError(reason)


def digest(raw): return hashlib.sha256(raw).hexdigest()


def read(path, limit=65536):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'unsafe_path')
    parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent); parent = nxt
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally: os.close(parent)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and st.st_nlink == 1
                and not st.st_mode & 0o022 and st.st_size <= limit, 'unsafe_file')
        raw = os.read(fd, limit + 1)
        require(len(raw) == st.st_size, 'file_changed')
        return raw
    finally: os.close(fd)


def publish(path, value):
    raw = (json.dumps(value, sort_keys=True) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
    except BaseException: raise


def hook(binding_path, raw):
    require(len(raw) <= 16384, 'hook_bound')
    b = json.loads(read(binding_path)); event = json.loads(raw)
    require(event.get('hook_event_name') == 'SessionStart' and event.get('session_id') == b['session_id']
            and event.get('cwd') == b['cwd'], 'hook_identity')
    path = event.get('transcript_path')
    require(type(path) is str and Path(path).is_absolute() and '..' not in Path(path).parts
            and Path(path).is_relative_to(Path.home() / '.claude/projects'), 'hook_transcript_path')
    publish(Path(binding_path).parent / 'sessionstart.json',
            {'session_id': b['session_id'], 'cwd': b['cwd'], 'transcript_path': path})


def settings(python, script, binding):
    return {'crossSessionInbound': 'accept', 'autoMemoryEnabled': False,
            'pluginConfigs': {'cc-plugin-agents-md@builtin': {'options': {'instructionFiles': 'managed-only'}}},
            'hooks': {'SessionStart': [{'hooks': [{'type': 'command', 'command': python,
                'args': ['-I', script, '--hook', binding], 'timeout': 2, 'onFailure': 'block'}]}]}}


def argv(session, settings_path, mcp_path):
    return [BINARY, '-p', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose',
            '--session-id', session, '--permission-mode', 'default', '--tools', '', '--disallowedTools', 'mcp__*',
            '--strict-mcp-config', '--mcp-config', str(mcp_path), '--disable-slash-commands',
            '--settings', str(settings_path), '--model', 'haiku', '--max-budget-usd', '1', '--max-turns', '1']


def job_idle(text):
    require('state = spawn scheduled' in text or 'state = not running' in text, 'job_not_broken_idle')
    require('active count = 0' in text and not any(x.strip().startswith('pid =') for x in text.splitlines()), 'active_child')


def loaded_job(text, plist_path, expected):
    """Bind the loaded job, not merely the on-disk replacement candidate."""
    job_idle(text)
    lines=[line.strip() for line in text.splitlines()]
    def scalar(key):
        values=[line[len(key)+3:] for line in lines if line.startswith(key+' = ')]
        require(len(values)==1, 'loaded_job_field')
        return values[0]
    require(scalar('path')==str(plist_path) and scalar('program')==expected['ProgramArguments'][0]
            and scalar('working directory')==expected['WorkingDirectory'], 'loaded_job_changed')
    require(lines.count('arguments = {')==1, 'loaded_job_arguments')
    begin=lines.index('arguments = {')+1
    try: end=lines.index('}',begin)
    except ValueError: raise ValueError('loaded_job_arguments')
    require(lines[begin:end]==expected['ProgramArguments'], 'loaded_job_arguments')


def configuration_gate(plan, cwd):
    """Operator-reviewed local-source coverage; never print configuration data.

    Review is a protected operator artifact, not a model-supplied assertion.
    Recheck its exact selected file/directory bytes immediately before spawn.
    Remote managed policy remains enabled, not independently enumerated.
    Bootstrap gates enrollment; it cannot prevent hooks already run at startup.
    """
    raw=read(plan['configuration_review_path'])
    require(digest(raw)==plan['configuration_review_sha256'], 'configuration_review_changed')
    review=json.loads(raw)
    require(review.get('format')=='minimoi.probe-configuration-review.v1' and review.get('approved') is True
            and review.get('cwd')==str(cwd) and review.get('home')==str(Path.home())
            and review.get('no_local_startup_hooks_except_profile') is True and review.get('no_local_enabled_third_party_plugins') is True
            and review.get('local_source_coverage_complete') is True,'configuration_not_reviewed')
    directory=Path(cwd).parent
    expected_settings=settings(sys.executable,str(Path(__file__).absolute()),str(directory/'binding.json'))
    expected_binding={'session_id':plan['session_id'],'cwd':str(cwd)}
    encoded=lambda value:(json.dumps(value,sort_keys=True)+'\n').encode()
    require(review.get('argv')==argv(plan['session_id'],directory/'settings.json',directory/'mcp.json')
            and review.get('settings_sha256')==digest(encoded(expected_settings))
            and review.get('binding_sha256')==digest(encoded(expected_binding))
            and review.get('remote_managed_policy') == 'retained, not independently enumerated','configuration_profile_changed')
    require(type(review.get('expires_at')) in (int,float) and time.time()<review['expires_at']<=plan['authority_expires_at'],
            'configuration_review_expired')
    files=review.get('files'); directories=review.get('directories')
    require(type(files) is dict and type(directories) is dict and len(files)<=40 and len(directories)<=20,'configuration_bound')
    required={str(Path.home()/'.claude/settings.json'),str(Path.home()/'.claude/settings.local.json'),
              '/Library/Application Support/ClaudeCode/managed-settings.json'}
    for parent in (Path(cwd),*Path(cwd).parents):
        required.update(str(parent/'.claude'/name) for name in ('settings.json','settings.local.json'))
    require(required<=set(files),'configuration_coverage_missing')
    require('/Library/Application Support/ClaudeCode/managed-settings.d' in directories,'managed_directory_missing')
    for path,expected in files.items():
        require(Path(path).is_absolute() and '..' not in Path(path).parts,'configuration_path')
        if expected is None: require(not os.path.lexists(path),'configuration_appeared')
        else: require(digest(read(path))==expected,'configuration_changed')
    for path,expected in directories.items():
        require(Path(path).is_absolute() and '..' not in Path(path).parts,'configuration_path')
        if expected is None: require(not os.path.lexists(path),'configuration_directory_appeared'); continue
        # No following directory links; review enumerates every drop-in by name.
        require(type(expected) is list and len(expected)<=40,'configuration_directory_bound')
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            names=os.listdir(fd)
            require(len(names)<=40 and sorted(names)==sorted(expected),'configuration_directory_changed')
            require(all(str(Path(path)/name) in files for name in names),'configuration_dropin_unpinned')
        finally:os.close(fd)


def validate_plan(p, now):
    require(type(p.get('authority_expires_at')) in (float, int) and math.isfinite(p['authority_expires_at'])
            and now + 30 < p['authority_expires_at'] <= AUTHORITY_END, 'authority_expired')
    require(p.get('label') == LABEL and p.get('binary_sha256') == BINARY_SHA, 'plan_identity')
    require(str(uuid.UUID(p['session_id'])) == p['session_id'], 'session_uuid')
    folder = Path(p['directory'])
    require(folder.parent == Path('/private/tmp') and folder.name.startswith('mc-claude-probe-'), 'directory_scope')
    require(not folder.exists() and not folder.is_symlink(), 'directory_exists')
    return folder


def run(plan_path):
    p = json.loads(read(plan_path)); folder = validate_plan(p, time.time())
    script = str(Path(__file__).absolute())
    require(digest(read(script)) == p['supervisor_sha256'], 'supervisor_changed')
    plist = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    before = read(plist); require(digest(before) == p['plist_sha256'], 'plist_changed')
    old = plistlib.loads(before); args = old['ProgramArguments']
    require(old['Label'] == LABEL and args[-2:] == ['run', '--start']
            and not Path(args[args.index('--claude-bin') + 1]).exists(), 'baseline_not_broken')
    require(digest(read(BINARY, 512*1024*1024)) == BINARY_SHA, 'binary_changed')
    configuration_gate(p,folder/'cwd')
    target = 'gui/' + str(os.getuid()) + '/' + LABEL
    def command(args, timeout=5):
        return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    snapshot = command(['/bin/launchctl', 'print', target])
    require(snapshot.returncode == 0, 'job_not_loaded'); loaded_job(snapshot.stdout,plist,old)
    folder.mkdir(mode=0o700)
    (folder/'cwd').mkdir(mode=0o700)
    binding = {'session_id': p['session_id'], 'cwd': str(folder/'cwd')}
    publish(folder/'binding.json', binding)
    publish(folder/'settings.json', settings(sys.executable, script, str(folder/'binding.json')))
    publish(folder/'mcp.json', {'mcpServers': {}})
    publish(folder/'intent.json', {'plan_sha256': digest(read(plan_path)), 'plist_sha256': digest(before),
                                  'label': LABEL, 'at': time.time(), 'action': 'one_bootout_then_one_child'})
    require(read(plist) == before, 'plist_changed')
    fresh = command(['/bin/launchctl','print',target]); require(fresh.returncode == 0,'job_changed'); loaded_job(fresh.stdout,plist,old)
    require(time.time()+20 < p['authority_expires_at'], 'authority_expired_before_bootout')
    stopped = command(['/bin/launchctl', 'bootout', target])
    require(stopped.returncode == 0, 'bootout_unknown')
    absent = command(['/bin/launchctl', 'print', target])
    require(absent.returncode != 0 and ('Could not find service' in absent.stderr), 'bootout_not_verified')
    require(time.time()+20 < p['authority_expires_at'], 'authority_expired')
    configuration_gate(p,folder/'cwd')
    require(digest(read(script))==p['supervisor_sha256'], 'supervisor_changed')
    expected={
        'binding.json':binding,
        'settings.json':settings(sys.executable,script,str(folder/'binding.json')),
        'mcp.json':{'mcpServers':{}}}
    for name,value in expected.items():
        require(read(folder/name)==(json.dumps(value,sort_keys=True)+'\n').encode(),'profile_changed')
    require(time.time()+20 < p['authority_expires_at'], 'authority_expired')
    env = {k: os.environ[k] for k in ('HOME','PATH','LANG') if k in os.environ}
    # Reserve eight seconds for the two bounded three-second reap attempts.
    deadline = min(time.monotonic()+172, time.monotonic()+p['authority_expires_at']-time.time()-8)
    child = subprocess.Popen(argv(p['session_id'],folder/'settings.json',folder/'mcp.json'),
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=binding['cwd'],env=env)
    sel = selectors.DefaultSelector(); buffers = {}; total = 0; bootstrap = False; ended = False
    try:
        publish(folder/'child.json', {'pid':child.pid, 'session_id':p['session_id'], 'binary_sha256':BINARY_SHA})
        for stream in (child.stdout,child.stderr):
            os.set_blocking(stream.fileno(),False); sel.register(stream, selectors.EVENT_READ); buffers[stream]=b''
        child.stdin.write((json.dumps({'type':'user','session_id':p['session_id'],
            'message':{'role':'user','content':PROMPT}})+'\n').encode()); child.stdin.flush()
        init = None
        while child.poll() is None and time.monotonic() < deadline:
            for key,_ in sel.select(min(.1,max(0,deadline-time.monotonic()))):
                raw = os.read(key.fileobj.fileno(),16384)
                if not raw: sel.unregister(key.fileobj); continue
                total += len(raw); require(total <= 2*1024*1024, 'output_bound')
                if key.fileobj is child.stderr: continue  # never retain arbitrary diagnostics
                buffers[key.fileobj] += raw; require(len(buffers[key.fileobj]) <= 131072,'line_bound')
                while b'\n' in buffers[key.fileobj]:
                    line,buffers[key.fileobj] = buffers[key.fileobj].split(b'\n',1)
                    ev = json.loads(line)
                    if ev.get('type') == 'system' and ev.get('subtype') == 'init':
                        require(ev.get('session_id') == p['session_id'] and ev.get('tools') == []
                                and ev.get('mcp_servers') == [] and str(ev.get('model','')).startswith('claude-haiku'), 'bootstrap_projection')
                        init = {'session_id':ev['session_id'],'model':ev['model'],'tools':[], 'mcp_servers':[]}
                    if ev.get('type') == 'result' and not bootstrap:
                        require(init is not None and ev.get('is_error') is False and ev.get('result','').strip() == READY, 'warmup_failed')
                        receipt_raw = read(folder/'sessionstart.json'); receipt = json.loads(receipt_raw)
                        require(receipt['session_id']==p['session_id'] and receipt['cwd']==binding['cwd'],'hook_binding')
                        # Exact selected PID metadata only; no directory enumeration.
                        record = json.loads(read(Path.home()/'.claude/sessions'/f'{child.pid}.json',16384))
                        require(record.get('sessionId')==p['session_id'] and record.get('cwd')==binding['cwd']
                                and type(record.get('messagingSocketPath')) is str and record.get('procStart'), 'pid_record')
                        require(child.poll() is None,'child_exited')
                        publish(folder/'bootstrap.json', {'format':'minimoi.owned-probe-bootstrap.v1', **init,
                            'pid':child.pid,'process_start':record['procStart'],'cwd':binding['cwd'],
                            'transcript':receipt['transcript_path'],'hook_receipt_sha256':digest(receipt_raw),
                            'supervisor_sha256':p['supervisor_sha256'],'binary_sha256':BINARY_SHA,
                            'expires_at':min(p['authority_expires_at'],time.time()+max(0,deadline-time.monotonic())),
                            'peer_address':'uds:'+record['messagingSocketPath'], 'authoritative_enrollment':False})
                        bootstrap = True
        ended = child.poll() is not None
    finally:
        # A second operator stop must not interrupt the bounded owned-child reap.
        handlers = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            sel.close()
            try: child.stdin.close()
            except (OSError,ValueError): pass
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.terminate()
                try: child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    publish(folder/'HOLD.json',{'reason':'owned_child_unreaped','pid':child.pid}); raise
            publish(folder/'EXIT.json',{'pid':child.pid,'returncode':child.returncode,'bootstrap':bootstrap,
                'early_exit':ended,'prior_job_restored':False,'plist_unchanged':read(plist)==before})
        finally:
            for sig, handler in handlers.items(): signal.signal(sig, handler)
    require(bootstrap,'bootstrap_not_observed')


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--hook': hook(sys.argv[2],sys.stdin.buffer.read(16385))
    elif len(sys.argv)==3 and sys.argv[1]=='--run':
        def stop(signum, frame): raise SystemExit('operator_stop')
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        run(sys.argv[2])
    else: raise SystemExit('Use --run reviewed-plan.json or --hook binding.json')
