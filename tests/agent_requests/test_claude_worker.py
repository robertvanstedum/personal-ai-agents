"""Worker provider seam; fake private registry and no native process."""
import json,time,hashlib
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.mc_host import agent_worker as W
from core.agent_requests import claude_fixed_send as C
from core.mc_tools.approved_handoff import canonical,digest
from tests.agent_requests.test_claude_fixed_send import TARGET


def setup(tmp_path,monkeypatch,execute=True,failed=False,intent=False,version=1):
    tmp_path.chmod(0o700)
    record=dict(version=1,owner_text_sha256=hashlib.sha256(b'Original fixture request').hexdigest(),principal='robert',environment='dev',conversation_id='c-123456789abc',note_request_id='original',
        target=asdict(TARGET),owner_text='Original fixture request',constraints=['Hold release'],expires_at=time.time()+60)
    action=canonical(dict(format='minimoi.approved-owner-handoff.v1',purpose='Existing owner-authorized Career spec/build handoff',
        owner_text=record['owner_text'],owner_note_request_id='original',constraints=record['constraints'],
        authority='Original owner request only; no new work, merge, production or private-data authority.'))
    if version==2:
        from core.agent_requests import handoff_payload as H
        record.update(version=2,purpose=H.PROBE_PURPOSE,constraints=H.PROBE_CONSTRAINTS.copy())
        action=H.action(record)
    registry=tmp_path/'registry.json';registry.write_text(json.dumps({'handoffs':{'original':record}}));registry.chmod(0o600)
    approved=dict(ref='original',record=record,sha256=digest(record),action=action)
    source=dict(principal='robert',conversation_id=record['conversation_id'],note_id='original',
        authorization_ref='approved-owner-handoff:original:'+digest(record))
    d=dict(execute=execute,request_ref='11111111-1111-4111-8111-111111111111',nonce='c'*64,expires_at=time.time()+20,
        target=asdict(TARGET),source=source,action=action,approved_handoff=approved)
    config=dict(principal='robert',environment='dev',private_root=str(tmp_path),targets={TARGET.alias:dict(target=asdict(TARGET),handoff_registry=str(registry))})
    calls=[];rid='r-'+'a'*24
    folder=tmp_path/'requests'/rid;folder.mkdir(parents=True)
    if intent:(folder/'intent.json').write_text('{}')
    def api(body):
        calls.append(body)
        if body['phase'] in ('claim','observe'):return d
        if body['phase']=='confirm':return dict(admitted=True,expires_at=d['expires_at'])
        return dict(state='recorded')
    class Box:
        def __init__(self,*a,confirm,**kw):self.root=tmp_path;self.confirm=confirm
        def enqueue(self,*a):return rid
        def dispatch(self,*a,**kw):
            calls.append(dict(phase='fake-launch'));self.confirm()
            if failed:raise ValueError('fixed refusal')
            return dict(state='dispatched')
        def observe(self,*a):calls.append(dict(phase='fake-read'));return dict(state='dispatched',proof={})
    monkeypatch.setattr(C,'ClaudeOutbox',Box)
    return config,d,api,calls


def test_claude_claim_and_read_only_observation_are_distinct(tmp_path,monkeypatch):
    config,d,api,calls=setup(tmp_path,monkeypatch)
    assert W.one(config,api,dict(mode='claim',request_ref=d['request_ref']))=='dispatched'
    d['execute']=False
    assert W.one(config,api,dict(mode='observe',request_ref=d['request_ref']))=='dispatched'
    assert [c['phase'] for c in calls].count('fake-launch')==1
    assert [c['phase'] for c in calls].count('fake-read')==1
    assert calls[-1]['phase']=='complete'

@pytest.mark.parametrize('intent,state',[(False,'not_started'),(True,'unknown')])
def test_failed_dispatch_classifies_durable_intent(tmp_path,monkeypatch,intent,state):
    config,d,api,calls=setup(tmp_path,monkeypatch,failed=True,intent=intent)
    assert W.one(config,api,dict(mode='claim',request_ref=d['request_ref']))==state
    assert calls[-1]['outcome']['state']==state

@pytest.mark.parametrize('field',['action','source','record'])
def test_protected_original_binding_blocks_substitution(tmp_path,monkeypatch,field):
    config,d,api,calls=setup(tmp_path,monkeypatch)
    if field=='action':d['action']='New work'
    elif field=='source':d['source']['note_id']='other'
    else:d['approved_handoff']['record']['owner_text']='New work'
    with pytest.raises(W.Refused):W.one(config,api,dict(mode='claim',request_ref=d['request_ref']))
    assert not any(c['phase']=='fake-launch' for c in calls)


def test_worker_accepts_only_shared_fixed_probe_action(tmp_path,monkeypatch):
    config,d,api,calls=setup(tmp_path,monkeypatch,version=2)
    assert W.one(config,api,dict(mode='claim',request_ref=d['request_ref']))=='dispatched'
    assert [c['phase'] for c in calls].count('fake-launch')==1
    d['action']=d['approved_handoff']['action']=d['action'].replace('Do not inspect files','Please inspect files')
    with pytest.raises(W.Refused,match='payload_changed'):W.one(config,api,dict(mode='claim',request_ref=d['request_ref']))
    assert [c['phase'] for c in calls].count('fake-launch')==1


def test_socket_transport_dispatch_returns_native_proof_to_callback(tmp_path,monkeypatch):
    from core.agent_requests import claude_socket_probe as S
    from core.agent_requests import handoff_payload as H
    config,d,api,calls=setup(tmp_path,monkeypatch,version=2)
    d['target']['transport']=S.TRANSPORT
    record=d['approved_handoff']['record'];record['target']['transport']=S.TRANSPORT
    d['approved_handoff']['sha256']=H.digest(record)
    d['source']['authorization_ref']='approved-owner-handoff:original:'+H.digest(record)
    enrolled=config['targets'][TARGET.alias];enrolled['target']['transport']=S.TRANSPORT
    Path(enrolled['handoff_registry']).write_text(json.dumps({'handoffs':{'original':record}}))
    proof={'state':'pickup_observed','msg_id':'exact','task_complete':False,'provenance_mode':S.MODE}
    class Box:
        def __init__(self,*args,confirm,**kwargs):self.root=tmp_path;self.confirm=confirm
        def enqueue(self,*args):return 'r-'+'a'*24
        def dispatch(self,*args,**kwargs):
            self.confirm();calls.append({'phase':'socket-fixture'})
            return {'state':'acknowledged','proof':proof}
    monkeypatch.setattr(S,'SocketProbeOutbox',Box)
    assert W.one(config,api,dict(mode='claim',request_ref=d['request_ref']))=='acknowledged'
    assert calls[-1]['outcome']['proof']==proof
    assert [c['phase'] for c in calls].count('socket-fixture')==1
    assert not any(c['phase']=='fake-launch' for c in calls)
