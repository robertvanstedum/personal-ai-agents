"""Disposable SQLite-only archive tests; no runtime/network/provider."""
from datetime import datetime, timezone, timedelta
import pytest

import archive as module
from app import create_app
from store import Store, Problem

NOW=datetime(2026,10,14,12,tzinfo=timezone.utc)
OLD=(NOW-timedelta(days=31)).isoformat()


@pytest.fixture
def setup(tmp_path):
    app=create_app(tmp_path/'private',testing=True)
    store=app.extensions['records_store']
    room=store.create_room('robert','create',dict(title='Synthetic archive',purpose='Temporary test only',recording_acknowledged=True))['result']['id']
    with store.connect() as db:db.execute('UPDATE rooms SET updated=? WHERE id=?',(OLD,room))
    archive=module.Archive(store,clock=lambda:NOW)
    return app,store,archive,room


def clear(archive):
    archive.external_pending=lambda room,version,seq:module.ExternalWorkEvidence(room,'clear',NOW,version,seq)


def payload(archive,room):
    item=next(r for r in archive.preview('robert')['rooms'] if r['id']==room)
    return {k:item[k] for k in ('version','archive_version','latest_seq','last_activity')}


def test_preview_no_external_proof_is_unknown_and_writes_nothing(setup):
    _,store,a,room=setup
    result=a.preview('robert')['rooms'][0]
    assert not result['eligible'] and result['reason']=='external_work_unavailable'
    with pytest.raises(Problem,match='external_work_unavailable'):
        a.change('robert','archive',room,payload(a,room))
    with store.connect() as db:assert db.execute('SELECT COUNT(*) FROM room_archive').fetchone()[0]==0


def test_owner_idempotent_archive_restore_preserves_schema_state_and_history(setup):
    _,store,a,room=setup;clear(a)
    before=store.room('robert',room)
    body=payload(a,room)
    receipt=a.change('robert','archive',room,body)
    assert receipt==a.change('robert','archive',room,body)
    assert receipt['result']['archived'] and receipt['receipt']['actor']=='robert'
    assert store.room('robert',room)==before
    restored=a.change('robert','restore',room,payload(a,room),restore=True)
    assert not restored['result']['archived'] and restored['result']['archive_version']==2
    assert store.room('robert',room)==before
    with store.connect() as db:
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]=='5'
        assert len(db.execute('PRAGMA table_info(rooms)').fetchall())==10
    # Previous source Store accepts additive table, preserves all data.
    previous=Store(store.root)
    assert previous.room('robert',room)==before


def test_restore_does_not_need_external_transport_or_close_meeting(setup):
    _,store,a,room=setup;clear(a)
    a.change('robert','archive',room,payload(a,room));a.external_pending=None
    assert not a.change('robert','restore',room,payload(a,room),restore=True)['result']['archived']
    assert store.room('robert',room)['state']=='active'


@pytest.mark.parametrize('change',['message','state','archive_revision'])
def test_activity_version_or_archive_revision_race_refused(setup,change):
    _,store,a,room=setup;clear(a);body=payload(a,room)
    with store.connect() as db:
        if change=='message':store._event(db,room,'robert','message','Synthetic race')
        elif change=='state':db.execute('UPDATE rooms SET version=version+1 WHERE id=?',(room,))
        else:db.execute('INSERT INTO room_archive(room,version) VALUES(?,1)',(room,))
    with pytest.raises(Problem,match='Session activity changed'):
        a.change('robert','archive',room,body)


@pytest.mark.parametrize('kind',['turn','cos','coordination','auto','unknown_cos'])
def test_pending_backend_work_blocks_inside_write_transaction(setup,kind):
    _,store,a,room=setup;clear(a);body=payload(a,room)
    with store.connect() as db:
        if kind=='turn':db.execute('''INSERT INTO turns(id,room,generation,addressee,trigger_seq,attempt,state,created,expires,updated)
            VALUES('synthetic',?,'g','test',1,1,'uncertain',?,?,?)''',(room,OLD,OLD,OLD))
        elif kind in {'cos','unknown_cos'}:db.execute('INSERT INTO cos_requests VALUES(?,?,?,?,?,?,?,?,?)',('synthetic',room,'robert','reply','guard','uncertain' if kind=='cos' else 'future_unknown',OLD,OLD,None))
        elif kind=='coordination':db.execute('''INSERT INTO coordination_items(id,room,kind,title,body,requester,assignee,state,version,created,updated)
            VALUES('synthetic',?,'review','Synthetic','Synthetic','robert','test','result_submitted',1,?,?)''',(room,OLD,OLD))
        else:
            store._event(db,room,'robert','message','Synthetic eligible trigger')
            db.execute('UPDATE rooms SET updated=? WHERE id=?',(OLD,room))
            db.execute('INSERT INTO cos_auto VALUES(?,1,?,0,1,?,?)',(room,'g',(NOW+timedelta(hours=1)).isoformat(),'synthetic'))
    body=payload(a,room)
    with pytest.raises(Problem,match='backend_work_pending'):
        a.change('robert','archive',room,body)


@pytest.mark.parametrize('state',['pending','unavailable','stale','wrong_room','wrong_version','wrong_seq','throws'])
def test_external_proof_missing_stale_mismatched_or_pending_fails_closed(setup,state):
    _,_,a,room=setup
    def proof(identity,version,seq):
        if state=='throws':raise RuntimeError('SECRET ERROR MUST NOT ESCAPE')
        return module.ExternalWorkEvidence('wrong' if state=='wrong_room' else identity,
            state if state in {'pending','unavailable'} else 'clear',
            NOW-timedelta(seconds=16) if state=='stale' else NOW,
            version+1 if state=='wrong_version' else version,seq+1 if state=='wrong_seq' else seq)
    a.external_pending=proof
    body=payload(a,room)
    with pytest.raises(Problem,match='external_work_') as error:a.change('robert','archive',room,body)
    assert 'SECRET' not in str(error.value)


def test_age_boundary_and_invalid_timestamp_are_not_guessed(setup):
    _,store,a,room=setup;clear(a)
    for when,expected in [(NOW-timedelta(days=30)+timedelta(seconds=1),False),(NOW-timedelta(days=30),True)]:
        with store.connect() as db:db.execute('UPDATE rooms SET updated=? WHERE id=?',(when.isoformat(),room))
        assert a.preview('robert')['rooms'][0]['eligible'] is expected
    with store.connect() as db:db.execute("UPDATE rooms SET updated='bad' WHERE id=?",(room,))
    assert a.preview('robert')['rooms'][0]['reason']=='activity_unavailable'


def test_nonowner_and_client_external_override_refused(setup):
    _,_,a,room=setup;clear(a)
    with pytest.raises(Problem):a.preview('other')
    with pytest.raises(Problem):a.change('other','archive',room,payload(a,room))
    body=payload(a,room);body['external_clear']=True
    with pytest.raises(Problem):a.change('robert','archive',room,body)

@pytest.mark.parametrize('mode',['expired','exhausted','idle'])
def test_idle_or_expired_auto_mode_is_not_pending_work(setup,mode):
    _,store,a,room=setup;clear(a)
    expires=(NOW-timedelta(hours=1) if mode=='expired' else NOW+timedelta(hours=1)).isoformat()
    with store.connect() as db:
        cursor=db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE room=?',(room,)).fetchone()[0]
        db.execute('INSERT INTO cos_auto VALUES(?,1,?,?,?,?,?)',(room,'synthetic-generation',cursor,0 if mode=='exhausted' else 1,expires,'synthetic'))
    assert a.preview('robert')['rooms'][0]['eligible']
    assert a.change('robert','archive-'+mode,room,payload(a,room))['result']['archived']
    with store.connect() as db:assert db.execute('SELECT enabled FROM cos_auto WHERE room=?',(room,)).fetchone()[0]==1
