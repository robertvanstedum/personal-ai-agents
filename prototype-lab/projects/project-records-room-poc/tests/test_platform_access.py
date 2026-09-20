"""Platform lifecycle, room authorization and saved-response recovery boundaries."""
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest
from app import create_app
from store import Store
from platform_access import AccessError


@pytest.fixture
def setup(tmp_path):
    app=create_app(tmp_path/'private',testing=True)
    store=app.extensions['records_store']
    owner={'Authorization':'Bearer '+store.owner_key}
    rooms=[]
    for name in ['A','B','C']:
        rooms.append(store.create_room('robert',str(uuid4()),dict(title=name,purpose='Synthetic test',recording_acknowledged=True))['result']['id'])
    legacy=store.add_principal('robert','principal',dict(id='claude-code',label='Claude Code'))['access_token']
    for room in rooms: store.membership('robert',str(uuid4()),room,dict(actor='claude-code'))
    def issue(grants=None,**extra):
        return store.platform_access.issue('robert',dict(principal='claude-code',label='Test installation',
            expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
            grants=grants or {rooms[0]:['read','post','receipt'],rooms[1]:['read','receipt']},**extra))
    return app,store,owner,rooms,legacy,issue


def auth(credential): return {'Authorization':'Bearer '+credential['access_token']}


def test_two_installations_revocation_cookie_and_legacy_retirement(setup):
    app,store,owner,rooms,legacy,issue=setup
    first,second=issue(),issue()
    client=app.test_client()
    assert client.post('/api/login',json={'token':first['access_token']}).status_code==200
    assert client.get('/api/v1/rooms/'+rooms[0]).status_code==200
    assert client.post('/api/v1/platform/credentials/'+first['credential_id']+'/revoke',headers=owner,json={}).status_code==200
    assert client.get('/api/v1/rooms/'+rooms[0]).status_code==401
    assert client.get('/api/v1/rooms/'+rooms[0],headers=auth(second)).status_code==200
    assert store.authenticate(legacy)
    store.platform_access.revoke('robert','legacy:claude-code')
    assert store.authenticate(legacy) is None
    assert Store(store.root).authenticate(legacy) is None  # migration must not resurrect
    assert store.authenticate(second['access_token'])['id']=='claude-code'


def test_membership_independent_from_installation_scope(setup):
    app,store,owner,rooms,legacy,issue=setup
    credential=issue(); client=app.test_client(); headers=auth(credential)
    assert {r['id'] for r in client.get('/api/v1/rooms',headers=headers).json['rooms']}==set(rooms[:2])
    assert client.get('/api/v1/rooms/'+rooms[2],headers=headers).status_code==403
    assert client.get('/api/v2/rooms',headers=headers).status_code==403
    assert client.get('/api/v1/search?q=private',headers=headers).status_code==403
    assert client.post('/api/v1/rooms/'+rooms[1]+'/events',headers={**headers,'Idempotency-Key':'bad'},json={'body':'denied'}).status_code==403
    assert client.post('/api/v1/rooms/'+rooms[0]+'/documents',headers={**headers,'Idempotency-Key':'badfile'},json={}).status_code in {400,403}
    store.membership('robert','remove-a',rooms[0],dict(actor='claude-code',role='remove'))
    assert client.get('/api/v1/rooms/'+rooms[0],headers=headers).status_code==404
    assert client.get('/api/v1/rooms/'+rooms[1],headers=headers).status_code==200
    assert store.authenticate(credential['access_token'])


def test_receipt_conflict_destination_and_revocation(setup):
    app,store,owner,rooms,legacy,issue=setup
    cred=issue(); client=app.test_client(); headers={**auth(cred),'Idempotency-Key':'recover'}
    path='/api/v1/rooms/'+rooms[0]+'/events'
    first=client.post(path,headers=headers,json={'body':'Saved exactly'}).json
    assert client.post(path,headers=headers,json={'body':'Changed'}).status_code==409
    assert client.post(path,headers=headers,json={'body':'Saved exactly'}).json==first
    lookup='/api/v1/operations/recover'
    assert client.get(lookup,headers=headers).status_code==400
    assert client.get(lookup+'?destination='+rooms[1],headers=headers).status_code==404
    assert client.get(lookup+'?destination='+rooms[0],headers=headers).json==first
    store.membership('robert','remove-a',rooms[0],dict(actor='claude-code',role='remove'))
    assert client.get(lookup+'?destination='+rooms[0],headers=headers).status_code==404


def test_expiry_rotation_no_secret_in_database(setup):
    app,store,owner,rooms,legacy,issue=setup
    first,second=issue(),issue()
    rotated=issue(installation_id=first['installation_id'],rotate_credential_id=first['credential_id'])
    assert store.authenticate(first['access_token']) is None
    assert store.authenticate(rotated['access_token'])
    assert store.authenticate(second['access_token'])
    with store.connect() as db:
        db.execute("UPDATE client_credentials SET expires='2000-01-01T00:00:00+00:00' WHERE id=?",(rotated['credential_id'],))
        dump='\n'.join(db.iterdump())
    assert store.authenticate(rotated['access_token']) is None
    for c in (first,second,rotated): assert c['access_token'] not in dump
    assert store.platform_access.authenticate(credential_id=rotated['credential_id']) is None


def test_owner_only_and_explicit_existing_grants(setup):
    app,store,owner,rooms,legacy,issue=setup
    with pytest.raises(AccessError): store.platform_access.issue('claude-code',{})
    with pytest.raises(AccessError): issue(grants={'unregistered':['read']})
    with pytest.raises(AccessError): issue(grants={rooms[0]:['admin']})
    first=issue()
    response=app.test_client().post('/api/v1/platform/credentials',headers=auth(first),json={})
    assert response.status_code==403
    with pytest.raises(AccessError): store.platform_access.revoke('robert','legacy:robert')


def test_import_declared_speakers_atomicity_export_and_closed_state(setup):
    from transcript_snapshot import capture
    from transcript_format import render
    app,store,owner,rooms,legacy,issue=setup
    cred=issue();client=app.test_client();headers={**auth(cred),'Idempotency-Key':'import-1'}
    labels=['robert','claude-code','unknown','outside-team','<img src=x onerror=alert(1)>']
    payload=dict(source_application='synthetic-client',coverage='Selected five turns; earlier chat unavailable',
                 turns=[dict(speaker=label,text='  Exact text\nsecond line  ',source_created_at='2026-09-20T10:00:00Z') for label in labels],
                 handoff='Proposed assignment only. Not approved.')
    path='/api/v1/rooms/'+rooms[0]+'/imports'
    response=client.post(path,headers=headers,json=payload)
    assert response.status_code==201,response.json
    result=response.json['result']; assert len(result['records'])==6
    assert all(record['actor']=='claude-code' for record in result['records'])
    assert [x['origin']['declared_speaker'] for x in result['records'][:-1]]==labels
    assert all(x['kind']=='message' for x in result['records'][:-1])
    assert result['records'][-1]['origin']['source_record_ids']==result['transcript_ids']
    assert client.post(path,headers=headers,json=payload).json==response.json
    legacy_export=store.export('robert',rooms[0])
    assert any(e['origin'] and e['origin'].get('declared_speaker')=='robert' and e['actor']=='claude-code' for e in legacy_export['events'])
    exported,_=capture(store,'robert',rooms[0])
    imported=[r for r in exported['raw_transcript'] if r.get('imported_source')]
    assert imported[0]['imported_source']['declared_speaker']=='robert'
    assert imported[0]['speaker_id']==imported[0]['submitted_by']=='claude-code'
    assert imported[0]['text']=='  Exact text\nsecond line  '
    bundle=render(exported,snapshot_at='2026-09-20T12:00:00Z')
    assert b'declared, not authenticated speaker' in bundle['transcript.md']
    with store.connect() as db:
        before=db.execute('SELECT count(*) FROM events').fetchone()[0]
    bad={**payload,'turns':[{'speaker':'robert','text':'pretend approval','kind':'decision'}]}
    assert client.post(path,headers={**headers,'Idempotency-Key':'invalid'},json=bad).status_code==400
    with store.connect() as db: assert db.execute('SELECT count(*) FROM events').fetchone()[0]==before
    store.state('robert','close',rooms[0],dict(state='closed',version=store.room('robert',rooms[0])['version'],checkpoint='Done'))
    assert client.post(path,headers={**headers,'Idempotency-Key':'closed'},json=payload).status_code==409


@pytest.mark.parametrize('payload',[
    dict(source_application='client',coverage='one available turn',turns=[dict(speaker='robert',text='Original')]),
    dict(source_application='client',coverage='summary only, no transcript available',handoff='A proposed next action'),
])
def test_transcript_only_and_handoff_only(setup,payload):
    app,store,owner,rooms,legacy,issue=setup
    result=store.import_conversation('claude-code',str(uuid4()),rooms[0],payload)['result']
    assert len(result['records'])==1
    assert bool(result['handoff_id'])==('handoff' in payload)


def test_cross_room_disclosure_is_separate_and_metadata_filtered(setup):
    app,store,owner,rooms,legacy,issue=setup
    source=store.append('robert','source',rooms[0],dict(body='Explicitly chosen content'))['result']['id']
    credential=issue(grants={rooms[0]:['read'],rooms[1]:['read','post','receipt']})
    client=app.test_client();headers={**auth(credential),'Idempotency-Key':'move'}
    target='/api/v1/rooms/'+rooms[1]+'/transfers'
    assert client.post(target,headers=headers,json={'grant_id':'absent'}).status_code==403
    payload=dict(actor='claude-code',destination=rooms[1],event_ids=[source],expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
    grant=client.post('/api/v1/rooms/'+rooms[0]+'/disclosures',headers={**owner,'Idempotency-Key':'grant'},json=payload)
    assert grant.status_code==201,grant.json
    gid=grant.json['result']['grant_id']
    response=client.post(target,headers=headers,json={'grant_id':gid})
    assert response.status_code==201,response.json
    result=response.json['result']; assert result['records'][0]['actor']=='claude-code'
    assert result['records'][0]['kind']=='message'
    assert rooms[0] not in json.dumps(response.json) and source not in json.dumps(response.json)
    assert client.post(target,headers=headers,json={'grant_id':gid}).json==response.json
    with store.connect() as db:
        audit=db.execute('SELECT * FROM room_transfers WHERE id=?',(result['transfer_id'],)).fetchone()
        assert audit['source']==rooms[0] and source in audit['event_ids']
    client.post('/api/v1/rooms/'+rooms[0]+'/disclosures',headers={**owner,'Idempotency-Key':'revoke-grant'},json={'revoke':gid})
    assert client.post(target,headers={**headers,'Idempotency-Key':'new-after-revoke'},json={'grant_id':gid}).status_code==403
