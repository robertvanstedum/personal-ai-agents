"""Real host hook and signed receiver; isolated synthetic state, no app bootstrap/network."""
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import pytest
from flask import Flask,g
from core import library_intake_transport as protocol
from domains.curator import preferences_store
from domains.curator.library_intake_http import install_configured
from minimoi_portal import guild_ui
from minimoi_portal.guild_ui import api,mc_library

ENV={'MINIMOI_LIBRARY_INTAKE':'1','MINIMOI_LIBRARY_INTAKE_ENV':'dev','MINIMOI_LIBRARY_INTAKE_OWNER':'robert','PORTAL_SECRET_KEY':'synthetic-only-0123456789abcdef0123456789'}

@pytest.fixture
def hook(tmp_path,monkeypatch):
    monkeypatch.setattr(preferences_store,'writer_domain',lambda:'Linux:11111111-1111-1111-1111-111111111111')
    path=tmp_path/'curator_preferences.json';receiver=Flask('synthetic-receiver')
    install_configured(receiver,environ=ENV,preferences_path=path,history_path=tmp_path/'curator_history.json',clock=lambda:100)
    client=receiver.test_client();sent=[];lookups=[];notes={};fresh={'id':'c-one','principal':'robert','archived':False};fail_chat=[False]
    class Reply:
        def __init__(self,result):self.status_code=result.status_code;self.headers=result.headers;self.raw=result.data
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,chunk_size):return(self.raw[i:i+1]for i in range(len(self.raw)))
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kw):
            assert url=='http://curator:8766/internal/library-intake'and self.trust_env is False and not kw['allow_redirects']
            sent.append(json.loads(kw['data']))
            return Reply(client.post(protocol.PATH,data=kw['data'],headers=kw['headers'],base_url='http://curator:8766'))
    actual=mc_library.host_command
    def host(*args,**kw):
        return actual(*args,**kw,sender=lambda profile,value:protocol.send(profile,value,session_factory=Session,clock=lambda:100))
    monkeypatch.setattr(mc_library,'host_command',host)
    monkeypatch.setattr(guild_ui,'feature_environ',lambda:ENV)
    current_principal=['robert']
    monkeypatch.setattr(api,'cfg',lambda:{'base_url':'https://dev.minimoi.ai','services':SimpleNamespace(mc_turns=True),'current_user':lambda:{'username':current_principal[0]}})
    def get(conversation,principal):lookups.append((conversation,principal));return dict(fresh,id=conversation)
    monkeypatch.setattr(api,'_conversations',lambda:SimpleNamespace(get=get))
    def add_note(key,text,author,**kw):
        if fail_chat[0]:raise OSError('synthetic receipt failure')
        value=notes.setdefault(key,{'id':key,'text':text})
        assert value['text']==text
        return SimpleNamespace(outcome='kept',value=value)
    floor=SimpleNamespace(add_note=add_note)
    kept_note={}
    floor.get_note=lambda note_id:dict(kept_note)
    monkeypatch.setattr(api,'_conv_floor',lambda conv:floor)
    app=Flask('synthetic-portal');app.secret_key='synthetic-test-session-secret'
    @app.post('/synthetic-host')
    def endpoint():
        refusal,found=api._mc_prelude()
        if refusal is not None:return refusal
        result=api._owner_library_command(found)
        return ('',204)if result is None else result
    portal=app.test_client()
    with portal.session_transaction()as session:session['guild_floor_csrf']='synthetic-csrf'
    def invoke(text='https://example.com/article\nFirst owner reason',conversation='c-one',attachments=None,principal='robert',record_mode='on_record',csrf=True):
        current_principal[0]=principal
        kept_note.update(text=text,who=principal,author_kind='owner')
        headers={'X-Record-Mode':record_mode,'Origin':'https://dev.minimoi.ai','Sec-Fetch-Site':'same-origin'}
        if csrf:headers['X-CSRF-Token']='synthetic-csrf'
        response=portal.post('/synthetic-host',json={'conversation_id':conversation,'note_request_id':'n-same-kept','request_id':'request-synthetic','attachment_ids':attachments or []},headers=headers)
        return None if response.status_code==204 else response.get_json()
    return SimpleNamespace(path=path,sent=sent,lookups=lookups,notes=notes,fresh=fresh,fail_chat=fail_chat,invoke=invoke)


def test_real_hook_signed_save_replay_and_conversation_bound_operation(hook):
    first=hook.invoke()
    assert first['operation']['saved']and first['history_saved']
    before=hook.path.read_bytes();again=hook.invoke()
    assert again['operation']['receipt']['repeated']and hook.path.read_bytes()==before
    second=hook.invoke('https://example.com/article\nDifferent owner reason',conversation='c-two')
    assert second['operation']['saved']and second['operation']['receipt']['revision']==2
    data=json.loads(hook.path.read_bytes());assert len(data['owner_library_operations'])==2
    row=next(iter(data['feedback_history'].values()))['saved'][0]
    assert [v['text']for v in row['owner_intake']['comments']]==['First owner reason','Different owner reason']
    assert len(hook.notes)==2 and hook.sent[0]['operation_id']!=hook.sent[-1]['operation_id']
    assert sum(len(v.get('saved',[]))for v in data['feedback_history'].values())==1


def test_private_foreign_and_attachment_requests_never_send(hook):
    assert hook.invoke(record_mode='off_record')['error']=='not_listening'
    assert hook.invoke(csrf=False)['error']=='csrf'
    hook.fresh['principal']='someone';assert not hook.invoke()['operation']['saved']
    hook.fresh['principal']='robert';assert not hook.invoke(principal='test-agent-full')['operation']['saved']
    assert hook.invoke(attachments=['d-'+('a'*16)])is None
    assert not hook.sent and not hook.path.exists()


def test_saved_library_chat_receipt_failure_uses_actual_js_fallback(hook):
    hook.fail_chat[0]=True;response=hook.invoke()
    assert response['operation']['saved']and response['reply_note']is None and response['history_saved']is False
    assert hook.path.exists()and len(json.loads(hook.path.read_bytes())['owner_library_operations'])==1
    node=shutil.which('node')
    if not node:pytest.skip('Node required for actual browser fallback function')
    root=Path(api.__file__).parent;raw=(root/'static/js/conversation.js').read_text()
    start=raw.index('function showAnswer(');function=raw[start:raw.index('\n}',start)+2]
    script='''const fs=require('fs'),vm=require('vm'),assert=require('assert');const input=JSON.parse(fs.readFileSync(0,'utf8'));let shown=null,arrived=0;
const c={applyHeader(){},platformLine:(who,text)=>({who,text}),announce(){},answerArrived(){arrived++},follow(){},failureLine(){throw Error('must not present a committed Library save as failure')},answerFailed(){throw Error('must not fail successful Library save')}};
vm.createContext(c);vm.runInContext(input.function,c);const outcome=c.showAnswer({replaceWith(v){shown=v}},{ok:true,body:input.response},{queued:false});
assert.equal(outcome.answered,true);assert.equal(outcome.error,null);assert.equal(arrived,1);assert.equal(shown.who,'Library');assert(shown.text.includes('item is saved'));assert(shown.text.includes('could not be kept in the conversation'));
'''
    result=subprocess.run([node,'-e',script],input=json.dumps({'function':function,'response':response}),text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_ordinary_chat_does_not_lookup_library_conversation(hook):
    assert hook.invoke('What changed today?')is None
    # Only the original owner-bound prelude lookup; no second Library lookup.
    assert len(hook.lookups)==1 and hook.sent==[]and not hook.path.exists()
