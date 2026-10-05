import json
from pathlib import Path

import pytest

from services.mc_operations.profile import Refused, candidate, install, make_plan, rollback_candidate, rollback, digest


def original():
    return {'agents': {'entries': {'main': {'workspace':'/personal'}},
                       'defaults': {'model': {'primary':'test/model'}}},
            'bindings': [{'agentId':'main','match':{'channel':'telegram'}}],
            'gateway': {'auth':{'token':'synthetic-secret'}}, 'tools': {'web': {}}}


def test_only_new_agent_changes(tmp_path):
    old = original()
    new = candidate(old, tmp_path, tmp_path/'workspace')
    agent = new['agents']['entries'].pop('mc-agent')
    assert new['agents']['entries']['main'].pop('default') is True
    assert new == old
    assert agent['tools'] == {'profile':'full'}
    assert 'skills' not in agent  # inherit bundled/native skill availability
    assert agent['agentDir'] != '/personal'
    assert agent['memory']['search']['enabled'] is False


def test_refuses_existing_mc(tmp_path):
    old = original(); old['agents']['entries']['mc-agent'] = {'workspace':'keep'}
    with pytest.raises(Refused): candidate(old,tmp_path,tmp_path/'workspace')


def test_plan_has_no_gateway_secrets_and_is_bound_to_config(tmp_path):
    p=tmp_path/'config.json'; p.write_text(json.dumps(original()))
    plan,_=make_plan(p,tmp_path/'workspace')
    assert 'synthetic-secret' not in json.dumps(plan)
    p.write_text(p.read_text()+'\n')
    with pytest.raises(Refused,match='plan_changed'):
        install(p,tmp_path/'workspace',plan['plan_id'],validator=lambda *_:None)


def test_validation_failure_preserves_original(tmp_path):
    p=tmp_path/'config.json'; p.write_text(json.dumps(original())); raw=p.read_bytes()
    plan,_=make_plan(p,tmp_path/'workspace')
    def fail(*_):raise Refused('invalid')
    with pytest.raises(Refused):install(p,tmp_path/'workspace',plan['plan_id'],validator=fail)
    assert p.read_bytes()==raw
    assert not list(tmp_path.glob('.mc-candidate-*'))


def test_concurrent_config_change_preserved(tmp_path):
    p=tmp_path/'config.json'; p.write_text(json.dumps(original()))
    plan,_=make_plan(p,tmp_path/'workspace')
    def change(*_):p.write_text('{"new":"owner update"}')
    with pytest.raises(Refused,match='config_changed'):
        install(p,tmp_path/'workspace',plan['plan_id'],validator=change)
    assert json.loads(p.read_text())=={'new':'owner update'}


def test_install_retains_private_backup_and_only_adds_mc(tmp_path):
    p=tmp_path/'config.json'; p.write_text(json.dumps(original())); raw=p.read_bytes()
    plan,_=make_plan(p,tmp_path/'workspace')
    result=install(p,tmp_path/'workspace',plan['plan_id'],validator=lambda *_:None)
    assert Path(result['backup']).read_bytes()==raw
    assert p.stat().st_mode & 0o777 == 0o600
    assert Path(result['backup']).stat().st_mode & 0o777 == 0o600
    saved=json.loads(p.read_text());del saved['agents']['entries']['mc-agent']
    saved['agents']['entries']['main'].pop('default')
    assert saved==original()
    assert result['runtime_verified'] is False


def test_symlink_config_refused(tmp_path):
    p=tmp_path/'config.json';p.write_text(json.dumps(original()))
    link=tmp_path/'link.json';link.symlink_to(p)
    with pytest.raises(Refused):make_plan(link,tmp_path/'workspace')


def test_rollback_preserves_unrelated_owner_changes(tmp_path):
    old=original();current=candidate(old,tmp_path,tmp_path/'workspace')
    current['new_owner_setting']='keep'
    current['agents']['entries']['main']['name']='Personal'
    restored=rollback_candidate(current,old,tmp_path/'workspace',tmp_path)
    assert 'mc-agent' not in restored['agents']['entries']
    assert 'default' not in restored['agents']['entries']['main']
    assert restored['new_owner_setting']=='keep'
    assert restored['agents']['entries']['main']['name']=='Personal'


def test_rollback_refuses_modified_mc(tmp_path):
    old=original();current=candidate(old,tmp_path,tmp_path/'workspace')
    current['agents']['entries']['mc-agent']['name']='Owner changed'
    with pytest.raises(Refused):rollback_candidate(current,old,tmp_path/'workspace',tmp_path)


@pytest.mark.parametrize('entries',[{}, {'another':{}}, {'main':{},'other':{}}])
def test_unsupported_roster_refused(tmp_path,entries):
    old=original();old['agents']['entries']=entries
    with pytest.raises(Refused):candidate(old,tmp_path,tmp_path/'workspace')


def test_real_rollback_keeps_unrelated_update(tmp_path):
    p=tmp_path/'config.json';p.write_text(json.dumps(original()))
    plan,_=make_plan(p,tmp_path/'workspace')
    result=install(p,tmp_path/'workspace',plan['plan_id'],validator=lambda *_:None)
    current=json.loads(p.read_bytes());current['owner']='new setting';p.write_text(json.dumps(current))
    rollback(p,result['backup'],tmp_path/'workspace',digest(p.read_bytes()),validator=lambda *_:None)
    restored=json.loads(p.read_bytes())
    assert restored.pop('owner')=='new setting'
    assert restored==original()
