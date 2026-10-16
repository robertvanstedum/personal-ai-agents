"""Synthetic key strings/stores only; no runtime, provider or production inputs."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import pytest
from services import records_runtime as RT

from services import records_prepare as P

@pytest.fixture
def fixture(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    # Existing runtime/1 host binding equals this synthetic runtime directory.
    inputs = {'destination': str(tmp_path/'empty'), 'runtime_data_dir': str(tmp_path/'empty'), 'key_sha256': {}}
    for k, text in {'owner': 'o'*48, 'session': 's'*48, 'portal': 'production-fixture:'+'p'*48}.items():
        f=tmp_path/(k+'.input');f.write_text(text+'\n');f.chmod(0o600)
        inputs[k+'_key_file']=str(f)
        inputs['key_sha256'][k]=hashlib.sha256(f.read_bytes()).hexdigest()
    return inputs

def prepare(fixture):
    result=P.prepare(fixture)
    return Path(fixture['destination']),result

def test_real_store_empty_schema_and_runtime_preflight(fixture,monkeypatch):
    root,result=prepare(fixture)
    assert result['prepared'] and result['content_rows']==0 and result['agent_grants']==0
    assert not (root/'INCOMPLETE').exists() and (root/'COMPLETE').is_file()
    monkeypatch.setattr(RT,'KEY_PATH',fixture['portal_key_file'])
    env={'BASE_URL':'https://minimoi.ai','MINIMOI_ROLE':'production','MINIMOI_RECORDS_PROFILE':'production','MINIMOI_RECORDS_PRODUCTION':'1','RECORDS_PORTAL_ENV':'production','RECORDS_ALLOWED_HOSTS':'minimoi-records:18880','RECORDS_PORTAL_KEY_FILE':fixture['portal_key_file']}
    assert RT.preflight(root,env)['provisioned_here'] is False
    with sqlite3.connect(root/'records.sqlite3') as db:
        P.verify_empty(db, hashlib.sha256(('o'*48).encode()).hexdigest())
        before={t:db.execute('SELECT * FROM "'+t+'"').fetchall() for(t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    production=RT.build_app(str(root),env)
    client=production.test_client()
    assert client.get('/health',base_url='http://minimoi-records:18880').status_code==200
    assert client.post('/api/login',base_url='http://minimoi-records:18880',json={'token':'synthetic'}).status_code==403
    with sqlite3.connect(root/'records.sqlite3') as db:
        after={t:db.execute('SELECT * FROM "'+t+'"').fetchall() for t in before}
    assert before==after
    assert all(p.stat().st_mode&0o777==0o600 for p in root.iterdir())
    assert root.stat().st_mode&0o777==0o700

def test_explicit_container_binding_is_retained(fixture):
    fixture['runtime_data_dir']='/var/lib/minimoi-records'
    root,_=prepare(fixture)
    assert json.loads((root/RT.MANIFEST).read_text())['data_dir']=='/var/lib/minimoi-records'

@pytest.mark.parametrize('change',['existing','symlink_key','public_key','hardlink_key','same_secret','wrong_pin','public_parent'])
def test_refuses_before_output(fixture,change,tmp_path):
    if change=='existing':
        Path(fixture['destination']).mkdir(mode=0o700)
        (Path(fixture['destination'])/'sentinel').write_text('keep')
    elif change=='symlink_key':
        f=Path(fixture['owner_key_file']);original=f.with_suffix('.original');f.rename(original);f.symlink_to(original)
    elif change=='public_key':Path(fixture['owner_key_file']).chmod(0o644)
    elif change=='hardlink_key':
        import os
        os.link(fixture['owner_key_file'],tmp_path/'linked')
    elif change=='same_secret':
        f=Path(fixture['session_key_file']);f.write_bytes(Path(fixture['owner_key_file']).read_bytes());fixture['key_sha256']['session']=hashlib.sha256(f.read_bytes()).hexdigest()
    elif change=='wrong_pin':fixture['key_sha256']['owner']='0'*64
    else:tmp_path.chmod(0o755)
    with pytest.raises((ValueError,OSError)):P.prepare(fixture)
    if change=='existing':assert (Path(fixture['destination'])/'sentinel').read_text()=='keep'
    else:assert not Path(fixture['destination']).exists()

def test_insufficient_disk_refuses_before_output(fixture,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(P.os,'fstatvfs',lambda fd:SimpleNamespace(f_bavail=100,f_frsize=4096))
    with pytest.raises(ValueError,match='disk_reserve'):P.prepare(fixture)
    assert not Path(fixture['destination']).exists()

def test_partial_failure_retained_and_refuses_retry(fixture,monkeypatch):
    def failed():raise ValueError('synthetic_setup_failure')
    monkeypatch.setattr(P,'store_type',failed)
    with pytest.raises(ValueError,match='synthetic_setup'):P.prepare(fixture)
    root=Path(fixture['destination'])
    assert (root/'INCOMPLETE').is_file() and not (root/RT.MANIFEST).exists()
    before={p.name:p.read_bytes() for p in root.iterdir()}
    with pytest.raises(FileExistsError):P.prepare(fixture)
    assert before=={p.name:p.read_bytes() for p in root.iterdir()}

def test_mid_prepare_input_mutation_refuses_completion(fixture,monkeypatch):
    factory=P.store_type
    def altered():
        cls=factory()
        def make(root):
            store=cls(root)
            Path(fixture['session_key_file']).write_text('x'*48+'\n')
            return store
        return make
    monkeypatch.setattr(P,'store_type',altered)
    with pytest.raises(ValueError,match='changed_during_prepare'):P.prepare(fixture)
    root=Path(fixture['destination'])
    assert (root/'INCOMPLETE').exists() and not (root/RT.MANIFEST).exists()

def test_incomplete_marker_dominates_complete_runtime(fixture,monkeypatch):
    root,_=prepare(fixture)
    candidate=RT
    monkeypatch.setattr(candidate,'KEY_PATH',fixture['portal_key_file'])
    env={'BASE_URL':'https://minimoi.ai','MINIMOI_ROLE':'production','MINIMOI_RECORDS_PROFILE':'production','MINIMOI_RECORDS_PRODUCTION':'1','RECORDS_PORTAL_ENV':'production','RECORDS_ALLOWED_HOSTS':'minimoi-records:18880','RECORDS_PORTAL_KEY_FILE':fixture['portal_key_file']}
    assert candidate.preflight(root,env)['provisioned_here'] is False
    (root/'INCOMPLETE').write_text('{}');(root/'INCOMPLETE').chmod(0o600)
    with pytest.raises(ValueError,match='preparation_incomplete'):candidate.preflight(root,env)
    (root/'INCOMPLETE').unlink();(root/'INCOMPLETE').symlink_to(root/'absent')
    with pytest.raises(ValueError,match='preparation_incomplete'):candidate.preflight(root,env)

def test_default_plan_does_not_read_keys_or_write(monkeypatch,capsys):
    monkeypatch.setattr(sys,'argv',['prepare'])
    monkeypatch.setattr(P,'read_private',lambda *args:pytest.fail('read input'))
    monkeypatch.setattr(P,'prepare',lambda *args:pytest.fail('prepared state'))
    P.main()
    assert json.loads(capsys.readouterr().out)['writes']==0


def test_no_key_generation_or_environment_cos_configuration(fixture,monkeypatch):
    import secrets
    monkeypatch.setattr(secrets,'token_urlsafe',lambda *args:pytest.fail('generated a key'))
    monkeypatch.setenv('RECORDS_COS_CONFIG','/does/not/exist/no-config-read')
    root,result=prepare(fixture)
    assert result['prepared'] and (root/'COMPLETE').exists()
