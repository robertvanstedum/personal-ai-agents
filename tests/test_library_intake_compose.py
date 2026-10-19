"""Exercise real Compose rendering with synthetic secrets and no Docker daemon."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def render(tmp_path, *, staging=False, enabled=False, reader=False):
    docker = shutil.which('docker')
    if not docker:
        pytest.skip('Docker Compose CLI not installed')
    if subprocess.run([docker, 'compose', 'version'], capture_output=True).returncode:
        pytest.skip('Docker Compose plugin not installed')
    prod = ROOT / 'docker-compose.prod.yml'
    dev = ROOT / 'docker-compose.staging.yml'
    text = prod.read_text() + dev.read_text()
    env = {'PATH': os.environ.get('PATH', '')}
    for name in re.findall(r'\$\{([A-Z_0-9]+):\?', text):
        env[name] = 'synthetic-placeholder'
    env.update(MINIMOI_ROOT=str(tmp_path), MINIMOI_IMAGE_TAG='synthetic',
               MINIMOI_LIBRARY_INTAKE_PRODUCTION='1' if enabled else '0')
    (tmp_path / '.env').write_text('PORTAL_SECRET_KEY=synthetic-master-0123456789abcdef0123456789\n')
    if enabled:
        env['MINIMOI_CURATOR_INTAKE_ENV_FILE'] = 'curator-library-intake.env'
        (tmp_path / 'curator-library-intake.env').write_text(
            'MINIMOI_LIBRARY_INTAKE_DERIVED_KEY=' + 'a' * 64 + '\n')
    if reader:
        env['MINIMOI_VAULT_ASK_URL'] = 'http://vault-ask:8771'
        env['MINIMOI_VAULT_READER_ENV_FILE'] = 'vault-reader.env'
        (tmp_path / 'vault-reader.env').write_text('MINIMOI_VAULT_ASK_TOKEN=synthetic-reader-token\n')
    args = [docker, 'compose', '-p', 'intake-contract-test', '--env-file',
            str(tmp_path / '.env'), '-f', str(prod)]
    if staging:
        # lib.sh's command-scoped assignment must override a production filename.
        lib = (ROOT / 'scripts/staging/lib.sh').read_text()
        assert 'MINIMOI_CURATOR_INTAKE_ENV_FILE=.env \\\n' in lib
        env['MINIMOI_CURATOR_INTAKE_ENV_FILE'] = '.env'
        assert 'MINIMOI_VAULT_READER_ENV_FILE=.env' in lib
        env['MINIMOI_VAULT_READER_ENV_FILE'] = '.env'
        args += ['-f', str(dev)]
    result = subprocess.run(args + ['config', '--format', 'json'], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)['services']


def test_default_off_needs_no_curator_key_file(tmp_path):
    services = render(tmp_path)
    assert not (tmp_path / 'curator-library-intake.env').exists()
    for service in ('portal', 'curator'):
        assert services[service]['environment']['MINIMOI_LIBRARY_INTAKE'] == '0'
    assert services['curator']['environment']['PORTAL_SECRET_KEY'] == ''
    assert services['portal']['environment']['PORTAL_SECRET_KEY'].startswith('synthetic-master')


def test_key_is_only_loaded_into_curator(tmp_path):
    services = render(tmp_path, enabled=True)
    for name, service in services.items():
        env = service.get('environment', {})
        assert ('MINIMOI_LIBRARY_INTAKE_DERIVED_KEY' in env) == (name == 'curator')
    for service in ('portal', 'curator'):
        assert services[service]['environment']['MINIMOI_LIBRARY_INTAKE'] == '1'
    assert services['curator']['environment']['PORTAL_SECRET_KEY'] == ''


def test_staging_stays_off_with_production_activation_in_environment(tmp_path):
    services = render(tmp_path, staging=True, enabled=True)
    for service in ('portal', 'curator'):
        env = services[service]['environment']
        assert env['MINIMOI_LIBRARY_INTAKE'] == '0'
        assert 'MINIMOI_LIBRARY_INTAKE_DERIVED_KEY' not in env


def test_reader_secret_only_reaches_mc_consumer(tmp_path):
    services = render(tmp_path, reader=True)
    for name, service in services.items():
        token = service.get('environment', {}).get('MINIMOI_VAULT_ASK_TOKEN')
        assert bool(token) == (name == 'cos-scheduler')
        if token:
            assert token == 'synthetic-reader-token'


def test_staging_excludes_production_reader_file(tmp_path):
    services = render(tmp_path, staging=True, reader=True)
    for service in services.values():
        assert not service.get('environment', {}).get('MINIMOI_VAULT_ASK_TOKEN')
    assert not services['cos-scheduler']['environment']['MINIMOI_VAULT_ASK_URL']
