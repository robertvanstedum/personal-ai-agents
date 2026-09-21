"""Installation-grant enforcement on the real coordination HTTP routes."""
from datetime import datetime,timedelta,timezone
from test_coordination import setup


def credential(store,room,operations):
    issued=store.platform_access.issue('robert',dict(principal='reviewer',label='Synthetic coordination client',
        expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),grants={room:operations}))
    return issued,{'Authorization':'Bearer '+issued['access_token'],'Idempotency-Key':'scoped-request'}


def test_coordination_installation_grants_and_idempotency(setup):
    app,s,q,room,other,_=setup;client=app.test_client()
    s.membership('robert','second-room-membership',other,dict(actor='reviewer',role='contributor'))
    payload=dict(kind='review',title='Independent review',body='Review this candidate',assignee='robert')
    _,readonly=credential(s,room,['read'])
    assert client.get(f'/api/v1/rooms/{room}/coordination',headers=readonly).status_code==200
    assert client.post(f'/api/v1/rooms/{room}/coordination',headers=readonly,json=payload).status_code==403
    _,postonly=credential(s,room,['post'])
    assert client.get(f'/api/v1/rooms/{room}/coordination',headers=postonly).status_code==403
    created=client.post(f'/api/v1/rooms/{room}/coordination',headers=postonly,json=payload)
    assert created.status_code==201
    assert client.post(f'/api/v1/rooms/{room}/coordination',headers=postonly,json=payload).json==created.json
    assert client.post(f'/api/v1/rooms/{room}/coordination',headers=postonly,json={**payload,'title':'Changed payload'}).status_code==409
    assert client.post(f'/api/v1/rooms/{other}/coordination',headers=postonly,json=payload).status_code==403
    assert client.post(f'/api/v1/rooms/{room}/executive-snapshots',headers=postonly,json={}).status_code==403


def test_scoped_inbox_filters_membership_and_rejects_revoked_credential(setup):
    app,s,q,room,other,_=setup;client=app.test_client()
    s.membership('robert','second-membership',other,dict(actor='reviewer',role='contributor'))
    for n,destination in enumerate([room,other]):
        q.create('robert',f'item-{n}',destination,dict(kind='review',title=f'Candidate {n}',body='Please inspect',assignee='reviewer'))
    issued,headers=credential(s,room,['read','post'])
    inbox=client.get('/api/v1/coordination/inbox',headers=headers)
    assert inbox.status_code==200 and [x['room'] for x in inbox.json['items']]==[room]
    s.membership('robert','remove-member',room,dict(actor='reviewer',role='remove'))
    assert client.get('/api/v1/coordination/inbox',headers=headers).json['items']==[]
    s.platform_access.revoke('robert',issued['credential_id'])
    assert client.get('/api/v1/coordination/inbox',headers=headers).status_code==401
    assert client.get(f'/api/v1/rooms/{room}/coordination',headers=headers).status_code==401
