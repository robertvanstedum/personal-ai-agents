from contextlib import contextmanager
from copy import deepcopy
import json
import pytest
from core.agent_requests.task_watch import TaskObserver, sha, report
from core.agent_requests.codex_read_observer import ReadEnrollment
from core.agent_requests.direct_contact import Refused
from tests.agent_requests.test_codex_desktop_queue import TARGET, IDENTITY

SPEC=dict(task_id='synthetic-build',generation='test-1',scope_sha256='a'*64,correlation='b'*64)


def item(id='final',outcome='ready_for_owner_uat',**changes):
    value=dict(format='minimoi.task-terminal.v1',**SPEC,outcome=outcome,evidence_refs=['fixture:sha256'],remaining='Owner UAT pending.')
    value.update(changes)
    return dict(id=id,type='agentMessage',phase='final_answer',text=json.dumps(value))


def turn(id,items=(),status='completed'):
    return dict(id=id,status=status,startedAt=100,completedAt=200 if status!='inProgress' else None,items=list(items))


class Reader:
    def __init__(self,turns):self.turns=turns;self.calls=[]
    @contextmanager
    def session(self):
        def call(method,args):
            self.calls.append((method,args))
            if method=='thread/read':return {'thread':{'id':TARGET.session_id,'cwd':TARGET.cwd}}
            if method=='thread/turns/list':
                start=int(args.get('cursor',0));end=start+args['limit']
                return {'data':[{k:v for k,v in t.items() if k!='items'} for t in self.turns[start:end]],
                        'nextCursor':str(end) if end<len(self.turns) else None}
            assert method=='thread/items/list'
            t=next(t for t in self.turns if t['id']==args['turnId'])
            start=int(args.get('cursor',0));end=start+args['limit']
            return {'data':[{'turnId':t['id'],'item':i} for i in t['items'][start:end]],
                    'nextCursor':str(end) if end<len(t['items']) else None}
        yield call


def observer(turns):
    r=Reader(turns)
    return TaskObserver(ReadEnrollment(TARGET,IDENTITY,'/synthetic/.codex'),reader=r),r


def test_old_marker_never_done_and_new_final_after_checkpoint():
    o,r=observer([turn('old',[item()])]);state=o.step(None,SPEC)['checkpoint']
    assert o.step(state,SPEC)['proof'] is None
    r.turns.insert(0,turn('new',[item('new-final')]))
    output=o.step(state,SPEC)
    assert output['proof']['item_id']=='new-final'
    assert output['checkpoint']['anchor']['id']=='new'
    assert all(m in {'thread/read','thread/turns/list','thread/items/list'} for m,a in r.calls)


@pytest.mark.parametrize('kind',['ordinary','tool','reasoning','quoted','commentary','interrupted'])
def test_non_reports_do_not_complete(kind):
    o,r=observer([turn('old')]);state=o.step(None,SPEC)['checkpoint']
    it=item()
    if kind=='ordinary':it['text']='Finished the request.'
    elif kind=='quoted':it['text']='Quoted marker:\n'+it['text']
    elif kind=='commentary':it['phase']='commentary'
    elif kind in ('tool','reasoning'):it['type']=kind
    r.turns.insert(0,turn('new',[it],status='interrupted' if kind=='interrupted' else 'completed'))
    out=o.step(state,SPEC)
    assert out['proof'] is None and out['checkpoint']['anchor']['id']=='new'


@pytest.mark.parametrize('field,value',[('correlation','c'*64),('scope_sha256','d'*64)])
def test_wrong_binding_refuses_without_checkpoint(field,value):
    o,r=observer([turn('old')]);state=o.step(None,SPEC)['checkpoint'];original=deepcopy(state)
    r.turns.insert(0,turn('new',[item(**{field:value})]))
    with pytest.raises(Refused,match='binding_changed'):o.step(state,SPEC)
    assert state==original


def test_conflicting_finals_refuse():
    o,r=observer([turn('old')]);state=o.step(None,SPEC)['checkpoint']
    r.turns.insert(0,turn('new',[item('a'),item('b')]))
    with pytest.raises(Refused,match='conflicting'):o.step(state,SPEC)


def test_active_hole_restart_streaming_final_remains_eligible():
    o,r=observer([turn('old')]);state=o.step(None,SPEC)['checkpoint']
    r.turns.insert(0,turn('active',[item('stream')],status='inProgress'))
    state=o.step(state,SPEC)['checkpoint']
    assert state['anchor']['id']=='old' and state['active']['id']=='active'
    r.turns[0]=turn('active',[item('stream')])
    restarted=TaskObserver(o.enrollment,reader=r)
    out=restarted.step(state,SPEC)
    assert out['proof']['item_id']=='stream' and out['checkpoint']['anchor']['id']=='active'


def test_active_initial_anchor_mutates_then_new_final_only():
    old=dict(id='tool',type='tool',output='working')
    user=dict(id='user',type='userMessage',content=[dict(type='text',text='Authorized work')])
    o,r=observer([turn('old',[old,user],status='inProgress')]);state=o.step(None,SPEC)['checkpoint']
    r.turns[0]=turn('old',[dict(old,output='finished'),user,item('new')])
    assert o.step(state,SPEC)['proof']['item_id']=='new'
    r.turns[0]['items'][1]['content'][0]['text']='Changed'
    with pytest.raises(Refused,match='source_changed'):o.step(state,SPEC)


def test_missing_or_changed_anchor_refuses():
    o,r=observer([turn('old')]);state=o.step(None,SPEC)['checkpoint']
    r.turns=[turn('other')]
    with pytest.raises(Refused,match='history_gap'):o.step(state,SPEC)
    r.turns=[turn('old')];r.turns[0]['completedAt']=201
    with pytest.raises(Refused,match='anchor_changed'):o.step(state,SPEC)


def test_paginated_turn_history_and_items_checkpoint_one_turn_at_time():
    o,r=observer([turn('old')]);state=o.step(None,SPEC)['checkpoint']
    r.turns=[turn('n'+str(i)) for i in range(30,0,-1)]+r.turns
    r.turns[-2]['items']=[dict(id='tool'+str(i),type='tool') for i in range(25)]
    out=o.step(state,SPEC)
    assert out['checkpoint']['anchor']['id']=='n1' and len(out['checkpoint']['items'])==25
    assert any(m=='thread/turns/list' and a.get('cursor')=='20' for m,a in r.calls)
    assert len([a for m,a in r.calls if m=='thread/items/list' and a['turnId']=='n1'])==3


def test_blocked_then_ready_is_not_terminal_in_observer():
    o,r=observer([turn('old')]);state=o.step(None,SPEC)['checkpoint']
    r.turns.insert(0,turn('block',[item('blocked',outcome='blocked')]))
    out=o.step(state,SPEC);assert out['state']=='armed' and out['proof']['report']['outcome']=='blocked'
    r.turns.insert(0,turn('ready',[item('ready')]))
    assert o.step(out['checkpoint'],SPEC)['proof']['report']['outcome']=='ready_for_owner_uat'


def test_page_bound_is_refusal_not_latest_jump():
    o,r=observer([turn('old')]);state=o.step(None,SPEC)['checkpoint']
    r.turns=[turn('n'+str(i)) for i in range(161)]+r.turns
    with pytest.raises(Refused,match='page_bound'):o.step(state,SPEC)


def test_duplicate_turn_ids_refuse():
    o,r=observer([turn('old')]);state=o.step(None,SPEC)['checkpoint']
    r.turns=[turn('dup'),turn('dup'),turn('old')]
    with pytest.raises(Refused,match='duplicate_turn'):o.step(state,SPEC)


def test_done_waits_for_frozen_batch_head_and_conflict_refuses():
    o,r=observer([turn('old')]);state=o.step(None,SPEC)['checkpoint']
    r.turns=[turn('newest',[item('conflict')]),turn('earlier',[item('first')]),*r.turns]
    out=o.step(state,SPEC)
    assert out['proof'] is None and out['checkpoint']['pending_done']['item_id']=='first'
    assert out['checkpoint']['batch_head']=='newest'
    with pytest.raises(Refused,match='conflicting'):o.step(out['checkpoint'],SPEC)


def test_new_arrivals_after_batch_head_do_not_extend_frozen_batch():
    o,r=observer([turn('old')]);state=o.step(None,SPEC)['checkpoint']
    r.turns=[turn('head'),turn('earlier',[item('first')]),*r.turns]
    state=o.step(state,SPEC)['checkpoint']
    r.turns.insert(0,turn('later-arrival',[item('not-in-frozen-batch')]))
    out=o.step(state,SPEC)
    assert out['checkpoint']['anchor']['id']=='head' and out['proof']['item_id']=='first'
    assert out['checkpoint']['batch_head'] is None


def test_pending_done_active_head_blocks_then_newer_blocked_conflicts():
    o,r=observer([turn('old')]);state=o.step(None,SPEC)['checkpoint']
    r.turns=[turn('head',[item('blocked',outcome='blocked')],status='inProgress'),turn('earlier',[item('first')]),*r.turns]
    out=o.step(state,SPEC);assert out['proof'] is None
    out=o.step(out['checkpoint'],SPEC);assert out['proof'] is None and out['checkpoint']['pending_done']
    r.turns[0]=turn('head',[item('blocked',outcome='blocked')])
    with pytest.raises(Refused,match='conflicting'):o.step(out['checkpoint'],SPEC)


def test_oversize_turn_cannot_advance_or_report(monkeypatch):
    o,r=observer([turn('old')]);state=o.step(None,SPEC)['checkpoint'];original=deepcopy(state)
    r.turns.insert(0,turn('new',[item()]))
    def refuse(*args):raise Refused('native_read_bound')
    monkeypatch.setattr(o,'_items',refuse)
    with pytest.raises(Refused,match='read_bound'):o.step(state,SPEC)
    assert state==original


@pytest.mark.parametrize('field',['task_id','generation'])
def test_foreign_subscription_marker_advances_without_poisoning(field):
    o,r=observer([turn('old')]);state=o.step(None,SPEC)['checkpoint']
    r.turns.insert(0,turn('test-generation',[item('test-final',**{field:'other'})]))
    out=o.step(state,SPEC)
    assert out['proof'] is None and out['checkpoint']['anchor']['id']=='test-generation'
    r.turns.insert(0,turn('real-generation',[item('real-final')]))
    assert o.step(out['checkpoint'],SPEC)['proof']['item_id']=='real-final'


def test_registration_old_stream_excluded_but_later_stream_same_turn_eligible():
    o,r=observer([turn('active',[dict(id='old-stream',type='agentMessage',phase='commentary',text='working')],status='inProgress')])
    state=o.step(None,SPEC)['checkpoint']
    r.turns[0]['items']=[item('old-stream'),item('new-stream')]
    state=o.step(state,SPEC)['checkpoint']
    assert state['excluded']==['old-stream']
    r.turns[0]=turn('active',[item('old-stream'),item('new-stream')])
    assert o.step(state,SPEC)['proof']['item_id']=='new-stream'


def test_foreign_marker_after_pending_done_does_not_conflict():
    o,r=observer([turn('old')]);state=o.step(None,SPEC)['checkpoint']
    r.turns=[turn('head',[item('foreign',generation='other')]),turn('earlier',[item('real')]),*r.turns]
    out=o.step(state,SPEC);assert out['proof'] is None
    assert o.step(out['checkpoint'],SPEC)['proof']['item_id']=='real'
