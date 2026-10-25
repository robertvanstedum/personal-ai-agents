import hashlib
import json
import time
import pytest
from core.agent_requests.task_watch import CHUNK, encoded, sha
from scripts.mc_host.agent_worker import task_one, Refused
from tests.agent_requests.test_agent_host_worker import setup
from tests.agent_requests.test_task_watch import SPEC


def fixture(tmp_path):
    config,d,_,calls,_,_=setup(tmp_path)
    checkpoint={'large':'x'*18000}
    raw=encoded(checkpoint)
    descriptor=dict(subscription_ref='11111111-1111-4111-8111-111111111111',nonce='n'*64,expires_at=time.time()+115,
        target=d['target'],principal=config['principal'],environment=config['environment'],spec=SPEC,
        checkpoint_bytes=len(raw),checkpoint_sha256=hashlib.sha256(raw).hexdigest())
    staged=[]
    def api(body):
        calls.append(body)
        phase=body['phase']
        if phase=='task_begin':return descriptor
        if phase=='task_chunk':return dict(offset=body['offset'],data=raw[body['offset']:body['offset']+CHUNK].decode())
        if phase=='task_stage':staged.append(body['data']);return {'state':'staged'}
        assert phase=='task_commit'
        assert hashlib.sha256(''.join(staged).encode()).hexdigest()==body['sha256']
        return {'state':'armed'}
    class Observer:
        def __init__(self,e):pass
        def step(self,state,spec):
            assert state==checkpoint and spec==SPEC
            return {'checkpoint':state,'proof':None,'state':'armed'}
    job={'mode':'task_observe','subscription_ref':descriptor['subscription_ref']}
    return config,descriptor,api,calls,Observer,job


def test_chunks_exact_whole_hash_and_no_queue_path(tmp_path):
    config,d,api,calls,observer,job=fixture(tmp_path)
    assert task_one(config,api,job,observer_factory=observer)=='armed'
    assert len([c for c in calls if c['phase']=='task_chunk'])==4
    assert len([c for c in calls if c['phase']=='task_stage'])==4
    assert all(c['phase'].startswith('task_') and len(json.dumps(c).encode())<16384 for c in calls)


@pytest.mark.parametrize('change',['digest','owner','target','expired','oversize'])
def test_changed_descriptor_refuses_before_checkpoint_commit(tmp_path,change):
    config,d,api,calls,observer,job=fixture(tmp_path)
    if change=='digest':d['checkpoint_sha256']='f'*64
    elif change=='owner':d['principal']='other'
    elif change=='target':d['target']=dict(d['target'],revision='other')
    elif change=='expired':d['expires_at']=time.time()-1
    else:d['checkpoint_bytes']=512*1024+1
    with pytest.raises(Refused):task_one(config,api,job,observer_factory=observer)
    assert not any(c['phase']=='task_commit' for c in calls)


def test_missing_chunk_never_commits(tmp_path):
    config,d,api,calls,observer,job=fixture(tmp_path)
    def fail(body):
        if body['phase']=='task_chunk' and body['offset']==CHUNK:raise OSError('synthetic lost response')
        return api(body)
    with pytest.raises(OSError):task_one(config,fail,job,observer_factory=observer)
    assert not any(c['phase']=='task_commit' for c in calls)
