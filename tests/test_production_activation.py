import importlib.util
import json
import os
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('activation_reader', ROOT/'scripts/operations/production_activation.py')
a = importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

def document():
    return {'schema':'minimoi.production-activation.v1','environment':{k:('' if k in a.KEYS[4:] else '0') for k in a.KEYS}}

def install(tmp_path, doc=None):
    tmp_path.chmod(0o700)
    p=tmp_path/'production-activation.json'
    p.write_text(json.dumps(document() if doc is None else doc));p.chmod(0o600)
    return p

def test_absent_no_override(tmp_path):
    assert a.read(tmp_path/'absent'/'cfg',trusted_uid=os.getuid()) is None
    tmp_path.chmod(0o700)
    assert a.read(tmp_path/'cfg',trusted_uid=os.getuid()) is None

def test_valid_six_only_and_voice_relations(tmp_path):
    d=document();d['environment'].update({a.KEYS[0]:'1',a.KEYS[1]:'1',a.KEYS[2]:'1',a.KEYS[3]:'1',a.KEYS[4]:'on',a.KEYS[5]:'5000000'})
    assert a.read(install(tmp_path,d),trusted_uid=os.getuid())==d['environment']

@pytest.mark.parametrize('key,value',[('IMAGE_TAG','evil'),('DATABASE_URL','secret'),('MINIMOI_MC_DOMAIN_HEALTH_PRODUCTION','1')])
def test_extra_key_refused(key,value):
    d=document();d['environment'][key]=value
    with pytest.raises(a.Refused): a.validate(d)

@pytest.mark.parametrize('amount',['0','20000001','1.0','1e6','１２','$(touch /tmp/bad)','\nBAD=x'])
def test_allowance_grammar_and_bounds(amount):
    d=document();d['environment'][a.KEYS[5]]=amount
    with pytest.raises(a.Refused): a.validate(d)

@pytest.mark.parametrize('which',[a.KEYS[2],a.KEYS[3]])
def test_incomplete_voice_activation_refused(which):
    d=document();d['environment'][which]='1'
    with pytest.raises(a.Refused):a.validate(d)

def test_files_private_regular_no_links(tmp_path):
    p=install(tmp_path)
    p.chmod(0o644)
    with pytest.raises(a.Refused):a.read(p,trusted_uid=os.getuid())
    p.chmod(0o600);link=tmp_path/'link';link.symlink_to(p)
    with pytest.raises(OSError):a.read(link,trusted_uid=os.getuid())
    link.unlink();os.link(p,link)
    with pytest.raises(a.Refused):a.read(p,trusted_uid=os.getuid())

def test_wrong_owner_parent_and_duplicate_json(tmp_path):
    p=install(tmp_path)
    with pytest.raises(a.Refused):a.read(p,trusted_uid=os.getuid()+1)
    tmp_path.chmod(0o755)
    with pytest.raises(a.Refused):a.read(p,trusted_uid=os.getuid())
    tmp_path.chmod(0o700);p.write_text('{"schema":"x","schema":"x"}')
    with pytest.raises(a.Refused):a.read(p,trusted_uid=os.getuid())

def test_both_deploy_paths_fail_before_mutations_and_never_eval():
    for path in ['scripts/deploy.sh','scripts/operations/deploy_scoped_release.sh']:
        source=(ROOT/path).read_text()
        assert source.index('ACTIVATION_ENV="$(python3 /opt/minimoi/scripts/production_activation.py)"') < source.index('aws ecr get-login-password')
        assert 'export "$activation_key=$activation_value"' in source
        assert not any(line.lstrip().startswith('eval ') for line in source.splitlines())
    workflow=(ROOT/'.github/workflows/deploy.yml').read_text()
    assert 'tar -czf - -C scripts/operations production_activation.py curator_preserve.sh' in workflow
    assert workflow.index('production_activation.py curator_preserve.sh') < workflow.index('- name: Deploy on EC2')


def test_real_shell_loader_exports_exact_values_and_failure_stops(tmp_path):
    import subprocess
    import sys
    helper=tmp_path/'reader.py'
    helper.write_text("print('MINIMOI_MC_BOARD_OUTCOMES=1')\nprint('MINIMOI_COS_WRITE_GUARD=on')\n")
    for rel in ['scripts/deploy.sh','scripts/operations/deploy_scoped_release.sh']:
        source=(ROOT/rel).read_text()
        start=source.index('ACTIVATION_ENV=')
        end=source.index('\nfi',start)+3
        block=source[start:end].replace('python3 /opt/minimoi/scripts/production_activation.py',f'{sys.executable} {helper}')
        result=subprocess.run(['bash','-c','set -euo pipefail\n'+block+'\nprintf "%s|%s" "$MINIMOI_MC_BOARD_OUTCOMES" "$MINIMOI_COS_WRITE_GUARD"'],capture_output=True,text=True,timeout=5)
        assert result.returncode==0 and result.stdout=='1|on'
        helper.write_text('import sys;sys.exit(1)\n')
        result=subprocess.run(['bash','-c','set -euo pipefail\n'+block+'\nprintf MUTATION'],capture_output=True,text=True,timeout=5)
        assert result.returncode==1 and 'MUTATION' not in result.stdout
        helper.write_text("print('MINIMOI_MC_BOARD_OUTCOMES=1')\nprint('MINIMOI_COS_WRITE_GUARD=on')\n")


def test_ci_installs_manual_script_to_same_canonical_path():
    workflow=(ROOT/'.github/workflows/deploy.yml').read_text()
    section=workflow.split('- name: Push Curator preservation kit to EC2',1)[1].split('- name: Deploy on EC2',1)[0]
    assert 'MANUAL_DEPLOY=$(base64 -w 0 scripts/deploy.sh)' in section
    assert 'echo $MANUAL_DEPLOY | base64 -d > /opt/minimoi/deploy.sh' in section
    assert 'chmod 755 /opt/minimoi/deploy.sh' in section
    assert '/opt/minimoi/scripts/deploy.sh' not in workflow
    assert 'bash /opt/minimoi/deploy.sh' in (ROOT/'scripts/deploy.sh').read_text()


def test_absent_under_existing_public_parent_preserves_no_override(tmp_path):
    tmp_path.chmod(0o755)
    assert a.read(tmp_path/'production-activation.json',trusted_uid=os.getuid()) is None
    assert a.read(tmp_path/'production-activation.json',trusted_uid=os.getuid()+1) is None
    p=tmp_path/'production-activation.json';p.write_text(json.dumps(document()));p.chmod(0o600)
    with pytest.raises(a.Refused):a.read(p,trusted_uid=os.getuid())
