"""Explicit contact evidence must not invent authority, presence or billing."""
from test_platform_access import setup, auth


def test_join_two_rooms_recovery_and_truthful_identity(setup):
    app,store,owner,rooms,legacy,issue=setup
    credential=issue(); client=app.test_client()
    headers={**auth(credential),'Idempotency-Key':'join-a'}
    with store.connect() as db: before=db.execute('select count(*) from events').fetchone()[0]
    joined=client.post('/api/v1/rooms/'+rooms[0]+'/join',headers=headers,json={})
    assert joined.status_code==201
    evidence=joined.json['result']
    assert evidence['principal']=='claude-code'
    assert evidence['credential_id']==credential['credential_id']
    assert evidence['stage']=='joined'
    assert evidence['evidence']=='authenticated_contact'
    assert not evidence['live_presence'] and not evidence['runtime_attested'] and not evidence['billing_attested']
    assert client.post('/api/v1/rooms/'+rooms[0]+'/join',headers=headers,json={}).json==joined.json
    assert client.get('/api/v1/operations/join-a?destination='+rooms[0],headers=auth(credential)).json==joined.json
    assert client.post('/api/v1/rooms/'+rooms[1]+'/join',headers={**headers,'Idempotency-Key':'join-b'},json={}).status_code==201
    with store.connect() as db: assert db.execute('select count(*) from events').fetchone()[0]==before


def test_join_denies_forgery_outside_scope_and_revocation(setup):
    app,store,owner,rooms,legacy,issue=setup
    credential=issue();client=app.test_client();headers={**auth(credential),'Idempotency-Key':'join'}
    url='/api/v1/rooms/'+rooms[0]+'/join'
    assert client.post(url,headers=headers,json={'principal':'robert'}).status_code==400
    assert client.post('/api/v1/rooms/'+rooms[2]+'/join',headers=headers,json={}).status_code==403
    store.membership('robert','remove',rooms[0],dict(actor='claude-code',role='remove'))
    assert client.post(url,headers=headers,json={}).status_code==404
    store.platform_access.revoke('robert',credential['credential_id'])
    assert client.post('/api/v1/rooms/'+rooms[1]+'/join',headers=headers,json={}).status_code==401


def test_join_closed_paused_no_new_receipt(setup):
    app,store,owner,rooms,legacy,issue=setup
    credential=issue();client=app.test_client();headers={**auth(credential),'Idempotency-Key':'join'}
    for state in ['paused','closed']:
        with store.connect() as db:db.execute('update rooms set state=? where id=?',(state,rooms[0]))
        assert client.post('/api/v1/rooms/'+rooms[0]+'/join',headers=headers,json={}).status_code==409
    assert client.get('/api/v1/operations/join?destination='+rooms[0],headers=auth(credential)).status_code==404
