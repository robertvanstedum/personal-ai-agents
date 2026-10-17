"""Synthetic managed host; real owner_api/check_write/budget/session factory.
No provider, native model, paid call or production schema/network operation.
"""
import functools
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from flask import Flask,jsonify,redirect,request,has_request_context
from core.realtime_voice.budget import VoiceBudget
from core.realtime_voice.controlled_session import Journal,SessionRefused
from minimoi_portal.guild_ui import cfg,feature_environ
from minimoi_portal.guild_ui import api,mc_managed_realtime as host
from minimoi_portal.guild_ui.security import csrf_token
from minimoi_portal.guild_ui.stores import WriteResult
from minimoi_portal.guild_ui.conversations import ConversationNotFound

class Provider:
    def __init__(self):self.sessions=[];self.closed=[]
    def create(self,*a):return 'synthetic-answer','rtc_synthetic'
    def attach(self,cid,observe):self.sessions.append(observe.__self__)
    def send(self,*a):pass
    def send_result(self,*a,**kw):pass
    def hangup(self,cid):self.closed.append(cid)
class Notes:
    floor="actual-owner-floor"
    def __init__(self):self.rows={};self.postits={}
    def add_note(self,key,text,author,**kw):
        if key in self.rows:
            old=self.rows[key]
            return WriteResult('kept' if old['text']==text and old['who']==author.id else 'idempotency_mismatch',old,True)
        value={'request_id':key,'id':key,'text':text,'who':author.id,'author_kind':author.kind,'context':{}}
        self.rows[key]=value;return WriteResult('kept',value,False)
    def add_postit(self,text,author,*,idempotency_key):
        old=self.postits.get(idempotency_key)
        row=old or {'id':len(self.postits)+1,'text':text}
        self.postits[idempotency_key]=row
        return WriteResult('added',row,old is not None)
class Conversations:
    def __init__(self):self.archived=False
    def get(self,cid,principal):
        if cid!='c-123456abcdef' or principal!='owner':raise ConversationNotFound()
        return {'id':cid,'principal':principal,'archived':self.archived,'notes_floor':'actual-owner-floor'}
@pytest.fixture
def ready(tmp_path,monkeypatch):
    app=Flask('managed-synthetic');app.secret_key='test-only-session-key'
    env={'MINIMOI_MC_MANAGED_REALTIME':'1','MINIMOI_MC_VOICE':'1','MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO':'5000000','MINIMOI_GUILD_JOBS':'1'}
    notes=Notes();convs=Conversations();provider=Provider();calls=[]
    ledger=VoiceBudget('sqlite:///'+str(tmp_path/'budget.db'),cap_usd=lambda:20);ledger.ensure_schema();Journal(ledger._tx).init()
    def current_user():return {'username':'owner','display_name':'Actual synthetic owner'} if request.headers.get('X-Synthetic-Owner')=='yes' else None
    def owner_guard(fn):
        @functools.wraps(fn)
        def guarded(*a,**k):return fn(*a,**k) if current_user() else redirect('/login')
        return guarded
    services=SimpleNamespace(mc=SimpleNamespace(kind='cos'),mc_turns=True)
    config={'owner_guard':owner_guard,'current_user':current_user,'url_prefix':'/guild-next','base_url':'http://localhost','services':services,'feature_environ':env}
    app.extensions['guild']=config
    monkeypatch.setattr(api,'_conversations',lambda:convs);monkeypatch.setattr(api,'_conv_floor',lambda conv:notes);monkeypatch.setattr(api,'_floor',lambda:notes)
    def turn(actual_services,**kw):
        assert not has_request_context() and cfg() is config and feature_environ()==env
        assert actual_services is services and kw['floor'] is notes and kw['principal']=='owner'
        calls.append(kw);return {'status':'answered','reply_note':{'text':'Synthetic native reply','request_id':'reply-id'},'history_saved':True},200
    monkeypatch.setattr(api,'run_mc_turn',turn)
    from minimoi_portal.guild_ui import jobs_api
    monkeypatch.setattr(jobs_api,'job_command',lambda found:None)
    @app.get('/csrf')
    def token():return jsonify(token=csrf_token())
    assert host.install(app,'guild',environ=env,budget=ledger,provider_factory=lambda:provider)
    client=app.test_client();token=client.get('/csrf').json['token']
    headers={'X-Synthetic-Owner':'yes','Origin':'http://localhost','Content-Type':'application/json','X-CSRF-Token':token,'X-Record-Mode':'on_record'}
    yield SimpleNamespace(app=app,client=client,headers=headers,notes=notes,convs=convs,provider=provider,calls=calls,config=config,ledger=ledger,env=env)
    for s in provider.sessions:s.end('test');s.worker.shutdown(wait=True)
PATH='/guild-next/api/v1/mc/voice/managed/session'
BODY={'provider':'openai','conversation_id':'c-123456abcdef','sdp':'synthetic-offer'}
def start(r):
    response=r.client.post(PATH,json=BODY,headers=r.headers);assert response.status_code==201
    return r.provider.sessions[-1],response.json

def test_actual_owner_and_write_guards_before_provider(ready):
    r=ready
    assert r.client.post(PATH,json=BODY).status_code==401
    for changes,expected in [({'X-Record-Mode':'off_record'},409),({'X-Record-Mode':'unknown'},422),({'X-CSRF-Token':'bad'},403)]:
        assert r.client.post(PATH,json=BODY,headers={**r.headers,**changes}).status_code==expected
    assert not r.provider.sessions and not r.notes.rows

def test_foreign_archived_missing_conversations_refused(ready):
    r=ready
    for cid in ('foreign','',None):assert r.client.post(PATH,json={**BODY,'conversation_id':cid},headers=r.headers).status_code==503
    r.convs.archived=True;assert r.client.post(PATH,json=BODY,headers=r.headers).status_code==503
    assert not r.provider.sessions

def test_actual_captured_worker_no_request_and_exact_idempotent_note(ready):
    r=ready;s,_=start(r)
    with ThreadPoolExecutor(max_workers=1) as worker:
        a=worker.submit(s.dispatch,'consult_cos_agent','Exact owner spoken words','c-123456abcdef','operation-123').result()
    assert a['reply']=='Synthetic native reply'
    assert r.calls[0]['note']['text']=='Exact owner spoken words' and r.calls[0]['note_id']=='operation-123' and r.calls[0]['request_id']=='operation-123'
    assert len(r.notes.rows)==1
    with ThreadPoolExecutor(max_workers=1) as worker:
        worker.submit(s.dispatch,'save_cos_note','Save a note: exact owner note','c-123456abcdef','note-operation-123').result()
        repeated=worker.submit(s.dispatch,'save_cos_note','Save a note: exact owner note','c-123456abcdef','note-operation-123').result()
    assert repeated['operation']['repeated'] and len(r.notes.rows)==2

def test_nonliteral_note_refused_before_write(ready):
    r=ready;s,_=start(r)
    with pytest.raises(SessionRefused):s.dispatch('save_cos_note','model invented a note','c-123456abcdef','operation-1')
    assert not r.notes.rows


def test_spoken_note_uses_normal_note_redaction_before_persistence(ready):
    r=ready;s,_=start(r)
    original='Keep this reminder: card 4242 4242 4242 4242'
    with ThreadPoolExecutor(max_workers=1) as worker:
        worker.submit(s.dispatch,'consult_cos_agent',original,'c-123456abcdef','redacted-operation').result()
    kept=r.notes.rows['redacted-operation']['text']
    assert '4242' not in kept and kept!=original
    assert r.calls[0]['note']['text']==kept

def test_literal_job_command_uses_existing_boundary_without_native(ready,monkeypatch):
    r=ready;s,_=start(r)
    from minimoi_portal.guild_ui import jobs_api
    def command(found):
        assert not has_request_context() and cfg() is r.config
        assert found[4]['text']=='run this as a job' and found[1]=='owner'
        return jsonify(message='Actual job receipt',job_command='run_job',job={'id':'synthetic-job'}),200
    monkeypatch.setattr(jobs_api,'job_command',command)
    with ThreadPoolExecutor(max_workers=1) as worker:out=worker.submit(s.dispatch,'consult_cos_agent','run this as a job','c-123456abcdef','job-operation-123').result()
    assert out['job_command']=='run_job' and not r.calls

def test_end_remains_authorized_when_owner_switches_private(ready):
    r=ready;s,body=start(r)
    response=r.client.post(PATH+'/'+body['session_id']+'/end',headers={**r.headers,'X-Record-Mode':'off_record'})
    assert response.status_code==200 and response.json['termination_confirmed']

def test_disabled_flag_registers_no_routes_or_calls(ready):
    r=ready;app=Flask('off');app.extensions['off']=r.config
    assert not host.install(app,'off',environ={**r.env,'MINIMOI_MC_MANAGED_REALTIME':'0'},budget=r.ledger,provider_factory=lambda:pytest.fail('provider'))
    assert app.test_client().post(PATH,json=BODY).status_code==404


def test_archive_after_start_refuses_before_note_or_native(ready):
    r=ready;s,_=start(r);r.convs.archived=True
    with pytest.raises(SessionRefused):s.dispatch('consult_cos_agent','Actual owner words','c-123456abcdef','later-operation')
    assert not r.calls and not r.notes.rows

def test_session_answer_is_never_cacheable(ready):
    r=ready;response=r.client.post(PATH,json=BODY,headers=r.headers)
    assert response.status_code==201 and response.headers['Cache-Control']=='no-store'


def test_release_readiness_gate_cannot_be_bypassed(ready,monkeypatch):
    from minimoi_portal.guild_ui import voice_release
    monkeypatch.setattr(voice_release,'SHARED_VOICE_READY',False)
    r=ready;app=Flask('release-off');app.extensions['off']=r.config
    assert not host.install(app,'off',environ=r.env,budget=r.ledger,provider_factory=lambda:pytest.fail('provider'))
    assert app.test_client().post(PATH,json=BODY).status_code==404

def test_canonical_hook_uses_actual_job_command_once_before_response(ready,monkeypatch):
    import importlib.util,threading
    from minimoi_portal.guild_ui import jobs_api
    r=ready;started=[];sent=[]
    manager=SimpleNamespace(store=SimpleNamespace(list=lambda cid:[]),wake=threading.Event(),
        view=lambda job:{'id':job['id'],'message':'Job accepted'})
    def begin(conv,principal,note,note_id,attachments):
        started.append((conv,principal,note,note_id));return {'id':'synthetic-job'},None
    manager.start=begin;r.config['jobs']=manager
    spec=importlib.util.spec_from_file_location('minimoi_portal.guild_ui.synthetic_actual_jobs_api',jobs_api.__file__)
    actual=importlib.util.module_from_spec(spec);spec.loader.exec_module(actual)
    monkeypatch.setattr(jobs_api,'job_command',actual.job_command)
    r.provider.send=lambda event:sent.append(event)
    s,_=start(r);s.observe({'type':'input_audio_buffer.committed','item_id':'spoken-job'})
    assert not sent and not r.notes.rows
    s.observe({'type':'conversation.item.input_audio_transcription.completed','item_id':'spoken-job',
        'transcript':'run this as a job','usage':{'type':'tokens','input_tokens':1,'output_tokens':1,'input_token_details':{'text_tokens':0,'audio_tokens':1}}})
    s.worker.submit(lambda:None).result(timeout=2)
    assert len(started)==1 and len(r.notes.rows)==2 and not r.calls
    assert sum(n['author_kind']=='owner' for n in r.notes.rows.values())==1
    assert sum(n['who'].startswith('managed_voice_transcript:') for n in r.notes.rows.values())==1
    assert 'platform_command_receipt' in sent[0]['item']['content'][0]['text']
    s.observe({'type':'conversation.item.input_audio_transcription.completed','item_id':'spoken-job',
        'transcript':'run this as a job','usage':{'type':'tokens','input_tokens':1,'output_tokens':1,'input_token_details':{'text_tokens':0,'audio_tokens':1}}})
    s.worker.submit(lambda:None).result(timeout=2)
    assert len(started)==1

def test_stop_without_active_job_is_ordinary_no_canonical_write(ready):
    r=ready;r.config['jobs']=SimpleNamespace(store=SimpleNamespace(list=lambda cid:[]))
    s,_=start(r)
    assert s.canonical_command_match('stop','c-123456abcdef') is False
    assert s.canonical_command_match('please describe a job','c-123456abcdef') is False
    assert not r.notes.rows and not r.calls

def test_canonical_spoken_board_command_and_tool_replay_make_one_postit(ready):
    r=ready;s,_=start(r)
    s.observe({'type':'input_audio_buffer.committed','item_id':'board-spoken-1'})
    s.observe({'type':'conversation.item.input_audio_transcription.completed','item_id':'board-spoken-1','transcript':'Make a post-it: Retest tomorrow',
               'usage':{'type':'tokens','input_tokens':1,'output_tokens':1,'input_token_details':{'text_tokens':0,'audio_tokens':1}}})
    s.worker.submit(lambda:None).result(timeout=2)
    assert len(r.notes.postits)==1 and not r.calls
    assert next(iter(r.notes.postits.values()))['text']=='Retest tomorrow'
    s.observe({'type':'response.created','response':{'id':'board-response'}})
    usage={'input_tokens':2,'output_tokens':2,'input_token_details':{'text_tokens':1,'audio_tokens':1,'image_tokens':0},'output_token_details':{'text_tokens':1,'audio_tokens':1}}
    s.observe({'type':'response.done','response':{'id':'board-response','usage':usage,'output':[{'type':'function_call','call_id':'board-call-replay','name':'consult_cos_agent'}]}})
    s.worker.submit(lambda:None).result(timeout=2)
    assert len(r.notes.postits)==1 and not r.calls
