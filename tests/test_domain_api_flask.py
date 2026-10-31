"""Exercise the actual Werkzeug decoding and Flask ingress boundaries."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from flask import Flask, request
import pytest

from core.domain_api.auth import Authenticator, KeyRegistry
from core.domain_api.contract import DomainError, KeyProfile, MAX_REQUEST
from core.domain_api.flask_adapter import verify_request
from core.domain_api.protocol import request_headers
from minimoi_portal.proxy import proxy_to

ROUTE = '/internal/domain-api/v1/german/woerter/phrases'


@pytest.fixture
def ingress():
    profile = KeyProfile('test-key', 'robert', 'cos', 'dev', 'german',
                         frozenset({'german.phrases.read'}), b't' * 32)
    auth = Authenticator(KeyRegistry([profile]).lookup, domain='german', environment='dev', clock=lambda: 1800000000)
    app = Flask(__name__)
    admitted = []
    @app.route(ROUTE, methods=['GET', 'POST'])
    def read():
        try:
            context, raw = verify_request(auth, request, capability='german.phrases.read')
        except DomainError as exc:
            return {'error': exc.code}, exc.status
        admitted.append((context, raw))
        return {'ok': True}
    return app.test_client(), profile, admitted


@pytest.mark.parametrize('suffix', ['?owner=someone_else&limit=100000', '?x=1', '?%00=1'])
def test_unsigned_query_rejected_before_handler_admission(ingress, suffix):
    client, profile, admitted = ingress
    headers = request_headers(profile, method='GET', route=ROUTE, raw=b'', now=1800000000)
    response = client.get(ROUTE + suffix, headers=headers)
    assert response.status_code == 400 and response.json['error'] == 'query_refused'
    assert admitted == []
    # The rejected alias consumed no nonce. The exact signed request still works.
    assert client.get(ROUTE, headers=headers).status_code == 200


def test_flask_ingress_enforces_body_bound(ingress):
    client, _, admitted = ingress
    assert client.post(ROUTE, data=b'x' * (MAX_REQUEST + 1)).status_code == 413
    assert admitted == []


def test_proxy_decoding_cannot_reach_internal_routes():
    received = []
    class Backend(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        app = Flask(__name__)
        @app.route('/app/german/<path:path>')
        def proxy(path):
            return proxy_to(f'http://127.0.0.1:{server.server_port}', path, '/app/german',
                            user={'tier': 'owner', 'auth_id': 7})
        client = app.test_client()
        assert client.get('/app/german/health').status_code == 200
        assert received == ['/health']
        for delimiter in ['%3F', '%23', '%253F', '%2523', '%5C', '%3B', '%00']:
            path = '/app/german' + ROUTE + delimiter + '%2F..' * 6 + '%2Fx'
            assert client.get(path).status_code == 404, path
        assert received == ['/health']
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_real_raw_proxy_refuses_dot_empty_and_encoded_aliases():
    import socket
    from werkzeug.serving import make_server
    received = []
    class Backend(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
        def log_message(self, *args): pass
    backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
    worker = threading.Thread(target=backend.serve_forever, daemon=True); worker.start()
    app = Flask(__name__)
    @app.route('/app/german/<path:path>')
    @app.route('/api/<path:path>')
    @app.route('/research/<path:path>')
    def proxy(path):
        return proxy_to(f'http://127.0.0.1:{backend.server_port}', path, '/app/german', user={'tier':'owner'})
    portal = make_server('127.0.0.1', 0, app, threaded=True)
    pw = threading.Thread(target=portal.serve_forever, daemon=True); pw.start()
    try:
        for prefix in ['/app/german/', '/api/', '/research/']:
            for tail in ['internal//../domain-api/v1/german/woerter/phrases',
                         'internal%2F%2F..%2Fdomain-api/v1/german/woerter/phrases',
                         'woerter%2F..%2Finternal%2F%2F..%2Fdomain-api/v1/german/woerter/phrases']:
                with socket.create_connection(('127.0.0.1', portal.server_port), timeout=2) as sock:
                    sock.sendall(('GET '+prefix+tail+' HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n').encode())
                    status = sock.recv(4096).split(b'\r\n',1)[0]
                    assert b'404' in status, (prefix, tail, status)
        assert received == []
    finally:
        portal.shutdown(); portal.server_close(); pw.join(timeout=2)
        backend.shutdown(); backend.server_close(); worker.join(timeout=2)


def test_proxy_refuses_every_internal_api_not_only_the_domain_api():
    # CoS 1.2 review S3: the CoS store's internal API (and any other /internal/ route) is the compose network's,
    # never reachable through the owner's browser proxy.
    from minimoi_portal.proxy import _internal_domain_api_path
    for path in ('internal/cos-store/v1/commands', '/internal/cos-store/v1/records/cos.goal/G1',
                 'internal/model-gateway/receipt', 'internal', 'ui/../internal/cos-store/v1/commands',
                 'internal%2Fcos-store%2Fv1%2Fcommands', 'internal/domain-api/v1/german/woerter/phrases'):
        assert _internal_domain_api_path(path), path
    for path in ('ui/send', 'ui/history/conversations', 'health', 'internalish/page'):
        assert not _internal_domain_api_path(path), path
