"""Offline profile/attestation tests: no launchd, Claude or provider calls."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time
import pytest
from scripts.operations import claude_probe_profile as P
from core.agent_requests import claude_socket_probe as S


def test_fixed_profile_does_not_enable_ledger_or_tools():
    args=P.argv('id','/settings','/mcp')
    assert args[args.index('--tools')+1]==''
    assert args[args.index('--max-budget-usd')+1]=='1'
    assert args[args.index('--model')+1]=='haiku'
    assert '--resume' not in args and '--continue' not in args
    assert '--strict-mcp-config' in args and '--permission-mode' in args
    cfg=P.settings('/python','/script','/binding')
    assert cfg['pluginConfigs']['cc-plugin-agents-md@builtin']['options']['instructionFiles']=='managed-only'
    assert cfg['hooks']['SessionStart'][0]['hooks'][0]['args']==['-I','/script','--hook','/binding']
    assert cfg['autoMemoryEnabled'] is False


@pytest.mark.parametrize('text',['state = running\nactive count = 1\npid = 12',
    'state = spawn scheduled\nactive count = 0\npid = 12', 'state = unknown\nactive count = 0'])
def test_active_or_unknown_job_refuses(text):
    with pytest.raises(ValueError):P.job_idle(text)


def test_idle_broken_and_authority_bound(tmp_path):
    P.job_idle('state = spawn scheduled\nactive count = 0')
    now=P.AUTHORITY_END-100
    plan={'authority_expires_at':P.AUTHORITY_END,'label':P.LABEL,'binary_sha256':P.BINARY_SHA,
          'session_id':'22222222-2222-2222-2222-222222222222','directory':'/private/tmp/mc-claude-probe-test-never-created'}
    assert P.validate_plan(plan,now).name.startswith('mc-claude-probe-')
    for expires in (float('nan'),float('inf'),now+20,P.AUTHORITY_END+1):
        with pytest.raises(ValueError):P.validate_plan(dict(plan,authority_expires_at=expires),now)
    with pytest.raises(ValueError):P.validate_plan(dict(plan,directory=str(tmp_path)),now)


def test_hook_metadata_only_identity_and_create_once(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    binding=tmp_path/'binding.json'; binding.write_text(json.dumps({'session_id':'s','cwd':'/empty'}));binding.chmod(0o600)
    event={'session_id':'s','cwd':'/empty','hook_event_name':'SessionStart',
           'transcript_path':str(tmp_path/'.claude/projects/x/session.jsonl'),'private':'must not be retained'}
    P.hook(str(binding),json.dumps(event).encode())
    got=json.loads((tmp_path/'sessionstart.json').read_text())
    assert set(got)=={'session_id','cwd','transcript_path'} and 'private' not in got
    with pytest.raises(FileExistsError):P.hook(str(binding),json.dumps(event).encode())
    for change in ({'session_id':'wrong'},{'cwd':'/wrong'},{'transcript_path':'/etc/private'},
                   {'transcript_path':str(tmp_path/'.claude/projects/../secret')},{'hook_event_name':'Other'}):
        with pytest.raises(ValueError):P.hook(str(binding),json.dumps(dict(event,**change)).encode())
    with pytest.raises(ValueError):P.hook(str(binding),b'x'*16385)


def fixture(tmp_path):
    target=SimpleNamespace(session_id='s',cwd='/empty')
    transcript=tmp_path/'session.jsonl';transcript.write_text('{}\n');transcript.chmod(0o600)
    supervisor=tmp_path/'supervisor.py';supervisor.write_text('# pinned fixture');supervisor.chmod(0o600)
    hook=tmp_path/'hook.json';hook.write_text(json.dumps({'session_id':'s','cwd':'/empty','transcript_path':str(transcript)}));hook.chmod(0o600)
    bootstrap=tmp_path/'bootstrap.json'
    value={'format':'minimoi.owned-probe-bootstrap.v1','pid':42,'process_start':'start','session_id':'s','cwd':'/empty',
       'transcript':str(transcript),'binary_sha256':S.BINARY_SHA256,'tools':[],'mcp_servers':[],'model':'claude-haiku-4-5',
       'expires_at':time.time()+100,'hook_receipt_sha256':P.digest(hook.read_bytes()),'supervisor_sha256':P.digest(supervisor.read_bytes())}
    bootstrap.write_text(json.dumps(value));bootstrap.chmod(0o600)
    st=transcript.stat()
    enrolled={'transcript':str(transcript),'owned_probe_attestation':{'reviewed':True,'bootstrap_path':str(bootstrap),
       'bootstrap_sha256':P.digest(bootstrap.read_bytes()),'hook_receipt_path':str(hook),'supervisor_path':str(supervisor),
       'transcript_identity':[st.st_dev,st.st_ino,st.st_uid]}}
    return enrolled,target,{'procStart':'start'},bootstrap,value


def test_owned_attestation_and_ordinary_path_remain_separate(tmp_path):
    e,t,r,b,v=fixture(tmp_path)
    S.transcript_binding(e,t,r,42)
    S.transcript_binding({'transcript':'/ordinary'},t,{'logPath':'/ordinary'},42)
    with pytest.raises(S.Refused):S.transcript_binding(e,t,dict(r,logPath='/wrong'),42)
    with pytest.raises(S.Refused):S.transcript_binding({'transcript':e['transcript']},t,r,42)
    with pytest.raises(S.Refused):S.transcript_binding(e,t,r,43)
    e['owned_probe_attestation']['reviewed']=False
    with pytest.raises(S.Refused):S.transcript_binding(e,t,r,42)


@pytest.mark.parametrize('change',[{'process_start':'other'},{'session_id':'other'},{'cwd':'/other'},
 {'tools':['Read']},{'mcp_servers':['server']},{'expires_at':0},{'expires_at':float('nan')},
 {'binary_sha256':'wrong'},{'model':'claude-opus'},{'transcript':'/wrong'}])
def test_attestation_bound_fields_even_when_new_hash_is_pinned(tmp_path,change):
    e,t,r,b,v=fixture(tmp_path);v.update(change);b.write_text(json.dumps(v))
    e['owned_probe_attestation']['bootstrap_sha256']=P.digest(b.read_bytes())
    with pytest.raises(S.Refused):S.transcript_binding(e,t,r,42)


def test_receipt_drift_and_transcript_replacement_refused(tmp_path):
    e,t,r,b,v=fixture(tmp_path);b.write_text('{}')
    with pytest.raises(S.Refused):S.transcript_binding(e,t,r,42)
    b.write_text(json.dumps(v))
    path=Path(e['transcript']);path.rename(tmp_path/'old');path.write_text('{}\n');path.chmod(0o600)
    with pytest.raises(S.Refused):S.transcript_binding(e,t,r,42)


def test_read_refuses_symlink(tmp_path):
    path=tmp_path/'file';path.write_text('safe');link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises(OSError):P.read(link)


def launch_fixture(tmp_path,monkeypatch,bad_result=False,bootout_timeout=False,wait_mode=None):
    import io
    import plistlib
    folder=tmp_path/'run'
    script=str(Path(P.__file__).absolute());plist=Path.home()/'Library/LaunchAgents'/(P.LABEL+'.plist')
    old=plistlib.dumps({'Label':P.LABEL,'WorkingDirectory':'/old/cwd','ProgramArguments':['python','manager','--claude-bin','/definitely-absent-probe-binary','run','--start']})
    now=time.time()
    plan={'session_id':'22222222-2222-2222-2222-222222222222','authority_expires_at':now+100,
          'supervisor_sha256':P.digest(Path(script).read_bytes()),'plist_sha256':P.digest(old)}
    pp=tmp_path/'plan.json';pp.write_text(json.dumps(plan));pp.chmod(0o600)
    original_read=P.read
    def read(path,limit=65536):
        if str(path)==str(plist):return old
        if str(path)==P.BINARY:return b'fake binary'
        if str(path).endswith('/sessions/12345.json'):
            return json.dumps({'sessionId':plan['session_id'],'cwd':str(folder/'cwd'),
                               'messagingSocketPath':'/selected/inbox.sock','procStart':'start'}).encode()
        return original_read(path,limit)
    monkeypatch.setattr(P,'read',read);monkeypatch.setattr(P,'BINARY_SHA',P.digest(b'fake binary'))
    monkeypatch.setattr(P,'validate_plan',lambda p,now:folder)
    monkeypatch.setattr(P,'configuration_gate',lambda p,cwd:None)
    calls=[];children=[]
    def command(args,**kw):
        calls.append(args)
        if args[1]=='bootout':
            if bootout_timeout:raise P.subprocess.TimeoutExpired(args,5)
            return SimpleNamespace(returncode=0,stdout='',stderr='')
        if any(c[1]=='bootout' for c in calls):return SimpleNamespace(returncode=113,stdout='',stderr='Could not find service')
        return SimpleNamespace(returncode=0,stdout='state = spawn scheduled\nactive count = 0\npath = '+str(plist)+'\nprogram = python\nworking directory = /old/cwd\narguments = {\npython\nmanager\n--claude-bin\n/definitely-absent-probe-binary\nrun\n--start\n}',stderr='')
    class Child:
        pid=12345
        returncode=None
        def __init__(self,args,**kw):
            children.append(self);self.calls=0;self.stdin=io.BytesIO();self.terminated=0;self.waits=0
            assert args[0]==P.BINARY and 'manager' not in args and '--resume' not in args
            assert 'MINIMOI_AGENT_REQUESTS_DIR' not in kw['env']
            ro,wo=os.pipe();re,we=os.pipe()
            self.stdout=os.fdopen(ro,'rb');self.stderr=os.fdopen(re,'rb')
            events=[{'type':'system','subtype':'init','session_id':plan['session_id'],'tools':[],
                     'mcp_servers':[],'model':'claude-haiku-4-5'},
                    {'type':'result','is_error':False,'result':'WRONG' if bad_result else P.READY}]
            os.write(wo,(''.join(json.dumps(x)+'\n' for x in events)).encode());os.close(wo);os.close(we)
            P.publish(folder/'sessionstart.json',{'session_id':plan['session_id'],'cwd':str(folder/'cwd'),
                                                  'transcript_path':'/exact/native/session.jsonl'})
        def poll(self):
            self.calls+=1
            if self.calls>=3:self.returncode=0
            return self.returncode
        def wait(self,timeout):
            self.waits+=1
            if wait_mode=='unreaped' or (wait_mode=='terminate' and self.waits==1):
                assert P.signal.getsignal(P.signal.SIGTERM)==P.signal.SIG_IGN
                assert P.signal.getsignal(P.signal.SIGINT)==P.signal.SIG_IGN
                raise P.subprocess.TimeoutExpired('owned-child',timeout)
            self.returncode=0;return 0
        def terminate(self):self.terminated+=1
    monkeypatch.setattr(P.subprocess,'run',command);monkeypatch.setattr(P.subprocess,'Popen',Child)
    return pp,folder,calls,children


def test_one_bootout_one_child_bootstrap_and_no_automatic_restore(tmp_path,monkeypatch):
    pp,folder,calls,children=launch_fixture(tmp_path,monkeypatch)
    P.run(str(pp))
    assert len(children)==1 and sum(c[1]=='bootout' for c in calls)==1
    assert not any(c[1] in ('bootstrap','kickstart') for c in calls)
    bootstrap=json.loads((folder/'bootstrap.json').read_text())
    assert bootstrap['authoritative_enrollment'] is False
    assert bootstrap['pid']==12345 and bootstrap['transcript']=='/exact/native/session.jsonl'
    assert json.loads((folder/'EXIT.json').read_text())['prior_job_restored'] is False
    with pytest.raises(ValueError,match="job_not_loaded"):P.run(str(pp))
    assert len(children)==1


def test_wrong_warmup_cleanup_without_enrollment_or_restart(tmp_path,monkeypatch):
    pp,folder,calls,children=launch_fixture(tmp_path,monkeypatch,bad_result=True)
    with pytest.raises(ValueError,match='warmup_failed'):P.run(str(pp))
    assert len(children)==1 and children[0].returncode==0
    assert not (folder/'bootstrap.json').exists() and (folder/'EXIT.json').exists()
    assert not any(c[1]=='bootstrap' for c in calls)


def test_uncertain_bootout_never_spawns_or_retries(tmp_path,monkeypatch):
    pp,folder,calls,children=launch_fixture(tmp_path,monkeypatch,bootout_timeout=True)
    with pytest.raises(P.subprocess.TimeoutExpired):P.run(str(pp))
    assert children==[] and sum(c[1]=='bootout' for c in calls)==1
    assert (folder/'intent.json').exists()


@pytest.mark.parametrize('mode',['terminate','unreaped'])
def test_bounded_terminate_and_unreaped_hold(tmp_path,monkeypatch,mode):
    pp,folder,calls,children=launch_fixture(tmp_path,monkeypatch,wait_mode=mode)
    prior={sig:P.signal.getsignal(sig) for sig in (P.signal.SIGINT,P.signal.SIGTERM)}
    if mode=='unreaped':
        with pytest.raises(P.subprocess.TimeoutExpired):P.run(str(pp))
        assert (folder/'HOLD.json').exists() and not (folder/'EXIT.json').exists()
    else:
        P.run(str(pp))
        assert (folder/'EXIT.json').exists() and not (folder/'HOLD.json').exists()
    assert children[0].waits==2 and children[0].terminated==1
    assert not any(c[1] in ('bootstrap','kickstart') for c in calls)
    assert all(P.signal.getsignal(sig)==handler for sig,handler in prior.items())


@pytest.mark.parametrize('field',['path','program','working directory','arguments'])
def test_loaded_job_fields_must_match_not_only_idle(field):
    expected={'ProgramArguments':['/python','/manager','run','--start'],'WorkingDirectory':'/cwd'}
    text='state = spawn scheduled\nactive count = 0\npath = /job.plist\nprogram = /python\nworking directory = /cwd\narguments = {\n/python\n/manager\nrun\n--start\n}'
    P.loaded_job(text,'/job.plist',expected)
    changed=text.replace({'path':'/job.plist','program':'program = /python','working directory':'/cwd','arguments':'/manager'}[field],'/changed')
    with pytest.raises(ValueError):P.loaded_job(changed,'/job.plist',expected)
    with pytest.raises(ValueError):P.loaded_job(text+'\nprogram = /python','/job.plist',expected)


def test_expiry_during_preflight_never_boots_out(tmp_path,monkeypatch):
    pp,folder,calls,children=launch_fixture(tmp_path,monkeypatch)
    expires=json.loads(pp.read_text())['authority_expires_at']
    monkeypatch.setattr(P.time,'time',lambda:expires-10)
    with pytest.raises(ValueError,match='authority_expired_before_bootout'):P.run(str(pp))
    assert not children and not any(c[1]=='bootout' for c in calls)


def test_configuration_gate_requires_local_review_and_truthful_remote_policy(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    cwd=tmp_path/'empty/cwd';directory=cwd.parent
    plan={'authority_expires_at':time.time()+100,'session_id':'s'}
    required={str(tmp_path/'.claude/settings.json'),str(tmp_path/'.claude/settings.local.json'),
              '/Library/Application Support/ClaudeCode/managed-settings.json'}
    for parent in (cwd,*cwd.parents):
        required.update(str(parent/'.claude'/name) for name in ('settings.json','settings.local.json'))
    raw=lambda value:(json.dumps(value,sort_keys=True)+'\n').encode()
    value={'format':'minimoi.probe-configuration-review.v1','approved':True,'cwd':str(cwd),'home':str(tmp_path),
       'no_local_startup_hooks_except_profile':True,'no_local_enabled_third_party_plugins':True,
       'local_source_coverage_complete':True,'remote_managed_policy':'retained, not independently enumerated','expires_at':time.time()+90,
       'argv':P.argv('s',directory/'settings.json',directory/'mcp.json'),
       'settings_sha256':P.digest(raw(P.settings(P.sys.executable,str(Path(P.__file__).absolute()),str(directory/'binding.json')))),
       'binding_sha256':P.digest(raw({'session_id':'s','cwd':str(cwd)})),
       'files':{path:None for path in required},'directories':{'/Library/Application Support/ClaudeCode/managed-settings.d':None}}
    receipt=tmp_path/'review.json';receipt.write_bytes(raw(value));receipt.chmod(0o600)
    plan.update(configuration_review_path=str(receipt),configuration_review_sha256=P.digest(receipt.read_bytes()))
    monkeypatch.setattr(P.os.path,'lexists',lambda path:False)
    P.configuration_gate(plan,cwd)
    for change in ({'remote_managed_policy':'bypassed'},{'remote_managed_policy':None},
                   {'no_local_startup_hooks_except_profile':False},{'local_source_coverage_complete':False},
                   {'no_local_enabled_third_party_plugins':False},
                   {'argv':['other']},{'files':{}},{'settings_sha256':'wrong'}):
        receipt.write_bytes(raw(dict(value,**change)));plan['configuration_review_sha256']=P.digest(receipt.read_bytes())
        with pytest.raises(ValueError):P.configuration_gate(plan,cwd)
    receipt.write_bytes(raw(value));plan['configuration_review_sha256']=P.digest(receipt.read_bytes())
    monkeypatch.setattr(P.os.path,'lexists',lambda path:True)
    with pytest.raises(ValueError,match='configuration_appeared'):P.configuration_gate(plan,cwd)



def test_second_configuration_check_expiry_never_spawns(tmp_path,monkeypatch):
    pp,folder,calls,children=launch_fixture(tmp_path,monkeypatch)
    expires=json.loads(pp.read_text())['authority_expires_at'];checks=[]
    def gate(plan,cwd):
        checks.append(True)
        if len(checks)==2:monkeypatch.setattr(P.time,'time',lambda:expires-10)
    monkeypatch.setattr(P,'configuration_gate',gate)
    with pytest.raises(ValueError,match='authority_expired'):P.run(str(pp))
    assert len(checks)==2 and not children
    assert sum(c[1]=='bootout' for c in calls)==1
    assert not any(c[1]=='bootstrap' for c in calls)
