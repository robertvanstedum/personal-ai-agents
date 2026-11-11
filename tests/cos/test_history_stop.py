"""Owner Stop and answer race at the canonical history boundary."""
import pytest
from flask import Flask
from domains.cos.history import History, HistoryRefused
from domains.cos.history_api import create_blueprint

@pytest.fixture
def turn(tmp_path):
    history = History(tmp_path / 'history', 'dev')
    cid = history.create('Synthetic stop test')['conversation_id']
    tid = 't-aaaaaaaa000040008000000000000001'
    history.begin({'schema':'minimoi.cos.send/1','conversation_id':cid,'turn_id':tid,'text':'Synthetic question','channel':'web'})
    return history,cid,tid

def test_stop_fences_before_recording_and_late_answer_never_recovers(turn):
    h,cid,tid=turn
    seen=[]
    def fence():
        seen.append(h.turn(cid,tid)['status'])
    stopped=h.stop(cid,tid,fence)
    assert seen==['pending'] and stopped['failure_code']=='owner_stop'
    with pytest.raises(HistoryRefused,match='owner_stopped'):
        h.answer(cid,tid,'Late answer')
    assert History(h.root.parent,'dev').turn(cid,tid)['reply_text'] is None
    assert h.stop(cid,tid,lambda:None)==stopped

def test_already_saved_answer_wins_and_stop_does_not_revoke(turn):
    h,cid,tid=turn
    saved=h.answer(cid,tid,'Saved first')
    assert h.stop(cid,tid,lambda:pytest.fail('completed turn must not be revoked'))==saved

def test_failed_fence_cannot_claim_stop(turn):
    h,cid,tid=turn
    def fail():raise RuntimeError('fence unavailable')
    with pytest.raises(RuntimeError):h.stop(cid,tid,fail)
    assert h.turn(cid,tid)['status']=='pending'

def test_stop_endpoint_requires_identity_and_known_turn(turn):
    h,cid,tid=turn
    owner=[None];calls=[]
    app=Flask(__name__)
    app.register_blueprint(create_blueprint(h,resolve_owner=lambda _:owner[0],stop_turn=calls.append))
    client=app.test_client();url=f'/ui/history/conversations/{cid}/turns/{tid}/stop'
    assert client.post(url).status_code==401 and calls==[]
    owner[0]='owner'
    assert client.post(url).json['stop_requested'] and calls==[tid]
    assert client.post(url.replace(tid,'t-bbbbbbbbbbbb')).status_code==400
