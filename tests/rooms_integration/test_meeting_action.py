from flask import Flask,session
from types import SimpleNamespace
from minimoi_portal.guild_ui import meeting_action as M


def test_meeting_reuses_current_identity_bridge_and_same_room(tmp_path):
    app=Flask(__name__);app.secret_key='test';calls=[]
    app.extensions['rooms_who']=lambda:(SimpleNamespace(ok=True,principal='robert'),None)
    app.extensions['rooms_access']=SimpleNamespace(directory=str(tmp_path))
    def call(method,path,payload=None,operation_key=None):
        calls.append((method,path,operation_key))
        return (200,{'state':'active'}) if method=='GET' else (201,{'result':{'id':'room1'}})
    app.extensions['rooms_call']=call
    with app.test_request_context('/'):
        session['user']={'username':'robert','tier':'owner'}
        for title in ['First','Changed']:
            reply=M.execute('MEETING mc,claude-code,codex: '+title,{'text':'Set up a meeting with all three'},'n',{'id':'c-test'})
            assert 'room=room1' in reply
        assert 'No meeting' in M.execute('MEETING codex: x',{'text':"Don't start a meeting"},'n',{'id':'other'})
    assert len([x for x in calls if x[:2]==('POST','/api/v1/rooms')])==1
    assert all(key for method,path,key in calls if method=='POST')


def test_revoked_owner_never_calls_records(tmp_path):
    app=Flask(__name__)
    app.extensions['rooms_who']=lambda:(SimpleNamespace(ok=False,principal=None),None)
    with app.test_request_context('/'):
        assert 'No meeting' in M.execute('MEETING codex: x',{'text':'Start meeting with Codex'},'n',{'id':'c'})


def test_meeting_hook_keeps_owner_context_in_stream_thread(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from minimoi_portal.guild_ui.jobs_api import JobHook
    app=Flask(__name__); app.secret_key='test'
    monkeypatch.setenv('MINIMOI_MC_MEETINGS','1')
    app.extensions['rooms_who']=lambda:(SimpleNamespace(ok=session['user']['username']=='robert',principal='robert'),None)
    app.extensions['rooms_access']=SimpleNamespace(directory=str(tmp_path))
    app.extensions['rooms_call']=lambda *a,**kw:(201,{'result':{'id':'thread-room'}})
    with app.test_request_context('/'):
        session['user']={'username':'robert','tier':'owner'}
        hook=JobHook(None,{'id':'thread-conv'},'robert',{'text':'Start meeting with Codex'},'n',[])
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(hook.buffers_whole_reply).result()
        decision=pool.submit(hook.decide,'MEETING codex: Test',clean=True).result()
        assert 'thread-room' in decision.text
