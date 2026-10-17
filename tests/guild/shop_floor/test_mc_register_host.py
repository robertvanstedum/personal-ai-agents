from types import SimpleNamespace
from pathlib import Path
import pytest
from flask import Flask
from core import work_registers as w
from minimoi_portal.guild_ui import mc_registers as m, api
import minimoi_portal.guild_ui as guild

@pytest.fixture
def host(tmp_path,monkeypatch):
 monkeypatch.setattr(w,'writer_domain',lambda:'Linux:11111111-1111-1111-1111-111111111111')
 root=tmp_path/'registers';s=w.RegisterStore(root,environment='dev');s.initialize()
 env={'MINIMOI_GUILD_REGISTERS':'1','MINIMOI_GUILD_REGISTERS_OWNER':'actual-owner',
      'MINIMOI_GUILD_REGISTERS_ENV':'dev','MINIMOI_GUILD_REGISTERS_ROOT':str(root)}
 conv={'id':'c-111111111111','principal':'actual-owner','record_mode':'on_record','archived':False}
 return s,env,conv


def test_fixed_owner_origin_private_guards_before_any_store(host,monkeypatch):
 s,e,c=host
 def forbidden(*a,**k):raise AssertionError('unauthorized store access')
 monkeypatch.setattr(w,'RegisterStore',forbidden)
 text='Please add this to your register: Preserve the decision trail'
 for principal,origin,conv in [('other','https://dev.minimoi.ai',c),('actual-owner','https://minimoi.ai',c),('actual-owner','https://dev.minimoi.ai',{**c,'record_mode':'off_record'})]:
  out=m.host_command(text,environ=e,principal=principal,origin=origin,conversation=conv,operation_id='owner-note')
  assert out['operation']['saved'] is False
 assert m.host_context('hello',environ=e,principal='other',origin='https://dev.minimoi.ai',conversation=c)==''
 assert not list(s.root.glob('mc/entries/*.md'))


def test_real_file_replay_source_reference_and_scrub(host):
 s,e,c=host;kw=dict(environ=e,principal='actual-owner',origin='https://dev.minimoi.ai',conversation=c,operation_id='guild-owner-note')
 text='Please add this to your register: Keep responsibility clear. password: synthetic-secret'
 first=m.host_command(text,**kw);second=m.host_command(text,**kw)
 assert first['status']=='saved'and second['operation']['repeated'] is True
 assert Path(first['operation']['path']).is_file()
 card=s.projection('mc')['entries'][0]
 assert card['references']==['guild:conversation/c-111111111111/operation/guild-owner-note']
 assert 'synthetic-secret' not in card['body']and '[credential removed]'in card['body']
 assert card['body'] in m.host_context('hello',environ=e,principal='actual-owner',origin='https://dev.minimoi.ai',conversation=c)


def test_actual_api_hook_keeps_receipt_and_scopes_operation_to_conversation(host,monkeypatch):
 s,e,c=host;kept=[];note={}
 # ConversationStore records have no record_mode. Authority must come from
 # actual CSRF/on-record/kept-owner-note checks in _mc_prelude.
 stored={k:v for k,v in c.items()if k!='record_mode'}
 assert 'record_mode'not in stored
 floor=SimpleNamespace(add_note=lambda key,text,*a,**k: kept.append((key,text))or SimpleNamespace(outcome='kept',value={'text':text}),get_note=lambda note_id:dict(note))
 monkeypatch.setattr(guild,'feature_environ',lambda:e)
 monkeypatch.setattr(api,'cfg',lambda:{'base_url':'https://dev.minimoi.ai','services':SimpleNamespace(mc_turns=True),'current_user':lambda:{'username':'actual-owner'}})
 monkeypatch.setattr(api,'_conversations',lambda:SimpleNamespace(get=lambda cid,p:{**stored,'id':cid}))
 monkeypatch.setattr(api,'_conv_floor',lambda conv:floor)
 app=Flask(__name__);app.secret_key='synthetic-session-secret'
 @app.post('/synthetic-register')
 def route():
  refusal,found=api._mc_prelude()
  if refusal is not None:return refusal
  return api._owner_register_command(found)
 client=app.test_client()
 with client.session_transaction()as session:session['guild_floor_csrf']='synthetic-csrf'
 headers={'X-CSRF-Token':'synthetic-csrf','X-Record-Mode':'on_record','Origin':'https://dev.minimoi.ai','Sec-Fetch-Site':'same-origin'}
 for cid,body in [('c-111111111111','First expectation'),('c-222222222222','Second expectation'),('c-111111111111','First expectation')]:
  note.update(text='Please add this to your register: '+body,author_kind='owner',who='actual-owner')
  response=client.post('/synthetic-register',json={'conversation_id':cid,'note_request_id':'same-kept-note-id','request_id':'request-synthetic'},headers=headers)
  assert response.status_code==200 and response.get_json()['operation']['saved'] is True
 assert len(s.projection('mc')['entries'])==2 and len(kept)==3
 # A real Private refusal never grants the ephemeral kept-owner scope.
 refused=client.post('/synthetic-register',json={'conversation_id':stored['id'],'note_request_id':'same-kept-note-id'},headers={**headers,'X-Record-Mode':'off_record'})
 assert refused.status_code==409 and len(kept)==3 and len(s.projection('mc')['entries'])==2
 # A handcrafted call without successful prelude cannot invent that scope.
 with app.test_request_context('/synthetic-register',method='POST',headers=headers,json={}):
  found=(None,'actual-owner',stored,floor,note,'same-kept-note-id',None,[])
  response,status=api._owner_register_command(found)
  assert status==200 and response.get_json()['operation']['saved'] is False
 assert len(s.projection('mc')['entries'])==2


def test_missing_configuration_refuses_literal_save_without_guessing_root():
 result=m.host_command('Please add this to your register: Keep receipts',environ={},principal='actual-owner',origin='https://dev.minimoi.ai',conversation={'id':'c-111111111111','principal':'actual-owner','record_mode':'on_record'},operation_id='note')
 assert result['operation']['saved'] is False


def test_saved_register_receipt_failure_uses_truthful_actual_js_path():
 import json,shutil,subprocess
 node=shutil.which('node')
 if not node:pytest.skip('Node is required for actual response handler check')
 source=(Path(__file__).resolve().parents[3]/'minimoi_portal/guild_ui/static/js/conversation.js').read_text()
 handler=source[source.index('function showAnswer(holder, r) {'):source.index('// Which files went into the request')]
 script="let lastAnswered=false,arrivals=0,failures=0,shown='';const applyHeader=()=>{},platformLine=(who,text)=>({who,text}),announce=()=>{},answerArrived=()=>arrivals++,answerFailed=()=>failures++,follow=()=>{};const holder={replaceWith:x=>shown=x.text};\n"+handler+"\nshowAnswer(holder,{ok:true,body:{status:'answered',reply_note:null,message:'Registered MC-0001',operation:{type:'work_register',saved:true,id:'MC-0001'}}});if(!lastAnswered||arrivals!==1||failures!==0||!shown.includes('could not be kept'))throw Error('untruthful saved state');"
 result=subprocess.run([node,'-e',script],capture_output=True,timeout=5)
 assert result.returncode==0,result.stderr
