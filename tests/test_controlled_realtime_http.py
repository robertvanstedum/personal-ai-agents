import importlib
import pathlib
import sys
import types
import pytest
from flask import Flask,abort
from core.realtime_voice.budget import VoiceBudget
from test_controlled_realtime_session import Provider,CONFIG

root=pathlib.Path(__file__).parents[1]
pkg=types.ModuleType('draft_voice');pkg.__path__=[str(root/'core/realtime_voice')];sys.modules['draft_voice']=pkg
sys.modules['draft_voice.budget']=importlib.import_module('core.realtime_voice.budget')
m=importlib.import_module('draft_voice.controlled_session')
a=importlib.import_module('draft_voice.controlled_api')
p=importlib.import_module('draft_voice.server_openai')
@pytest.fixture
def http(tmp_path):
    b=VoiceBudget('sqlite:///'+str(tmp_path/'db'),cap_usd=lambda:20);b.ensure_schema();j=m.Journal(b._tx);j.init()
    app=Flask('synthetic');sessions=[];providers=[]
    def guard(req):
        if req.headers.get('X-Test-Owner')!='owner' or req.headers.get('X-Test-CSRF')!='valid':abort(403)
        return 'owner'
    def build(principal,conversation):
        if conversation!='c1':raise m.SessionRefused('Unknown conversation')
        provider=Provider();providers.append(provider)
        s=m.ControlledSession(journal=j,budget=b,provider=provider,principal=principal,conversation=conversation,allowance_micro=5_000_000,dispatch=lambda *args:{'reply':'synthetic'})
        sessions.append(s);return s
    app.register_blueprint(a.blueprint(name='test',guard=guard,build_session=build,build_config=lambda uid:CONFIG),url_prefix='/voice')
    yield app.test_client(),sessions,providers
    for s in sessions:s.end('test');s.worker.shutdown(wait=True)
HEADERS={'X-Test-Owner':'owner','X-Test-CSRF':'valid'}
BODY={'sdp':'synthetic-sdp','provider':'openai','conversation_id':'c1'}
def test_authenticated_exchange_no_secret(http):
    client,s,p=http;r=client.post('/voice/session',json=BODY,headers=HEADERS)
    assert r.status_code==201 and r.json['sdp']=='answer' and 'client_secret' not in r.json
    assert r.json['session_id']==s[0].sid
    end=client.post('/voice/session/'+s[0].sid+'/end',headers=HEADERS)
    assert end.status_code==200 and end.json['termination_confirmed'] and not end.json['native_actions_cancelled']
def test_auth_before_provider(http):
    client,s,p=http;assert client.post('/voice/session',json=BODY).status_code==403;assert not p
@pytest.mark.parametrize('extra',[{'provider':'xai'},{'conversation_id':'other'},{'tool_args':'model-owner-approval'}])
def test_invalid_contract_refused(http,extra):
    client,s,p=http;r=client.post('/voice/session',json={**BODY,**extra},headers=HEADERS)
    assert r.status_code in (400,503) and not p
def test_no_browser_tool_route(http):
    client,s,p=http;assert client.post('/voice/tool',json={'approval':True},headers=HEADERS).status_code==404

def test_vendor_adapter_exact_installed_sdk_shape():
    emitted=[];sent=[];stopped=[]
    cfg=importlib.import_module('draft_voice.controlled_config').configuration('Synthetic persona')
    class Message:
        def model_dump(self,**kw):return {'type':'session.updated','session':cfg}
    class Socket:
        def recv(self):return Message()
        def __iter__(self):return iter(())
        def send(self,e):sent.append(e)
        def close(self):pass
    class Manager:
        def __enter__(self):return Socket()
        def __exit__(self,*args):pass
    class Calls:
        def hangup(self,c,**kw):stopped.append(c)
    def post(path,**kw):
        import json,httpx
        from openai._legacy_response import HttpxBinaryResponseContent
        assert path=='/realtime/calls' and json.loads(kw['body']['session'])['model']==m.MODEL
        assert kw['options']['headers']['Content-Type']=='multipart/form-data'
        assert kw['options']['headers']['OpenAI-Safety-Identifier']=='hashed'
        return HttpxBinaryResponseContent(httpx.Response(201,headers={'location':'/v1/realtime/calls/rtc_synthetic'},text='answer'))
    client=types.SimpleNamespace(post=post,realtime=types.SimpleNamespace(calls=Calls(),connect=lambda **kw:Manager()))
    provider=p.OpenAIControlledProvider(client)
    answer,c=provider.create('offer',cfg,'hashed');assert answer=='answer'
    provider.attach(c,emitted.append);provider.send_result('call1',{'reply':'safe'},continue_response=False)
    provider.hangup(c);assert stopped==['rtc_synthetic']
    assert sent[0]=={'type':'session.update','session':{'type':'realtime'}}
    assert sent[1]['item']['call_id']=='call1'

def test_shared_configuration_retains_provider_barge_in():
    cfg=importlib.import_module('draft_voice.controlled_config').configuration('Reviewed shared-agent persona')
    vad=cfg['audio']['input']['turn_detection']
    assert vad['create_response'] is False and vad['interrupt_response'] is True
    assert vad['prefix_padding_ms']==300 and vad['silence_duration_ms']==1200
    assert cfg['tools'][0]['parameters']['properties']=={}


def test_actual_sdk_sends_multipart_unified_webrtc_request():
    import httpx,json
    from openai import OpenAI
    captured=[]
    def serve(request):
        captured.append(request)
        return httpx.Response(201,headers={'location':'/v1/realtime/calls/rtc_wire'},text='answer')
    with OpenAI(api_key='synthetic-not-a-secret',max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(serve))) as client:
        answer,call=p.OpenAIControlledProvider(client).create('synthetic-offer',CONFIG,'hashed')
    assert answer=='answer' and call=='rtc_wire' and len(captured)==1
    request=captured[0];body=request.content
    assert request.headers['content-type'].startswith('multipart/form-data; boundary=')
    assert b'name="sdp"' in body and b'synthetic-offer' in body
    assert b'name="session"' in body and json.dumps(CONFIG).encode() in body
    assert request.headers['openai-safety-identifier']=='hashed'
