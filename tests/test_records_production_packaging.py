import importlib.util,pathlib,stat,pytest,yaml
ROOT=pathlib.Path(__file__).parents[1]
s=importlib.util.spec_from_file_location('records_prod_plan',ROOT/'scripts/rooms/production_plan.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
@pytest.fixture
def inputs(tmp_path):
 tmp_path.chmod(0o700);root=tmp_path/'production';root.mkdir(mode=0o700)
 for n in ('production-runtime.json','records.sqlite3','owner-key.txt','session-key.txt'):
  p=root/n;p.write_text('synthetic');p.chmod(0o600)
 key=tmp_path/'key';key.write_text('synthetic');key.chmod(0o600)
 receipt=tmp_path/'backup.json';receipt.write_text('{}');receipt.chmod(0o600)
 return {'image':'332704997792.dkr.ecr.us-east-1.amazonaws.com/minimoi/portal@sha256:'+'a'*64,'state':str(root),'portal_key':str(key),'network':'minimoi-records-production-reviewed','backup_receipt':str(receipt)}
def test_plan_is_readonly_and_runtime_preflight_has_no_execute(inputs):
 before={str(p):(p.stat().st_mtime_ns,p.read_bytes())for p in pathlib.Path(inputs['state']).rglob('*')}
 result=m.plan(inputs);cmd=result['runtime_preflight_argv'];assert result['plan_only']and not result['activated']and not result['provisioned_here'];assert '--network=none'in cmd and '--pull=never'in cmd and '--execute'not in cmd
 assert {str(p):(p.stat().st_mtime_ns,p.read_bytes())for p in pathlib.Path(inputs['state']).rglob('*')}==before
@pytest.mark.parametrize('key,value',[('image','portal:latest'),('network','minimoi-staging_default'),('state','/nonexistent/prod'),('portal_key','/Users/example/minimoi-staging/key')])
def test_missing_or_dev_inputs_refused(inputs,key,value):
 inputs[key]=value
 with pytest.raises((ValueError,FileNotFoundError)):m.plan(inputs)
def test_no_directory_auto_creation(inputs,tmp_path):
 inputs['state']=str(tmp_path/'missing')
 with pytest.raises(FileNotFoundError):m.plan(inputs)
 assert not pathlib.Path(inputs['state']).exists()
def test_symlink_and_public_state_refused(inputs,tmp_path):
 alias=tmp_path/'alias';alias.symlink_to(inputs['state']);inputs['state']=str(alias)
 with pytest.raises(ValueError,match='symlink'):m.plan(inputs)
def test_optional_profile_no_ports_external_network_no_bootstrap():
 config=yaml.safe_load((ROOT/'docker-compose.records-production.yml').read_text());service=config['services']['minimoi-records']
 assert service['profiles']==['records-production'];assert service['restart']=='no';assert 'ports'not in service and 'build'not in service and 'env_file'not in service
 assert config['networks']['records-production']['external']is True
 assert all(v['bind']['create_host_path']is False for v in service['volumes']);assert len(service['volumes'])==2
 assert service['command']==['python','-m','services.records_runtime','--data-dir','/var/lib/minimoi-records','--execute']
 assert service['cap_drop']==['ALL']and service['read_only']is True
 assert service['environment']['MINIMOI_ROLE']=='production'


def test_exact_health_command_passes_production_host_guard(tmp_path,monkeypatch):
 # Reuse P1's real prepared runtime fixture, not a substring host assertion.
 import urllib.request,urllib.error
 fixture_path=ROOT/'tests/guild/shop_floor/test_records_production_profile.py'
 spec=importlib.util.spec_from_file_location('records_packaging_production_fixture',fixture_path);fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
 prepared=fixture.prepared.__wrapped__(tmp_path,monkeypatch)
 fixture.RT.preflight(prepared.data,prepared.env)
 app=prepared.module.create_app(str(prepared.data),runtime_environ=prepared.env)
 client=app.test_client()
 assert client.get('/health',base_url='http://127.0.0.1:18880').status_code==403
 class Result:
  def __init__(self,response):self.status=response.status_code
 calls=[]
 def local_urlopen(req,timeout):
  assert isinstance(req,urllib.request.Request);assert req.full_url=='http://127.0.0.1:18880/health';assert timeout==3
  response=client.get('/health',base_url='http://127.0.0.1:18880',headers=dict(req.header_items()));calls.append(response.status_code)
  if response.status_code>=400:raise urllib.error.HTTPError(req.full_url,response.status_code,'refused',{},None)
  return Result(response)
 monkeypatch.setattr(urllib.request,'urlopen',local_urlopen)
 command=yaml.safe_load((ROOT/'docker-compose.records-production.yml').read_text())['services']['minimoi-records']['healthcheck']['test']
 assert command[:3]==['CMD','python','-c']
 exec(compile(command[3],'<exact-compose-health>','exec'),{})
 assert calls==[200]
