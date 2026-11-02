"""Direct voice follows the selected chat without borrowing another chat's context."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent))
import cos_voice_rig as rig
from domains.cos.history import History
from domains.cos.direct_voice import ChiefOfStaff
from domains.cos.voice_store import FileStore

@pytest.fixture
def bound(monkeypatch, tmp_path):
    folder = tmp_path / 'history'; folder.mkdir()
    history = History(folder, 'dev', clock=lambda: '2026-10-01T10:00:00+00:00')
    history.ensure_default()
    cid = history.create('Voice here')['conversation_id']
    other = history.create('Other chat')['conversation_id']
    for n, c in enumerate((cid, other)):
        turn, _ = history.begin({'schema':'minimoi.cos.send/1','conversation_id':c,
            'turn_id':f't-{n:016x}','text': 'selected chat words' if n == 0 else 'other chat secret', 'channel':'web'})
        history.answer(c, turn['turn_id'], 'Reply in this chat')
    return rig.build(monkeypatch, tmp_path, history=history), history, cid, other

def start(r, cid):
    response = r.client.post(rig.BASE+'/bootstrap', headers=rig.OWNER,
        json={'persona':'chief_of_staff','provider':'openai','conversation_id':cid})
    assert response.status_code == 200, response.get_json()
    return response.get_json()['voice_session_id']

def test_selected_chat_context_consult_and_durable_binding(bound):
    r, h, cid, other = bound
    sid = start(r, cid)
    assert 'selected chat words' in r.mints[0]['instructions']
    assert 'other chat secret' not in r.mints[0]['instructions']
    reply = r.client.post(rig.BASE+'/consult', headers=rig.OWNER, json={
        'conversation_id':cid, 'session_id':sid, 'call_id':'host-call1', 'name':'consult',
        'owner_transcript_item':'What is stuck?', 'owner_item_id':'t1', 'arguments':{}})
    assert reply.status_code == 200, reply.get_json()
    assert reply.get_json()['outcome'] == 'done'
    assert r.turns[-1]['conversation_id'] == cid
    store = FileStore(r.voice)
    assert (cid, sid) in store.sessions('7', needing_attention=False)
    wrong = r.client.get(rig.BASE+f'/sessions/{sid}?conversation_id={other}', headers=rig.READ)
    assert wrong.status_code == 403
    assert r.client.post(rig.BASE+f'/session/{sid}/end', headers=rig.OWNER,
        json={'conversation_id':cid, 'last_item_id':None, 'unacknowledged':[]}).status_code == 200
    correct = r.client.get(rig.BASE+f'/sessions/{sid}?conversation_id={cid}', headers=rig.READ)
    assert correct.status_code == 200 and correct.get_json()['conversation_id'] == cid

@pytest.mark.parametrize('kind', ['private','read_only','missing'])
def test_unusable_chat_refused_before_provider(bound, kind):
    r,h,cid,_ = bound
    if kind == 'private': cid=h.create('Private',private=True)['conversation_id']
    elif kind == 'read_only':
        cid='cosc-aaaaaaaaaaaa'; h._ensure_conversation(cid,'Old',private=False,read_only=True)
    else: cid='cosc-ffffffffffff'
    res=r.client.post(rig.BASE+'/bootstrap',headers=rig.OWNER,
        json={'persona':'chief_of_staff','provider':'openai','conversation_id':cid})
    assert res.status_code in (404,422) and not r.mints

def test_voice_continuity_is_chat_scoped_and_newer_than_old_text(bound):
    r,h,cid,other=bound
    for n in range(12):
        t,_=h.begin({'schema':'minimoi.cos.send/1','conversation_id':cid,
            'turn_id':f't-{n+20:016x}','text':f'old text {n}','channel':'web'})
        h.answer(cid,t['turn_id'],'old answer')
    sid=start(r,cid)
    rig.post(r,sid,[rig.event(r,'user','v1','new spoken words',partial=True)])
    r.client.post(rig.BASE+f'/session/{sid}/end',headers=rig.OWNER,json={'reason':'user_ended'})
    start(r,cid)
    assert '(partial): new spoken words' in r.mints[-1]['instructions']
    start(r,other)
    assert 'new spoken words' not in r.mints[-1]['instructions']
