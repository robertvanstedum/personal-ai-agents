"""Disposable prepared stores, real human assertions, no HTTP/network/provisioning."""
from pathlib import Path
import hashlib,importlib.util,json,sqlite3,sys
from types import SimpleNamespace
import pytest
from flask import Flask,abort
from minimoi_portal import records_bridge as B,rooms_identity as RI
from minimoi_portal.transport_profiles import DEV,PRODUCTION,select_records_profile
from services import records_runtime as RT
ROOT=Path(__file__).resolve().parents[3]
APPDIR=ROOT/'prototype-lab/projects/project-records-room-poc'
ENV={'BASE_URL':'https://minimoi.ai','MINIMOI_ROLE':'production','MINIMOI_RECORDS_PROFILE':'production','MINIMOI_RECORDS_PRODUCTION':'1','RECORDS_PORTAL_ENV':'production','RECORDS_ALLOWED_HOSTS':'minimoi-records:18880','RECORDS_BACKEND':'http://minimoi-records:18880'}
def app_module():
 if str(APPDIR)not in sys.path:sys.path.append(str(APPDIR))
 s=importlib.util.spec_from_file_location('records_production_fixture',APPDIR/'app.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
@pytest.fixture
def prepared(tmp_path,monkeypatch):
 app=app_module();data=tmp_path/'prepared';dev=app.create_app(str(data),runtime_environ={'RECORDS_PORTAL_ENV':'dev'},portal_key=('dev-key','d'*48));key=tmp_path/'portal.key';key.write_text('prod-key:'+('p'*48));key.chmod(0o600);monkeypatch.setattr(RT,'KEY_PATH',str(key));env=dict(ENV,RECORDS_PORTAL_KEY_FILE=str(key))
 with sqlite3.connect(data/'records.sqlite3')as db:schema=RT.schema_hash(db)
 marker={'format':'records-production-runtime/1','environment':'production','data_dir':str(data),'schema_sha256':schema,'owner_key_sha256':hashlib.sha256((data/'owner-key.txt').read_bytes().strip()).hexdigest(),'session_key_sha256':hashlib.sha256((data/'session-key.txt').read_bytes().strip()).hexdigest(),'portal_key_kid':'prod-key'}
 (data/RT.MANIFEST).write_text(json.dumps(marker));(data/RT.MANIFEST).chmod(0o600)
 return SimpleNamespace(module=app,data=data,dev=dev,key=key,env=env)
def rows(root):
 with sqlite3.connect(root/'records.sqlite3')as db:return {t:db.execute('SELECT * FROM '+t).fetchall() for(t,)in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
@pytest.mark.parametrize('missing',['MINIMOI_ROLE','MINIMOI_RECORDS_PROFILE','MINIMOI_RECORDS_PRODUCTION'])
def test_production_profile_requires_all_explicit_config(missing):
 env=dict(ENV);env.pop(missing);assert select_records_profile(ENV['BASE_URL'],env)is None
@pytest.mark.parametrize('url',['http://127.0.0.1:18880','http://localhost:18880','http://minimoi-records:18881','https://minimoi-records:18880','http://minimoi-records:18880/','http://u:p@minimoi-records:18880','http://minimoi-records:18880?q=x','http://minimoi-records:18880#x'])
def test_production_backend_exact(url):
 with pytest.raises(B.BackendRefused):B.validate_backend(url,profile=PRODUCTION)
def test_valid_prepared_start_creates_no_principal_key_or_row(prepared):
 p=prepared;before=rows(p.data);keys=[(p.data/n).read_bytes()for n in('owner-key.txt','session-key.txt')];result=RT.preflight(p.data,p.env);prod=p.module.create_app(str(p.data),runtime_environ=p.env)
 assert result['provisioned_here']is False and before==rows(p.data);assert keys==[(p.data/n).read_bytes()for n in('owner-key.txt','session-key.txt')]
 assert prod.test_client().get('/health',base_url=PRODUCTION.backend).status_code==200
 assert prod.test_client().get('/health',base_url='http://127.0.0.1:18880').status_code==403
 assert prod.test_client().post('/api/login',base_url=PRODUCTION.backend,json={'token':'fake'}).status_code==403
@pytest.mark.parametrize('remove',['records.sqlite3','owner-key.txt','session-key.txt',RT.MANIFEST])
def test_missing_prepared_state_refuses_before_store_never_bootstraps(prepared,monkeypatch,remove):
 p=prepared;(p.data/remove).unlink();monkeypatch.setattr(p.module,'Store',lambda *a:pytest.fail('Store must not run'))
 with pytest.raises((ValueError,OSError)):p.module.create_app(str(p.data),runtime_environ=p.env)
 assert not(p.data/remove).exists()
def test_corrupt_schema_and_wrong_owner_refuse_readonly(prepared):
 p=prepared
 with sqlite3.connect(p.data/'records.sqlite3')as db:db.execute("UPDATE principals SET token_hash='wrong' WHERE id='robert'")
 before=rows(p.data)
 with pytest.raises(ValueError,match='prepared_owner'):RT.preflight(p.data,p.env)
 assert rows(p.data)==before
@pytest.mark.parametrize('origin',['https://dev.minimoi.ai','http://minimoi.ai','https://minimoi.ai/','https://www.minimoi.ai'])
def test_production_origin_no_dev_or_reflected_fallback(origin):assert select_records_profile(origin,ENV)is None
class Transport:
 def __init__(self,records):self.client=records.test_client(use_cookies=False);self.calls=[]
 def request(self,method,url,headers,data,**kw):
  from urllib.parse import urlsplit
  self.calls.append((method,url,headers));u=urlsplit(url);r=self.client.open(u.path+('?' +u.query if u.query else ''),method=method,base_url=u.scheme+'://'+u.netloc,headers=headers,data=data)
  return SimpleNamespace(status_code=r.status_code,content=r.data,headers=r.headers,json=lambda:r.get_json())
def bridge(p,records,profile,key=None):
 a=Flask('bridge-'+profile.name);a.secret_key='synthetic';state={'active':True,'account':True};transport=Transport(records)
 class Access:
  def decide(self,*args,account_active=True):return RI.Decision('ok','robert')if state['active']and account_active else RI.Decision('no_access')
 def guard(f):
  def wrapped(*args,**kw):
   from flask import request
   if request.headers.get('X-Test-Login')!='owner':abort(403)
   return f(*args,**kw)
  return wrapped
 B.install(a,guard,lambda f:f,backend=PRODUCTION.backend,profile=profile,transport=transport,identity={'access':Access(),'key_file':str(key or p.key),'current_user':lambda:{'username':'robert'},'account_active':lambda u:state['account']})
 return a,a.test_client(),state,transport
def test_dev_prod_same_process_assertion_env_and_key_replay_boundaries(prepared):
 p=prepared;prod=p.module.create_app(str(p.data),runtime_environ=p.env);pa,pc,state,t=bridge(p,prod,PRODUCTION)
 good=pc.get('/app/records/api/v1/me',base_url=PRODUCTION.origin,headers={'X-Test-Login':'owner','Authorization':'Bearer fake','X-Portal-Identity':'fake','Cookie':'fake'})
 assert good.status_code==200 and good.headers['X-Records-Environment']=='production'
 method,url,headers=t.calls[-1];assert 'Cookie'not in headers and 'Authorization'not in headers and headers['X-Portal-Identity']!='fake'
 assert prod.test_client().get('/api/v1/me',base_url=PRODUCTION.backend,headers=headers).status_code==401 # nonce replay
 da,dc,_,_=bridge(p,prod,DEV);assert dc.get('/app/records/api/v1/me',base_url=DEV.origin,headers={'X-Test-Login':'owner'}).status_code==502 # correct key, wrong dev env
 other=p.key.with_name('wrong.key');other.write_text('dev-key:'+('d'*48));other.chmod(0o600)
 _,wrong,_,_=bridge(p,prod,PRODUCTION,other);assert wrong.get('/app/records/api/v1/me',base_url=PRODUCTION.origin,headers={'X-Test-Login':'owner'}).status_code==502
 assert pc.get('/app/records/api/v1/me',base_url=DEV.origin,headers={'X-Test-Login':'owner'}).status_code==404
 assert pc.get('/app/records/api/v1/me',base_url=PRODUCTION.origin).status_code==403
 state['active']=False;assert pc.get('/app/records/api/v1/me',base_url=PRODUCTION.origin,headers={'X-Test-Login':'owner'}).status_code==403
 state['active']=True;state['account']=False;assert pc.get('/app/records/api/v1/me',base_url=PRODUCTION.origin,headers={'X-Test-Login':'owner'}).status_code==403
def test_browser_cannot_reach_archive_or_machine_routes(prepared):
 p=prepared;prod=p.module.create_app(str(p.data),runtime_environ=p.env);a,c,_,t=bridge(p,prod,PRODUCTION);headers={'X-Test-Login':'owner','Origin':PRODUCTION.origin}
 assert c.post('/app/records/api/v1/rooms/00000000-0000-0000-0000-000000000000/archive',base_url=PRODUCTION.origin,headers=headers,json={}).status_code==403
 assert not t.calls
 assert c.post('/app/records/api/v1/turns/claim',base_url=PRODUCTION.origin,headers=headers,json={}).status_code==403
 assert c.post('/app/records/api/v1/rooms',base_url=PRODUCTION.origin,headers={**headers,'Origin':DEV.origin},json={}).status_code==403
@pytest.mark.parametrize('change',['marker_symlink','key_symlink','public_key','missing_portal_key','wrong_marker_env','wrong_schema','missing_generation'])
def test_runtime_refuses_tampered_or_unprepared_material_before_store(prepared,monkeypatch,change):
 p=prepared;marker=p.data/RT.MANIFEST
 if change=='marker_symlink':
  copy=marker.with_name('saved.marker');marker.rename(copy);marker.symlink_to(copy)
 elif change=='key_symlink':
  copy=p.key.with_name('saved.key');p.key.rename(copy);p.key.symlink_to(copy)
 elif change=='public_key':p.key.chmod(0o644)
 elif change=='missing_portal_key':p.key.unlink()
 elif change=='wrong_marker_env':
  d=json.loads(marker.read_text());d['environment']='dev';marker.write_text(json.dumps(d))
 elif change=='wrong_schema':
  with sqlite3.connect(p.data/'records.sqlite3')as db:db.execute('CREATE TABLE unexpected(value TEXT)')
 elif change=='missing_generation':
  with sqlite3.connect(p.data/'records.sqlite3')as db:db.execute("DELETE FROM meta WHERE key='rooms_r1_schema'")
 monkeypatch.setattr(p.module,'Store',lambda *a:pytest.fail('Store must not run'))
 with pytest.raises((OSError,ValueError)):p.module.create_app(str(p.data),runtime_environ=p.env)
def test_archive_caller_uses_production_profile_without_weakening_its_authority(prepared):
 from datetime import datetime,timezone
 import base64
 from minimoi_portal.rooms_archive_caller import from_env,CallerRefused
 p=prepared;access=p.data/'access';access.mkdir(mode=0o700)
 (access/'bindings.json').write_text(json.dumps({'version':1,'bindings':[{'account':'account:robert','principal':'robert','active':True,'granted_by':'synthetic','granted_at':'2026-10-15'}]}))
 (access/'archive-schedule.json').write_text(json.dumps({'enabled':True,'delegation_id':'synthetic-proof','owner':'robert'}))
 for f in access.iterdir():f.chmod(0o600)
 env=dict(p.env,MINIMOI_ROOMS_ACCESS_DIR=str(access));caller=from_env(env,run_id='synthetic-run');caller.clock=lambda:datetime(2026,10,15,8,15,tzinfo=timezone.utc);caller.load_users=lambda:[{'username':'robert','tier':'owner'}]
 calls=[]
 class Fake:
  def request(self,method,url,**kw):calls.append((method,url,kw));return SimpleNamespace(status_code=200,content=b'{"synthetic":true}')
 caller.transport=Fake();caller('GET','/api/v1/rooms/00000000-0000-0000-0000-000000000000/archive-check')
 header=calls[0][2]['headers']['X-Portal-Identity'];claims=json.loads(base64.urlsafe_b64decode(header.split('.')[1]+'==='));assert claims['env']=='production' and claims['sub']=='robert'
 assert caller.profile==PRODUCTION and caller.backend==PRODUCTION.backend
 caller.clock=lambda:datetime(2026,10,15,18,15,tzinfo=timezone.utc)
 with pytest.raises(ValueError):caller.authorize()
 caller.clock=lambda:datetime(2026,10,15,8,15,tzinfo=timezone.utc);caller.load_users=lambda:[]
 with pytest.raises(CallerRefused):caller.authorize()
def test_installer_cannot_make_a_production_route_from_flags_alone(prepared):
 p=prepared
 for env in ({},dict(p.env,MINIMOI_RECORDS_PRODUCTION='0'),dict(p.env,MINIMOI_ROLE='standby')):
  app=Flask('off');state=B.install_configured(app,base_url=PRODUCTION.origin,environ=env,require_login=lambda f:f,require_owner=lambda f:f)
  assert state['state']=='off_not_dev' and not [r.rule for r in app.url_map.iter_rules()if r.rule.startswith('/app/records')]
 app=Flask('configured');state=B.install_configured(app,base_url=PRODUCTION.origin,environ=p.env,require_login=lambda f:f,require_owner=lambda f:f)
 assert state['state']=='on' and app.extensions['records_transport_profile']=='production'
 # No access/key provisioning: on is route configuration, not access acceptance.
 assert app.test_client().get('/app/records/api/v1/me',base_url=PRODUCTION.origin).status_code==503

def test_production_role_never_falls_back_to_dev_profile():
 from minimoi_portal.rooms_archive_caller import from_env,CallerRefused
 env=dict(ENV,BASE_URL=DEV.origin,MINIMOI_RECORDS_PROFILE='dev')
 assert select_records_profile(DEV.origin,env)is None
 with pytest.raises(CallerRefused):from_env(env,run_id='synthetic')
