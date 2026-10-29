"""Owned fake sender only. No installed Claude/native/provider execution."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import pytest
from core.agent_requests import claude_fixed_send as C
from core.agent_requests import claude_send_guard as G
from core.agent_requests import handoff_payload as H
from core.agent_requests.direct_contact import Target,OwnerSource,_read

TARGET=Target('claude-career','claude-code','22222222-2222-2222-2222-222222222222','local','/synthetic/repo',C.TRANSPORT,'fixture-v1')

FAKE=r'''
import json,os,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from core.agent_requests import claude_send_guard as G
folder=Path(sys.argv[2]);mode=sys.argv[3];transcript=Path(sys.argv[4]);sid=sys.argv[5]
b=json.loads((folder/'sender-binding.json').read_text());canonical=b['input'];tool='toolu_fixture'
if mode=='timeout':time.sleep(30);sys.exit(0)
e={'hook_event_name':'PreToolUse','tool_name':'SendMessage','session_id':b['sender_session_id'],'tool_use_id':tool,'tool_input':canonical}
G.admit(G.encoded(e),b,folder,binary_hash=G.BINARY_SHA256)
def out(v):print(json.dumps(v),flush=True)
out({'type':'assistant','session_id':b['sender_session_id'],'message':{'content':[{'type':'tool_use','id':tool,'name':'SendMessage','input':canonical}]}})
if mode=='partial':sys.exit(0)
content={'success':True,'msg_id':'fixture-message'}
if mode=='denied':content={'success':False}
out({'type':'user','session_id':b['sender_session_id'],'message':{'content':[{'type':'tool_result','tool_use_id':tool,'is_error':mode=='denied','content':[{'type':'text','text':json.dumps(content)}]}]}})
if mode=='pickup':
 from datetime import datetime,timezone
 record={'type':'user','sessionId':sid,'isSidechain':False,'uuid':'receipt','timestamp':datetime.now(timezone.utc).isoformat(),
  'origin':{'kind':'peer','verifiedPeerPid':os.getpid(),'msg_id':'fixture-message','body':canonical['message']},'message':{'role':'user','content':'opaque native display'}}
 with transcript.open('a') as f:f.write(json.dumps(record)+'\n');f.flush();os.fsync(f.fileno())
 time.sleep(.5)
'''


def fixture(tmp_path,monkeypatch,mode='pickup',seconds=20,version=1):
    tmp_path.chmod(0o700)
    transcript=tmp_path/'native.jsonl';transcript.write_text('{}\n');transcript.chmod(0o600)
    absent=str(tmp_path/'absent-settings.json');config={absent:None}
    enrolled=dict(target=asdict(TARGET),configuration=config,configuration_sha256=G.configuration_hash(config),
        native_target="Fixture today's Career [abcdef]",transcript=str(transcript),python_path=sys.executable,
        hook_path='/immutable/hook.py',home=str(tmp_path),model='claude-haiku-4-5',budget_usd=.10,native_shape_validated=True)
    record=dict(version=1,principal='robert',environment='dev',conversation_id='c-123456789abc',note_request_id='original',target=asdict(TARGET),owner_text='Approved fixture only',owner_text_sha256=hashlib.sha256(b'Approved fixture only').hexdigest(),constraints=['Hold release'],expires_at=time.time()+3600)
    if version==2:record.update(version=2,purpose=H.PROBE_PURPOSE,constraints=H.PROBE_CONSTRAINTS.copy())
    approved=dict(ref='career-original',sha256=H.digest(record),record=record,action=H.action(record))
    source=OwnerSource('robert',record['conversation_id'],'original','approved-owner-handoff:career-original:'+H.digest(record))
    fake=tmp_path/'fake.py';fake.write_text(FAKE)
    calls=[];children=[];boxes=[]
    def popen(argv,**kwargs):
        calls.append((argv,kwargs));folder=Path(argv[argv.index('--settings')+1]).parent
        child=subprocess.Popen([sys.executable,str(fake),str(Path(__file__).resolve().parents[2]),str(folder),mode,str(transcript),TARGET.session_id],
            stdin=kwargs['stdin'],stdout=kwargs['stdout'],stderr=kwargs['stderr'],env=kwargs['env'],start_new_session=kwargs['start_new_session'])
        children.append(child);return child
    monkeypatch.setattr(C,'preflight',lambda e,t:[1,2,3,4,5,6,7])
    deadline=time.time()+seconds
    box=C.ClaudeOutbox(tmp_path,{TARGET.alias:TARGET},enrolled,approved,deadline,popen=popen,
        confirm=lambda:dict(admitted=True,expires_at=deadline))
    rid=box.enqueue(TARGET.alias,source,approved['action'])
    return box,rid,calls,children,transcript


def test_enqueue_then_live_pid_pickup_and_no_second_send(tmp_path,monkeypatch):
    box,rid,calls,children,path=fixture(tmp_path,monkeypatch)
    result=box.dispatch(rid)
    assert result['state']=='dispatched'
    assert all(c.poll() is not None for c in children)
    observed=box.observe(rid)
    assert observed['state']=='acknowledged' and observed['proof']['provenance_mode']=='owned-live-pid-v1'
    assert observed['proof']['task_complete'] is False
    box.dispatch(rid);box.observe(rid)
    assert len(calls)==1
    argv,kw=calls[0]
    assert argv[argv.index('--permission-mode')+1]=='default'
    assert not any(v in argv for v in ('--restricted','--setting-sources','--dangerously-skip-permissions','--resume'))
    assert argv[argv.index('--max-turns')+1]=='1' and argv[argv.index('--max-budget-usd')+1]=='0.10'
    assert 'PYTHONPATH' not in kw['env']
    assert not (box.root/'requests'/rid/'native-reply.json').exists()


def test_the_fixed_sender_gives_claude_code_only_names_inside_the_one_launch_policy(tmp_path,monkeypatch):
    from core.mc_delegation_runner import launch_env
    six=('ANTHROPIC_API_KEY','OPENAI_API_KEY','XAI_API_KEY','GH_TOKEN','AWS_SECRET_ACCESS_KEY','TELEGRAM_BOT_TOKEN')
    for name in six:monkeypatch.setenv(name,'sk-test-not-real')
    box,rid,calls,children,path=fixture(tmp_path,monkeypatch)
    box.dispatch(rid)
    env=calls[0][1]['env']
    # It copies nothing from its parent: a fixed HOME, PATH and LANG, all names on the policy's base list.
    assert set(env)=={'HOME','PATH','LANG'} and set(env)<=set(launch_env.BASE_ALLOW)
    assert not set(six)&set(env) and 'sk-test-not-real' not in env.values() and env['LANG']=='en_US.UTF-8'


@pytest.mark.parametrize('mode',['partial','denied'])
def test_unknown_after_admission_never_reexecutes(tmp_path,monkeypatch,mode):
    box,rid,calls,children,path=fixture(tmp_path,monkeypatch,mode)
    assert box.dispatch(rid)['state']=='unknown'
    assert box.dispatch(rid)['state']=='unknown'
    assert len(calls)==1 and all(c.poll() is not None for c in children)


def test_timeout_reaps_owned_child_and_keeps_intent(tmp_path,monkeypatch):
    box,rid,calls,children,path=fixture(tmp_path,monkeypatch,'timeout')
    start=time.monotonic();assert box.dispatch(rid,timeout=1)['state']=='unknown'
    assert time.monotonic()-start<4 and children[0].poll() is not None
    box.dispatch(rid);assert len(calls)==1


def test_claim_budget_exhaustion_never_launches(tmp_path,monkeypatch):
    box,rid,calls,children,path=fixture(tmp_path,monkeypatch,seconds=7)
    assert box.dispatch(rid)['state']=='not_started' and not calls
    assert not (box.root/'requests'/rid/'intent.json').exists()


def test_final_live_confirmation_refusal_never_launches(tmp_path,monkeypatch):
    box,rid,calls,children,path=fixture(tmp_path,monkeypatch)
    box.confirm=lambda:dict(admitted=False)
    with pytest.raises(C.Refused):box.dispatch(rid)
    assert not calls and not (box.root/'requests'/rid/'intent.json').exists()


def test_late_receiver_record_after_sender_exit_never_becomes_pickup(tmp_path,monkeypatch):
    box,rid,calls,children,path=fixture(tmp_path,monkeypatch,'enqueue')
    assert box.dispatch(rid)['state']=='dispatched'
    assert box.observe(rid)['native_observation']=='enqueue_only'
    # No retrospective PID lookup or post-exit PID-only match is attempted.
    assert len(calls)==1 and not (box.root/'requests'/rid/'native-pickup.json').exists()


def test_tool_result_projection_requires_one_native_text_block():
    data={'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'id','content':[{'type':'text','text':json.dumps({'success':True,'msg_id':'m'})}]}]}}
    assert C.normalize_result(data,'id')['state']=='enqueued'
    data['message']['content'][0]['content'].append({'type':'text','text':'extra'})
    assert C.normalize_result(data,'id') is None


def test_first_native_candidate_requires_exact_operator_review_before_pickup(tmp_path,monkeypatch):
    box,rid,calls,children,path=fixture(tmp_path,monkeypatch)
    box.native_enrollment['native_shape_validated']=False
    assert box.dispatch(rid)['state']=='dispatched'
    folder=box.root/'requests'/rid
    candidate=_read(folder/'native-pickup-candidate.json')
    assert candidate['authoritative'] is False
    assert candidate['owned_child_alive_before_and_after'] is True
    assert children[0].poll() is not None
    assert box.observe(rid)['state']=='dispatched'
    box.native_enrollment['native_shape_validated']=True
    assert box.observe(rid)['state']=='dispatched'
    box.native_enrollment['pickup_candidate_sha256']='f'*64
    assert box.observe(rid)['state']=='dispatched'
    box.native_enrollment['pickup_candidate_sha256']=G.digest(candidate)
    assert box.observe(rid)['state']=='acknowledged'
    assert len(calls)==1


def test_native_string_tool_result_still_requires_exact_tool_and_strict_receipt():
    data={'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'id','content':json.dumps({'success':True,'msg_id':'m'})}]}}
    assert C.normalize_result(data,'id')['state']=='enqueued'
    assert C.normalize_result(data,'other') is None
    data['message']['content'][0]['content']='{"success":true,"success":false,"msg_id":"m"}'
    assert C.normalize_result(data,'id')['state']=='unknown'
    data['type']='assistant'
    assert C.normalize_result(data,'id') is None


def test_fixed_probe_owned_sender_payload_and_single_intent(tmp_path,monkeypatch):
    box,rid,calls,children,path=fixture(tmp_path,monkeypatch,version=2)
    assert box.dispatch(rid)['state']=='dispatched'
    binding=_read(box.root/'requests'/rid/'sender-binding.json')
    message=json.loads(binding['input']['message'])
    assert message['purpose']==H.PROBE_PURPOSE and message['handoff']['payload']==H.PROBE_TEXT
    assert 'Career' not in binding['input']['message']
    assert box.observe(rid)['state']=='acknowledged'
    box.dispatch(rid)
    assert len(calls)==1 and all(c.poll() is not None for c in children)
