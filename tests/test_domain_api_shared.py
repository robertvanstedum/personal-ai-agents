"""Independent domain protocol tests; synthetic credentials and fixed endpoints."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from uuid import uuid4

import hashlib
import pytest

from core.domain_api.auth import Authenticator, KeyRegistry, ReplayWindow
from core.domain_api.client import DomainClient, Endpoint, Verified, NotApplied, Unknown
from core.domain_api.contract import DomainError, KeyProfile, TransportUnknown, decode, packed
from core.domain_api.protocol import request_headers, response_signature, verify_response

ROUTE = '/internal/domain-api/v1/german/woerter/phrases'
OP = '12345678-1234-4234-8234-123456789abc'

CAP = 'german.phrases.create'
DIGEST = hashlib.sha256(packed({'german': 'Hallo'})).hexdigest()


def receipt(operation=OP, **changes):
    value = dict(schema='minimoi.domain-operation/1', operation_id=operation,
                 domain='german', capability='phrases.create', state='verified',
                 result='created', target_ref='german:phrase:ph_001', target_revision=1,
                 request_digest=DIGEST, actor='cos', recorded_at='2026-10-22T00:00:00Z',
                 content_state='stored', replayed=False,
                 undo={'available': True, 'operation_ref': operation})
    value.update(changes)
    return value

NOW = 1800000000
RAW = packed({'german': 'Grüß dich!'})
CAP = 'german.phrases.create'


@pytest.fixture
def profile():
    return KeyProfile('cos-german-dev', 'robert', 'cos', 'dev', 'german',
                      frozenset({CAP, 'german.phrases.read'}), b'k' * 32, numeric_user_id=7)


def signed(profile, **kwargs):
    params = dict(method='POST', route=ROUTE, raw=RAW, operation=OP, now=NOW)
    params.update(kwargs)
    return request_headers(profile, **params)


def verify(auth, profile, *, headers=None, **kwargs):
    params = dict(method='POST', route=ROUTE, raw=RAW, headers=signed(profile) if headers is None else headers,
                  capability=CAP)
    params.update(kwargs)
    return auth.verify(**params)


def test_identity_is_bound_to_the_key_not_the_body(profile):
    auth = Authenticator(lambda _: profile, domain='german', environment='dev', clock=lambda: NOW)
    raw = packed({'principal': 'someone_else', 'actor': 'mc', 'german': 'Hallo'})
    context = verify(auth, profile, raw=raw, headers=signed(profile, raw=raw))
    assert context.profile.principal == 'robert' and context.profile.actor == 'cos'
    assert context.profile.numeric_user_id == 7
    # Domain payload schemas must reject these unknown fields; authentication
    # does not reinterpret model content as authority even before that rejection.
    assert b'k' * 32 not in repr(profile).encode()


@pytest.mark.parametrize('field,value', [('X-MiniMoi-Audience', 'curator'), ('X-MiniMoi-Environment', 'prod'),
    ('X-MiniMoi-Key', 'other'), ('X-MiniMoi-Domain-Version', '2'),
    ('Idempotency-Key', str(uuid4())), ('X-MiniMoi-Nonce', 'a' * 32), ('X-MiniMoi-Time', str(NOW - 1))])
def test_every_header_identity_is_signed(profile, field, value):
    headers = signed(profile)
    headers[field] = value
    auth = Authenticator(lambda _: profile, domain='german', environment='dev', clock=lambda: NOW)
    with pytest.raises(DomainError):
        verify(auth, profile, headers=headers)


def test_body_route_and_method_tampering_refuse(profile):
    auth = Authenticator(lambda _: profile, domain='german', environment='dev', clock=lambda: NOW)
    headers = signed(profile)
    for kwargs in ({'raw': RAW + b' '}, {'route': ROUTE + '/another'}, {'method': 'GET'}):
        with pytest.raises(DomainError):
            verify(auth, profile, headers=headers, **kwargs)


@pytest.mark.parametrize('age,allowed', [(30, True), (30.01, False), (-2, True), (-2.01, False)])
def test_exact_freshness_window(profile, age, allowed):
    auth = Authenticator(lambda _: profile, domain='german', environment='dev', clock=lambda: NOW + age)
    if allowed:
        verify(auth, profile)
    else:
        with pytest.raises(DomainError, match='signature_expired'):
            verify(auth, profile)


def test_nonce_replay_and_capacity_refuse_without_evicting_active_nonces(profile):
    replay = ReplayWindow(capacity=1)
    auth = Authenticator(lambda _: profile, domain='german', environment='dev', clock=lambda: NOW, replay=replay)
    headers = signed(profile)
    verify(auth, profile, headers=headers)
    with pytest.raises(DomainError, match='nonce_replayed'):
        verify(auth, profile, headers=headers)
    with pytest.raises(DomainError, match='nonce_capacity') as caught:
        verify(auth, profile)
    assert caught.value.status == 429
    with pytest.raises(DomainError, match='nonce_replayed'):
        verify(auth, profile, headers=headers)


def test_nonce_claim_is_atomic_across_concurrent_requests(profile):
    auth = Authenticator(lambda _: profile, domain='german', environment='dev', clock=lambda: NOW)
    headers = signed(profile)
    def attempt(_):
        try:
            verify(auth, profile, headers=headers)
            return True
        except DomainError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 1


def test_rate_limit_is_per_key_and_bounded(profile):
    profile = replace(profile, requests_per_minute=2)
    clock = [NOW]
    auth = Authenticator(lambda _: profile, domain='german', environment='dev', clock=lambda: clock[0],
                         replay=ReplayWindow(clock=lambda: clock[0]))
    verify(auth, profile)
    verify(auth, profile)
    with pytest.raises(DomainError, match='rate_limited'):
        verify(auth, profile)
    clock[0] += 60
    verify(auth, profile, headers=signed(profile, now=clock[0]))


@pytest.mark.parametrize('change', [dict(enabled=False), dict(actor='mc'), dict(principal='other'),
    dict(environment='prod'), dict(numeric_user_id=8), dict(secret=b'x' * 32),
    dict(capabilities=frozenset({'german.phrases.read'}))])
def test_live_revocation_and_scope_changes_fence_admitted_mutations(profile, change):
    current = [profile]
    auth = Authenticator(lambda _: current[0], domain='german', environment='dev', clock=lambda: NOW)
    context = verify(auth, profile)
    assert auth.revalidate(context, capability=CAP) == profile
    current[0] = replace(profile, **change)
    with pytest.raises(DomainError, match='scope_denied'):
        auth.revalidate(context, capability=CAP)


@pytest.mark.parametrize('change', [dict(principal='other'), dict(actor='mc'), dict(numeric_user_id=8)])
def test_old_wire_request_cannot_be_reinterpreted_under_reassigned_key(profile, change):
    headers = signed(profile)
    current = replace(profile, **change)
    auth = Authenticator(lambda _: current, domain='german', environment='dev', clock=lambda: NOW)
    with pytest.raises(DomainError, match='authentication_required'):
        verify(auth, profile, headers=headers)


def test_registry_provisions_separate_keys_and_revokes_live(profile):
    registry = KeyRegistry([profile])
    with pytest.raises(DomainError, match='key_not_independent'):
        registry.replace([profile, replace(profile, key_id='mc-german-dev', actor='mc')])
    assert registry.lookup(profile.key_id) == profile  # failed replacement is atomic
    other = replace(profile, key_id='mc-german-dev', actor='mc', secret=b'm' * 32)
    registry.replace([profile, other])
    auth = Authenticator(registry.lookup, domain='german', environment='dev', clock=lambda: NOW)
    context = verify(auth, profile)
    registry.replace([replace(profile, enabled=False), other])
    with pytest.raises(DomainError, match='scope_denied'):
        auth.revalidate(context, capability=CAP)


def test_retained_auth_context_expires_and_cannot_switch_capability(profile):
    clock = [NOW]
    auth = Authenticator(lambda _: profile, domain='german', environment='dev', clock=lambda: clock[0])
    context = verify(auth, profile)
    with pytest.raises(DomainError):
        auth.revalidate(context, capability='german.phrases.read')
    clock[0] += 31
    with pytest.raises(DomainError):
        auth.revalidate(context, capability=CAP)


@pytest.mark.parametrize('suffix', ['?x=1', '/../x', '/%2e%2e', '//x', '/', '/a.b'])
def test_no_query_normalization_or_path_escape(profile, suffix):
    with pytest.raises(DomainError, match='route_invalid'):
        signed(profile, route=ROUTE + suffix)


def test_get_body_operation_and_compression_rejected(profile):
    with pytest.raises(DomainError):
        signed(profile, method='GET')
    auth = Authenticator(lambda _: profile, domain='german', environment='dev', clock=lambda: NOW)
    headers = signed(profile)
    headers['Content-Encoding'] = 'gzip'
    with pytest.raises(DomainError, match='compression_refused'):
        verify(auth, profile, headers=headers)
    headers = signed(profile, method='GET', raw=b'', operation='')
    context = verify(auth, profile, headers=headers, method='GET', raw=b'', capability='german.phrases.read')
    assert context.operation_id == ''


@pytest.mark.parametrize('raw', [b'{"a":1,"a":2}', b'{"a":{"x":1,"x":2}}', b'{"x":NaN}',
                               b'{"x":Infinity}', b'[]', b'null', b'\xff', b'{'])
def test_json_is_strict(raw):
    with pytest.raises(DomainError):
        decode(raw)


def test_raw_unicode_signature_is_exact(profile):
    auth = Authenticator(lambda _: profile, domain='german', environment='dev', clock=lambda: NOW)
    ascii_raw = json.dumps({'german': 'Grüß dich!'}).encode()
    with pytest.raises(DomainError):
        verify(auth, profile, raw=ascii_raw, headers=signed(profile))


def test_response_signature_binds_status_nonce_scope_and_exact_bytes(profile):
    nonce = 'a' * 32
    raw = packed({'state': 'verified'})
    signature = response_signature(profile, nonce=nonce, status=201, raw=raw)
    verify_response(profile, nonce=nonce, status=201, raw=raw, signature=signature)
    for kwargs in ({'status': 200}, {'nonce': 'b' * 32}, {'raw': raw + b' '}):
        params = dict(nonce=nonce, status=201, raw=raw, signature=signature)
        params.update(kwargs)
        with pytest.raises(DomainError):
            verify_response(profile, **params)


@pytest.mark.parametrize('host', ['example.com', '169.254.169.254', '0.0.0.0', '8.8.8.8', 'http://german',
                                 'german@evil', '/tmp/service', '127.0.0.1/path', '224.0.0.1'])
def test_endpoint_refuses_public_metadata_and_model_urls(host):
    with pytest.raises(DomainError):
        Endpoint('german', host, 8767)


def test_client_never_retries_and_preserves_operation_for_reconciliation(profile):
    attempts = []
    def timeout(endpoint, method, route, raw, headers):
        attempts.append(headers)
        raise TimeoutError('private provider detail must not escape')
    client = DomainClient(profile, Endpoint('german', '127.0.0.1', 8767), clock=lambda: NOW, exchange=timeout)
    for _ in range(2):
        result = client.submit(ROUTE, {'german': 'Hallo'}, operation_id=OP, capability=CAP, expected_request_digest=DIGEST)
        assert result == Unknown(OP) and 'private' not in repr(result)
    assert len(attempts) == 2  # two explicit caller attempts, no automatic retries
    assert all(h['Idempotency-Key'] == OP for h in attempts)
    assert attempts[0]['X-MiniMoi-Nonce'] != attempts[1]['X-MiniMoi-Nonce']


def test_client_only_believes_signed_replies_and_does_not_follow_redirects(profile):
    mode = ['ok']
    sent = []
    def exchange(endpoint, method, route, raw, headers):
        sent.append((method, route, headers))
        status = 302 if mode[0] == 'redirect' else 201
        body = packed({'receipt': receipt()})
        signature = response_signature(profile, nonce=headers['X-MiniMoi-Nonce'], status=status, raw=body)
        if mode[0] == 'tampered':
            body += b' '
        return status, body, signature
    client = DomainClient(profile, Endpoint('german', 'german', 8767), clock=lambda: NOW, exchange=exchange)
    assert isinstance(client.submit(ROUTE, {'german': 'Hallo'}, operation_id=OP, capability=CAP, expected_request_digest=DIGEST), Verified)
    for value in ['redirect', 'tampered']:
        mode[0] = value
        assert client.submit(ROUTE, {'german': 'Hallo'}, operation_id=OP, capability=CAP, expected_request_digest=DIGEST) == Unknown(OP)
    assert len(sent) == 3


def test_oversized_or_noncanonical_request_never_sends(profile):
    client = DomainClient(profile, Endpoint('german', 'german', 8767), exchange=lambda *args: pytest.fail('sent'))
    with pytest.raises(DomainError, match='body_bound'):
        client.submit(ROUTE, {'german': 'x' * 17000}, operation_id=OP, capability=CAP, expected_request_digest=DIGEST)
    with pytest.raises(DomainError, match='operation_id_invalid'):
        client.submit(ROUTE, {}, operation_id=OP.upper(), capability=CAP, expected_request_digest=DIGEST)


@pytest.mark.parametrize('path', ['internal/domain-api/v1/german/woerter/phrases',
    '/internal/domain-api/v1/german/woerter/phrases',
    'x/../internal/domain-api/v1/german/woerter/phrases',
    '%69nternal%2fdomain-api/v1/german/woerter/phrases',
    '%2569nternal%252fdomain-api/v1/german/woerter/phrases'])
def test_public_proxy_never_forwards_internal_api(path):
    from flask import Flask
    from unittest.mock import patch
    from minimoi_portal.proxy import proxy_to
    app = Flask(__name__)
    with app.test_request_context('/app/german/' + path), \
            patch('minimoi_portal.proxy.requests.request') as transport:
        response = proxy_to('http://german:8767', path, '/app/german', user={'tier': 'owner', 'auth_id': 7})
    assert response.status_code == 404
    transport.assert_not_called()


@pytest.mark.parametrize('host', ['fd00:ec2::254', 'fd00:ec2::23', '::ffff:169.254.169.254'])
def test_ipv6_metadata_is_refused(host):
    with pytest.raises(DomainError, match='endpoint_not_internal'):
        Endpoint('german', host, 8767)


@pytest.mark.parametrize('host', ['127.0.0.1', '::1', '10.0.0.2', '192.168.1.2', '172.17.0.2'])
def test_internal_and_loopback_endpoints_remain_usable(host):
    assert Endpoint('german', host, 8767).host == host


@pytest.mark.parametrize('known', [True, False])
@pytest.mark.parametrize('change', [{'X-MiniMoi-Audience': 'curator'}, {'X-MiniMoi-Environment': 'prod'}, {}])
def test_unauthenticated_key_probes_have_uniform_errors(profile, known, change):
    headers = signed(profile)
    headers.update(change)
    headers['X-MiniMoi-Signature'] = '0' * 64
    auth = Authenticator(lambda _: profile if known else None, domain='german', environment='dev')
    with pytest.raises(DomainError) as caught:
        verify(auth, profile, headers=headers, raw=b'')
    assert (caught.value.status, caught.value.code) == (401, 'authentication_required')


@pytest.mark.parametrize('change', [dict(schema='other'), dict(state='unknown'), dict(operation_id=str(uuid4())),
    dict(request_digest='0' * 64), dict(domain='curator'), dict(actor='mc'), dict(capability='phrases.undo'),
    dict(target_revision=True), dict(recorded_at='yesterday'), dict(replayed='false'), dict(undo={})])
def test_signed_mismatched_receipt_never_confirms_a_save(profile, change):
    def exchange(endpoint, method, route, raw, headers):
        body = packed({'receipt': receipt(**change)})
        return 201, body, response_signature(profile, nonce=headers['X-MiniMoi-Nonce'], status=201, raw=body)
    client = DomainClient(profile, Endpoint('german', 'german', 8767), exchange=exchange)
    assert client.submit(ROUTE, {'german': 'Hallo'}, operation_id=OP,
                         capability=CAP, expected_request_digest=DIGEST) == Unknown(OP)


@pytest.mark.parametrize('body,status', [({'state': 'verified'}, 201),
    ({'error': 'persistence_unconfirmed', 'state': 'unknown'}, 503)])
def test_missing_receipt_and_signed_unknown_use_same_outcome(profile, body, status):
    def exchange(endpoint, method, route, raw, headers):
        encoded = packed(body)
        return status, encoded, response_signature(profile, nonce=headers['X-MiniMoi-Nonce'], status=status, raw=encoded)
    client = DomainClient(profile, Endpoint('german', 'german', 8767), exchange=exchange)
    assert client.submit(ROUTE, {'german': 'Hallo'}, operation_id=OP,
                         capability=CAP, expected_request_digest=DIGEST) == Unknown(OP)
    if status == 503:
        with pytest.raises(TransportUnknown):
            client.read(ROUTE)


def test_receipt_digest_uses_trusted_normalized_expectation_not_raw_wire(profile):
    def exchange(endpoint, method, route, raw, headers):
        assert hashlib.sha256(raw).hexdigest() != DIGEST  # whitespace normalized by domain adapter
        body = packed({'receipt': receipt()})
        return 201, body, response_signature(profile, nonce=headers['X-MiniMoi-Nonce'], status=201, raw=body)
    client = DomainClient(profile, Endpoint('german', 'german', 8767), exchange=exchange)
    assert isinstance(client.submit(ROUTE, {'german': ' Hallo '}, operation_id=OP,
                                    capability=CAP, expected_request_digest=DIGEST), Verified)


def test_nonce_retention_is_monotonic_and_capacity_is_per_key(profile):
    ticks = [0]
    window = ReplayWindow(capacity=1, clock=lambda: ticks[0])
    other = replace(profile, key_id='other', secret=b'o' * 32)
    window.claim(profile, 'a')
    window.claim(other, 'a')  # first key does not exhaust second key's space
    ticks[0] = 32
    with pytest.raises(DomainError, match='nonce_replayed'):
        window.claim(profile, 'a')
    ticks[0] = 34
    window.claim(profile, 'a')


def test_clock_rollback_does_not_resurrect_expired_signed_requests(profile):
    wall, ticks = [NOW], [0]
    auth = Authenticator(lambda _: profile, domain='german', environment='dev', clock=lambda: wall[0],
                         replay=ReplayWindow(clock=lambda: ticks[0]))
    headers = signed(profile)
    verify(auth, profile, headers=headers)
    wall[0], ticks[0] = NOW + 100, 100
    with pytest.raises(DomainError, match='signature_expired|clock_quarantine'):
        verify(auth, profile, headers=headers)
    wall[0] = NOW
    with pytest.raises(DomainError, match='signature_expired|clock_quarantine'):
        verify(auth, profile, headers=headers)


def test_frozen_wall_clock_does_not_extend_signature_lifetime(profile, monkeypatch):
    import core.domain_api.auth as module
    ticks = [0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: ticks[0])
    auth = Authenticator(lambda _: profile, domain='german', environment='dev', clock=lambda: NOW,
                         replay=ReplayWindow(clock=lambda: ticks[0]))
    headers = signed(profile)
    verify(auth, profile, headers=headers)
    ticks[0] = 34  # nonce has expired but the wall clock has stopped
    with pytest.raises(DomainError, match='signature_expired|clock_quarantine'):
        verify(auth, profile, headers=headers)


def test_forward_clock_correction_recovers_after_stable_full_window(profile):
    ticks, wall = [0.0], [NOW]
    auth = Authenticator(lambda _: profile, domain='german', environment='dev', clock=lambda: wall[0],
                         monotonic=lambda: ticks[0], replay=ReplayWindow(clock=lambda: ticks[0]))
    verify(auth, profile)
    ticks[0] = 1; wall[0] = NOW + 3600
    verify(auth, profile, headers=signed(profile, now=wall[0]))
    ticks[0] = 2; wall[0] = NOW + 2
    with pytest.raises(DomainError, match='clock_quarantine'):
        verify(auth, profile, headers=signed(profile, now=wall[0]))
    ticks[0] = 34; wall[0] = NOW + 34
    with pytest.raises(DomainError, match='clock_quarantine'):
        verify(auth, profile, headers=signed(profile, now=wall[0]))
    ticks[0] = 35; wall[0] = NOW + 35
    verify(auth, profile, headers=signed(profile, now=wall[0]))
    with pytest.raises(DomainError, match='signature_expired'):
        verify(auth, profile, headers=signed(profile, now=NOW + 3600))


def test_listener_startup_quarantine_expires_old_nonce_window(profile):
    ticks = [0.0]
    auth = Authenticator(lambda _: profile, domain='german', environment='dev', clock=lambda: NOW + ticks[0],
                         monotonic=lambda: ticks[0], startup_quarantine=33)
    old = signed(profile)
    with pytest.raises(DomainError, match='startup_quarantine'):
        verify(auth, profile, headers=old)
    ticks[0] = 33
    with pytest.raises(DomainError, match='signature_expired|clock_quarantine'):
        verify(auth, profile, headers=old)
    verify(auth, profile, headers=signed(profile, now=NOW + 33))
