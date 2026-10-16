"""Applied-package HTTP acceptance; run after the private patch is integrated."""
from datetime import datetime, timedelta, timezone
import pytest
import archive as module
from app import create_app

NOW=datetime(2026,10,14,12,tzinfo=timezone.utc)


@pytest.fixture
def ready(tmp_path):
    proof=lambda room,version,seq:module.ExternalWorkEvidence(room,'clear',NOW,version,seq)
    app=create_app(tmp_path/'private',testing=True,archive_external_pending=proof)
    store=app.extensions['records_store'];store.archive.clock=lambda:NOW
    room=store.create_room('robert','create',dict(title='Synthetic',purpose='Temporary only',recording_acknowledged=True))['result']['id']
    with store.connect() as db:db.execute('UPDATE rooms SET updated=? WHERE id=?',((NOW-timedelta(days=31)).isoformat(),room))
    owner={'Authorization':'Bearer '+store.owner_key,'Idempotency-Key':'archive'}
    return app,store,room,owner


def test_authenticated_archive_list_history_restore_and_idempotent_receipt(ready):
    app,store,room,owner=ready;client=app.test_client()
    snapshot=client.get('/api/v1/rooms/archive-preview',headers=owner)
    assert snapshot.status_code==200
    item=snapshot.json['rooms'][0]
    body={k:item[k] for k in ('version','archive_version','latest_seq','last_activity')}
    before=client.get('/api/v1/rooms/'+room,headers=owner).json
    saved=client.post('/api/v1/rooms/'+room+'/archive',headers=owner,json=body)
    assert saved.status_code==200 and saved.json['result']['archived']
    assert client.post('/api/v1/rooms/'+room+'/archive',headers=owner,json=body).json==saved.json
    assert client.get('/api/v1/rooms',headers=owner).json['rooms']==[]
    assert client.get('/api/v1/rooms?view=archived',headers=owner).json['rooms'][0]['id']==room
    assert client.get('/api/v1/rooms/'+room,headers=owner).json==before
    item=client.get('/api/v1/rooms/archive-preview',headers=owner).json['rooms'][0]
    body={k:item[k] for k in ('version','archive_version','latest_seq','last_activity')}
    owner={**owner,'Idempotency-Key':'restore'}
    assert client.post('/api/v1/rooms/'+room+'/restore',headers=owner,json=body).status_code==200
    assert client.get('/api/v1/rooms',headers=owner).json['rooms'][0]['id']==room


def test_archive_check_and_reconcile_bind_actual_room_route(ready):
    app,store,room,owner=ready;client=app.test_client()
    checked=client.get('/api/v1/rooms/'+room+'/archive-check',headers=owner)
    assert checked.status_code==200 and checked.json['room']==room
    # A malformed request is refused by the actual handler rather than Flask
    # failing argument binding before the archive service is reached.
    reply=client.post('/api/v1/rooms/'+room+'/archive-reconcile',headers=owner,json={})
    assert reply.status_code==400


def test_unauthenticated_and_nonowner_archive_are_refused(ready):
    app,store,room,owner=ready;client=app.test_client()
    assert client.get('/api/v1/rooms/archive-preview').status_code==401
    token=store.add_principal('robert','other',dict(id='other',label='Synthetic other'))['access_token']
    headers={'Authorization':'Bearer '+token,'Idempotency-Key':'forbidden'}
    assert client.get('/api/v1/rooms/archive-preview',headers=headers).status_code==403
    assert client.post('/api/v1/rooms/'+room+'/archive',headers=headers,json={}).status_code==403


def test_default_external_connection_absence_refuses_http_archive(tmp_path):
    app=create_app(tmp_path/'private',testing=True);store=app.extensions['records_store']
    store.archive.clock=lambda:NOW
    room=store.create_room('robert','create',dict(title='Synthetic',purpose='Temporary only',recording_acknowledged=True))['result']['id']
    with store.connect() as db:db.execute('UPDATE rooms SET updated=? WHERE id=?',((NOW-timedelta(days=31)).isoformat(),room))
    client=app.test_client();owner={'Authorization':'Bearer '+store.owner_key,'Idempotency-Key':'archive'}
    item=client.get('/api/v1/rooms/archive-preview',headers=owner).json['rooms'][0]
    body={k:item[k] for k in ('version','archive_version','latest_seq','last_activity')}
    assert client.post('/api/v1/rooms/'+room+'/archive',headers=owner,json=body).status_code==409


def test_signed_server_proof_accepted_and_bearer_forgery_refused(tmp_path):
    from minimoi_portal import rooms_identity as identity
    import json
    key=('synthetic',b'synthetic-archive-signing-key-000000000000')
    app=create_app(tmp_path/'private',testing=True,portal_key=key)
    store=app.extensions['records_store'];store.archive.clock=lambda:NOW
    room=store.create_room('robert','create',dict(title='Synthetic',purpose='Temporary only',recording_acknowledged=True))['result']['id']
    with store.connect() as db:db.execute('UPDATE rooms SET updated=? WHERE id=?',((NOW-timedelta(days=31)).isoformat(),room))
    metadata=store.archive.work_snapshot('robert',room)
    payload={k:metadata[k] for k in ('version','archive_version','latest_seq','last_activity')}
    proof={'kind':'portal_locked_archive_v1','room':room,'state':'clear','checked_at':NOW.isoformat(),
           'room_version':metadata['version'],'latest_seq':metadata['latest_seq'],
           'mapping_sha256':'a'*64,'request_count':0}
    payload['_portal_archive_proof']=proof
    path=f'/api/v1/rooms/{room}/archive';raw=json.dumps(payload).encode()
    client=app.test_client()
    forged=client.post(path,data=raw,content_type='application/json',headers={
        'Authorization':'Bearer '+store.owner_key,'Idempotency-Key':'forged'})
    assert forged.status_code==403
    signed={'X-Portal-Identity':identity.sign(key,principal='robert',method='POST',target=path,body=raw,env='dev'),
            'Idempotency-Key':'archive-signed'}
    response=client.post(path,data=raw,content_type='application/json',headers=signed)
    assert response.status_code==200 and response.json['result']['archived']
    assert response.json['result']['external_proof']['mapping_sha256']=='a'*64
    assert module.VERIFIED_EXTERNAL.get() is None


def test_new_recorded_activity_reveals_archived_session_without_closing(ready):
    app,store,room,owner=ready
    item=store.archive.preview('robert')['rooms'][0]
    body={k:item[k] for k in ('version','archive_version','latest_seq','last_activity')}
    store.archive.change('robert','archive',room,body)
    assert store.rooms('robert')==[]
    store.append('robert','new-activity',room,{'body':'Synthetic new owner message'})
    assert store.rooms('robert')[0]['id']==room
    assert store.room('robert',room)['state']=='active'


def test_expired_attempt_reconciliation_uses_write_barrier(ready):
    _,store,room,_=ready
    item=store.archive.preview('robert')['rooms'][0]
    original={k:item[k] for k in ('version','archive_version','latest_seq','last_activity')}
    fresh={'operation_key':'not-sent','checked_at':(NOW-timedelta(seconds=29)).isoformat(),'payload':original}
    with pytest.raises(Exception,match='still uncertain'):store.archive.reconcile('robert',room,fresh)
    expired={'operation_key':'not-sent','checked_at':(NOW-timedelta(seconds=31)).isoformat(),'payload':original}
    assert store.archive.reconcile('robert',room,expired)=={'state':'not_committed'}
    item=store.archive.preview('robert')['rooms'][0]
    body={k:item[k] for k in ('version','archive_version','latest_seq','last_activity')}
    saved=store.archive.change('robert','archive',room,body)
    expired['operation_key']='archive'
    resolved=store.archive.reconcile('robert',room,expired)
    assert resolved['state']=='committed' and resolved['receipt']==saved


def test_reconciliation_never_claims_other_payload_commit(ready):
    _,store,room,_=ready
    item=store.archive.preview('robert')['rooms'][0]
    body={k:item[k] for k in ('version','archive_version','latest_seq','last_activity')}
    store.archive.change('robert','archive',room,body)
    wrong={**body,'latest_seq':body['latest_seq']+1}
    request={'operation_key':'archive','checked_at':(NOW-timedelta(seconds=31)).isoformat(),'payload':wrong}
    assert store.archive.reconcile('robert',room,request)=={'state':'conflict'}


def test_parent_and_legacy_lists_agree_on_archive_view_and_keep_direct_history(ready):
    app,store,room,owner=ready;client=app.test_client()
    before=client.get('/api/v1/rooms/'+room,headers=owner).json
    parent=before['parent_room_id']
    item=store.archive.preview('robert')['rooms'][0]
    body={k:item[k] for k in ('version','archive_version','latest_seq','last_activity')}
    assert client.post('/api/v1/rooms/'+room+'/archive',headers=owner,json=body).status_code==200
    assert client.get('/api/v1/rooms',headers=owner).json['rooms']==[]
    assert client.get('/api/v2/rooms/'+parent,headers=owner).json['sessions']==[]
    assert client.get('/api/v2/rooms',headers=owner).json['rooms'][0]['visible_session_count']==0
    for path,key in [('/api/v1/rooms?view=archived','rooms'),('/api/v2/rooms/'+parent+'?view=archived','sessions')]:
        value=client.get(path,headers=owner).json[key][0]
        assert value['id']==room and value['archived_at'] and value['archive_version']==1
    assert client.get('/api/v2/rooms?view=archived',headers=owner).json['rooms'][0]['visible_session_count']==1
    assert client.get('/api/v1/rooms/'+room,headers=owner).json==before
    assert client.get('/api/v2/rooms/'+parent+'?view=invalid',headers=owner).status_code==400
