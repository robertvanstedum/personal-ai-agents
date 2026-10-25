import json
from copy import deepcopy
import pytest
from core.agent_requests.status_v2 import message_for,correlate_status,correlate_versioned
from core.agent_requests.codex_desktop_queue import message_for as old_message
from core.agent_requests.codex_read_observer import correlate as old_correlate
from core.agent_requests.direct_contact import Refused,_hash
from tests.agent_requests.test_codex_desktop_queue import TARGET

CORR='a'*48
RID='r-'+'b'*24
REQUEST={'request_id':RID,'action':'Existing fixed status request.'}


def frame(report='Still implementing the approved build.'):
    return 'MC-STATUS:'+CORR+'\n'+json.dumps(dict(format='minimoi.status.v2',request_id=RID,correlation=CORR,report=report))+'\nMC-STATUS-END:'+CORR


def fixture():
    message=message_for(TARGET,REQUEST,CORR,version=2,mode='status_request')
    items=[dict(id='input',type='userMessage',text=message,sha256='c'*64),
           dict(id='status',type='agentMessage',phase='commentary',text=frame(),sha256=_hash(frame()))]
    snapshot=dict(thread_id=TARGET.session_id,target_revision=TARGET.revision,observed_at=300,
        turns=[dict(id='turn',status='inProgress',startedAt=100,completedAt=None,items=items)])
    baseline=dict(thread_id=TARGET.session_id,target_revision=TARGET.revision,items={})
    return snapshot,baseline,message


def test_legacy_bytes_hash_and_final_correlation_unchanged():
    assert message_for(TARGET,REQUEST,CORR)==old_message(TARGET,REQUEST,CORR)
    assert _hash(message_for(TARGET,REQUEST,CORR))==_hash(old_message(TARGET,REQUEST,CORR))
    s,b,m=fixture();m=old_message(TARGET,REQUEST,CORR)
    s['turns'][0].update(status='completed',completedAt=200)
    s['turns'][0]['items'][0]['text']=m
    s['turns'][0]['items'][1].update(phase='final_answer',text='MC-RESULT:'+CORR+'\nOld report')
    assert correlate_versioned(s,b,m,CORR,RID)==old_correlate(s,b,m,CORR)


def test_v2_fixed_scope_preservation_no_model_freeform():
    text=message_for(TARGET,dict(REQUEST,action='untrusted new work'),CORR,version=2,mode='status_request')
    assert 'untrusted new work' not in text
    assert 'does not pause, cancel, replace' in text
    assert 'then continue the already-authorized work' in text
    assert 'No final response is required' in text


def test_active_turn_commentary_two_observations_is_status_not_completion():
    s,b,m=fixture();first=correlate_status(s,b,m,CORR,RID)
    assert first['state']=='status_candidate'
    s['observed_at']=302
    answer=correlate_status(s,b,m,CORR,RID,first)
    assert answer['state']=='status_report' and answer['native_turn_status']=='inProgress'
    assert not answer['build_complete'] and not answer['native_dispatch_required']


@pytest.mark.parametrize('kind',['user','tool','reasoning','final','quoted','old','before_input','partial'])
def test_nonmatching_or_partial_text_does_not_receipt(kind):
    s,b,m=fixture();item=s['turns'][0]['items'][1]
    if kind in ('user','tool','reasoning'):item['type']=kind
    elif kind=='final':item['phase']='final_answer'
    elif kind=='quoted':item['text']='Quote: '+item['text']
    elif kind=='old':b['items']['status']=None
    elif kind=='before_input':s['turns'][0]['items'].reverse()
    else:item['text']=item['text'][:-10]
    assert correlate_status(s,b,m,CORR,RID)['state']=='unverified'


def test_changed_stream_requires_new_stable_observations():
    s,b,m=fixture();first=correlate_status(s,b,m,CORR,RID)
    s['observed_at']=302;s['turns'][0]['items'][1].update(text=frame('Updated status'),sha256='d'*64)
    second=correlate_status(s,b,m,CORR,RID,first)
    assert second['state']=='status_candidate'
    s['observed_at']=304
    assert correlate_status(s,b,m,CORR,RID,second)['state']=='status_report'


@pytest.mark.parametrize('change',['thread','revision','duplicate','source','request'])
def test_binding_ambiguity_and_changed_source_refuse(change):
    s,b,m=fixture()
    if change=='thread':s['thread_id']='other'
    elif change=='revision':s['target_revision']='other'
    elif change=='duplicate':s['turns'][0]['items'].append(dict(s['turns'][0]['items'][1],id='second'))
    elif change=='source':b['items']['input']='e'*64
    else:s['turns'][0]['items'][1]['text']=frame().replace(RID,'r-'+'f'*24)
    with pytest.raises(Refused):correlate_status(s,b,m,CORR,RID)

@pytest.mark.parametrize('bad',[True,float('inf'),float('-inf'),float('nan'),-1])
def test_nonfinite_or_bool_observation_refused(bad):
    s,b,m=fixture();s['observed_at']=bad
    with pytest.raises(Refused):correlate_status(s,b,m,CORR,RID)

@pytest.mark.parametrize('report',['','   ','x'*4001])
def test_empty_or_oversized_report_refused(report):
    s,b,m=fixture();s['turns'][0]['items'][1]['text']=frame(report)
    with pytest.raises(Refused):correlate_status(s,b,m,CORR,RID)

@pytest.mark.parametrize('state',['inProgress','interrupted','completed','failed'])
def test_turn_state_never_changes_status_into_completion(state):
    s,b,m=fixture();s['turns'][0]['status']=state
    first=correlate_status(s,b,m,CORR,RID);s['observed_at']+=2
    out=correlate_status(s,b,m,CORR,RID,first)
    assert out['state']=='status_report' and out['build_complete'] is False
    assert out['native_turn_status']==state


def test_replayed_observation_and_bool_version_refused():
    s,b,m=fixture();first=correlate_status(s,b,m,CORR,RID)
    with pytest.raises(Refused):correlate_status(s,b,m,CORR,RID,first)
    with pytest.raises(Refused):message_for(TARGET,REQUEST,CORR,version=True)


def test_same_candidate_less_than_one_second_waits_without_resetting_first():
    s,b,m=fixture();first=correlate_status(s,b,m,CORR,RID)
    s['observed_at']+=.5
    second=correlate_status(s,b,m,CORR,RID,first)
    assert second['state']=='status_candidate' and second['first_observed_at']==300
    s['observed_at']=301.1
    assert correlate_status(s,b,m,CORR,RID,second)['state']=='status_report'
