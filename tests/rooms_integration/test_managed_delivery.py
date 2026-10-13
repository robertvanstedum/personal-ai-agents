"""Real Records + existing worker journal + managed ledger; no model call."""
import sys
from pathlib import Path
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'rooms_connector'))
from test_connector import env, home
from services.rooms_connector.managed_runner import ManagedRunner
from core import agent_requests as ledger


def test_managed_reply_reaches_room_once_under_agent_identity(env,tmp_path,monkeypatch):
    root=str(tmp_path/'ledger')
    runner=ManagedRunner(root,'claude-code',poll=.005)
    monkeypatch.setattr(runner,'ready',lambda:True)
    env.worker.relay=runner;env.worker.on_committed=None
    stop=threading.Event()
    def agent():
        while not stop.wait(.005):
            for doc in ledger.pending(root,'claude-code'):
                ledger.result(root,doc['request']['request_id'],'claude-code','A verified test reply.')
    thread=threading.Thread(target=agent);thread.start()
    try:
        assert env.call('POST','/teammates/claude-code/prove',{'confirm':True})[0]==202
        env.worker.run_once()
        assert env.call('GET','/teammates/claude-code/prove')[1]['proven_at']
        assert env.call('POST',f'/rooms/{env.room}/invite',{'actor':'claude-code'})[0] in (200,201)
        env.worker.housekeeping()
        assert env.call('POST',f'/rooms/{env.room}/events',{'body':'@Claude answer this test'})[0] in (200,201)
        assert env.worker.run_once()
        env.worker.run_once()
        events=env.call('GET',f'/rooms/{env.room}')[1]['events']
        replies=[e for e in events if e['actor']=='claude-code' and e['kind']=='message']
        assert len(replies)==1
        assert replies[0]['body']=='A verified test reply.'
        assert replies[0]['origin']['agent_id']=='claude-code'
    finally:
        stop.set();thread.join(2)
