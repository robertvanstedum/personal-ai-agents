"""Records package boundaries, using disposable stores and isolated imports."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / 'prototype-lab/projects/project-records-room-poc'


def run_python(args, cwd, *, repository_path=True):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    if repository_path:
        env['PYTHONPATH'] = str(ROOT)
    return subprocess.run([sys.executable, *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=30)


def test_canonical_import_ignores_foreign_bare_modules(tmp_path):
    result = run_python(['-c', '''
import sys, types
for name in ('store', 'app', 'archive', 'platform_access', 'meetings'):
    sys.modules[name] = types.ModuleType(name)
from domains.records.app import create_app
from domains.records.store import Store
assert Store.__module__ == 'domains.records.store'
assert callable(create_app)
'''], tmp_path)
    assert result.returncode == 0, result.stderr


def test_legacy_import_shares_canonical_state(tmp_path):
    result = run_python(['-c', f'''
import sys
sys.path.insert(0, {str(LEGACY)!r})
import store
from domains.records import store as canonical
assert store is canonical
store.compatibility_probe = object()
assert canonical.compatibility_probe is store.compatibility_probe
'''], tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('script', ['app.py', 'manage.py', 'roomctl.py', 'accessctl.py',
                                        'integration/cos_request_worker.py',
                                        'integration/synthetic_cos_setup.py'])
def test_legacy_cli_from_other_directory(script, tmp_path):
    result = run_python([str(LEGACY / script), '--help'], tmp_path, repository_path=False)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout.lower()
    assert list(tmp_path.iterdir()) == []


def test_static_response_uses_canonical_assets(tmp_path):
    from domains.records.app import create_app
    app = create_app(tmp_path / 'records', testing=True, cos_sessions=[],
                     runtime_environ={'RECORDS_PORTAL_ENV': 'dev'})
    asset = ROOT / 'domains/records/static/style.css'
    response = app.test_client().get('/static/style.css', base_url='http://localhost:18880')
    assert response.status_code == 200
    assert response.data == asset.read_bytes()
    assert (LEGACY / 'static/style.css').resolve() == asset
    assert (LEGACY / 'contracts/index.json').resolve() == ROOT / 'domains/records/contracts/index.json'


def test_production_refusal_precedes_store_creation(tmp_path, monkeypatch):
    from domains.records import app
    def forbidden(*args, **kwargs):
        pytest.fail('Store constructed before production preflight')
    monkeypatch.setattr(app, 'Store', forbidden)
    data = tmp_path / 'not-prepared'
    with pytest.raises((ValueError, RuntimeError)):
        app.create_app(data, runtime_environ={'RECORDS_PORTAL_ENV': 'production'})
    assert not data.exists()


def test_relocation_preserves_routes_schema_and_asset_bytes(tmp_path):
    """Compare the approved parent runtime with this package in separate processes."""
    import io
    import json
    import tarfile
    baseline = '85ae32a6213b248f6e3d91b245ee8b37c04f51ad'
    relative = str(LEGACY.relative_to(ROOT))
    archive = subprocess.check_output(['git', 'archive', baseline, relative], cwd=ROOT)
    original = tmp_path / 'original'
    original.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(original, filter='data')
    probe = '''
import hashlib, importlib, json, pathlib, sqlite3, sys
source, module, data = sys.argv[1:]
sys.path.insert(0, source)
m = importlib.import_module(module)
a = m.create_app(data, testing=True, cos_sessions=[], runtime_environ={'RECORDS_PORTAL_ENV':'dev'})
with sqlite3.connect(pathlib.Path(data)/'records.sqlite3') as db:
    schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
assets = pathlib.Path(m.__file__).parent
hashes = {str(p.relative_to(assets)): hashlib.sha256(p.read_bytes()).hexdigest()
          for name in ('static','contracts') for p in (assets/name).rglob('*') if p.is_file()}
print(json.dumps({'routes': sorted((str(r),sorted(r.methods)) for r in a.url_map.iter_rules()),
                  'schema': schema, 'assets': hashes}, sort_keys=True))
'''
    before = run_python(['-c', probe, str(original / relative), 'app', str(tmp_path / 'before')], tmp_path)
    after = run_python(['-c', probe, str(ROOT), 'domains.records.app', str(tmp_path / 'after')], tmp_path)
    assert before.returncode == 0, before.stderr
    assert after.returncode == 0, after.stderr
    assert json.loads(before.stdout) == json.loads(after.stdout)


@pytest.mark.parametrize('script', ['app.py', 'manage.py', 'roomctl.py', 'accessctl.py'])
def test_flat_container_launcher_layout(script, tmp_path):
    import shutil
    container = tmp_path / 'app'
    shutil.copytree(ROOT / 'domains/records', container / 'domains/records')
    shutil.copyfile(LEGACY / script, container / script)
    other = tmp_path / 'other'
    other.mkdir()
    result = run_python([str(container / script), '--help'], other, repository_path=False)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout.lower()


def test_container_preserves_provisioning_launcher_and_removes_preview_fixtures():
    dockerfile = (ROOT / 'docker/Dockerfile.records').read_text()
    assert 'prototype-lab/projects/project-records-room-poc/manage.py' in dockerfile
    assert 'rm -rf /app/domains/records/static/v2/fixtures' in dockerfile


def test_full_ci_and_nightly_include_records_acceptance():
    for name in ('deploy.yml', 'nightly-tests.yml'):
        workflow = (ROOT / '.github/workflows' / name).read_text()
        assert 'pytest tests/ prototype-lab/projects/project-records-room-poc/tests/' in workflow


def test_flat_container_app_serves_canonical_static_assets(tmp_path):
    import shutil
    container = tmp_path / 'app'
    shutil.copytree(ROOT / 'domains/records', container / 'domains/records')
    shutil.copyfile(LEGACY / 'app.py', container / 'app.py')
    # Execute the real launcher and application setup, replacing only the server
    # loop so this never binds a socket or leaves a background process.
    probe = """
import pathlib, runpy, sys
from flask import Flask
launcher, data = sys.argv[1:]
def inspect(app, *args, **kwargs):
    expected = pathlib.Path(launcher).parent/'domains/records/static/style.css'
    assert pathlib.Path(app.static_folder) == expected.parent
    response = app.test_client().get('/static/style.css', base_url='http://localhost:18880')
    assert response.status_code == 200
    assert response.data == expected.read_bytes()
Flask.run = inspect
sys.argv = [launcher, '--data-dir', data]
runpy.run_path(launcher, run_name='__main__')
"""
    result = run_python(['-c', probe, str(container / 'app.py'), str(tmp_path / 'data')],
                        tmp_path, repository_path=False)
    assert result.returncode == 0, result.stderr
