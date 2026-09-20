"""Four acceptance cases requested by Claude's independent increment-01 review."""
import base64
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from app import create_app


@pytest.fixture
def context(tmp_path):
    app = create_app(tmp_path / 'private', testing=True)
    store = app.extensions['records_store']
    room = store.create_room('robert', 'room', dict(
        title='Review acceptance', purpose='Synthetic evidence only',
        recording_acknowledged=True))['result']['id']

    def installation(principal, operations, **extra):
        with store.connect() as db:
            exists = db.execute('SELECT 1 FROM principals WHERE id=?', (principal,)).fetchone()
        if not exists:
            store.add_principal('robert', str(uuid4()), dict(id=principal, label=principal))
            store.membership('robert', str(uuid4()), room, dict(actor=principal))
        return store.platform_access.issue('robert', dict(
            principal=principal, label='Synthetic client',
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            grants={room: operations}, **extra))

    return app.test_client(), store, room, installation


def headers(credential, key=None):
    result = {'Authorization': 'Bearer ' + credential['access_token']}
    if key:
        result['Idempotency-Key'] = key
    return result


def test_absent_usage_evidence_survives_import_response_and_api_export(context):
    client, store, room, installation = context
    credential = installation('claude-code', ['read', 'post', 'export'])
    response = client.post(f'/api/v1/rooms/{room}/imports', headers=headers(credential, 'usage'), json=dict(
        source_application='synthetic-client', coverage='One available turn and derived handoff',
        turns=[dict(speaker='robert', text='Quoted text')], handoff='Proposed follow-up'))
    assert response.status_code == 201
    imported = response.json['result']['records']
    exported = client.get(f'/api/v1/rooms/{room}/export', headers=headers(credential))
    assert exported.status_code == 200
    import json
    events = {event['id']: event for event in json.loads(exported.data)['events']}
    assert len(imported) == 2
    for record in imported:
        evidence = record['origin']['usage_evidence']
        assert evidence['status'] == 'none'
        assert evidence['reason'] == 'explicit import; no inference invoked'
        assert events[record['id']]['origin']['usage_evidence'] == evidence
        assert 'cost' not in evidence and 'tokens' not in evidence


def test_valid_upload_requires_explicit_upload_grant(context):
    client, store, room, installation = context
    denied = installation('claude-code', ['read', 'post'])
    permitted = installation('claude-code', ['read', 'upload'])
    payload = dict(name='synthetic.txt', source_note='Synthetic fixture',
                   base64=base64.b64encode(b'Valid source bytes').decode(), context_class='external_source')
    with store.connect() as db:
        before = db.execute('SELECT count(*) FROM documents').fetchone()[0]
    response = client.post(f'/api/v1/rooms/{room}/documents', headers=headers(denied, 'denied-upload'), json=payload)
    assert response.status_code == 403
    assert 'Credential does not grant' in response.json['error']
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM documents').fetchone()[0] == before
        assert db.execute("SELECT 1 FROM operations WHERE actor='claude-code' AND key='denied-upload'").fetchone() is None
    # Identical valid payload succeeds with a separate installation's upload grant.
    allowed = client.post(f'/api/v1/rooms/{room}/documents', headers=headers(permitted, 'permitted-upload'), json=payload)
    assert allowed.status_code == 201


def test_receipt_keys_are_isolated_between_principals_in_same_room(context):
    client, store, room, installation = context
    first = installation('claude-code', ['read', 'post', 'receipt'])
    second = installation('grok-cli', ['read', 'post', 'receipt'])
    key = 'same-request-id'
    first_write = client.post(f'/api/v1/rooms/{room}/events', headers=headers(first, key), json={'body': 'First principal payload'})
    assert first_write.status_code == 201
    lookup = f'/api/v1/operations/{key}?destination={room}'
    assert client.get(lookup, headers=headers(second)).status_code == 404
    second_write = client.post(f'/api/v1/rooms/{room}/events', headers=headers(second, key), json={'body': 'Second principal payload'})
    assert second_write.status_code == 201
    assert first_write.json['receipt']['id'] != second_write.json['receipt']['id']
    assert client.get(lookup, headers=headers(first)).json == first_write.json
    assert client.get(lookup, headers=headers(second)).json == second_write.json


def test_rotation_preserves_historical_actor_and_original_receipt(context):
    client, store, room, installation = context
    first = installation('claude-code', ['read', 'post', 'receipt'])
    write = client.post(f'/api/v1/rooms/{room}/events', headers=headers(first, 'before-rotation'), json={'body': 'Historical original'})
    assert write.status_code == 201
    before = write.json
    rotated = installation('claude-code', ['read', 'post', 'receipt'],
                           installation_id=first['installation_id'], rotate_credential_id=first['credential_id'])
    lookup = f'/api/v1/operations/before-rotation?destination={room}'
    assert client.get(lookup, headers=headers(first)).status_code == 401
    assert client.get(lookup, headers=headers(rotated)).json == before
    current = client.get(f'/api/v1/rooms/{room}', headers=headers(rotated))
    event = next(event for event in current.json['events'] if event['id'] == before['result']['id'])
    assert event['actor'] == before['result']['actor'] == before['receipt']['actor'] == 'claude-code'
    assert event['body'] == before['result']['body'] == 'Historical original'
    assert event['created'] == before['result']['created']
