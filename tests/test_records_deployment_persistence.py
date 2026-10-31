"""Private metadata, shell seams and offline Compose only; no daemon or network."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import pytest
import yaml
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('records_deployment',ROOT/'scripts/rooms/deployment.py');D=importlib.util.module_from_spec(s);s.loader.exec_module(D)

@pytest.fixture
def inputs(tmp_path):
    tmp_path.chmod(0o700)
    state=tmp_path/'empty';state.mkdir(mode=0o700)
    for name in ('production-runtime.json','records.sqlite3','owner-key.txt','session-key.txt'):
        f=state/name;f.write_text('synthetic');f.chmod(0o600)
    for name in ('bridge.key','backup.json'):
        f=tmp_path/name;f.write_text('synthetic');f.chmod(0o600)
    return {'image':'332704997792.dkr.ecr.us-east-1.amazonaws.com/minimoi/portal@sha256:'+'a'*64,'state':str(state),'portal_key':str(tmp_path/'bridge.key'),'network':'minimoi-records-production-fixture','backup_receipt':str(tmp_path/'backup.json')}

def network(inputs):return {'Name':inputs['network'],'Internal':True,'Containers':{}}
def admit(inputs,rows=None,net=None):
    return D.admit(inputs,rows={}if rows is None else rows,network=network(inputs)if net is None else net,portal_file=ROOT/'docker-compose.records-production-portal.yml',backend_file=ROOT/'docker-compose.records-production.yml')
def record(inputs,project=D.PROJECT):
    return {'Config':{'Labels':{'com.docker.compose.project':project,'com.docker.compose.service':'minimoi-records'}},'NetworkSettings':{'Networks':{inputs['network']:{}}},'Mounts':[{'Destination':'/var/lib/minimoi-records','Source':inputs['state'],'RW':True},{'Destination':'/run/secrets/rooms-bridge/portal-bridge.key','Source':inputs['portal_key'],'RW':False}]}

def test_absent_binding_preserves_default_but_never_silently_drops_active(tmp_path):
    assert D.read(tmp_path/'absent'/'binding.json',trusted_uid=os.getuid())is None
    assert D.admit(None,rows={})is None
    for rows in ({'minimoi-records':{}},{'minimoi-portal':{'Config':{'Env':['MINIMOI_RECORDS_PRODUCTION=1']}}},{'minimoi-portal':{'Mounts':[{'Destination':'/run/secrets/rooms-bridge/portal-bridge.key'}]}},{'minimoi-portal':{'NetworkSettings':{'Networks':{'minimoi-records-production-fixture':{}}}}}):
        with pytest.raises(D.Refused):D.admit(None,rows=rows)

def install(tmp_path,inputs):
    p=tmp_path/'binding.json';p.write_text(json.dumps({'schema':'minimoi.records-production-binding.v1','inputs':inputs}));p.chmod(0o600);return p

def test_private_typed_five_field_binding_reuses_existing_plan(tmp_path,inputs):
    path=install(tmp_path,inputs)
    assert D.read(path,trusted_uid=os.getuid())==inputs
    result=admit(inputs)
    assert result['environment_references']['MINIMOI_RECORDS_IMAGE']==inputs['image']
    assert 'MINIMOI_IMAGE_TAG'not in result['environment_references']
    assert set(result['environment_references'])==set(D.ENV_KEYS)
    bad=dict(inputs,overlay='/tmp/arbitrary.yml')
    with pytest.raises(ValueError):admit(bad)

@pytest.mark.parametrize('change',['public','wrong_owner','symlink','hardlink','public_parent','duplicate'])
def test_untrusted_host_config_refused(tmp_path,inputs,change):
    p=install(tmp_path,inputs);uid=os.getuid()
    if change=='public':p.chmod(0o644)
    elif change=='wrong_owner':uid+=1
    elif change=='symlink':old=p.with_suffix('.old');p.rename(old);p.symlink_to(old)
    elif change=='hardlink':os.link(p,p.with_suffix('.link'))
    elif change=='public_parent':tmp_path.chmod(0o755)
    else:p.write_text('{"schema":"a","schema":"b"}')
    with pytest.raises((ValueError,OSError)):D.read(p,trusted_uid=uid)

@pytest.mark.parametrize('change',['wrong_project','state','key','writable_key','extra_network','external_network','unrelated_member'])
def test_active_runtime_admission_fails_closed(inputs,change):
    row=record(inputs);net=network(inputs)
    if change=='wrong_project':row['Config']['Labels']['com.docker.compose.project']='minimoi'
    elif change=='state':row['Mounts'][0]['Source']='/different'
    elif change=='key':row['Mounts'][1]['Source']='/different'
    elif change=='writable_key':row['Mounts'][1]['RW']=True
    elif change=='extra_network':row['NetworkSettings']['Networks']['default']={}
    elif change=='external_network':net['Internal']=False
    else:net['Containers']['x']={'Name':'unrelated'}
    with pytest.raises(D.Refused):admit(inputs,{'minimoi-records':row},net)

def test_missing_fixed_code_layer_refuses_before_any_deploy(inputs,tmp_path):
    with pytest.raises(OSError):D.admit(inputs,rows={},network=network(inputs),portal_file=tmp_path/'missing',backend_file=ROOT/'docker-compose.records-production.yml')

def test_backend_commands_are_explicit_separate_and_cannot_touch_main(inputs):
    result=admit(inputs,{'minimoi-records':record(inputs)})
    for action in ('start','status'):
        plan=D.backend_plan(result,action);cmd=plan['argv']
        assert plan['plan_only']and not plan['activated']
        assert cmd[:3]==['docker-compose','--project-name','minimoi-records-production']
        assert cmd[-1]=='minimoi-records'and 'portal'not in cmd
        assert 'docker-compose.prod.yml'not in str(cmd)
        assert '--remove-orphans'not in cmd
    assert '--pull'in D.backend_plan(result,'start')['argv']
    preflight=D.backend_plan(result,'preflight')['argv']
    assert '--network=none'in preflight and '--execute'not in preflight
    with pytest.raises(D.Refused):D.backend_plan(result,'down')
    with pytest.raises(D.Refused):D.backend_plan(None,'start')

def loader_block(rel):
    source=(ROOT/rel).read_text()
    begin=source.index('RECORDS_ENV=');end=source.index('\nfi',begin)+3
    assert begin<source.index('aws ecr get-login-password')
    return source[begin:end]

def test_both_actual_shell_blocks_preserve_tag_and_use_fixed_overlay_or_refuse(tmp_path):
    helper=tmp_path/'binding.py'
    outputs='MINIMOI_RECORDS_IMAGE=separate-backend-digest\nMINIMOI_RECORDS_STATE=/private/example state\nMINIMOI_RECORDS_PORTAL_KEY=/private/example key\nMINIMOI_RECORDS_NETWORK=minimoi-records-production-fixture\n'
    for rel in ('scripts/deploy.sh','scripts/operations/deploy_scoped_release.sh'):
        source=(ROOT/rel).read_text();assert 'COMPOSE=(docker-compose --project-name minimoi -f /opt/minimoi/docker-compose.prod.yml)'in source
        assert 'eval 'not in loader_block(rel)
        block=loader_block(rel).replace('python3 /opt/minimoi/scripts/rooms/deployment.py',str(sys.executable)+' '+str(helper))
        trailer='\nprintf "%s\\n" "${COMPOSE[@]}" "$MINIMOI_IMAGE_TAG" "${MINIMOI_RECORDS_PORTAL_KEY:-}"'
        prefix='set -euo pipefail\nCOMPOSE=(docker-compose --project-name minimoi -f /opt/minimoi/docker-compose.prod.yml)\nMINIMOI_IMAGE_TAG=future-release\n'
        helper.write_text('print('+repr(outputs)+',end="")\n')
        r=subprocess.run(['bash','-c',prefix+block+trailer],capture_output=True,text=True,timeout=5)
        assert r.returncode==0 and r.stdout.splitlines()==['docker-compose','--project-name','minimoi','-f','/opt/minimoi/docker-compose.prod.yml','-f','/opt/minimoi/docker-compose.records-production-portal.yml','future-release','/private/example key']
        helper.write_text('')
        r=subprocess.run(['bash','-c',prefix+block+trailer],capture_output=True,text=True,timeout=5)
        assert r.returncode==0 and 'records-production-portal'not in r.stdout
        helper.write_text('raise SystemExit(1)')
        r=subprocess.run(['bash','-c',prefix+block+'\necho MUTATION'],capture_output=True,text=True,timeout=5)
        assert r.returncode!=0 and 'MUTATION'not in r.stdout

def test_ci_installs_only_fixed_support_before_deploy():
    source=(ROOT/'.github/workflows/deploy.yml').read_text()
    section=source.split('- name: Push optional Records deployment support to EC2',1)[1].split('- name: Deploy on EC2',1)[0]
    assert 'scripts/rooms/production_plan.py scripts/rooms/deployment.py docker-compose.records-production.yml docker-compose.records-production-portal.yml'in section
    assert '/etc/minimoi'not in section and '.env'not in section
    assert '[ "$STATUS" = Success ] ||'in section
    assert yaml.safe_load((ROOT/'docker-compose.records-production.yml').read_text())['name']==D.PROJECT

def test_real_offline_render_proves_project_isolation_and_future_portal_tag(tmp_path,inputs):
    executable=shutil.which('docker-compose')
    if not executable:pytest.skip('standalone Compose CLI needed for offline project check')
    (tmp_path/'.env').write_text('BASE_URL=https://minimoi.ai\n')
    env={'PATH':os.environ.get('PATH',''),'HOME':str(tmp_path),'COMPOSE_DISABLE_ENV_FILE':'1','DOCKER_HOST':'unix:///nonexistent/records-no-daemon.sock','COMPOSE_PROJECT_NAME':'hostile-inherited-name','MINIMOI_ROOT':str(tmp_path),'MINIMOI_IMAGE_TAG':'future-release','MINIMOI_MODEL_GATEWAY_KEY':'synthetic','XAI_API_KEY':'synthetic','ANTHROPIC_API_KEY':'synthetic','OPENAI_API_KEY':'synthetic','MINIMOI_MODEL_GATEWAY_RECEIPT_KEY':'synthetic','COS_AGENT_A_GATEWAY_TOKEN':'synthetic'}
    env.update(D.plan_module().plan(inputs)['environment_references'])
    def render(project,files):
        argv=[executable,'--project-name',project,'--project-directory',str(ROOT)]
        for file in files:argv+=['-f',str(file)]
        argv+=['--profile','records-production','config','--format','json']
        result=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=20)
        assert result.returncode==0,'offline project render refused'
        return json.loads(result.stdout)
    main=render('minimoi',[ROOT/'docker-compose.prod.yml',ROOT/'docker-compose.records-production-portal.yml'])
    backend=render(D.PROJECT,[ROOT/'docker-compose.records-production.yml'])
    assert main['name']=='minimoi' and backend['name']=='minimoi-records-production'
    assert 'minimoi-records'not in main['services'] and set(backend['services'])=={'minimoi-records'}
    assert main['services']['portal']['image'].endswith(':future-release')
    assert backend['services']['minimoi-records']['image']==inputs['image']
    assert set(main['services']['portal']['networks'])=={'default','iotconnect-edge','mc-portal','records-production'}

def test_incomplete_attempt_is_never_an_active_binding(inputs):
    marker=Path(inputs['state'])/'INCOMPLETE';marker.write_text('{}')
    with pytest.raises(D.Refused):admit(inputs)
    marker.unlink();marker.symlink_to(marker.with_name('absent'))
    with pytest.raises(D.Refused):admit(inputs)

def test_inventory_failure_never_proves_absence():
    def failed(args):raise D.Refused('synthetic listing error')
    with pytest.raises(D.Refused):D.inventory(failed)
    def inconsistent(args):return b'minimoi-records\n'if args[0]=='ps'else b'[]'
    with pytest.raises(D.Refused):D.inventory(inconsistent)

def test_cli_missing_binding_active_stops_before_output(monkeypatch,capsys):
    monkeypatch.setattr(D.os,'geteuid',lambda:0)
    monkeypatch.setattr(D,'read',lambda:None)
    monkeypatch.setattr(D,'inventory',lambda:{'minimoi-records':{}})
    monkeypatch.setattr(sys,'argv',['deployment','--portal-env'])
    with pytest.raises(D.Refused):D.main()
    assert capsys.readouterr().out==''


def test_operator_headers_never_instruct_combining_backend_into_main():
    header=(ROOT/'docker-compose.records-production-portal.yml').read_text().split('services:',1)[0]
    assert 'NEVER combine the backend Compose file into the main project.'in header
    assert '--project-name minimoi -f docker-compose.prod.yml -f THIS_FILE'in header
    assert 'separate --project-name minimoi-records-production'in header
    assert 'Include with docker-compose.prod.yml AND docker-compose.records-production.yml'not in header

def ready_record(inputs):
    row=record(inputs)
    row['Id']='a'*64
    row['State']={'Running':True,'Health':{'Status':'healthy'}}
    row['Config'].update({'Image':inputs['image'],'Cmd':D.RUNTIME_COMMAND,'User':'','Entrypoint':None,'WorkingDir':'/app','Env':[k+'='+v for k,v in D.PRODUCTION_ENV.items()],'Healthcheck':{'Test':D.HEALTH_TEST,'Interval':30000000000,'Timeout':5000000000,'StartPeriod':15000000000,'Retries':3}})
    row['HostConfig']={'ReadonlyRootfs':True,'Privileged':False,'CapAdd':None,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges:true'],'Memory':384*1024*1024,'MemorySwap':384*1024*1024,'NanoCpus':500000000,'PidsLimit':64,'NetworkMode':inputs['network'],'PublishAllPorts':False,'PortBindings':{},'RestartPolicy':{'Name':'no'},'Tmpfs':{'/tmp':'size=16m,mode=1777'}}
    row['NetworkSettings']['Ports']={'5001/tcp':None}
    for m in row['Mounts']:m['Type']='bind'
    return row

def cli_fixture(monkeypatch,inputs,row,net):
    original=D.admit
    def files(value,**kwargs):
        return original(value,**kwargs,portal_file=ROOT/'docker-compose.records-production-portal.yml',backend_file=ROOT/'docker-compose.records-production.yml')
    monkeypatch.setattr(D,'admit',files)
    # Only main's first euid check represents the root operator; subsequent
    # metadata checks use the real synthetic fixture UID (no actual chown).
    fixture_uid=os.geteuid()
    first=[True]
    def operator_then_fixture():
        if first:
            first.pop();return 0
        return fixture_uid
    monkeypatch.setattr(D.os,'geteuid',operator_then_fixture)
    monkeypatch.setattr(D,'read',lambda:inputs)
    monkeypatch.setattr(D,'inventory',lambda:{}if row is None else {'minimoi-records':row})
    def read_network(args):
        assert args==['network','inspect',inputs['network']]
        return json.dumps([net]).encode()
    monkeypatch.setattr(D,'local_docker',read_network)
    monkeypatch.setattr(sys,'argv',['deployment','--portal-env'])

def test_portal_cli_exports_only_after_complete_backend_readiness(inputs,monkeypatch,capsys):
    row=ready_record(inputs);net=network(inputs);net['Containers'][row['Id']]={'Name':'minimoi-records'}
    cli_fixture(monkeypatch,inputs,row,net)
    D.main()
    lines=capsys.readouterr().out.splitlines()
    assert len(lines)==4 and {line.split('=',1)[0]for line in lines}==set(D.ENV_KEYS)

@pytest.mark.parametrize('failure',['absent','stopped','unhealthy','wrong_image','wrong_profile','wrong_host','wrong_key_reference','wrong_command','wrong_health_command','writable_root','privileged','added_capability','missing_cap_drop','missing_no_new_privileges','memory','swap','cpu','pids','published_port','publish_all','network_port','extra_bind','state_readonly','key_wrong_type','missing_network_member'])
def test_portal_cli_exports_nothing_on_unready_backend(inputs,monkeypatch,capsys,failure):
    row=ready_record(inputs);net=network(inputs);net['Containers'][row['Id']]={'Name':'minimoi-records'}
    if failure=='absent':row=None;net['Containers']={}
    elif failure=='stopped':row['State']['Running']=False
    elif failure=='unhealthy':row['State']['Health']['Status']='starting'
    elif failure=='wrong_image':row['Config']['Image']=inputs['image'][:-1]+'b'
    elif failure in {'wrong_profile','wrong_host','wrong_key_reference'}:
        key={'wrong_profile':'MINIMOI_RECORDS_PROFILE','wrong_host':'RECORDS_ALLOWED_HOSTS','wrong_key_reference':'RECORDS_PORTAL_KEY_FILE'}[failure]
        row['Config']['Env']=[s for s in row['Config']['Env']if not s.startswith(key+'=')]+[key+'=wrong']
    elif failure=='wrong_command':row['Config']['Cmd']=['true']
    elif failure=='wrong_health_command':row['Config']['Healthcheck']['Test']=['CMD','true']
    elif failure=='writable_root':row['HostConfig']['ReadonlyRootfs']=False
    elif failure=='privileged':row['HostConfig']['Privileged']=True
    elif failure=='added_capability':row['HostConfig']['CapAdd']=['SYS_ADMIN']
    elif failure=='missing_cap_drop':row['HostConfig']['CapDrop']=[]
    elif failure=='missing_no_new_privileges':row['HostConfig']['SecurityOpt']=[]
    elif failure in {'memory','swap','cpu','pids'}:
        row['HostConfig'][{'memory':'Memory','swap':'MemorySwap','cpu':'NanoCpus','pids':'PidsLimit'}[failure]]=0
    elif failure=='published_port':row['HostConfig']['PortBindings']={'18880/tcp':[{'HostPort':'18880'}]}
    elif failure=='publish_all':row['HostConfig']['PublishAllPorts']=True
    elif failure=='network_port':row['NetworkSettings']['Ports']['5001/tcp']=[{'HostPort':'5001'}]
    elif failure=='extra_bind':row['Mounts'].append({'Type':'bind','Source':'/private','Destination':'/outside','RW':False})
    elif failure=='state_readonly':row['Mounts'][0]['RW']=False
    elif failure=='key_wrong_type':row['Mounts'][1]['Type']='volume'
    else:net['Containers']={}
    cli_fixture(monkeypatch,inputs,row,net)
    with pytest.raises(D.Refused):D.main()
    assert capsys.readouterr().out==''

def test_backend_preparation_does_not_claim_portal_readiness(inputs):
    absent=admit(inputs)
    assert D.backend_plan(absent,'start')['plan_only']is True
    stopped=ready_record(inputs);stopped['State']['Running']=False
    planned=admit(inputs,{'minimoi-records':stopped})
    assert D.backend_plan(planned,'preflight')['activated']is False
    with pytest.raises(D.Refused):D.admit(inputs,rows={'minimoi-records':stopped},network=network(inputs),portal_file=ROOT/'docker-compose.records-production-portal.yml',backend_file=ROOT/'docker-compose.records-production.yml',portal_required=True)

def test_readiness_constants_match_reviewed_optional_compose():
    backend=yaml.safe_load((ROOT/'docker-compose.records-production.yml').read_text())['services']['minimoi-records']
    assert D.PRODUCTION_ENV==backend['environment']
    assert D.RUNTIME_COMMAND==backend['command']
    assert D.HEALTH_TEST==backend['healthcheck']['test']
    assert backend['read_only']is True and backend['cap_drop']==['ALL'] and backend['security_opt']==['no-new-privileges:true']
    assert backend['mem_limit']=='384m' and backend['memswap_limit']=='384m' and backend['cpus']==0.5 and backend['pids_limit']==64


def test_fifo_binding_refuses_without_waiting_for_writer(tmp_path):
    path=tmp_path/'binding.json'
    os.mkfifo(path, mode=0o600)
    # Execute in a bounded child: removing O_NONBLOCK would time out.
    code="import importlib.util,os,sys; s=importlib.util.spec_from_file_location('d',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.read(sys.argv[2],trusted_uid=os.getuid())"
    result=subprocess.run([sys.executable,'-c',code,str(ROOT/'scripts/rooms/deployment.py'),str(path)],capture_output=True,timeout=3)
    assert result.returncode!=0 and b'records_deployment_refused' in result.stderr
