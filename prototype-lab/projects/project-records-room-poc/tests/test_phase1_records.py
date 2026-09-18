"""Transcript/derived-document separation and honest runtime provenance."""
import json
import sqlite3
from uuid import uuid4

import pytest

from app import create_app
from store import Store, Problem


@pytest.fixture
def setup(tmp_path):
    store=Store(tmp_path/'private')
    room=store.create_room('robert','open',dict(title='Meeting',purpose='Retain full discussion',mode='meeting',recording_acknowledged=True))['result']['id']
    token=store.add_principal('robert','agent',dict(id='cos-dev',label='CoS connector'))['access_token']
    store.membership('robert','invite',room,dict(actor='cos-dev',role='contributor'))
    return store,room,token


def payload(store,room,**overrides):
    return dict(title='Discussion outcome',body='An interpretation, not an approval.',
        source_through_seq=store.room('robert',room)['contribution_guard']['last_seq'],**overrides)


def test_notes_after_close_preserve_full_discussion_and_export(setup):
    store,room,_=setup
    discussion=store.append('robert','message',room,dict(body='Keep the disagreement too.'))['result']
    store.state('robert','close',room,dict(state='closed',version=1,checkpoint='Meeting ended'))
    before=store.room('robert',room)['events']
    data=payload(store,room,kind='decision_summary')
    first=store.note('cos-dev','note',room,data)
    assert store.note('cos-dev','note',room,data)==first
    result=store.export('robert',room)
    assert result['events'][:len(before)]==before
    assert len(result['notes'])==1 and result['state']=='closed'
    assert not any(e['kind']=='decision' for e in result['events'])
    assert discussion['body'] in store.transcript('robert',room)
    assert first['result']['body'] not in store.transcript('robert',room)
    with pytest.raises(Problem):store.append('cos-dev','closed',room,dict(body='Later discussion'))


def test_notes_are_versioned_and_source_bound(setup):
    store,room,_=setup
    first=store.note('robert','first',room,payload(store,room))['result']
    second=store.note('robert','second',room,payload(store,room,supersedes=first['id']))['result']
    assert len(store.room('robert',room)['notes'])==2
    assert second['supersedes']==first['id']
    with pytest.raises(Problem):store.note('robert','wrong-kind',room,payload(store,room,kind='next_steps',supersedes=first['id']))
    data=payload(store,room);data['source_through_seq']=999999
    with pytest.raises(Problem):store.note('robert','missing-source',room,data)
    with pytest.raises(Problem):store.note('robert','first',room,payload(store,room))


def test_note_access_and_search_revocation(setup):
    store,room,_=setup
    store.note('cos-dev','note',room,payload(store,room))
    assert store.search('cos-dev','interpretation')
    store.membership('robert','observe',room,dict(actor='cos-dev',role='observer'))
    with pytest.raises(Problem):store.note('cos-dev','denied',room,payload(store,room))
    store.membership('robert','remove',room,dict(actor='cos-dev',role='remove'))
    assert store.search('cos-dev','interpretation')==[]
    with pytest.raises(Problem):store.operation('cos-dev','note')


def test_origin_is_declared_and_writer_remains_authenticated(setup):
    store,room,_=setup
    origin=dict(source_application='cos',mode='agent_response',agent_id='agent-a',runtime='openclaw',execution_id='attempt-1')
    result=store.append('cos-dev','response',room,dict(body='Proposed answer',origin=origin))['result']
    assert result['actor']=='cos-dev' and result['origin']==origin
    assert Store(store.root).room('robert',room)['events'][-1]['origin']==origin
    assert 'not attested' in store.room('robert',room)['origin_assurance']
    assert 'Declared origin (not runtime attestation)' in store.transcript('robert',room)
    assert store.append('cos-dev','response',room,dict(body='Proposed answer',origin=origin))['result']==result
    with pytest.raises(Problem):store.append('cos-dev','response',room,dict(body='Proposed answer',origin={**origin,'runtime':'other'}))


@pytest.mark.parametrize('origin',[{},[],{'mode':'agent_response','source_application':'cos'},
    {'mode':'relay','source_application':'cos','verified':True}])
def test_invalid_or_forged_origin_rejected(setup,origin):
    store,room,_=setup
    with pytest.raises(Problem):store.append('cos-dev',str(uuid4()),room,dict(body='Message',origin=origin))


def test_v3_migration_atomic_and_legacy_receipts_preserved(setup,monkeypatch):
    store,room,_=setup
    saved=store.append('robert','legacy',room,dict(body='Before migration'))
    # Reconstruct v3's actual shape and historical receipt (which had no origin).
    saved['result'].pop('origin')
    with store.connect() as db:
        db.execute('DROP TABLE notes')
        db.execute('ALTER TABLE events DROP COLUMN origin')
        db.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
        db.execute("UPDATE operations SET response=? WHERE key='legacy'",(json.dumps(saved),))
    original=sqlite3.connect
    class Broken(sqlite3.Connection):
        def execute(self,sql,*args,**kwargs):
            if sql.startswith('CREATE TABLE notes'):raise sqlite3.OperationalError('Interrupted notes migration')
            return super().execute(sql,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3,'connect',lambda *a,**kw:original(*a,**kw,factory=Broken))
        with pytest.raises(sqlite3.OperationalError):Store(store.root)
    with store.connect() as db:
        assert 'origin' not in [r[1] for r in db.execute('PRAGMA table_info(events)')]
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]=='3'
    migrated=Store(store.root)
    assert migrated.room('robert',room)['events'][-1]['origin'] is None
    assert migrated.operation('robert','legacy')==saved


def test_notes_http_and_cross_room_source_guard(setup):
    store,room,token=setup
    other=store.create_room('robert','other',dict(title='Private',purpose='Private',recording_acknowledged=True))['result']['id']
    data=payload(store,other)
    with pytest.raises(Problem):store.note('robert','wrong-room-source',room,data)
    app=create_app(store.root,testing=True);client=app.test_client()
    headers={'Authorization':'Bearer '+token,'Idempotency-Key':'api-note'}
    response=client.post(f'/api/v1/rooms/{room}/notes',json=payload(store,room),headers=headers)
    assert response.status_code==201
    assert client.get(f'/api/v1/rooms/{other}',headers=headers).status_code==404


@pytest.mark.parametrize('kind',[[],{},42,None])
def test_malformed_note_kind_is_client_error(setup,kind):
    store,room,token=setup
    client=create_app(store.root,testing=True).test_client()
    response=client.post(f'/api/v1/rooms/{room}/notes',json=payload(store,room,kind=kind),
        headers={'Authorization':'Bearer '+token,'Idempotency-Key':'bad-kind'})
    assert response.status_code==400
