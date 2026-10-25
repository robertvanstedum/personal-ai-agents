"""Fixed purpose contract only; no real native/provider or private input."""
import hashlib
import json
import time
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
import pytest
from core.agent_requests import handoff_payload as H
from core.agent_requests.direct_contact import Target
from core.mc_tools.approved_handoff import ApprovedHandoffs

T=Target('claude-probe','claude-code','22222222-2222-2222-2222-222222222222','local','/synthetic','claude-fixed-owner-handoff-v1','fixture')
def record(version=2):
    d=dict(version=version,principal='owner',environment='dev',conversation_id='c-aaaaaaaaaaaa',note_request_id='owner-request',owner_text='Owner explicitly approves this fixed synthetic test.',target=asdict(T),expires_at=time.time()+60)
    d.update(owner_text_sha256=hashlib.sha256(d['owner_text'].encode()).hexdigest(),constraints=H.PROBE_CONSTRAINTS.copy() if version==2 else ['Hold release'])
    if version==2:d['purpose']=H.PROBE_PURPOSE
    return d

def service(d):return SimpleNamespace(principal='owner',environment='dev',read_owner_note=lambda c,n:{'text':d['owner_text']})

def test_v1_career_action_and_send_envelope_byte_compatibility():
    d=record(1)
    old=H.canonical(dict(format='minimoi.approved-owner-handoff.v1',purpose='Existing owner-authorized Career spec/build handoff',owner_text=d['owner_text'],owner_note_request_id=d['note_request_id'],constraints=d['constraints'],authority='Original owner request only; no new work, merge, production or private-data authority.'))
    assert H.action(d)==old
    out=H.send_input(d,target='exact name',request_id='rid',correlation='corr',approved_action=old)
    expected=H.canonical(dict(format='minimoi.fixed-owner-handoff.v1',purpose='Existing owner-authorized Career spec/build handoff',request_id='rid',correlation='corr',handoff=json.loads(old),reply='Report in your own session when appropriate; no return SendMessage required. Preserve existing release hold.'))
    assert out==dict(to='exact name',message=expected,summary='Original owner Career spec handoff; preserve release hold')

def test_v2_fixed_payload_never_transmits_owner_authorization_as_instructions():
    d=record();approved=ApprovedHandoffs(lambda:{'probe':d}).resolve(service(d),'probe',T,d['conversation_id'])
    out=H.send_input(d,target='exact name',request_id='rid',correlation='corr',approved_action=approved['action'])
    text=out['message'];assert H.PROBE_TEXT in json.loads(text)['handoff']['payload']
    assert d['owner_text'] not in text and 'Career' not in text
    assert json.loads(text)['purpose']==H.PROBE_PURPOSE
    assert H.valid_reply(d,dict(state='reply_observed',report=H.TOKEN))
    assert not H.valid_reply(d,dict(state='reply_observed',report='Done '+H.TOKEN))

@pytest.mark.parametrize('change',[{'purpose':'career'},{'version':3},{'version':True},{'constraints':['Build something']},{'payload':'new task'},{'expires_at':0},{'expires_at':float('nan')},{'principal':'other'},{'environment':'prod'},{'conversation_id':'c-bbbbbbbbbbbb'},{'owner_text':'altered'}])
def test_bad_registry_or_authority_refused(change):
    d=record();svc=service(deepcopy(d));d.update(change)
    with pytest.raises(ValueError):ApprovedHandoffs(lambda:{'probe':d}).resolve(svc,'probe',T,'c-aaaaaaaaaaaa')

def test_wrong_source_and_target_refused():
    d=record();svc=service(d);svc.read_owner_note=lambda c,n:{'text':'different'}
    with pytest.raises(ValueError,match='source_changed'):ApprovedHandoffs(lambda:{'probe':d}).resolve(svc,'probe',T,d['conversation_id'])
    d['target']['revision']='other'
    with pytest.raises(ValueError):ApprovedHandoffs(lambda:{'probe':d}).resolve(service(d),'probe',T,d['conversation_id'])

def test_altered_action_never_constructs_send():
    d=record()
    with pytest.raises(ValueError,match='payload_changed'):H.send_input(d,target='exact',request_id='rid',correlation='c',approved_action=H.action(d)+' ')
