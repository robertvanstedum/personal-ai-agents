"""Offline Compose rendering + in-process signed Records production roundtrip.
No daemon, network, existing credentials/accounts or production state are used.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
import yaml
from flask import Flask, abort
from minimoi_portal import records_bridge as B, rooms_identity as RI
from minimoi_portal.transport_profiles import DEV, PRODUCTION

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT/'docker-compose.prod.yml'
RECORDS = ROOT/'docker-compose.records-production.yml'
PORTAL = ROOT/'docker-compose.records-production-portal.yml'
EXPECTED = {'BASE_URL':'https://minimoi.ai','MINIMOI_ROLE':'production','MINIMOI_RECORDS_PROFILE':'production','MINIMOI_RECORDS_PRODUCTION':'1','RECORDS_BACKEND':PRODUCTION.backend,'RECORDS_PORTAL_KEY_FILE':'/run/secrets/rooms-bridge/portal-bridge.key','MINIMOI_ROOMS_ACCESS_DIR':'/app/runtime/guild/rooms'}

def compose():
    executable=shutil.which('docker-compose')
    if executable:return [executable]
    executable=shutil.which('docker')
    if executable:return [executable,'compose']
    pytest.skip('Compose CLI required for offline render')

@pytest.fixture
def environment(tmp_path):
    tmp_path.chmod(0o700)
    (tmp_path/'.env').write_text('BASE_URL=https://minimoi.ai\nPRIVATE_SENTINEL=synthetic-preserved\n')
    (tmp_path/'.env').chmod(0o600)
    env={'PATH':os.environ.get('PATH',''),'HOME':str(tmp_path),'COMPOSE_DISABLE_ENV_FILE':'1','DOCKER_HOST':'unix:///nonexistent/records-test-no-daemon.sock','MINIMOI_ROOT':str(tmp_path),'MINIMOI_MODEL_GATEWAY_KEY':'synthetic','XAI_API_KEY':'synthetic','ANTHROPIC_API_KEY':'synthetic','MINIMOI_MODEL_GATEWAY_RECEIPT_KEY':'synthetic','COS_AGENT_A_GATEWAY_TOKEN':'synthetic','MINIMOI_RECORDS_IMAGE':'332704997792.dkr.ecr.us-east-1.amazonaws.com/minimoi/portal@sha256:'+'a'*64,'MINIMOI_RECORDS_STATE':str(tmp_path/'empty-production'),'MINIMOI_RECORDS_PORTAL_KEY':str(tmp_path/'private-key'),'MINIMOI_RECORDS_NETWORK':'minimoi-records-production-fixture'}
    return env

def render(env,*files):
    command=compose()+['--project-name','records-prod-fixture','--project-directory',str(ROOT)]
    for f in files:command+=['-f',str(f)]
    command+=['--profile','records-production','config','--format','json']
    run=subprocess.run(command,env=env,capture_output=True,text=True,timeout=20)
    assert run.returncode==0, 'offline Compose render refused synthetic configuration'
    return json.loads(run.stdout)

def test_optional_portal_yaml_has_exact_narrow_surface():
    value=yaml.safe_load(PORTAL.read_text());assert set(value)=={'services','networks'}
    assert set(value['services'])=={'portal'}
    portal=value['services']['portal'];assert set(portal)=={'environment','volumes','networks'}
    assert portal['environment']==EXPECTED
    assert 'MINIMOI_RECORDS_MACHINE_BRIDGE' not in portal['environment']
    assert portal['networks']==['default','iotconnect-edge','records-production']
    assert value['networks']==yaml.safe_load(RECORDS.read_text())['networks']
    mount=portal['volumes'];assert len(mount)==1
    assert mount[0]=={'type':'bind','source':'${MINIMOI_RECORDS_PORTAL_KEY:?existing distinct production bridge key required}','target':EXPECTED['RECORDS_PORTAL_KEY_FILE'],'read_only':True,'bind':{'create_host_path':False}}

def test_actual_render_changes_only_portal_env_one_mount_one_network(environment):
    before=render(environment,PROD,RECORDS)
    after=render(environment,PROD,RECORDS,PORTAL)
    expected=copy.deepcopy(before)
    service=expected['services']['portal']
    service['environment'].update(EXPECTED)
    service['networks']['records-production']=None
    records_key=next(v for v in before['services']['minimoi-records']['volumes'] if v['target']==EXPECTED['RECORDS_PORTAL_KEY_FILE'])
    service['volumes'].append(copy.deepcopy(records_key))
    # Volume serialization order is not a container configuration difference.
    for config in (expected,after):config['services']['portal']['volumes'].sort(key=lambda x:x['target'])
    assert after==expected
    assert before['services']['minimoi-records']==after['services']['minimoi-records']
    assert after['services']['portal']['environment']['PRIVATE_SENTINEL']=='synthetic-preserved'
    assert set(after['services']['portal']['networks'])=={'default','iotconnect-edge','records-production'}
    assert after['services']['portal']['image']==before['services']['portal']['image']
    assert after['services']['portal']['ports']==before['services']['portal']['ports']
    assert not Path(environment['MINIMOI_RECORDS_STATE']).exists()
    assert not Path(environment['MINIMOI_RECORDS_PORTAL_KEY']).exists()

def test_normal_base_without_optin_is_unchanged_even_when_variables_exist(environment):
    with_vars=render(environment,PROD)
    without_vars=render({k:v for k,v in environment.items() if not k.startswith('MINIMOI_RECORDS_')},PROD)
    assert with_vars==without_vars
    assert 'records-production'not in with_vars['networks']
    assert 'minimoi-records'not in with_vars['services']
    assert not set(EXPECTED).intersection({'MINIMOI_RECORDS_PROFILE','MINIMOI_RECORDS_PRODUCTION','RECORDS_BACKEND'})&set(with_vars['services']['portal']['environment'])

@pytest.mark.parametrize('missing',['MINIMOI_RECORDS_PORTAL_KEY','MINIMOI_RECORDS_NETWORK'])
def test_explicit_inputs_missing_refuse_offline_render(environment,missing):
    del environment[missing]
    with pytest.raises(AssertionError,match='render refused'):render(environment,PROD,RECORDS,PORTAL)

def production_fixture(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('portal_wiring_prepared_fixture',ROOT/'tests/guild/shop_floor/test_records_production_profile.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module,module.prepared.__wrapped__(tmp_path,monkeypatch)

def test_signed_configured_production_roundtrip_and_live_refusal(tmp_path,monkeypatch):
    fixture,prepared=production_fixture(tmp_path,monkeypatch)
    records=prepared.module.create_app(str(prepared.data),runtime_environ=prepared.env)
    transport=fixture.Transport(records)
    access=tmp_path/'access';access.mkdir(mode=0o700)
    binding={'version':1,'bindings':[{'account':'account:synthetic-owner','principal':'robert','active':True,'granted_by':'synthetic-fixture','granted_at':'2026-10-16'}]}
    path=access/'bindings.json';path.write_text(json.dumps(binding));path.chmod(0o600)
    state={'active':True}
    portal=Flask('production-wiring-fixture')
    def guard(function):
        from functools import wraps
        from flask import request
        @wraps(function)
        def wrapped(*args,**kwargs):
            if request.headers.get('X-Fixture-Owner')!='yes':abort(403)
            return function(*args,**kwargs)
        return wrapped
    # Explicit operator-authenticated harness, no fabricated Flask login session.
    env=dict(EXPECTED,RECORDS_PORTAL_KEY_FILE=str(prepared.key))
    monkeypatch.setattr(B,'_TRANSPORT',transport)
    installed=B.install_configured(portal,base_url=env['BASE_URL'],environ=env,require_login=guard,require_owner=guard,identity={'access':RI.RoomsAccess(str(access)),'key_file':env['RECORDS_PORTAL_KEY_FILE'],'current_user':lambda:{'username':'synthetic-owner'},'account_active':lambda user:state['active']})
    assert installed['state']=='on'
    client=portal.test_client();url=PRODUCTION.origin+B.PREFIX+'/api/v1/me'
    good=client.get(url,headers={'X-Fixture-Owner':'yes','Cookie':'synthetic','Authorization':'Bearer untrusted','X-Portal-Identity':'untrusted'})
    assert good.status_code==200 and good.headers['X-Records-Environment']=='production'
    _,_,headers=transport.calls[-1]
    assert 'Cookie'not in headers and 'Authorization'not in headers and headers['X-Portal-Identity']!='untrusted'
    assert records.test_client().get('/api/v1/me',base_url=PRODUCTION.backend,headers=headers).status_code==401
    calls=len(transport.calls)
    assert client.get(url).status_code==403
    assert client.get(DEV.origin+B.PREFIX+'/api/v1/me',headers={'X-Fixture-Owner':'yes'}).status_code==404
    assert client.post(url,headers={'X-Fixture-Owner':'yes','Origin':DEV.origin},json={}).status_code==403
    assert len(transport.calls)==calls
    binding['bindings'][0]['active']=False;path.write_text(json.dumps(binding))
    assert client.get(url,headers={'X-Fixture-Owner':'yes'}).status_code==403
    assert len(transport.calls)==calls
    binding['bindings'][0]['active']=True;path.write_text(json.dumps(binding));state['active']=False
    assert client.get(url,headers={'X-Fixture-Owner':'yes'}).status_code==403
    assert len(transport.calls)==calls

@pytest.mark.parametrize('change',[{'MINIMOI_ROLE':'standby'},{'MINIMOI_RECORDS_PROFILE':'dev'},{'MINIMOI_RECORDS_PRODUCTION':'0'},{'BASE_URL':'https://dev.minimoi.ai'}])
def test_prod_switches_cannot_partially_activate(change):
    env=dict(EXPECTED,**change);app=Flask('off')
    state=B.install_configured(app,base_url=env['BASE_URL'],environ=env,require_login=lambda f:f,require_owner=lambda f:f)
    assert state['state']=='off_not_dev'
    assert not any(r.rule.startswith(B.PREFIX)for r in app.url_map.iter_rules())
