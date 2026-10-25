"""Actual native interrupted/null -> completed reconciliation, synthetic only."""
from copy import deepcopy
import pytest
from core.agent_requests.task_watch import anchor_lifecycle
from core.agent_requests.direct_contact import Refused
from tests.agent_requests.test_task_watch import observer,turn,item,SPEC


def provisional(items=()):
    value=turn('anchor',items,status='interrupted');value['completedAt']=None;return value


def test_same_anchor_reconciliation_rereads_all_items_preserves_exclusions_and_settles():
    user=dict(id='owner',type='userMessage',content=[dict(type='text',text='Authorized build')])
    o,r=observer([provisional([user,item('excluded')])]);state=o.step(None,SPEC)['checkpoint'];original=deepcopy(state)
    r.turns[0]=turn('anchor',[user,item('excluded'),item('new')])
    result=o.step(state,SPEC)
    assert result['proof']['item_id']=='new' and result['checkpoint']['batch_head'] is None
    assert result['checkpoint']['excluded']==original['excluded']
    assert state==original
    assert any(m=='thread/items/list' for m,a in r.calls)


def test_post_baseline_stream_seen_interrupted_remains_eligible_when_completed():
    o,r=observer([turn('anchor',status='inProgress')]);state=o.step(None,SPEC)['checkpoint']
    r.turns[0]=provisional([item('stream')]);state=o.step(state,SPEC)['checkpoint']
    assert 'stream' in state['items'] and not state['excluded']
    r.turns[0]=turn('anchor',[item('stream')])
    result=o.step(state,SPEC)
    assert result['proof']['item_id']=='stream'


def test_reconciled_done_waits_for_entire_frozen_batch_and_conflicting_newer_report():
    o,r=observer([provisional()]);state=o.step(None,SPEC)['checkpoint']
    r.turns=[turn('newer',[item('block',outcome='blocked')]),turn('anchor',[item('done')])]
    result=o.step(state,SPEC)
    assert result['proof'] is None and result['checkpoint']['pending_done']['item_id']=='done'
    assert result['checkpoint']['batch_head']=='newer'
    with pytest.raises(Refused,match='conflicting'):o.step(result['checkpoint'],SPEC)

@pytest.mark.parametrize('change',['missing','user','start','token','duplicate'])
def test_reconciliation_never_waives_source_or_report_binding(change):
    user=dict(id='owner',type='userMessage',content=[dict(type='text',text='Original')])
    o,r=observer([provisional([user])]);state=o.step(None,SPEC)['checkpoint']
    current=turn('anchor',[deepcopy(user),item('new')]);r.turns[0]=current
    if change=='missing':current['items'].pop(0)
    elif change=='user':current['items'][0]['content'][0]['text']='Changed'
    elif change=='start':current['startedAt']=101
    elif change=='token':current['items'][1]=item('new',correlation='d'*64)
    else:current['items'].append(item('duplicate'))
    with pytest.raises(Refused):o.step(state,SPEC)

@pytest.mark.parametrize('change',['end','id','start','failed','active','infinite','bool'])
def test_only_observed_provisional_to_completed_transition_is_allowed(change):
    before={k:v for k,v in provisional().items() if k!='items'}
    after={k:v for k,v in turn('anchor').items() if k!='items'}
    if change=='end':after['completedAt']=99
    elif change=='id':after['id']='other'
    elif change=='start':after['startedAt']=101
    elif change=='failed':after['status']='failed'
    elif change=='active':after.update(status='inProgress',completedAt=None)
    elif change=='infinite':after['completedAt']=float('inf')
    else:after['completedAt']=True
    with pytest.raises(Refused):anchor_lifecycle(before,after,now=300)


def test_sealed_completed_anchor_still_rejects_end_time_drift():
    before={k:v for k,v in turn('anchor').items() if k!='items'}
    assert anchor_lifecycle(before,dict(before),now=300)=='unchanged'
    with pytest.raises(Refused):anchor_lifecycle(before,dict(before,completedAt=201),now=300)
