"""Current-source rebase regression: no daemon, runtime, credentials or data."""
from pathlib import Path
import subprocess
import pytest
ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('rel',['scripts/deploy.sh','scripts/operations/deploy_scoped_release.sh'])
def test_release_receipts_preserved_and_records_admission_precedes_them(rel):
    new=(ROOT/rel).read_text()
    receipts=[line for line in new.splitlines()if 'python3 /opt/minimoi/scripts/release_status.py'in line]
    assert len(receipts)==2
    assert ' begin 'in receipts[0]and ' finish 'in receipts[1]
    assert new.index(receipts[0])<new.index('aws ecr get-login-password')<new.index(receipts[1])
    if rel=='scripts/deploy.sh':
        assert all('"$MINIMOI_IMAGE_TAG" portal curator german portuguese system-bot cos-bot cos-scheduler cos-agent-a model-gateway'in line for line in receipts)
    else:
        assert all('"$IMAGE_TAG" "${SERVICES[@]}"'in line for line in receipts)
    assert new.index('RECORDS_ENV=')<new.index('release_status.py begin')<new.index('aws ecr get-login-password')
    subprocess.run(['/bin/bash','-n',str(ROOT/rel)],check=True,capture_output=True,timeout=5)

def test_workflow_preserves_current_release_manifest_and_original_steps():
    new=(ROOT/'.github/workflows/deploy.yml').read_text()
    start=new.index('      - name: Push optional Records deployment support to EC2')
    end=new.index('      - name: Deploy on EC2',start)
    support=new[start:end]
    assert 'release_status.py'not in support
    assert new.index('RELEASE_MANIFEST=')<start<end
    assert 'RELEASE_MANIFEST=$(python3 scripts/operations/release_status.py manifest' in new

def test_records_is_never_in_default_production_graph():
    import yaml
    backend=yaml.safe_load((ROOT/'docker-compose.records-production.yml').read_text())
    assert backend['name']=='minimoi-records-production'
    assert set(backend['services'])=={'minimoi-records'}
    main=yaml.safe_load((ROOT/'docker-compose.prod.yml').read_text())
    assert 'minimoi-records'not in main['services']
