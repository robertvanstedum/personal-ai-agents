"""Disposable SQLite/Flask only; actual candidate modules, no provider/network."""
import importlib,json,sys,types
from pathlib import Path
import pytest
from flask import Flask,abort
from core.realtime_voice.budget import VoiceBudget
m=importlib.import_module('core.realtime_voice.controlled_session');host=importlib.import_module('core.realtime_voice.controlled_host')
@pytest.fixture
def rig(tmp_path):
 b=VoiceBudget('sqlite:///'+str(tmp_path/'fixture.db'),cap_usd=lambda:20);b.ensure_schema();j=m.Journal(b._tx);j.init();calls=[]
 def factory():calls.append('factory');raise m.SessionRefused('private synthetic factory detail')
 a=Flask('admission-test')
 def guard(req):
  if req.headers.get('X-CSRF')!='valid':abort(403)
  return 'owner'
 a.register_blueprint(host.create_host_blueprint(name='test',domain='mc',guard=guard,conversation=lambda p,r:'canonical',instructions=lambda p:'safe',dispatch=lambda *a:None,provider_factory=factory,environ={'MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO':'5000000'},budget=b),url_prefix='/voice')
 return b,j,calls,a.test_client()
def snapshot(b):
 with b._tx() as q:return [q('SELECT * FROM '+t).fetchall() for t in ('voice_control_owner','voice_control_session','voice_control_call','voice_control_usage','voice_budget_day','voice_budget_entry')]
def request(c,headers=None):return c.post('/voice/session',json={'provider':'openai','conversation_id':'canonical','sdp':'synthetic'},headers=headers or {'X-CSRF':'valid'})
@pytest.mark.parametrize('state',['connecting','active','ending','termination_uncertain'])
def test_known_voice_gate_before_provider_factory_and_unchanged_holds(rig,state):
 b,j,calls,c=rig;sid=j.create('owner','old',5000000,'mc');j.state(sid,state);before=snapshot(b);r=request(c)
 assert r.status_code==409 and r.json['code']=='voice_session_unresolved';assert 'Typing still works' in r.json['error'];assert sid not in r.text and 'private' not in r.text;assert calls==[] and snapshot(b)==before
 with pytest.raises(m.UnresolvedOperation) as e:j.create('owner','other',1,'cos')
 assert e.value.code=='voice_session_unresolved' and snapshot(b)==before
@pytest.mark.parametrize('state',['attempting','uncertain'])
def test_agent_gate_before_factory_preserves_exact_call_and_budget(rig,state):
 b,j,calls,c=rig;sid=j.create('owner','canonical',5000000,'mc');j.state(sid,'ended')
 with b._tx() as q:q('INSERT INTO voice_control_call(session_id,call_id,operation_id,item_id,text_hash,state) VALUES(?,?,?,?,?,?)',(sid,'private-call','op','item','hash',state))
 before=snapshot(b);r=request(c);assert r.status_code==409 and r.json['code']=='agent_action_unresolved';assert 'private-call' not in r.text;assert calls==[] and snapshot(b)==before
@pytest.mark.parametrize('headers',[{}, {'X-CSRF':'bad'}])
def test_auth_csrf_gate_no_admission_disclosure_or_factory(rig,headers):
 b,j,calls,c=rig;j.create('owner','old',1,'mc');r=c.post('/voice/session',json={'provider':'openai'},headers=headers);assert r.status_code==403 and 'voice_session_unresolved' not in r.text and calls==[]
def test_other_error_stays_generic503_no_raw_detail(rig):
 b,j,calls,c=rig;r=request(c);assert r.status_code==503 and 'code' not in r.json and 'private' not in r.text;assert calls==['factory']
def test_atomic_create_keeps_gate_if_preflight_was_stale(rig):
 b,j,calls,c=rig;j.check_admission('owner','canonical');j.create('owner','new',1,'mc')
 with pytest.raises(m.UnresolvedOperation):j.create('owner','canonical',1,'mc')
 assert calls==[]
