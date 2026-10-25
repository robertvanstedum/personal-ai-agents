import copy
from datetime import timedelta
import pytest
from scripts.operations.dev_release_publish import (VOLUME,TARGET,LABELS,WORKER,fresh,single_mount_delta,volume_valid,container_command)
from scripts.operations.dev_release_status import dev_observation
from scripts.operations.release_status import EMPTY,SERVICES
from datetime import datetime,timezone

NOW=datetime(2026,10,24,3,30,tzinfo=timezone.utc)
IMAGE='sha256:'+'b'*64

def doc():
    rows={s:dict(EMPTY) for s in SERVICES}
    rows['portal'].update(image_id=IMAGE,running=True)
    return dev_observation('finish','a'*40,{'portal':IMAGE},rows,now=NOW)

@pytest.mark.parametrize('age',[0,60])
def test_only_fresh_observations(age):
    assert fresh(doc(),NOW+timedelta(seconds=age))['environment']=='dev'

@pytest.mark.parametrize('age',[-1,61,86400])
def test_stale_or_future_samples_are_not_republished(age):
    with pytest.raises(ValueError):fresh(doc(),NOW+timedelta(seconds=age))

def test_production_doc_refused():
    d=doc();d['environment']='production'
    with pytest.raises(ValueError):fresh(d,NOW)

def configs():
    old={'services':{'portal':{'image':IMAGE,'environment':{'A':'unchanged'},'volumes':[{'type':'bind','source':'/data','target':'/data','read_only':True}]}},'volumes':{}}
    new=copy.deepcopy(old)
    new['services']['portal']['volumes'].append({'type':'volume','source':VOLUME,'target':TARGET,'read_only':True})
    new['volumes'][VOLUME]={'name':VOLUME,'external':True}
    return old,new

def test_only_one_readonly_mount_is_allowed():
    assert single_mount_delta(*configs())

@pytest.mark.parametrize('change',['env','image','port','rw','bind','service','extra_mount','volume_options'])
def test_other_compose_changes_rejected(change):
    old,new=configs();p=new['services']['portal']
    if change=='env':p['environment']['A']='changed'
    if change=='image':p['image']='latest'
    if change=='port':p['ports']=['5013:5013']
    if change=='rw':p['volumes'][-1]['read_only']=False
    if change=='bind':p['volumes'][-1]['type']='bind'
    if change=='service':new['services']['extra']={}
    if change=='extra_mount':p['volumes'].append({'target':'/extra'})
    if change=='volume_options':new['volumes'][VOLUME]['driver_opts']={'device':'/'}
    with pytest.raises(ValueError):single_mount_delta(old,new)

@pytest.mark.parametrize('change',[{'Name':'other'},{'Driver':'nfs'},{'Options':{'device':'/'}},{'Labels':{}}])
def test_foreign_or_host_bound_volume_is_rejected(change):
    row={'Name':VOLUME,'Driver':'local','Options':None,'Labels':LABELS};row.update(change)
    with pytest.raises(ValueError):volume_valid(row)

def test_scoped_volume_and_pinned_container():
    volume_valid({'Name':VOLUME,'Driver':'local','Options':{},'Labels':LABELS})
    cmd=container_command(IMAGE,'publish')
    assert '--network=none' in cmd and '--read-only' in cmd and '--cap-drop=ALL' in cmd
    assert '--pull=never' in cmd and cmd.count('--mount')==1 and WORKER in cmd
    assert 'volume-nocopy' in cmd[cmd.index('--mount')+1]
    for image in ('latest','sha256:bad'):
        with pytest.raises(ValueError):container_command(image,'publish')
