import importlib.util,sys
from pathlib import Path
import pytest
from flask import Flask
from core.realtime_voice.budget import VoiceBudget
from core.realtime_voice.controlled_session import Journal
sp=importlib.util.spec_from_file_location('draft_cos_managed',Path(__file__).parents[2]/'domains/cos/managed_realtime.py');m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
class Provider:
 def create(self,*a):return 'answer','rtc_synthetic'
 def attach(self,*a):pass
 def hangup(self,*a):pass
ENV={'MINIMOI_COS_MANAGED_REALTIME':'1','MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO':'1000000'}
H={'X-Minimoi-Auth-Id':'7','X-Minimoi-User-Tier':'owner','X-Minimoi-Voice-Write-Verified':'1'}
@pytest.fixture
def setup(tmp_path):
 b=VoiceBudget('sqlite:///'+str(tmp_path/'ledger'),cap_usd=lambda:20);b.ensure_schema();Journal(b._tx).init()
 app=Flask('synthetic');calls=[]
 assert m.install(app,environ=ENV,process_turn=lambda *a,**kw:None,instructions=lambda p:'Synthetic persona',budget=b,provider_factory=lambda:calls.append(1) or Provider())
 return app.test_client(),calls
@pytest.mark.parametrize('headers',[{}, {'X-Minimoi-Auth-Id':'7','X-Minimoi-User-Tier':'owner'}, {**H,'X-Minimoi-User-Tier':'guest'},{'X-Minimoi-Voice-Write-Verified':'1','X-Minimoi-User-Tier':'owner'}])
def test_owner_proxy_and_verified_write_required(setup,headers):
 c,calls=setup;assert c.post('/api/realtime-voice/managed/session',headers=headers,json={'provider':'openai','sdp':'offer','conversation_id':'owner'}).status_code==403;assert not calls
@pytest.mark.parametrize('conversation',[None,'foreign','private',{},''])
def test_arbitrary_conversation_refused_before_provider(setup,conversation):
 c,calls=setup;r=c.post('/api/realtime-voice/managed/session',headers=H,json={'provider':'openai','sdp':'offer','conversation_id':conversation});assert r.status_code==503;assert not calls

def test_real_registered_start_end_and_no_secret(setup):
 c,calls=setup;r=c.post('/api/realtime-voice/managed/session',headers=H,json={'provider':'openai','sdp':'offer','conversation_id':'owner'});assert r.status_code==201 and r.json['sdp']=='answer' and 'client_secret' not in r.json
 ended=c.post('/api/realtime-voice/managed/session/'+r.json['session_id']+'/end',headers=H);assert ended.json['termination_confirmed']

def test_feature_off_and_schema_missing_register_nothing(tmp_path):
 app=Flask('off');assert not m.install(app,environ={},process_turn=None,instructions=None)
 assert app.test_client().post('/api/realtime-voice/managed/session').status_code==404
 b=VoiceBudget('sqlite:///'+str(tmp_path/'missing'),cap_usd=lambda:20);b.ensure_schema()
 assert not m.install(app,environ=ENV,process_turn=None,instructions=None,budget=b)
