"""Synthetic paired v2 HMAC HTTP+real Records/ledger, no network/model/runtime."""
import copy,json
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from minimoi_portal import rooms_archive_caller as caller_module
from minimoi_portal.guild_ui import rooms_archive_api as orchestration,rooms_archive_runner as runner
NOW=datetime(2026,10,14,8,tzinfo=timezone.utc)

@pytest.mark.parametrize('explicit',[False,True])
def test_command_selects_installed_registry_before_any_execution(tmp_path,monkeypatch,explicit):
    import importlib.util
    from pathlib import Path
    source=Path(__file__).resolve().parents[3]/'scripts/rooms/archive.py'
    spec=importlib.util.spec_from_file_location('archive_command_registry_test',source)
    command=importlib.util.module_from_spec(spec);spec.loader.exec_module(command)
    monkeypatch.setattr(command,'datetime',SimpleNamespace(now=lambda zone:NOW))
    env=tmp_path/'runtime.json';selected=tmp_path/'explicit.json'
    monkeypatch.setenv('MINIMOI_JOBS_REGISTRY',str(env))
    observed=[]
    class RegistryChecked(Exception):pass
    def load(path):observed.append(Path(path));raise RegistryChecked
    monkeypatch.setattr(command.registry,'load',load)
    monkeypatch.setattr(command,'from_env',lambda *a,**k:pytest.fail('authority reached before registry validation'))
    args=['--execute']+(['--registry',str(selected)] if explicit else [])
    with pytest.raises(RegistryChecked):command.main(args)
    assert observed==[selected if explicit else env]

@pytest.fixture
def configured(tmp_path):
    access=tmp_path/'access';access.mkdir(mode=0o700)
    policy=access/'archive-schedule.json';policy.write_text(json.dumps({'enabled':True,'delegation_id':'synthetic-installed','owner':'robert'}));policy.chmod(0o600)
    binding=access/'bindings.json';binding.write_text(json.dumps({'version':1,'bindings':[{'account':'account:robert','principal':'robert','active':True}]}));binding.chmod(0o600)
    key=access/'key';key.write_text('synthetic:'+'s'*48);key.chmod(0o600)
    calls=[]
    class Transport:
        def request(self,*args,**kw):calls.append((args,kw));return SimpleNamespace(status_code=200,content=b'{}')
    users=[{'username':'robert','tier':'owner'}]
    clock=[NOW]
    caller=caller_module.ArchiveCaller(backend='http://127.0.0.1:18880',access_dir=access,key_file=key,
        run_id='synthetic-run',load_users=lambda:users,transport=Transport(),clock=lambda:clock[0])
    return SimpleNamespace(caller=caller,access=access,policy=policy,binding=binding,key=key,calls=calls,users=users,clock=clock)

@pytest.mark.parametrize('method,path',[('POST','/api/v1/rooms/00000000-0000-0000-0000-000000000000/restore'),('POST','/api/v1/rooms'),('GET','/api/v1/rooms'),('GET','/api/v1/rooms/archive-preview?q=1'),('DELETE','/api/v1/rooms/archive-preview'),('GET','/api/v1/rooms/%2f/archive-check'),('POST','/api/v1/rooms/00000000-0000-0000-0000-000000000000/events')])
def test_fixed_routes_refuse_before_transport(configured,method,path):
    with pytest.raises(caller_module.CallerRefused):configured.caller(method,path)
    assert not configured.calls

@pytest.mark.parametrize('mutation',['disable','wrong_owner','duplicate_user','missing_user','family','binding_revoked','key_symlink','policy_symlink','window'])
def test_authority_live_reread_each_call(configured,mutation):
    r=configured;r.caller('GET','/api/v1/rooms/archive-preview');assert len(r.calls)==1
    if mutation in {'disable','wrong_owner'}:
        r.policy.write_text(json.dumps({'enabled':mutation!='disable','delegation_id':'synthetic-installed','owner':'other' if mutation=='wrong_owner' else 'robert'}))
    elif mutation=='duplicate_user':r.users.append(dict(r.users[0]))
    elif mutation=='missing_user':r.users.clear()
    elif mutation=='family':r.users[0]['tier']='family'
    elif mutation=='binding_revoked':r.binding.write_text(json.dumps({'version':1,'bindings':[]}))
    elif mutation in {'key_symlink','policy_symlink'}:
        path=r.key if mutation=='key_symlink' else r.policy;target=r.access/'target';path.rename(target);path.symlink_to(target)
    else:r.clock[0]=NOW.replace(hour=11)
    with pytest.raises(Exception):r.caller('GET','/api/v1/rooms/archive-preview')
    assert len(r.calls)==1


def test_uncertain_replay_keeps_original_key_and_precondition(monkeypatch):
    from uuid import uuid4
    room=str(uuid4());pre={'version':1,'archive_version':0,'latest_seq':0,'last_activity':(NOW-timedelta(days=31)).isoformat()}
    previous={'kind':'automated_archive_job','attempts':[{'room':room,'key':'ra-'+'a'*48,'precondition':pre,'authority':{'run_id':'original-run'},'state':'uncertain'}]}
    snapshots=[];calls=[]
    class Caller:
        def authorize(self):return None,{'purpose':'synthetic'},NOW
        def __call__(self,*a,**k):pytest.fail('no fresh preview after uncertain reconciliation')
    def fail(**kw):calls.append(kw);return {'error':'archive_unconfirmed'},409
    monkeypatch.setattr(orchestration,'archive_room',fail)
    result=runner.run_once(caller=Caller(),ledger_root='/synthetic',record=lambda v:snapshots.append(copy.deepcopy(v)),previous=previous,clock=lambda:NOW)
    assert result['state']=='failed' and len(calls)==1 and calls[0]['key']==previous['attempts'][0]['key']
    assert snapshots[0]['attempts'][0]['authority']['run_id']=='original-run'
    assert snapshots[0]['attempts'][0]['state']=='uncertain' and calls[0]['precondition']==pre

@pytest.mark.parametrize('origin',['','https://minimoi.ai','https://dev.minimoi.ai.attacker.test','https://x@dev.minimoi.ai'])
def test_factory_refuses_nondev_origin(origin):
    with pytest.raises(caller_module.CallerRefused):caller_module.from_env({'BASE_URL':origin},run_id='synthetic')


def test_cli_default_has_no_calls_or_writes(monkeypatch,capsys):
    import importlib.util
    from pathlib import Path
    # Source path resolved by private harness; after integration use repository path.
    script=Path(__file__).resolve().parents[3]/'scripts/rooms/archive.py'
    spec=importlib.util.spec_from_file_location('archive_cli',script);cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    monkeypatch.setattr(cli,'from_env',lambda *a,**k:pytest.fail('authority not needed for offline plan'))
    assert cli.main([])==0
    assert json.loads(capsys.readouterr().out)['calls_performed']==0
    monkeypatch.setattr(cli.registry,'load',lambda path=None:SimpleNamespace(get=lambda key:None))
    monkeypatch.setattr(cli,'batch_deadline',lambda _:NOW)
    with pytest.raises(ValueError,match='registered_host_required'):cli.main(['--execute'])
