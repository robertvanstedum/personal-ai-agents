"""Real loopback HTTP transport; fake domain data and synthetic independent key."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import time

import hashlib
import pytest

from core.domain_api.auth import Authenticator, KeyRegistry
from core.domain_api.client import DomainClient, Endpoint, Verified, NotApplied, Unknown
from core.domain_api.contract import KeyProfile, MAX_RESPONSE, TransportUnknown, packed
from core.domain_api.protocol import response_signature

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



@pytest.fixture
def server():
    profile = KeyProfile('cos-german-dev', 'robert', 'cos', 'dev', 'german',
                         frozenset({'german.phrases.create', 'german.phrases.read'}), b's' * 32)
    auth = Authenticator(KeyRegistry([profile]).lookup, domain='german', environment='dev')
    state = {'mode': 'ok', 'requests': []}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.receive('german.phrases.create')

        def do_GET(self):
            self.receive('german.phrases.read')

        def receive(self, capability):
            raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
            context = auth.verify(method=self.command, route=self.path, raw=raw, headers=self.headers,
                                  capability=capability)
            state['requests'].append({'method': self.command, 'raw': raw, 'context': context})
            mode = state['mode']
            if mode == 'disconnect':
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if mode == 'slow_headers':
                try:
                    self.wfile.write(b'HTTP/1.1 200 OK\r\nX-Slow: ')
                    self.wfile.flush()
                    for _ in range(10):
                        time.sleep(0.8)
                        self.wfile.write(b'x')
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            status = 409 if mode == 'refused' else 302 if mode == 'redirect' else 201 if self.command == 'POST' else 200
            body = packed({'error': 'idempotency_mismatch', 'state': 'not_applied'} if mode == 'refused'
                          else {'item': {'german': 'Hallo'}, 'receipt': receipt(context.operation_id)})
            if mode == 'unknown':
                status, body = 503, packed({'error': 'persistence_unconfirmed', 'state': 'unknown'})
            if mode == 'duplicate_json':
                body = b'{"item":1,"item":2}'
            signature = response_signature(profile, nonce=context.nonce, status=status, raw=body)
            if mode == 'large':
                body = b'x' * (MAX_RESPONSE + 1)
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-MiniMoi-Receipt', signature)
            if mode == 'compressed':
                self.send_header('Content-Encoding', 'gzip')
            if mode == 'redirect':
                self.send_header('Location', 'http://169.254.169.254/latest/meta-data')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    client = DomainClient(profile, Endpoint('german', '127.0.0.1', http.server_port))
    try:
        yield state, client
    finally:
        http.shutdown()
        http.server_close()
        worker.join(timeout=2)


def test_signed_http_round_trip_ignores_proxy_environment(server, monkeypatch):
    state, client = server
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    response = client.submit(ROUTE, {'german': 'Hallo'}, operation_id=OP, capability=CAP, expected_request_digest=DIGEST)
    assert isinstance(response, Verified) and response.receipt['operation_id'] == OP
    response = client.read(ROUTE)
    assert response.status == 200 and state['requests'][-1]['raw'] == b''
    assert len(state['requests']) == 2


def test_signed_refusal_is_distinct_from_unknown(server):
    state, client = server
    state['mode'] = 'refused'
    reply = client.submit(ROUTE, {'german': 'Hallo'}, operation_id=OP, capability=CAP, expected_request_digest=DIGEST)
    assert reply == NotApplied(OP, 'idempotency_mismatch')


@pytest.mark.parametrize('mode', ['redirect', 'large', 'compressed', 'duplicate_json', 'disconnect', 'unknown'])
def test_invalid_or_incomplete_http_result_is_unknown_without_retry(server, mode):
    state, client = server
    state['mode'] = mode
    assert client.submit(ROUTE, {'german': 'Hallo'}, operation_id=OP, capability=CAP, expected_request_digest=DIGEST) == Unknown(OP)
    assert len(state['requests']) == 1


def test_slow_headers_cannot_extend_the_total_response_deadline(server):
    state, client = server
    state['mode'] = 'slow_headers'
    started = time.monotonic()
    assert client.submit(ROUTE, {'german': 'Hallo'}, operation_id=OP, capability=CAP, expected_request_digest=DIGEST) == Unknown(OP)
    elapsed = time.monotonic() - started
    assert 4.5 <= elapsed < 7.5
    assert len(state['requests']) == 1


def test_dns_timeout_is_bounded_and_worker_capacity_is_not_unbounded(monkeypatch):
    import core.domain_api.client as transport
    entered, release = threading.Event(), threading.Event()
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(transport, '_RESOLVER_SLOTS', slots)
    def blocked(*args, **kwargs):
        entered.set()
        release.wait(2)
        return []
    monkeypatch.setattr(transport.socket, 'getaddrinfo', blocked)
    try:
        started = time.monotonic()
        with pytest.raises(transport.DomainError, match='resolution_failed'):
            transport._connect(Endpoint('german', 'german', 8767), started + 0.05)
        assert entered.is_set() and time.monotonic() - started < 0.5
        with pytest.raises(transport.DomainError, match='resolver_busy'):
            transport._connect(Endpoint('german', 'german', 8767), time.monotonic() + 1)
    finally:
        release.set()
        assert slots.acquire(timeout=2)
        slots.release()


@pytest.mark.parametrize('address', ['8.8.8.8', '169.254.169.254', 'fd00:ec2::254'])
def test_named_endpoint_cannot_resolve_to_public_or_metadata(monkeypatch, address):
    import core.domain_api.client as transport
    monkeypatch.setattr(transport.socket, 'getaddrinfo', lambda *a, **k:
                        [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 8767))])
    monkeypatch.setattr(transport.socket, 'socket', lambda *a, **k: pytest.fail('must not connect'))
    with pytest.raises(transport.DomainError, match='endpoint_not_internal'):
        transport._connect(Endpoint('german', 'german', 8767), time.monotonic() + 2)


def test_multiple_addresses_share_one_connect_deadline(monkeypatch):
    import core.domain_api.client as transport
    ticks, timeouts, closes = [0.0], [], []
    monkeypatch.setattr(transport.time, 'monotonic', lambda: ticks[0])
    addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 8767))] * 3
    monkeypatch.setattr(transport.socket, 'getaddrinfo', lambda *a, **k: addresses)
    class Socket:
        def settimeout(self, timeout):
            timeouts.append(timeout)
        def connect(self, address):
            ticks[0] += min(1.5, timeouts[-1])
            raise TimeoutError()
        def close(self):
            closes.append(True)
    monkeypatch.setattr(transport.socket, 'socket', lambda *a: Socket())
    with pytest.raises(transport.DomainError, match='connection_deadline'):
        transport._connect(Endpoint('german', 'german', 8767), 2.0)
    assert timeouts == [2.0, 0.5] and len(closes) == 2
