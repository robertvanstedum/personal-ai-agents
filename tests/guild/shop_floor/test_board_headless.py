"""Actual signed HTTP and domain persistence, using only disposable fixtures."""
from dataclasses import replace
from threading import Thread
from uuid import uuid4
import time
import pytest
from werkzeug.serving import make_server
from core.domain_api.auth import Authenticator, KeyRegistry
from core.domain_api.client import Endpoint, Verified, NotApplied, Unknown
from core.domain_api.contract import packed
from core.domain_api.protocol import request_headers, verify_response
from domains.guild.board_operations import BASE
from domains.guild.board_client import BoardClient
from minimoi_portal.guild_ui.board_headless import create_app
from test_board_actions import board_case, CAPS


@pytest.fixture
def headless(board_case):
    from core.domain_api.contract import KeyProfile
    db, store, _, _ = board_case
    profile = KeyProfile('board-test-dev', 'test-owner', 'test-application', 'dev', 'guild',
                         CAPS | {'guild.board.capabilities.read'}, b'synthetic-independent-key-12345678')
    registry = KeyRegistry([profile])
    auth = Authenticator(registry.lookup, domain='guild', environment='dev')
    app = create_app(store=store, owner='test-owner', environment='dev', cursor_key=b'test-cursor-key' * 3,
                     registry=registry, item_allowed=lambda owner, item: item == 12, authenticator=auth)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    worker = Thread(target=server.serve_forever, daemon=True); worker.start()
    client = BoardClient(Endpoint('guild', '127.0.0.1', server.server_port), profile, board=store.floor)
    try:
        yield db, store, app, client, profile, registry
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=2)


def test_signed_create_query_edit_done_undo_and_recovery(headless):
    db, store, app, client, profile, registry = headless
    op = str(uuid4())
    first = client.call('note.create', {'text': 'Morgen prüfen 😀'}, operation_uuid=op)
    assert isinstance(first, Verified), first
    ref = first.receipt['target_ref']
    replay = client.call('note.create', {'text': 'Morgen prüfen 😀'}, operation_uuid=op)
    assert isinstance(replay, Verified) and replay.body['replayed']
    assert db.count('floor_postits') == 1
    conflict = client.call('note.create', {'text': 'Different'}, operation_uuid=op)
    assert isinstance(conflict, NotApplied)
    query = client.call('notes.query', {'query': 'Morgen'})
    assert query.body['notes'][0]['id'] == ref
    assert client.call('note.read', target=ref).body['semantic_revision'] == 1
    change = client.call('note.update', {'changes': {'label': 'idea', 'done': True}, 'expected_revision': 1},
                         target=ref, operation_uuid=str(uuid4()))
    assert isinstance(change, Verified) and change.body['current']['state'] == 'done'
    undone = client.call('operation.undo', {'expected_revision': 2}, target=change.receipt['operation_id'],
                         operation_uuid=str(uuid4()))
    assert isinstance(undone, Verified) and undone.body['current']['state'] == 'active'
    recovered = client.call('operation.read', target=op)
    assert recovered.body['receipt'] == first.receipt and recovered.body['current']['semantic_revision'] == 3
    assert 'guild.board.note.create' in client.call('capabilities.read').body['capabilities']


def test_signed_ingress_rejects_identity_fields_tamper_replay_and_wrong_scope(headless):
    db, store, app, client, profile, registry = headless
    http = app.test_client()
    route = BASE + '/notes'
    raw = packed({'text': 'one'})
    headers = request_headers(profile, method='POST', route=route, raw=raw, operation=str(uuid4()), now=time.time())
    assert http.post(route, data=raw + b' ', headers=headers).status_code == 401
    assert http.post(route + '?owner=other', data=raw, headers=headers).status_code == 400
    reply = http.post(route, data=raw, headers=headers)
    assert reply.status_code == 200
    verify_response(profile, nonce=headers['X-MiniMoi-Nonce'], status=reply.status_code,
                    raw=reply.data, signature=reply.headers['X-MiniMoi-Receipt'])
    assert http.post(route, data=raw, headers=headers).status_code == 401
    for fields in ({'principal': 'other'}, {'executor': 'mc'}, {'operation_id': str(uuid4())}):
        bad = packed({'text': 'two', **fields})
        h = request_headers(profile, method='POST', route=route, raw=bad, operation=str(uuid4()), now=time.time())
        assert http.post(route, data=bad, headers=h).status_code == 400
    for changed in (replace(profile, principal='other'), replace(profile, actor='mc-chat'), replace(profile, environment='prod')):
        registry.replace([changed])
        h = request_headers(changed, method='POST', route=route, raw=raw, operation=str(uuid4()), now=time.time())
        assert http.post(route, data=raw, headers=h).status_code == 403
    assert db.count('floor_postits') == 1


def test_lost_reply_is_unknown_and_same_id_recovers(headless):
    db, store, app, client, profile, registry = headless
    send = client.transport.exchange
    def lost(*args):
        send(*args)
        raise TimeoutError('synthetic dropped response after real commit')
    client.transport.exchange = lost
    op = str(uuid4())
    assert isinstance(client.call('note.create', {'text': 'exactly once'}, operation_uuid=op), Unknown)
    client.transport.exchange = send
    assert client.call('operation.read', target=op).body['state'] == 'verified'
    assert isinstance(client.call('note.create', {'text': 'exactly once'}, operation_uuid=op), Verified)
    assert db.count('floor_postits') == 1


def test_revocation_and_app_scope_prevent_undo(headless):
    db, store, app, client, profile, registry = headless
    op = str(uuid4())
    first = client.call('note.create', {'text': 'keep'}, operation_uuid=op)
    assert isinstance(first, Verified)
    second = replace(profile, key_id='second-key', actor='other-app', secret=b'other-independent-synthetic-key-32')
    registry.replace([profile, second])
    other = BoardClient(client.transport.endpoint, second, board=store.floor)
    assert other.call('operation.read', target=op).body['state'] == 'unknown'
    assert isinstance(other.call('operation.undo', {'expected_revision': 1}, target=op, operation_uuid=str(uuid4())), NotApplied)
    registry.replace([replace(profile, enabled=False), second])
    assert isinstance(client.call('note.create', {'text': 'forbidden'}, operation_uuid=str(uuid4())), Unknown)
    # Unsigned rejection is unknown to the remote client, and database evidence proves no effect here.
    assert db.count('floor_postits') == 1


def test_process_lease_excludes_second_worker_and_releases_on_exit(board_case):
    import os
    dsn = os.environ.get('BOARD_ACTIONS_TEST_DSN')
    if not dsn:
        pytest.skip('requires disposable PostgreSQL')
    import psycopg2
    from core.domain_api.contract import DomainError
    from minimoi_portal.guild_ui.board_headless_runner import SingleProcessLease
    connect = lambda: psycopg2.connect(dsn)
    owner = 'lease-' + uuid4().hex
    first = SingleProcessLease(connect, owner=owner, environment='dev')
    try:
        first.check()
        with pytest.raises(DomainError, match='board_api_already_running'):
            SingleProcessLease(connect, owner=owner, environment='dev')
        independent = SingleProcessLease(connect, owner=owner, environment='prod')
        independent.close()
    finally:
        first.close()
    successor = SingleProcessLease(connect, owner=owner, environment='dev')
    successor.check(); successor.close()


def test_profile_file_changes_are_live_and_private(tmp_path):
    import json
    from minimoi_portal.guild_ui.board_headless_runner import InstalledKeys, private_bytes
    from core.domain_api.contract import DomainError
    path = tmp_path / 'synthetic-profiles.json'
    row = dict(key_id='test-key', principal='test-owner', actor='test-app', environment='dev', domain='guild',
               capabilities=['guild.board.notes.read'], secret_hex=(b'fake-test-key-32bytes-long-enough!').hex())
    path.write_text(json.dumps({'profiles': [row]})); path.chmod(0o600)
    class Lease:
        def check(self): pass
    registry = InstalledKeys(path, Lease())
    with registry.admission():
        assert registry.lookup('test-key').enabled
        row['enabled'] = False
        path.write_text(json.dumps({'profiles': [row]}))
        assert not registry.lookup('test-key').enabled
    path.chmod(0o644)
    with pytest.raises(DomainError, match='private_file_required'):
        private_bytes(path)
    path.chmod(0o600)
    symlink = tmp_path / 'link'; symlink.symlink_to(path)
    with pytest.raises(OSError):
        private_bytes(symlink)
