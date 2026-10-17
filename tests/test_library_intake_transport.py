import json
import pytest
from flask import Flask
from core import library_intake_transport as p
from domains.curator.library_intake_http import install_configured
from minimoi_portal.guild_ui.mc_library import host_command,parse

ENV={'MINIMOI_LIBRARY_INTAKE':'1','MINIMOI_LIBRARY_INTAKE_ENV':'dev','MINIMOI_LIBRARY_INTAKE_OWNER':'robert','PORTAL_SECRET_KEY':'synthetic-secret-0123456789abcdef0123456789'}
NONCE='a'*32

def body(profile=None):return p.payload(profile or p.configured(ENV),'c-synthetic','n-synthetic','https://example.com/article','Why I kept it')
def request(client,raw,headers,base='http://curator:8766'):
    return client.post(p.PATH,data=raw,headers=headers,base_url=base)

@pytest.fixture
def receiver(tmp_path):
    calls=[];app=Flask(__name__)
    class Fake:
        def __init__(self,*args,**kw):calls.append(('constructor',args,kw))
        def capture(self,**kw):calls.append(('capture',kw));return {'result':'saved','source_content':'not_fetched','path':'/synthetic/curator_preferences.json','article_id':'owner-synthetic'}
    install_configured(app,environ=ENV,preferences_path=tmp_path/'curator_preferences.json',history_path=tmp_path/'curator_history.json',intake_factory=Fake,clock=lambda:100)
    return app.test_client(),calls

def test_disabled_factory_has_no_route_or_state_io(tmp_path):
    app=Flask(__name__);assert not install_configured(app,environ={},preferences_path=tmp_path/'absent',history_path=tmp_path/'missing')
    assert not list(tmp_path.iterdir())and app.test_client().post(p.PATH).status_code==404

@pytest.mark.parametrize('key,value',[('PORTAL_SECRET_KEY','short'),('MINIMOI_LIBRARY_INTAKE_ENV','other'),('MINIMOI_LIBRARY_INTAKE_OWNER',''),('MINIMOI_LIBRARY_INTAKE_OWNER','owner'),('MINIMOI_LIBRARY_INTAKE_OWNER','test-agent-full')])
def test_enabled_invalid_config_refuses(key,value):
    with pytest.raises(ValueError):p.configured({**ENV,key:value})

def test_signed_receiver_scope_and_authenticated_receipt(receiver):
    client,calls=receiver;profile=p.configured(ENV);raw=p.packed(body());h=p.headers(profile,raw,now=100,nonce=NONCE)
    result=request(client,raw,h)
    assert result.status_code==200 and len(calls)==2
    assert calls[-1][1]['principal']=='robert'and calls[-1][1]['on_record']is True
    assert result.headers['X-MiniMoi-Receipt']==p.response_signature(profile,NONCE,result.data)
    assert request(client,raw,h).status_code==403 and len(calls)==2

@pytest.mark.parametrize('change',['missing','tamper','expired','future','wrong_key','scope','duplicate','wrong_host','query'])
def test_invalid_auth_never_constructs_intake(receiver,change):
    client,calls=receiver;profile=p.configured(ENV);value=body();raw=p.packed(value);h=p.headers(profile,raw,now=100,nonce=NONCE);base='http://curator:8766';url=p.PATH
    if change=='missing':h={'X-User':'owner','Cookie':'session=anything','Content-Type':'application/json'}
    elif change=='tamper':raw=raw.replace(b'article',b'changed')
    elif change=='expired':h=p.headers(profile,raw,now=69,nonce=NONCE)
    elif change=='future':h=p.headers(profile,raw,now=103,nonce=NONCE)
    elif change=='wrong_key':h=p.headers(p.configured({**ENV,'PORTAL_SECRET_KEY':'x'*40}),raw,now=100,nonce=NONCE)
    elif change=='scope':value['environment']='production';raw=p.packed(value);h=p.headers(profile,raw,now=100,nonce=NONCE)
    elif change=='duplicate':raw=raw[:-1]+b',"url":"https://other.invalid/"}';h=p.headers(profile,raw,now=100,nonce=NONCE)
    elif change=='wrong_host':base='http://other:8766'
    elif change=='query':url+='?url=https://other.invalid/'
    result=client.post(url,data=raw,headers=h,base_url=base)
    assert result.status_code in {403,404}and calls==[]


def test_transport_is_fixed_no_proxy_no_redirect_and_receipt_bound(receiver):
    client,calls=receiver;profile=p.configured(ENV);seen=[]
    class Reply:
        def __init__(self,r):self.status_code=r.status_code;self.headers=r.headers;self.data=r.data
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def iter_content(self,chunk_size):assert chunk_size==1;return (self.data[i:i+1]for i in range(len(self.data)))
    class Session:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def post(self,url,**kw):
            assert self.trust_env is False and url=='http://curator:8766/internal/library-intake'
            assert kw['allow_redirects']is False and kw['timeout']==(1,3)and kw['stream']is True
            assert set(kw['headers'])=={'Content-Type','X-MiniMoi-Time','X-MiniMoi-Nonce','X-MiniMoi-Signature'}
            seen.append(url);return Reply(request(client,kw['data'],kw['headers']))
    receipt=p.send(profile,body(),session_factory=Session,clock=lambda:100)
    assert receipt['result']=='saved'and len(seen)==1

@pytest.mark.parametrize('failure',['redirect','unsigned','timeout','oversized'])
def test_transport_no_retry_or_false_success(failure):
    class Reply:
        status_code=302 if failure=='redirect'else 200
        headers={}
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def iter_content(self,chunk_size):return [b'x'*(p.MAX_REPLY+1)]if failure=='oversized'else[b'{}']
    calls=[]
    class Session:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def post(self,*a,**kw):
            calls.append(1)
            if failure=='timeout':raise TimeoutError('synthetic no secret output')
            return Reply()
    with pytest.raises(p.IntakeTransportUnconfirmed):p.send(p.configured(ENV),body(),session_factory=Session,clock=lambda:100)
    assert len(calls)==1

@pytest.mark.parametrize('text',['https://example.com/','Save this to Library: https://example.com/\nMy own reason','Please save this to Library: https://example.com/'])
def test_literal_and_bare_url_path_passes_owner_text(text):
    calls=[]
    def sender(profile,value):calls.append(value);return {'result':'saved','source_content':'not_fetched'}
    r=host_command(text,environ=ENV,principal='robert',origin='https://dev.minimoi.ai',conversation={'id':'c-fixture','principal':'robert','record_mode':'on_record'},note_id='n-fixture',sender=sender)
    assert r['operation']['saved']is True and len(calls)==1 and calls[0]['operation_id']==p.operation('robert','c-fixture','n-fixture')
    assert set(calls[0])==set(body())and 'tags'not in calls[0]

@pytest.mark.parametrize('text',["Example: Save this to Library: https://example.com/",'> Save this to Library: https://example.com/','Tell me about https://example.com/'])
def test_quoted_or_other_intent_is_not_intercepted(text):assert parse(text)is None

@pytest.mark.parametrize('change',['private','wrong_owner','archived','wrong_origin','multiple','disabled'])
def test_host_refusal_never_sends(change):
    text='https://example.com/';env=dict(ENV);conv={'id':'c-fixture','principal':'robert','record_mode':'on_record'};principal='robert';origin='https://dev.minimoi.ai'
    if change=='private':conv['record_mode']='off_record'
    elif change=='wrong_owner':principal='someone'
    elif change=='archived':conv['archived']=True
    elif change=='wrong_origin':origin='https://minimoi.ai'
    elif change=='multiple':text+='\nhttps://other.invalid/'
    elif change=='disabled':env={}
    def sender(*a):raise AssertionError('must not dispatch')
    r=host_command(text,environ=env,principal=principal,origin=origin,conversation=conv,note_id='n-fixture',sender=sender)
    assert r['operation']['saved']is False


def test_signed_real_intake_persists_and_fresh_nonce_replay_is_durable(tmp_path,monkeypatch):
    from domains.curator import preferences_store
    monkeypatch.setattr(preferences_store,'writer_domain',lambda:'Linux:11111111-1111-1111-1111-111111111111')
    path=tmp_path/'curator_preferences.json';app=Flask(__name__)
    install_configured(app,environ=ENV,preferences_path=path,history_path=tmp_path/'curator_history.json',clock=lambda:100)
    client=app.test_client();profile=p.configured(ENV);raw=p.packed(body())
    response=request(client,raw,p.headers(profile,raw,now=100,nonce='1'*32))
    assert response.status_code==200 and response.json['receipt']['path']==str(path)
    before=path.read_bytes();second=request(client,raw,p.headers(profile,raw,now=100,nonce='2'*32))
    assert second.status_code==200 and second.json['receipt']['repeated']and path.read_bytes()==before
    data=json.loads(before);row=next(iter(data['feedback_history'].values()))['saved'][0]
    assert row['your_words']=='Why I kept it'and row['owner_intake']['annotations'][0]['tags']==[]
    assert len(data['owner_library_operations'])==1


def test_operation_id_and_response_signature_bound_to_original_request():
    profile=p.configured(ENV);value=body();value['operation_id']='forged'
    raw=p.packed(value);header=p.headers(profile,raw,now=100,nonce=NONCE)
    with pytest.raises(ValueError,match='operation_invalid'):p.verify(profile,raw,header,now=100)
    reply=b'{"status":"saved"}'
    assert p.response_signature(profile,'a'*32,reply)!=p.response_signature(profile,'b'*32,reply)


def test_signed_noncanonical_principal_never_maps_to_owner(receiver):
    client,calls=receiver;profile=p.configured(ENV);value=body();value['principal']='test-agent-full'
    raw=p.packed(value);h=p.headers(profile,raw,now=100,nonce='3'*32)
    assert request(client,raw,h).status_code==403 and calls==[]
