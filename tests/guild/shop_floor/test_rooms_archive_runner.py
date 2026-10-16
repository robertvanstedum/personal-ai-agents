"""Synthetic exact original-vs-extracted orchestration; no network or app auth fabrication."""
import importlib.util
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from flask import Flask
from minimoi_portal.guild_ui import rooms_archive_api as revised,rooms_archive_runner as runner
from minimoi_portal.guild_ui import api,agent_ask
NOW=datetime(2026,10,14,8,tzinfo=timezone.utc)
ROOM=str(uuid4())
PRE={'version':1,'archive_version':0,'latest_seq':3,'last_activity':(NOW-timedelta(days=31)).isoformat()}

@pytest.mark.parametrize('case',['ok','stale','remote_refused','timeout','bad_receipt','external_pending','reconcile_committed','reconcile_uncertain'])
def test_exact_original_response_and_fence_parity(tmp_path,monkeypatch,case):
    outcomes=[];traces=[];states=[]
    for module in (revised,):
        root=tmp_path/module.__name__;root.mkdir(mode=0o700);(root/'requests').mkdir(mode=0o700)
        body={**PRE,'room':ROOM,'_key':'op-archive'}
        if case.startswith('reconcile'):
            module.update_mapping(root,ROOM,lambda d:d.__setitem__('archive_attempt',{'operation_key':'op-archive','checked_at':NOW.isoformat(),'payload':PRE}))
        trace=[]
        def call(method,path,payload=None,**kw):
            trace.append((method,path,kw.get('operation_key')))
            if path.endswith('archive-reconcile'):
                return (200,{'state':'committed','receipt':{'same':'receipt'}}) if case=='reconcile_committed' else (0,None)
            if method=='GET':return 200,{**PRE,'version':2 if case=='stale' else 1,'owner_message_seqs':[3]}
            if case=='remote_refused':return 409,{'error':'synthetic'}
            if case=='timeout':return 0,None
            return 200,{'result':{'room':ROOM,'archived':True,'archive_version':1,'through_seq':3,'last_activity':PRE['last_activity']},'receipt':{'actor':'other' if case=='bad_receipt' else 'robert'}}
        @contextmanager
        def clear(root,room,metadata):
            if case=='external_pending':raise module.WorkUnknown('external_work_pending')
            yield {'kind':'synthetic-proof','checked_at':NOW.isoformat()}
        monkeypatch.setattr(module,'external_clear',clear)
        app=Flask('parity');app.extensions.update(rooms_call=call,rooms_who=lambda:(SimpleNamespace(ok=True,principal='robert'),None))
        monkeypatch.setattr(api,'_write_body',lambda:(None,body));monkeypatch.setattr(agent_ask,'service',lambda:SimpleNamespace(root=root))
        out=revised.archive_room(root=root,call=call,principal='robert',room=ROOM,key='op-archive',precondition=PRE)
        outcomes.append(out);traces.append(trace)
        with module.room_lock(root,ROOM):states.append(module.strict_mapping(root,ROOM))
    body,code=outcomes[0]
    expected={'ok':200,'stale':409,'remote_refused':409,'timeout':502,'bad_receipt':502,'external_pending':409,'reconcile_committed':200,'reconcile_uncertain':409}
    assert code==expected[case]
    if case=='ok':assert body['result']['archived'] and body['receipt']['actor']=='robert'
    if case=='reconcile_committed':assert body=={'same':'receipt'}
    if case in {'timeout','bad_receipt','reconcile_uncertain'}:assert 'archive_attempt' in states[0]
    else:assert 'archive_attempt' not in states[0]
    if case in {'external_pending','stale'}:assert all(method!='POST' for method,_,_ in traces[0])
    if case=='ok':assert traces[0][-1][2]=='op-archive'


def row(**changes):return {'id':ROOM,**PRE,'archived_at':None,'backend_work':'clear','external_work':'unavailable',**changes}
def plan(rows,now=NOW):return runner.plan({'threshold_days':30,'rooms':rows},now=now)

def test_dry_run_candidates_not_grants_and_boundary():
    result=plan([row()]);assert result['rooms'][0]['candidate']
    assert result['scheduled_authorization']=='not_configured' and result['writes_performed']==0
    assert result['rooms'][0]['reason']=='candidate_needs_locked_proof'
    assert plan([row(last_activity=(NOW-timedelta(days=30)).isoformat())])['rooms'][0]['candidate']
    assert not plan([row(last_activity=(NOW-timedelta(days=30)+timedelta(seconds=1)).isoformat())])['rooms'][0]['candidate']

@pytest.mark.parametrize('changes',[{'archived_at':NOW.isoformat()},{'backend_work':'pending'},{'backend_work':'unavailable'},{'external_work':'pending'}])
def test_pending_and_archived_not_candidates(changes):assert not plan([row(**changes)])['rooms'][0]['candidate']


def test_window_and_bad_metadata_fail_closed():
    for time in (datetime(2026,10,14,11,tzinfo=timezone.utc),datetime(2026,10,14,7),datetime(2026,10,15,4,tzinfo=timezone.utc)):
        with pytest.raises(runner.PlanRefused):plan([row()],time)
    for rows in ([row(),row()],[row(version=True)],[row(last_activity='unknown')],[row(last_activity=(NOW+timedelta(seconds=1)).isoformat())]):
        with pytest.raises(runner.PlanRefused):plan(rows)


def test_untrusted_identity_cannot_call_transport(tmp_path):
    body,code=revised.archive_room(root=tmp_path,call=lambda *a,**k:pytest.fail('transport'),principal='service-owner',room=ROOM,key='k',precondition=PRE)
    assert code==403 and body['error']=='not_owner'
