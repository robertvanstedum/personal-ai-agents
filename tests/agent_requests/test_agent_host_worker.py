from dataclasses import asdict
import time
from types import SimpleNamespace
import pytest
from scripts.mc_host import agent_worker as W
from tests.agent_requests.test_codex_desktop_queue import TARGET, SOURCE, IDENTITY


def setup(tmp_path):
    tmp_path.chmod(0o700)
    target=asdict(TARGET)
    source=asdict(SOURCE)
    source['authorization_ref']='dev:kept-owner-note:'+SOURCE.note_id
    config={'principal':SOURCE.principal,'environment':'dev','private_root':str(tmp_path),
            'targets':{TARGET.alias:{'target':target,'binary_identity':list(IDENTITY),
            'routing_proof_sha256':'a'*64,'native_return_proof_sha256':'b'*64,'codex_home':'/synthetic/.codex',
            'same_app_routing_verified':True,'authenticated_native_return_ready':True}}}
    descriptor={'execute':True,'request_ref':'11111111-1111-4111-8111-111111111111','nonce':'c'*64,
                'expires_at':time.time()+20,'target':target,'source':source,'action':'Status only.'}
    calls=[]
    def api(body):
        calls.append(body)
        return descriptor if body['phase'] in ('claim','observe') else {'state':'recorded'}
    class Observer:
        def __init__(self,enrollment):self.enrollment=enrollment
        def snapshot(self,baseline=None):
            return {'thread_id':TARGET.session_id,'target_revision':TARGET.revision,'observed_at':time.time(),'turns':[]}
    class Queue(W.DeadlineQueue):
        def deliver(self,target,envelope,*,timeout):
            from core.agent_requests.direct_contact import Delivery
            calls.append({'phase':'native-queue'})
            return Delivery('accepted',target.session_id)
    return config,descriptor,api,calls,Observer,Queue


def test_claim_queue_once_then_observe_never_resends(tmp_path):
    config,d,api,calls,observer,queue=setup(tmp_path)
    job={'mode':'claim','request_ref':d['request_ref']}
    assert W.one(config,api,job,observer_factory=observer,queue_factory=queue)=='dispatched'
    assert len([c for c in calls if c['phase']=='native-queue'])==1
    d['execute']=False
    assert W.one(config,api,dict(job,mode='observe'),observer_factory=observer,queue_factory=queue)=='dispatched'
    assert len([c for c in calls if c['phase']=='native-queue'])==1


def test_unproven_real_pickup_gate_never_queues(tmp_path):
    config,d,api,calls,observer,queue=setup(tmp_path)
    config['targets'][TARGET.alias]['authenticated_native_return_ready']=False
    assert W.one(config,api,{'mode':'claim','request_ref':d['request_ref']},observer_factory=observer,queue_factory=queue)=='not_started'
    assert not any(c['phase']=='native-queue' for c in calls)
    assert calls[-1]['outcome']['state']=='not_started'


@pytest.mark.parametrize('change',['owner','revision','thread','expired','mode','future'])
def test_descriptor_binding_checked_before_native_access(tmp_path,change):
    config,d,api,calls,observer,queue=setup(tmp_path)
    # Detach dictionaries because operator config and server are separate sources.
    from copy import deepcopy
    d['target']=deepcopy(d['target'])
    if change=='owner':d['source']['principal']='other'
    elif change=='revision':d['target']['revision']='other'
    elif change=='thread':d['target']['session_id']='22222222-2222-2222-2222-222222222222'
    elif change=='expired':d['expires_at']=time.time()-1
    elif change=='future':d['expires_at']=time.time()+100
    else:d['execute']=False
    with pytest.raises(W.Refused):W.one(config,api,{'mode':'claim','request_ref':d['request_ref']},observer_factory=observer,queue_factory=queue)
    assert not any(c['phase']=='native-queue' for c in calls)


@pytest.mark.parametrize('url',['https://example.com/internal/mc/v1/agent-worker',
    'http://localhost:8765/internal/mc/v1/agent-worker','http://user@127.0.0.1:8765/internal/mc/v1/agent-worker',
    'http://127.0.0.1:8765/other','http://127.0.0.1:8765/internal/mc/v1/agent-worker?x=1'])
def test_fixed_loopback_endpoint_no_redirect_or_proxy_destination(url):
    with pytest.raises(W.Refused):W.Endpoint(url,'t'*40)


def test_observe_without_local_intent_is_unknown_and_does_not_dispatch(tmp_path):
    config,d,api,calls,observer,queue=setup(tmp_path)
    d['execute']=False
    assert W.one(config,api,{'mode':'observe','request_ref':d['request_ref']},observer_factory=observer,queue_factory=queue)=='unknown'
    assert not any(c['phase']=='native-queue' for c in calls)


def test_observed_queue_uses_same_pinned_home_without_inherited_credentials(tmp_path,monkeypatch):
    from core.agent_requests import codex_desktop_queue as Q
    from core.agent_requests.codex_observed_queue import ObservedQueue
    from core.agent_requests.codex_read_observer import ReadEnrollment
    from core.agent_requests.direct_contact import Outbox
    from tests.agent_requests.test_codex_desktop_queue import binding
    monkeypatch.setattr(Q,'_binary',lambda p:IDENTITY)
    monkeypatch.setenv('SYNTHETIC_EXECUTOR_SECRET','must-not-be-in-child')
    tmp_path.chmod(0o700)
    observer=SimpleNamespace(enrollment=ReadEnrollment(TARGET,IDENTITY,'/synthetic/.codex'),snapshot=lambda:{
        'thread_id':TARGET.session_id,'target_revision':TARGET.revision,'turns':[]})
    seen=[]
    def run(argv,**kwargs):seen.append(kwargs);return SimpleNamespace(returncode=0)
    box=Outbox(tmp_path,{TARGET.alias:TARGET},ObservedQueue(binding(),observer,run=run))
    rid=box.enqueue(TARGET.alias,SOURCE,'Status only.')
    assert box.dispatch(rid)['state']=='dispatched'
    assert seen[0]['env']=={'HOME':'/synthetic','CODEX_HOME':'/synthetic/.codex','PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8'}
