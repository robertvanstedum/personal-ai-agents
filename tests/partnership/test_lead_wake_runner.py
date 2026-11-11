from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from core.canonical_json import digest
from core.lead_wake.inbox import Inbox, StaleWake
from core.lead_wake.runner import Runner

OP='OB-wake-example'
PACKET={'task_id':'TK-demo','kind':'helper_result_ready','lead':'mc','result_digest':'sha256:'+'a'*64}
REVIEW={'verdict':'approve','reasons':'Evidence supports answer.','answer':'The helper found file.py.'}
RECEIPT={'store':'mc-floor','id':'conversation:answer','result_digest':PACKET['result_digest']}


@pytest.fixture
def rig(tmp_path):
    state={'current':True,'busy':False,'calls':0,'reserved':0,'accepted':0,'published':0,'receipt':None,'fail_publish':False,'cancel_in_review':False}
    def resolve(op):
        if not state['current']:raise StaleWake('stopped')
        return dict(PACKET)
    inbox=Inbox(tmp_path/'inbox',resolve)
    inbox.deliver(OP,digest(PACKET),task_id=PACKET['task_id'],kind=PACKET['kind'],attempt=1)
    def reserve(*a):state['reserved']+=1
    def review(*a):
        state['calls']+=1
        if state['cancel_in_review']:state['current']=False
        return dict(REVIEW)
    def accept(*a):state['accepted']+=1;return True
    def publish(*a):
        if state['fail_publish']:raise OSError('store unavailable')
        state['published']+=1
        state['receipt']=dict(RECEIPT)
        return dict(RECEIPT)
    lane=SimpleNamespace(presentation=lambda *a:state['receipt'],context=lambda *a:{},reserve=reserve,review=review,accept=accept,publish=publish)
    @contextmanager
    def acquire(*a):yield None if state['busy'] else lane
    return inbox,Runner(inbox,SimpleNamespace(lane=acquire)),state,lane


def test_busy_owner_lane_does_not_dispatch(rig):
    inbox,runner,state,lane=rig
    state['busy']=True
    assert runner.pass_once()==[(OP,'busy')]
    assert state['calls']==state['reserved']==0
    state['busy']=False
    assert runner.pass_once()==[(OP,'presented')]
    assert inbox.status(OP)=='presented'
    assert runner.pass_once()==[]


def test_kept_review_recovers_publication_without_second_model(rig):
    inbox,runner,state,lane=rig
    state['fail_publish']=True
    assert runner.pass_once()==[(OP,'unavailable')]
    assert inbox.status(OP)=='reviewed'
    runner.inbox=Inbox(inbox.path,inbox.resolve)
    state['fail_publish']=False
    assert runner.pass_once()==[(OP,'presented')]
    assert state['calls']==state['reserved']==state['published']==1


def test_cancel_during_review_prevents_acceptance_and_presentation(rig):
    inbox,runner,state,lane=rig
    state['cancel_in_review']=True
    assert runner.pass_once()==[(OP,'superseded')]
    assert state['accepted']==state['published']==0
    assert inbox.review(OP) is None


def test_lost_inference_never_dispatches_again(rig):
    inbox,runner,state,lane=rig
    def timeout(*a):state['calls']+=1;raise TimeoutError()
    lane.review=timeout
    assert runner.pass_once()==[(OP,'uncertain')]
    runner.inbox=Inbox(inbox.path,inbox.resolve)
    assert runner.pass_once()==[]
    assert state['calls']==1


def test_saved_inline_answer_suppresses_duplicate_review(rig):
    inbox,runner,state,lane=rig
    state['receipt']=dict(RECEIPT)
    assert runner.pass_once()==[(OP,'presented')]
    assert state['calls']==state['reserved']==state['published']==0


def test_exhausted_budget_is_blocked_without_model_dispatch(rig):
    from services.helper_seats.spend import CapReached
    inbox,runner,state,lane=rig
    def capped(*a):raise CapReached('task')
    lane.reserve=capped
    assert runner.pass_once()==[(OP,'budget_blocked')]
    assert inbox.status(OP)=='budget_blocked'
    assert state['calls']==state['accepted']==state['published']==0
