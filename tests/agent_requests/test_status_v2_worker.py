from dataclasses import asdict
import pytest
from core.agent_requests.direct_contact import _hash,Refused
from core.agent_requests import ledger as L
from core.agent_requests.status_v2 import protocol_for
from scripts.mc_host import agent_worker as W
from tests.agent_requests.test_agent_host_worker import setup
from tests.agent_requests.test_codex_desktop_queue import TARGET


def versioned(tmp_path):
    config,d,api,calls,observer,queue=setup(tmp_path)
    rid=L.request_id_for(_hash({'target':asdict(TARGET),'source':d['source']}))
    d['reply_protocol']=protocol_for(TARGET,rid,'a'*48,'status_request')
    return config,d,api,calls,observer,queue


def test_v2_requires_explicit_worker_enrollment_gate_before_native_queue(tmp_path):
    config,d,api,calls,observer,queue=versioned(tmp_path)
    with pytest.raises(Refused):W.one(config,api,dict(mode='claim',request_ref=d['request_ref']),observer_factory=observer,queue_factory=queue)
    assert not any(c['phase']=='native-queue' for c in calls)
    config['targets'][TARGET.alias]['status_protocol_versions']=[1,2]
    assert W.one(config,api,dict(mode='claim',request_ref=d['request_ref']),observer_factory=observer,queue_factory=queue)=='dispatched'
    assert len([c for c in calls if c['phase']=='native-queue'])==1

@pytest.mark.parametrize('change',['mode','message','version','id'])
def test_protocol_drift_cannot_dispatch(tmp_path,change):
    config,d,api,calls,observer,queue=versioned(tmp_path)
    config['targets'][TARGET.alias]['status_protocol_versions']=[1,2]
    if change=='mode':d['reply_protocol']['mode']='clarification_request'
    elif change=='message':d['reply_protocol']['message']='different'
    elif change=='version':d['reply_protocol']['version']=True
    else:d['reply_protocol']['request_id']='r-'+'f'*24
    with pytest.raises(Refused):W.one(config,api,dict(mode='claim',request_ref=d['request_ref']),observer_factory=observer,queue_factory=queue)
    assert not any(c['phase']=='native-queue' for c in calls)
