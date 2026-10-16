"""Real disposable ledger, locks and strict readers; no agent/provider calls."""
import json
import os
from uuid import uuid4
import pytest
from core import agent_requests as ar
from minimoi_portal.guild_ui import rooms_archive_api as module

ROOM=str(uuid4())


@pytest.fixture
def ledger(tmp_path):
    root=tmp_path/'ledger';root.mkdir(mode=0o700);(root/'requests').mkdir(mode=0o700)
    return str(root)


def metadata():return {'version':1,'latest_seq':3,'owner_message_seqs':[3]}


def request(root):
    rid=ar.request_id_for(f'handoff:{ROOM}:3:codex')
    ar.create_request(root,request_id=rid,frm='robert',to='codex',action='Synthetic only',origin='owner_handoff_from_room')
    return rid


def test_no_handoffs_proved_by_complete_deterministic_candidates(ledger):
    with module.room_lock(ledger,ROOM):
        with module.external_clear(ledger,ROOM,metadata()) as proof:
            assert proof['state']=='clear' and proof['request_count']==0
            assert len(proof['mapping_sha256'])==64


def test_unmapped_legacy_request_is_found_and_blocks_archive(ledger):
    request(ledger)
    with module.room_lock(ledger,ROOM),pytest.raises(module.WorkUnknown,match='external_work_pending'):
        with module.external_clear(ledger,ROOM,metadata()):pass


def test_closed_unposted_result_blocks_then_posted_result_clears(ledger):
    rid=request(ledger)
    ar.result(ledger,rid,'codex','Synthetic result')
    with module.room_lock(ledger,ROOM):
        module.update_mapping(ledger,ROOM,lambda doc:doc['items'].append({'request_id':rid,'agent':'codex','seq':3}))
        with pytest.raises(module.WorkUnknown,match='milestones_unposted'):
            with module.external_clear(ledger,ROOM,metadata()):pass
        module.update_mapping(ledger,ROOM,lambda doc:doc['posted'].extend([rid+':picked_up',rid+':reported']))
        with module.external_clear(ledger,ROOM,metadata()) as proof:
            assert proof['request_count']==1
            assert 'text' not in proof


def test_corrupt_mapping_is_not_an_empty_mapping(ledger):
    with module.room_lock(ledger,ROOM):
        path=os.path.join(ledger,'room-handoffs',ROOM+'.json')
        with open(path,'w') as stream:stream.write('not-json')
        with pytest.raises(module.WorkUnknown):module.strict_mapping(ledger,ROOM)


def test_damaged_event_line_is_not_skipped(ledger):
    rid=request(ledger);ar.result(ledger,rid,'codex','Synthetic result')
    with open(os.path.join(ledger,'requests',rid,'events.jsonl'),'ab') as stream:stream.write(b'bad-line\n')
    with module.room_lock(ledger,ROOM),pytest.raises(module.WorkUnknown,match='ledger_invalid'):
        with module.external_clear(ledger,ROOM,metadata()):pass


def test_wrong_request_content_digest_is_refused(ledger):
    rid=request(ledger);path=os.path.join(ledger,'requests',rid,'request.json')
    doc=json.loads(open(path).read());doc['action']='Changed synthetic payload'
    with open(path,'w') as stream:json.dump(doc,stream)
    with module.room_lock(ledger,ROOM),pytest.raises(module.WorkUnknown,match='request_integrity_invalid'):
        with module.external_clear(ledger,ROOM,metadata()):pass


def test_partial_admission_is_retained_and_not_reported_clear(ledger):
    rid=ar.request_id_for(f'handoff:{ROOM}:3:codex')
    with module.room_lock(ledger,ROOM):
        module.update_mapping(ledger,ROOM,lambda doc:doc['items'].append({'request_id':rid,'agent':'codex','seq':3}))
        with pytest.raises(FileNotFoundError):
            with module.external_clear(ledger,ROOM,metadata()):pass


def test_external_check_requires_common_admission_lock(ledger):
    with pytest.raises(module.WorkUnknown,match='admission_lock_required'):
        with module.external_clear(ledger,ROOM,metadata()):pass


def test_reentrant_room_lock_and_private_fence_persist(ledger):
    with module.room_lock(ledger,ROOM):
        with module.room_lock(ledger,ROOM):
            module.update_mapping(ledger,ROOM,lambda doc:doc.__setitem__('archive_attempt',{'operation_key':'synthetic-op','checked_at':'2026-10-14T12:00:00+00:00','payload':{'version':1,'archive_version':0,'latest_seq':3,'last_activity':'2026-09-01T00:00:00+00:00'}}))
        assert module.strict_mapping(ledger,ROOM)['archive_attempt']['operation_key']=='synthetic-op'
    assert os.stat(os.path.join(ledger,'room-handoffs',ROOM+'.json')).st_mode&0o777==0o600


def test_archive_timeout_keeps_fence_and_refuses_unresolved_retry(ledger,monkeypatch):
    from flask import Flask
    from types import SimpleNamespace
    from minimoi_portal.guild_ui import api,agent_ask
    app=Flask('synthetic-archive-orchestrator')
    body={'room':ROOM,'version':1,'archive_version':0,'latest_seq':3,'last_activity':'2026-09-01T00:00:00+00:00','_key':'synthetic-archive'}
    monkeypatch.setattr(api,'_write_body',lambda:(None,body))
    monkeypatch.setattr(agent_ask,'service',lambda:SimpleNamespace(root=ledger))
    app.extensions['rooms_who']=lambda:(SimpleNamespace(ok=True,principal='robert'),None)
    calls=[]
    def transport(method,path,payload=None,operation_key=None):
        calls.append((method,path))
        if method=='GET':return 200,{**metadata(),'archive_version':0,'last_activity':body['last_activity'],'archived_at':None}
        if path.endswith('archive-reconcile'):return 409,{'error':'still uncertain'}
        return 0,None
    app.extensions['rooms_call']=transport
    with app.app_context():
        result=module.archive();assert result.status_code==502
        assert module.strict_mapping(ledger,ROOM)['archive_attempt']['operation_key']=='synthetic-archive'
        result=module.archive();assert result.status_code==409
    assert len([x for x in calls if x[1].endswith('/archive')])==1


def test_generic_proxy_cannot_sign_browser_archive_proof(monkeypatch):
    from flask import Flask
    from minimoi_portal import records_bridge
    app=Flask('synthetic-generic-proxy')
    records_bridge.install(app,lambda fn:fn,lambda fn:fn,backend='http://127.0.0.1:18880')
    result=app.test_client().post('/app/records/api/v1/rooms/'+ROOM+'/archive',json={'_portal_archive_proof':{'state':'clear'}},headers={'Origin':'http://localhost'})
    assert result.status_code==403 and result.json['code']=='archive_orchestrator_required'


def test_admission_mapping_is_durable_before_request_create(ledger,monkeypatch):
    from flask import Flask
    from types import SimpleNamespace
    from minimoi_portal.guild_ui import api,agent_ask,rooms_handoff_api as handoffs
    app=Flask('synthetic-handoff-admission');app.secret_key='synthetic-session-key'
    body={'room':ROOM,'seq':3,'agents':['codex'],'_key':'synthetic-handoff'}
    monkeypatch.setattr(api,'_write_body',lambda:(None,body))
    monkeypatch.setattr(agent_ask,'service',lambda:SimpleNamespace(root=ledger,scrub=lambda s:s,listening=lambda a:False))
    app.extensions['rooms_who']=lambda:(SimpleNamespace(ok=True,principal='robert'),None)
    def transport(method,path,payload=None,operation_key=None):
        if path.endswith('archive-check'):return 200,{**metadata(),'archived_at':None}
        if path.endswith('approved-agents'):return 200,{'agents':[{'agent':'codex','approved':True}]}
        return 200,{'title':'Synthetic','events':[{'actor':'robert','kind':'message','seq':3,'body':'Synthetic owner-approved work'}]}
    app.extensions['rooms_call']=transport
    real=ar.create_request;observed=[]
    def checked(root,**kwargs):
        observed.append(module.strict_mapping(root,ROOM)['items'])
        return real(root,**kwargs)
    monkeypatch.setattr(ar,'create_request',checked)
    with app.test_request_context():
        response=handoffs.handoff()
        assert response.status_code==200 and response.json['handed'][0]['ok']
    assert observed[0][0]['request_id']==ar.request_id_for(f'handoff:{ROOM}:3:codex')


def test_unknown_archive_attempt_blocks_new_handoff_before_any_dispatch(ledger,monkeypatch):
    from flask import Flask
    from types import SimpleNamespace
    from minimoi_portal.guild_ui import api,agent_ask,rooms_handoff_api as handoffs
    app=Flask('synthetic-uncertain-admission')
    body={'room':ROOM,'seq':3,'agents':['codex'],'_key':'synthetic'}
    monkeypatch.setattr(api,'_write_body',lambda:(None,body))
    monkeypatch.setattr(agent_ask,'service',lambda:SimpleNamespace(root=ledger))
    app.extensions['rooms_call']=lambda *args,**kwargs:pytest.fail('No upstream call while fenced')
    with module.room_lock(ledger,ROOM):
        module.update_mapping(ledger,ROOM,lambda doc:doc.__setitem__('archive_attempt',{'operation_key':'archive-unknown','checked_at':'2026-10-14T12:00:00+00:00','payload':{'version':1,'archive_version':0,'latest_seq':3,'last_activity':'2026-09-01T00:00:00+00:00'}}))
    with app.app_context():
        response=handoffs.handoff()
        assert response.status_code==409 and response.json['error']=='archive_uncertain'
    assert os.listdir(os.path.join(ledger,'requests'))==[]


def test_delivery_append_waits_for_proof_inode_lock(ledger):
    import threading
    rid=request(ledger);ar.result(ledger,rid,'codex','Synthetic result')
    with module.room_lock(ledger,ROOM):
        module.update_mapping(ledger,ROOM,lambda doc:doc['items'].append({'request_id':rid,'agent':'codex','seq':3}))
        module.update_mapping(ledger,ROOM,lambda doc:doc['posted'].extend([rid+':picked_up',rid+':reported']))
        started=threading.Event();finished=threading.Event()
        def append():
            started.set()
            ar.record_delivery(ledger,rid,adapter='synthetic',state='accepted',attempt_id='synthetic-test')
            finished.set()
        with module.external_clear(ledger,ROOM,metadata()):
            thread=threading.Thread(target=append);thread.start()
            assert started.wait(1) and not finished.wait(0.05)
        thread.join(1);assert finished.is_set()


def test_owner_archive_replay_uses_same_receipt_without_new_mutation(ledger,monkeypatch):
    from flask import Flask
    from types import SimpleNamespace
    from minimoi_portal.guild_ui import api,agent_ask
    app=Flask('synthetic-owner-archive')
    body={'room':ROOM,'version':1,'archive_version':0,'latest_seq':3,'last_activity':'2026-09-01T00:00:00+00:00','_key':'synthetic-idempotent'}
    monkeypatch.setattr(api,'_write_body',lambda:(None,body))
    monkeypatch.setattr(agent_ask,'service',lambda:SimpleNamespace(root=ledger))
    app.extensions['rooms_who']=lambda:(SimpleNamespace(ok=True,principal='robert'),None)
    saved=None
    def transport(method,path,payload=None,operation_key=None):
        nonlocal saved
        if method=='GET':
            return 200,{**metadata(),'archive_version':1 if saved else 0,
                        'last_activity':body['last_activity'],'archived_at':'synthetic-committed' if saved else None}
        if saved is None:
            assert payload['_portal_archive_proof']['state']=='clear'
            saved={'result':{'room':ROOM,'archived':True,'archive_version':1,'through_seq':3,'last_activity':body['last_activity']},
                   'receipt':{'actor':'robert','id':'synthetic-commit'}}
        return 200,saved
    app.extensions['rooms_call']=transport
    with app.app_context():
        first=module.archive();second=module.archive()
        assert first.status_code==second.status_code==200 and first.json==second.json==saved
    assert 'archive_attempt' not in module.strict_mapping(ledger,ROOM)


def test_malformed_success_reply_keeps_uncertain_fence(ledger,monkeypatch):
    from flask import Flask
    from types import SimpleNamespace
    from minimoi_portal.guild_ui import api,agent_ask
    app=Flask('synthetic-malformed-archive')
    body={'room':ROOM,'version':1,'archive_version':0,'latest_seq':3,'last_activity':'2026-09-01T00:00:00+00:00','_key':'synthetic-unknown'}
    monkeypatch.setattr(api,'_write_body',lambda:(None,body))
    monkeypatch.setattr(agent_ask,'service',lambda:SimpleNamespace(root=ledger))
    app.extensions['rooms_who']=lambda:(SimpleNamespace(ok=True,principal='robert'),None)
    app.extensions['rooms_call']=lambda method,*args,**kwargs: (200,{**metadata(),'archive_version':0,'last_activity':body['last_activity'],'archived_at':None}) if method=='GET' else (200,None)
    with app.app_context():assert module.archive().status_code==502
    assert module.strict_mapping(ledger,ROOM)['archive_attempt']['operation_key']=='synthetic-unknown'


def test_parent_symlink_swap_cannot_redirect_private_map_write(ledger,tmp_path):
    import pathlib
    original=pathlib.Path(ledger);moved=tmp_path/'moved-ledger';outside=tmp_path/'outside';outside.mkdir(mode=0o700)
    with module.room_lock(ledger,ROOM):
        original.rename(moved);original.symlink_to(outside,target_is_directory=True)
        try:
            module.update_mapping(ledger,ROOM,lambda data:data['posted'].append('synthetic-marker'))
            assert not list(outside.iterdir())
            assert json.loads((moved/'room-handoffs'/f'{ROOM}.json').read_text())['posted']==['synthetic-marker']
        finally:original.unlink();moved.rename(original)


def test_ancestor_symlink_is_refused_before_any_lock_or_write(ledger,tmp_path):
    import pathlib
    alias=tmp_path/'alias';alias.symlink_to(pathlib.Path(ledger),target_is_directory=True)
    with pytest.raises(OSError):
        with module.room_lock(str(alias),ROOM):pass
    assert not pathlib.Path(ledger,'room-handoffs').exists()


def test_foreign_actor_result_cannot_falsely_close_handoff(ledger):
    rid=request(ledger)
    with open(os.path.join(ledger,'requests',rid,'events.jsonl'),'ab') as stream:
        stream.write(json.dumps({'kind':'result','actor':'mc','text':'Synthetic invalid attribution'}).encode()+b'\n')
    with module.room_lock(ledger,ROOM),pytest.raises(module.WorkUnknown,match='event_actor_invalid'):
        with module.external_clear(ledger,ROOM,metadata()):pass
