"""Durable commentary observations with synthetic reader and queue only."""
import json,time
from copy import deepcopy
from types import SimpleNamespace
import pytest
from core.agent_requests.direct_contact import Outbox,Delivery,_hash,_read,Refused
from core.agent_requests import ledger as L
from core.agent_requests.codex_observed_queue import ObservedQueue
from core.agent_requests.status_v2 import protocol_for,request_message
from tests.agent_requests.test_codex_desktop_queue import TARGET,SOURCE,binding,IDENTITY


def setup(tmp_path):
    tmp_path.chmod(0o700)
    rid=L.request_id_for(_hash({'target':TARGET.__dict__,'source':SOURCE.__dict__}))
    protocol=protocol_for(TARGET,rid,'a'*48,'status_request')
    snapshots=[];sent=[]
    class Observer:
        enrollment=SimpleNamespace(target=TARGET,binary_identity=IDENTITY,codex_home='/synthetic/.codex')
        def snapshot(self,baseline=None):
            if baseline is None:return dict(thread_id=TARGET.session_id,target_revision=TARGET.revision,observed_at=100,turns=[])
            value=snapshots.pop(0)
            if isinstance(value,Exception):raise value
            return value
    observer=Observer()
    class Queue(ObservedQueue):
        def deliver(self,target,envelope,*,timeout):
            sent.append(request_message(target,envelope['request'],envelope['correlation']))
            return Delivery('accepted',target.session_id)
    queue=Queue(binding(),observer)
    box=Outbox(tmp_path,{TARGET.alias:TARGET},queue,clock=lambda:500)
    box.enqueue(TARGET.alias,SOURCE,'Fixed legacy action',reply_protocol=protocol)
    assert box.dispatch(rid)['state']=='dispatched'
    def snapshot(at,report='A'):
        frame='MC-STATUS:'+protocol['correlation']+'\n'+json.dumps(dict(format='minimoi.status.v2',request_id=rid,correlation=protocol['correlation'],report=report))+'\nMC-STATUS-END:'+protocol['correlation']
        items=[dict(id='input',type='userMessage',text=protocol['message'],sha256='b'*64)]
        if report is not None:items.append(dict(id='status',type='agentMessage',phase='commentary',text=frame,sha256=_hash(frame)))
        return dict(thread_id=TARGET.session_id,target_revision=TARGET.revision,observed_at=at,
            turns=[dict(id='turn',startedAt=100,status='inProgress',completedAt=None,items=items)])
    return box,rid,observer,snapshots,sent,snapshot,protocol

@pytest.mark.parametrize('interlude',['B',None])
def test_durable_last_pointer_resets_A_B_A_and_missing_across_restart(tmp_path,interlude):
    box,rid,observer,snapshots,sent,snapshot,protocol=setup(tmp_path)
    for at,report in [(300,'A'),(302,interlude),(304,'A')]:
        snapshots.append(snapshot(at,report));assert box.observe(rid,observer)['state']=='dispatched'
        box=Outbox(tmp_path,{TARGET.alias:TARGET},clock=lambda:500)
    snapshots.append(snapshot(306,'A'))
    assert box.observe(rid,observer)['state']=='status_report'
    assert box.dispatch(rid)['state']=='status_report' and len(sent)==1
    folder=box.root/'requests'/rid
    assert not (folder/'native-result.json').exists()
    assert _read(folder/'native-status.json')['build_complete'] is False
    assert sent==[protocol['message']]
    # Immutable observed report does not call the reader again.
    assert box.observe(rid,observer)['state']=='status_report'


def test_same_snapshot_timestamp_never_counts_twice(tmp_path):
    box,rid,observer,snapshots,sent,snapshot,protocol=setup(tmp_path)
    snapshots.extend([snapshot(300),snapshot(300)])
    box.observe(rid,observer)
    with pytest.raises(Refused):box.observe(rid,observer)
    assert not (box.root/'requests'/rid/'native-status.json').exists()
    assert len(sent)==1


def test_protocol_message_and_mode_tamper_refused_before_send(tmp_path):
    box,rid,observer,snapshots,sent,snapshot,protocol=setup(tmp_path)
    changed=deepcopy(protocol);changed['mode']='clarification_request'
    with pytest.raises(Refused):box.enqueue(TARGET.alias,SOURCE,'Fixed legacy action',reply_protocol=changed)
    assert len(sent)==1


def test_failed_observation_invalidates_candidate_durably_before_restart(tmp_path):
    box,rid,observer,snapshots,sent,snapshot,protocol=setup(tmp_path)
    snapshots.append(snapshot(300));box.observe(rid,observer)
    snapshots.append(OSError('synthetic read failure'))
    with pytest.raises(OSError):box.observe(rid,observer)
    box=Outbox(tmp_path,{TARGET.alias:TARGET},clock=lambda:500)
    snapshots.append(snapshot(502))
    assert box.observe(rid,observer)['state']=='dispatched'
    snapshots.append(snapshot(504))
    assert box.observe(rid,observer)['state']=='status_report'
    assert len(sent)==1


def test_corrupt_cached_status_cannot_claim_report_on_replay(tmp_path):
    box,rid,observer,snapshots,sent,snapshot,protocol=setup(tmp_path)
    snapshots.extend([snapshot(300),snapshot(302)])
    box.observe(rid,observer);box.observe(rid,observer)
    path=box.root/'requests'/rid/'native-status.json'
    proof=_read(path);proof['build_complete']=True
    path.write_text(json.dumps(proof))
    with pytest.raises(Refused):box.status(rid)
    with pytest.raises(Refused):box.observe(rid,observer)
    assert len(sent)==1
