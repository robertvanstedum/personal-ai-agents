"""Disposable SQLite/Flask only; no provider/network or live DB."""
import importlib,sys,types,json,sqlite3
from pathlib import Path
import pytest
from flask import Flask,abort
from core.realtime_voice.budget import VoiceBudget
ROOT=Path(__file__).parents[1]/'core/realtime_voice'
pkg=types.ModuleType('end_receipt_candidate');pkg.__path__=[str(ROOT)];sys.modules[pkg.__name__]=pkg
sys.modules[pkg.__name__+'.budget']=importlib.import_module('core.realtime_voice.budget')
m=importlib.import_module(pkg.__name__+'.controlled_session');api=importlib.import_module(pkg.__name__+'.controlled_api')
CONFIG={'model':m.MODEL,'type':'realtime','audio':{'input':{'turn_detection':{'create_response':False}}}}
class Provider:
 def __init__(self):self.hangups=[];self.creates=0;self.fail=False
 def create(self,*args):self.creates+=1;return 'synthetic-answer','rtc_synthetic'
 def attach(self,call,observe):pass
 def hangup(self,call):
  self.hangups.append(call)
  if self.fail:raise TimeoutError('private synthetic provider error')
@pytest.fixture
def rig(tmp_path):
 b=VoiceBudget('sqlite:///'+str(tmp_path/'disposable.db'),cap_usd=lambda:20);b.ensure_schema();j=m.Journal(b._tx);j.init();p=Provider();s=m.ControlledSession(journal=j,budget=b,provider=p,principal='owner',conversation='c1',allowance_micro=5_000_000,dispatch=lambda *a:None,domain='mc')
 yield b,j,p,s
 s.end('test_cleanup');s.worker.shutdown(wait=True)
def start(s):return s.start('synthetic-offer',CONFIG)
def app(j,session=None,*,surface='mc'):
 a=Flask('synthetic-end-recovery')
 def guard(req):
  if req.headers.get('X-Test-CSRF')!='valid':abort(403)
  return req.headers.get('X-Test-Owner','owner')
 def build(*args):
  assert session is not None,'recovery must not construct provider/session'
  return session
 a.register_blueprint(api.blueprint(name='recovery',guard=guard,build_session=build,build_config=lambda p:CONFIG,journal=j,surface=surface),url_prefix='/voice');return a.test_client()
H={'X-Test-CSRF':'valid'}
def test_provider_identity_is_persisted_before_attach(rig):
 b,j,p,s=rig
 def attach(*args):
  with b._tx() as q:assert q('SELECT provider_call_id,surface FROM voice_control_session WHERE id=?',(s.sid,)).fetchone()==('rtc_synthetic','mc')
 p.attach=attach;start(s)
def test_repeated_end_returns_original_receipt_without_another_hangup(rig):
 b,j,p,s=rig;start(s);first=s.end('duration_limit');assert s.end('user_ended')==first;assert p.hangups==['rtc_synthetic']
 with b._tx() as q:
  stored=q('SELECT state,end_receipt FROM voice_control_session WHERE id=?',(s.sid,)).fetchone();assert stored[0]=='ended';assert json.loads(stored[1])==first
 assert j.end_receipt(s.sid,'owner','mc')==first
 assert 'rtc_synthetic' not in json.dumps(first)
def test_restart_readback_is_authenticated_and_does_not_construct_provider(rig):
 b,j,p,s=rig;start(s);first=s.end('user_ended');client=app(j)
 response=client.post('/voice/session/'+s.sid+'/end',headers=H);assert response.status_code==200 and response.json['termination_confirmed'] is True
 assert response.json['reason']==first['reason'] and p.hangups==['rtc_synthetic'] and p.creates==1
 assert client.post('/voice/session/'+s.sid+'/end').status_code==403
 assert client.post('/voice/session/'+s.sid+'/end',headers={**H,'X-Test-Owner':'foreign'}).status_code==403
 assert app(j,surface='cos').post('/voice/session/'+s.sid+'/end',headers=H).status_code==403
 assert p.hangups==['rtc_synthetic']
def test_failed_hangup_remains_uncertain_on_replay_and_restart(rig):
 b,j,p,s=rig;start(s);p.fail=True;first=s.end('user_ended');assert first['termination_confirmed'] is False
 p.fail=False;assert s.end('user_ended')==first;assert app(j).post('/voice/session/'+s.sid+'/end',headers=H).json['termination_confirmed'] is False
 assert p.hangups==['rtc_synthetic']
 with pytest.raises(m.SessionRefused):j.create('owner','c2',100,'mc')
def test_known_identity_survives_create_exception_without_raw_error(rig):
 b,j,p,s=rig
 def fail(*args):p.call_id='rtc_known';raise ValueError('raw SDP/private text')
 p.create=fail
 with pytest.raises(m.SessionRefused):start(s)
 with b._tx() as q:assert q('SELECT provider_call_id,state FROM voice_control_session WHERE id=?',(s.sid,)).fetchone()==('rtc_known','ended')
 assert p.hangups==['rtc_known'];assert 'private' not in json.dumps(s.end_receipt)
def test_unknown_create_identity_stays_held(rig):
 b,j,p,s=rig;p.create=lambda *a:(_ for _ in ()).throw(TimeoutError('raw provider text'))
 with pytest.raises(m.SessionRefused):start(s)
 assert not s.end_receipt['termination_confirmed'] and not p.hangups
 with pytest.raises(m.SessionRefused):j.create('owner','c2',100,'mc')
def test_legacy_and_interrupted_rows_are_never_promoted_or_retried(rig):
 b,j,p,s=rig;sid=j.create('legacy-owner','old',100,'mc');j.state(sid,'termination_uncertain')
 receipt=j.end_receipt(sid,'legacy-owner','mc');assert receipt['termination_confirmed'] is False
 with pytest.raises(m.SessionRefused):j.record_end(sid,dict(receipt,termination_confirmed=True))
 with b._tx() as q:
  assert q('SELECT state,end_receipt FROM voice_control_session WHERE id=?',(sid,)).fetchone()==('termination_uncertain',None)
  q('UPDATE voice_control_session SET surface=NULL WHERE id=?',(sid,))
 with pytest.raises(m.SessionRefused):j.end_receipt(sid,'legacy-owner','mc')
def test_restart_readback_does_not_change_budget_or_session_bytes(rig):
 b,j,p,s=rig;start(s);s.end('user_ended')
 def rows():
  with b._tx() as q:return [q('SELECT * FROM '+t).fetchall() for t in ('voice_control_session','voice_budget_day','voice_budget_entry')]
 before=rows();assert app(j).post('/voice/session/'+s.sid+'/end',headers=H).status_code==200;assert rows()==before

def test_end_during_attach_does_not_reactivate_session(rig):
 b,j,p,s=rig;p.attach=lambda *a:s.end('control_uncertain')
 with pytest.raises(m.SessionRefused):start(s)
 with b._tx() as q:assert q('SELECT state FROM voice_control_session WHERE id=?',(s.sid,)).fetchone()==('ended',)
 assert p.hangups==['rtc_synthetic']

def test_schema_upgrade_is_not_implicit_for_existing_journal(tmp_path):
 b=VoiceBudget('sqlite:///'+str(tmp_path/'legacy.db'));b.ensure_schema()
 with b._tx() as q:q('CREATE TABLE voice_control_session (id TEXT PRIMARY KEY, who TEXT NOT NULL, conversation TEXT NOT NULL,state TEXT NOT NULL,allowance BIGINT NOT NULL,used BIGINT NOT NULL DEFAULT 0)')
 j=m.Journal(b._tx);j.init()
 with b._tx() as q:assert 'surface' not in {r[1] for r in q('PRAGMA table_info(voice_control_session)').fetchall()}
 with pytest.raises(Exception):j.create('owner','c1',100,'mc')

def test_actual_adapter_keeps_valid_location_identity_when_sdp_is_rejected():
 server=importlib.import_module(pkg.__name__+'.server_openai')
 def post(*args,**kwargs):return types.SimpleNamespace(response=types.SimpleNamespace(headers={'location':'/v1/realtime/calls/rtc_known_location'}),text='')
 p=server.OpenAIControlledProvider(types.SimpleNamespace(post=post))
 with pytest.raises(m.SessionRefused):p.create('synthetic-offer',CONFIG,'synthetic-hash')
 assert p.call_id=='rtc_known_location'

def test_journal_end_failure_preserves_identity_and_hold_without_hangup_retry(rig):
 b,j,p,s=rig;start(s)
 j.record_end=lambda *a:(_ for _ in ()).throw(RuntimeError('private synthetic DB error'))
 receipt=s.end('user_ended');assert receipt['termination_confirmed'] is True and receipt['journal_confirmed'] is False
 assert s.end('user_ended')==receipt and p.hangups==['rtc_synthetic']
 recovered=j.end_receipt(s.sid,'owner','mc');assert recovered['termination_confirmed'] is False and recovered['reason']=='recovery_required'
 with b._tx() as q:assert q('SELECT provider_call_id,state FROM voice_control_session WHERE id=?',(s.sid,)).fetchone()==('rtc_synthetic','ending')
 with pytest.raises(m.SessionRefused):j.create('owner','c2',100,'mc')
