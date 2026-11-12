"""Production's delivery record (#536): what MC reads at /minimoi/docs/delivery/PRODUCTION.md.

Fixture release observations in a temporary folder; no Docker, no host, no network.
"""
import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('write_production_delivery_record',
                                              ROOT / 'scripts/operations/write_production_delivery_record.py')
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)

COMMIT = 'dfa932d6' + 'a' * 32
REG = '332704997792.dkr.ecr.us-east-1.amazonaws.com/minimoi'


def observation(status='selected_images_observed', tag='dfa932d6'):
    return {'environment': 'production', 'status': status, 'requested_tag': tag, 'observed_at': '2026-11-12T20:00:00+00:00',
            'selected': ['portal'], 'current': {'portal': {'image': f'{REG}/portal:{tag}', 'running': True},
                                                'curator': {'image': None, 'running': None}}}


def manifest(commit=COMMIT):
    return {'format': 'minimoi.released-source-manifest/1', 'source_commit': commit, 'subject': 'Merge PR: fast-follow',
            'version': '1.2', 'capabilities': [{'id': 'x', 'summary': 'MC reads its GitHub feed again', 'services': ['portal']}]}


@pytest.fixture
def roots(tmp_path):
    status, docs = tmp_path / 'release-status', tmp_path / 'docs'
    status.mkdir(mode=0o755)
    docs.mkdir(mode=0o755)
    return status, docs


def put(folder, name, doc):
    path = folder / name
    path.write_text(json.dumps(doc))
    path.chmod(0o644)


def test_a_confirmed_release_writes_a_readable_record(roots):
    status, docs = roots
    put(status, 'status.json', observation())
    put(status, 'manifest.json', manifest())
    path = W.write(status, docs, at=1_800_000_000)
    assert path == docs / 'delivery' / 'PRODUCTION.md'
    text = path.read_text()
    assert 'What is in production' in text and '`dfa932d6` on `main`: Merge PR: fast-follow' in text
    assert 'MC reads its GitHub feed again' in text and '- portal: `dfa932d6`' in text and '- curator: `not running`' in text
    # Agents read it, never write it: folder 0755, files 0644.
    assert stat.S_IMODE((docs / 'delivery').stat().st_mode) == 0o755
    assert all(stat.S_IMODE((docs / 'delivery' / n).stat().st_mode) == 0o644 for n in ('PRODUCTION.md', 'PRODUCTION.json'))
    doc = json.loads((docs / 'delivery' / 'PRODUCTION.json').read_text())
    assert doc['commit'] == COMMIT and doc['selected'] == ['portal']
    W.write(status, docs, at=1_800_000_100)
    assert len((docs / 'delivery' / 'history.jsonl').read_text().splitlines()) == 2


def test_an_unconfirmed_release_writes_nothing(roots):
    status, docs = roots
    put(status, 'status.json', observation(status='selected_image_check_failed'))
    with pytest.raises(W.Refused):
        W.write(status, docs)
    assert not (docs / 'delivery').exists()
    assert W.main(['--status-root', str(status), '--docs-root', str(docs)]) == 1


def test_no_observation_yet_writes_nothing(roots):
    status, docs = roots
    with pytest.raises(W.Refused, match='no release observation'):
        W.write(status, docs)


def test_a_manifest_from_another_release_is_not_claimed(roots):
    status, docs = roots
    put(status, 'status.json', observation(tag='0123abcd'))
    put(status, 'manifest.json', manifest())
    text = W.write(status, docs).read_text()
    assert COMMIT[:8] not in text.split('Running now')[0] and 'not recorded for this release' in text


def test_a_group_writable_input_is_refused(roots):
    status, docs = roots
    put(status, 'status.json', observation())
    (status / 'status.json').chmod(0o666)
    with pytest.raises(W.Refused):
        W.write(status, docs)


def test_mc_sees_the_record_read_only_and_deploys_write_it():
    import yaml
    mc = yaml.safe_load((ROOT / 'docker-compose.mc.prod.yml').read_text())
    volumes = [v for s in mc['services'].values() for v in s.get('volumes', []) if isinstance(v, str)]
    assert '/opt/minimoi/docs:/minimoi/docs:ro' in volumes                        # read-only, and no wider
    entries = {e['repo']: e for e in json.loads((ROOT / 'config/host_scripts.json').read_text())['entries']}
    assert entries['scripts/operations/write_production_delivery_record.py']['ship'] is True
    deploy = (ROOT / 'scripts/operations/deploy_scoped_release.sh').read_text()
    finish = deploy.index('release_status.py finish')
    assert deploy.index('write_production_delivery_record.py', finish) > finish   # only after the finish observation
