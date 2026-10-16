import json
from types import SimpleNamespace
from uuid import uuid4
import pytest
from flask import Flask
from minimoi_portal import records_machine_bridge as B
from services.rooms_worker.transport_profile import from_env,validate_endpoint,PRODUCTION_ENDPOINT
PROD={'MINIMOI_ROLE':'production','MINIMOI_RECORDS_PROFILE':'production','MINIMOI_RECORDS_PRODUCTION':'1','MINIMOI_RECORDS_MACHINE_BRIDGE':'1','RECORDS_BACKEND':'http://minimoi-records:18880'}
BASE='https://minimoi.ai';TOKEN='x'*48;ROOM='11111111-1111-4111-8111-111111111111';TURN='22222222-2222-4222-8222-222222222222'
class Response:
 def __init__(self,status=200,body=b'{"ok":true}',ctype='application/json'):
  self.status_code=status;self.body=body;self.headers={'Content-Type':ctype,'Set-Cookie':'private','Location':'https://elsewhere.invalid'};self.closed=False
 def __enter__(self):return self
 def __exit__(self,*args):self.closed=True
 def iter_content(self,n):
  for i in range(0,len(self.body),n):yield self.body[i:i+n]
class Transport:
 def __init__(self,response=None):self.calls=[];self.response=response or Response()
 def request(self,*args,**kwargs):self.calls.append((args,kwargs));return self.response
 def close(self):pass

def setup(env=None,base=BASE,transport=None):
 app=Flask(__name__);t=transport or Transport();installed=B.install_configured(app,base_url=base,environ=PROD if env is None else env,transport=t);return app.test_client(use_cookies=False),t,installed

def send(client,path='/api/v1/turns/claim',method='POST',headers=None,body=b'{"addressees":["mc"]}'):
 h={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json','Idempotency-Key':'operation-1',**(headers or{})};return client.open(B.PREFIX+path,base_url=BASE,method=method,headers=h,data=body if method=='POST'else None)

@pytest.mark.parametrize('missing',list(PROD))
def test_every_production_gate_required(missing):
 env={**PROD};env.pop(missing);client,t,installed=setup(env);assert not installed;assert send(client).status_code==404;assert not t.calls
@pytest.mark.parametrize('origin',['https://dev.minimoi.ai','http://minimoi.ai','https://minimoi.ai.evil.invalid','https://minimoi.ai/'])
def test_other_profiles_or_origins_never_install(origin):assert not setup(base=origin)[2]

def test_exact_credential_and_operation_forwarded_no_assertion_or_cookie():
 c,t,_=setup();r=send(c,headers={'X-Forwarded-Host':'evil.invalid','X-Minimoi-Username':'robert'});assert r.status_code==200
 args,kw=t.calls[0];assert args==('POST','http://minimoi-records:18880/api/v1/turns/claim');assert kw['headers']=={'Authorization':'Bearer '+TOKEN,'Accept':'application/json','Content-Type':'application/json','Idempotency-Key':'operation-1'};assert kw['data']==b'{"addressees":["mc"]}';assert kw['allow_redirects']is False and kw['stream']is True;assert t.response.closed;assert not r.headers.get('Set-Cookie')and not r.headers.get('Location')
@pytest.mark.parametrize('header,value',[('Cookie','portal=owner'),('Origin',BASE),('X-Portal-Identity','valid-looking-signed-owner')])
def test_browser_and_assertion_channels_denied_even_with_bearer(header,value):
 c,t,_=setup();assert send(c,headers={header:value}).status_code==403;assert not t.calls
@pytest.mark.parametrize('auth',['','Bearer short','Basic owner','Bearer '+('x'*300)])
def test_machine_credential_required(auth):
 c,t,_=setup();assert send(c,headers={'Authorization':auth}).status_code==401;assert not t.calls
@pytest.mark.parametrize('path,method',[
 ('/api/login','POST'),('/api/v1/approved-agents','POST'),('/api/v1/agent-install/pending','GET'),('/api/v1/principals','POST'),('/api/v1/rooms','POST'),('/api/v1/rooms/'+ROOM+'/archive','POST'),('/api/v1/turns/'+TURN+'/stop','POST'),('/api/v1/turns/claim?host=evil','POST'),('/api/v1/operations/op','GET'),('/api/v1/documents/'+ROOM+'?destination=other','GET'),('/api/v1/hosted-teammates/owner/reachable','POST'),('/api/v1/turns/../claim','POST')])
def test_unneeded_or_privileged_routes_closed(path,method):
 c,t,_=setup();assert send(c,path,method).status_code in(403,404);assert not t.calls
@pytest.mark.parametrize('path,method',[
 ('/api/v1/hosted-teammates','GET'),('/api/v1/hosted-teammates/claude-code/reachable','POST'),('/api/v1/turns/'+TURN+'/start','POST'),('/api/v1/turns/'+TURN+'/heartbeat','POST'),('/api/v1/turns/'+TURN+'/fail','POST'),('/api/v1/turns/'+TURN+'/cancel-ack','POST'),('/api/v1/turns/'+TURN+'/recover','POST'),('/api/v1/turns/'+TURN+'/reconciled','POST'),('/api/v1/rooms/'+ROOM+'/rsvp','POST'),('/api/v1/rooms/'+ROOM+'/events','POST'),('/api/v1/documents/'+ROOM,'GET'),('/api/v1/operations/reply:1?destination='+ROOM,'GET')])
def test_existing_worker_route_closure(path,method):
 c,t,_=setup();assert send(c,path,method).status_code==200;assert len(t.calls)==1
@pytest.mark.parametrize('upstream',[
 Response(302),Response(200,b'html','text/html'),Response(200,b'x'*(B.MAX_RESPONSE+1))])
def test_redirect_bad_json_type_and_oversize_response_fail_closed(upstream,monkeypatch):
 monkeypatch.setattr(B,"MAX_RESPONSE",1024)
 c,t,_=setup(transport=Transport(upstream));assert send(c).status_code==502;assert upstream.closed

def test_document_bytes_remain_exact_and_forced_download():
 data=b'%PDF synthetic\x00\xff';c,t,_=setup(transport=Transport(Response(200,data,'application/pdf')));r=send(c,'/api/v1/documents/'+ROOM,'GET');assert r.data==data;assert r.headers['Content-Disposition']=='attachment'
@pytest.mark.parametrize('body,headers,status',[(b'[]',{},400),(b'invalid',{},400),(b'{}',{'Idempotency-Key':''},400),(b'x'*(B.MAX_BODY+1),{},413),(b'{}',{'Content-Type':'text/plain'},415)], ids=['json-not-object','invalid-json','missing-idempotency','request-too-large','wrong-content-type'])
def test_bounded_write_contract(body,headers,status):
 c,t,_=setup();assert send(c,body=body,headers=headers).status_code==status;assert not t.calls

def test_signed_assertion_alone_never_becomes_machine_authority():
 from minimoi_portal import rooms_identity as RI
 signed=RI.sign(('synthetic-prod','p'*40),principal='robert',method='POST',target='/api/v1/turns/claim',body=b'{}',env='production')
 c,t,_=setup();assert send(c,headers={'Authorization':'','X-Portal-Identity':signed},body=b'{}').status_code==403;assert not t.calls

@pytest.mark.parametrize('url',['http://minimoi.ai/internal/records','https://minimoi.ai/internal/records/','https://minimoi.ai.evil.invalid/internal/records','https://user:password@minimoi.ai/internal/records','https://dev.minimoi.ai/internal/records','https://minimoi.ai/internal/records?key=secret','https://minimoi.ai/internal/records#fragment','http://127.0.0.1:18881'])
def test_production_laptop_destination_is_exact(url):
 with pytest.raises(ValueError):validate_endpoint(url,'production')
def test_explicit_laptop_profile_separate_from_dev_default():
 assert from_env({'ROOMS_RECORDS_PROFILE':'production','ROOMS_RECORDS_PRODUCTION':'1','RECORDS_URL':PRODUCTION_ENDPOINT},'http://127.0.0.1:18881')==(PRODUCTION_ENDPOINT,'production')
 with pytest.raises(ValueError):from_env({'ROOMS_RECORDS_PROFILE':'production','RECORDS_URL':PRODUCTION_ENDPOINT},'http://127.0.0.1:18881')
 with pytest.raises(ValueError):validate_endpoint(PRODUCTION_ENDPOINT,'dev')

@pytest.fixture
def real_machine(tmp_path):
 import importlib.util,sys
 from urllib.parse import urlsplit
 from rooms_helpers import records_app,RECORDS_DIR
 app=records_app(tmp_path/'production-records-fixture');store=app.extensions['records_store'];spec=importlib.util.spec_from_file_location('records_machine_manage_fixture',RECORDS_DIR/'manage.py');manage=importlib.util.module_from_spec(spec);spec.loader.exec_module(manage)
 out=tmp_path/'keys';manage.provision_rooms(store,'mc','MC',out)
 tokens={n:(out/f'{n}.token').read_text().strip()for n in ('mc','rooms-worker')}
 class RealTransport:
  def __init__(self):self.calls=[];self.client=app.test_client(use_cookies=False)
  def request(self,method,url,**kwargs):
   self.calls.append((method,url,kwargs));p=urlsplit(url);response=self.client.open(p.path+('?' +p.query if p.query else''),base_url=p.scheme+'://'+p.netloc,method=method,headers=kwargs['headers'],data=kwargs['data']);return Response(response.status_code,response.data,response.content_type)
 transport=RealTransport();client,_,_=setup(transport=transport);return SimpleNamespace(client=client,transport=transport,store=store,tokens=tokens,app=app,manage=manage)

def test_real_records_existing_work_token_claim_no_owner_assertion(real_machine):
 e=real_machine;r=send(e.client,headers={'Authorization':'Bearer '+e.tokens['rooms-worker']});assert r.status_code==200,r.data;assert not any('X-Portal-Identity'in call[2]['headers']for call in e.transport.calls)

def test_real_records_membership_token_cannot_claim_work(real_machine):
 e=real_machine;assert send(e.client,headers={'Authorization':'Bearer '+e.tokens['mc']}).status_code==403

def test_real_records_missing_membership_cannot_fetch_file(real_machine):
 e=real_machine;r=send(e.client,'/api/v1/documents/'+ROOM,'GET',headers={'Authorization':'Bearer '+e.tokens['mc']});assert r.status_code in(403,404)

def test_separate_environment_store_does_not_accept_other_store_work_token(real_machine,tmp_path):
 from rooms_helpers import records_app
 e=real_machine;other=records_app(tmp_path/'dev-records-fixture');out=tmp_path/'devkeys';e.manage.provision_rooms(other.extensions['records_store'],'mc','MC',out);devtoken=(out/'rooms-worker.token').read_text().strip();assert send(e.client,headers={'Authorization':'Bearer '+devtoken}).status_code==401


def test_real_records_revoked_key_stops_next_request(real_machine):
 e=real_machine;auth=e.store.platform_access.authenticate(token=e.tokens['rooms-worker']);e.store.platform_access.revoke('robert',auth['credential_id']);assert send(e.client,headers={'Authorization':'Bearer '+e.tokens['rooms-worker']}).status_code==401

def test_dribbling_response_is_observed_each_byte_and_deadline_refused():
 class Dribble(Response):
  def iter_content(self,n):
   assert n==1
   yield b'{'
   yield b'}'
 ticks=iter([0,1,11]);app=Flask(__name__);transport=Transport(Dribble());B.install_configured(app,base_url=BASE,environ=PROD,transport=transport,clock=lambda:next(ticks));client=app.test_client(use_cookies=False);assert send(client).status_code==502


@pytest.mark.parametrize('kind', ['connector','worker'])
def test_production_runtime_requires_explicit_nondev_locators(kind):
 from services.rooms_worker.transport_profile import admit_runtime
 env={'ROOMS_RECORDS_PROFILE':'production','ROOMS_RECORDS_PRODUCTION':'1','RECORDS_URL':'https://minimoi.ai/internal/records'}
 with pytest.raises(ValueError):admit_runtime(env,kind=kind)
 if kind=='connector':env.update(ROOMS_CONNECTOR_SECRETS='/private/prod/secrets',ROOMS_CONNECTOR_STATE='/private/prod/state')
 else:env.update(ROOMS_SECRETS_DIR='/private/prod/secrets',ROOMS_JOURNAL_DIR='/private/prod/journal',MC_RELAY_TOKEN_FILE='/private/prod/relay.token')
 assert admit_runtime(env,kind=kind)==('https://minimoi.ai/internal/records','production')
 key='ROOMS_CONNECTOR_STATE' if kind=='connector' else 'ROOMS_JOURNAL_DIR'
 for bad in ('/Users/example/minimoi-staging/data/managed','/journal','relative/path','/prod/../journal','/state/production','/journal/prod','/run/secrets/rooms/subdir'):
  changed=dict(env);changed[key]=bad
  with pytest.raises(ValueError):admit_runtime(changed,kind=kind)

def test_connector_production_refusal_precedes_runner_and_state(monkeypatch,tmp_path):
 from services.rooms_connector import connector
 env={'ROOMS_RECORDS_PROFILE':'production','ROOMS_RECORDS_PRODUCTION':'1','RECORDS_URL':'https://minimoi.ai/internal/records'}
 monkeypatch.setattr(connector,'ClaudeRunner',lambda **kw:pytest.fail('runner initialized'))
 monkeypatch.setattr(connector,'read_secret',lambda *a:pytest.fail('secret read'))
 monkeypatch.setattr(connector.Path,'mkdir',lambda *a,**kw:pytest.fail('state created'))
 with pytest.raises(ValueError):connector.build(env)
 monkeypatch.setattr(connector.os,'environ',dict(env,ROOMS_MANAGED_LEDGER=str(tmp_path/'managed')))
 with pytest.raises(ValueError):connector.main()

def test_worker_production_refusal_precedes_journal_and_secret(monkeypatch):
 from services.rooms_worker import worker
 env={'ROOMS_RECORDS_PROFILE':'production','ROOMS_RECORDS_PRODUCTION':'1','RECORDS_URL':'https://minimoi.ai/internal/records'}
 monkeypatch.setattr(worker.os,'environ',env)
 monkeypatch.setattr(worker.Path,'mkdir',lambda *a,**kw:pytest.fail('journal created'))
 monkeypatch.setattr(worker,'read_secret',lambda *a:pytest.fail('secret read'))
 with pytest.raises(ValueError):worker.main()


def test_production_locator_normalized_overlap_rejected():
 from services.rooms_worker.transport_profile import admit_runtime
 env={'ROOMS_RECORDS_PROFILE':'production','ROOMS_RECORDS_PRODUCTION':'1','RECORDS_URL':'https://minimoi.ai/internal/records','ROOMS_CONNECTOR_SECRETS':'/private/prod/state','ROOMS_CONNECTOR_STATE':'/private/prod/./state'}
 with pytest.raises(ValueError,match='overlap'):admit_runtime(env,kind='connector')
