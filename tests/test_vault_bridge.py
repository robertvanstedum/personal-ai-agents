import json

import pytest
from flask import Flask

from minimoi_portal import vault_bridge as bridge

TOKEN = "a" * 64
URL = "https://dev.minimoi.ai/internal/vault/v1/ask"
ENV = {"MINIMOI_VAULT_BRIDGE": "1", "MINIMOI_VAULT_BRIDGE_BACKEND": "http://127.0.0.1:8771"}


class Upstream:
    def __init__(self, status=200, body=None, content_type="application/json"):
        self.status_code = status
        self.headers = {"Content-Type": content_type, "Set-Cookie": "must-not-forward", "Location": "https://elsewhere.invalid"}
        self.raw = json.dumps(body or {"status": "ok", "keyword": [], "meaning": []}).encode()
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        for start in range(0, len(self.raw), chunk_size):
            yield self.raw[start:start + chunk_size]


class Transport:
    def __init__(self, upstream=None):
        self.upstream = upstream or Upstream()
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.upstream


def setup(environ=None, base=bridge.ORIGIN, upstream=None):
    app, transport = Flask(__name__), Transport(upstream)
    bridge.install_if_dev(app, base_url=base, environ=ENV if environ is None else environ, transport=transport)
    return app.test_client(), transport


def send(client, **kw):
    return client.post(URL, headers={"Authorization": "Bearer " + TOKEN, **kw.pop("headers", {})}, json={"query": "earlier decision"}, **kw)


@pytest.mark.parametrize("base,env", [
    (bridge.ORIGIN, {}), (bridge.ORIGIN, {**ENV, "MINIMOI_VAULT_BRIDGE": "0"}),
    ("https://minimoi.ai", ENV), ("http://dev.minimoi.ai", ENV),
    (bridge.ORIGIN, {**ENV, "MINIMOI_VAULT_BRIDGE_BACKEND": "http://example.com:8771"}),
    (bridge.ORIGIN, {**ENV, "MINIMOI_VAULT_BRIDGE_BACKEND": "http://127.0.0.1:8771/"}),
])
def test_unconfigured_or_production_route_absent(base, env):
    client, transport = setup(env, base)
    assert send(client).status_code == 404
    assert not transport.calls


def test_only_fixed_ask_json_and_bearer_forwarded():
    client, transport = setup()
    response = send(client, headers={"Cookie": "owner-session", "X-Minimoi-Username": "robert", "X-Portal-Identity": "owner", "X-Forwarded-Host": "evil"})
    assert response.status_code == 200
    url, kwargs = transport.calls[0]
    assert url == "http://127.0.0.1:8771/v1/ask"
    assert kwargs["headers"] == {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json", "Accept": "application/json"}
    assert json.loads(kwargs["data"]) == {"query": "earlier decision"}
    assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
    assert kwargs["timeout"] == (2, 8)
    assert response.headers["Cache-Control"] == "no-store"
    assert not response.headers.get("Set-Cookie") and not response.headers.get("Location")
    assert transport.upstream.closed


@pytest.mark.parametrize("auth", ["", "Basic owner", "Bearer short", "Bearer " + "x" * 257, "Bearer token?leak"])
def test_cookie_or_malformed_bearer_insufficient(auth):
    client, transport = setup()
    assert send(client, headers={"Authorization": auth}).status_code == 401
    assert not transport.calls


def test_wrong_host_query_path_and_method_refused():
    client, transport = setup()
    assert client.post(URL.replace("dev.minimoi.ai", "minimoi.ai"), json={}, headers={"Authorization": "Bearer " + TOKEN}).status_code == 404
    assert client.post(URL + "?host=evil", json={}, headers={"Authorization": "Bearer " + TOKEN}).status_code == 400
    assert client.post(URL + "/other", json={}, headers={"Authorization": "Bearer " + TOKEN}).status_code == 404
    assert client.get(URL).status_code == 405
    assert not transport.calls


def test_wrong_content_type_and_oversized_payload_refused_before_transport():
    client, transport = setup()
    headers = {"Authorization": "Bearer " + TOKEN}
    assert client.post(URL, data="x", headers=headers).status_code == 415
    assert client.post(URL, data="x" * (bridge.MAX_BODY + 1), content_type="application/json", headers=headers).status_code == 413
    assert not transport.calls


@pytest.mark.parametrize("status,code", [(400, "bad_arguments"), (401, "unauthorized"), (403, "tier_not_permitted"), (429, "rate_limited"), (503, "registry_unavailable")])
def test_live_ask_denials_kept(status, code):
    client, _ = setup(upstream=Upstream(status, {"error": code, "secret": "not forwarded"}))
    response = send(client)
    assert response.status_code == status and response.json == {"error": code}


@pytest.mark.parametrize("upstream", [Upstream(302), Upstream(500), Upstream(content_type="text/html")])
def test_redirect_or_bad_upstream_refused(upstream):
    client, _ = setup(upstream=upstream)
    response = send(client)
    assert response.status_code == 503 and response.json == {"error": "unavailable"}
    assert upstream.closed


@pytest.mark.parametrize("raw", [b"not json SECRET", b"[]", b"x" * (bridge.MAX_RESPONSE + 1)], ids=["invalid-json", "json-not-object", "response-too-large"])
def test_malformed_or_oversized_answer_refused(raw):
    upstream = Upstream()
    upstream.raw = raw
    client, _ = setup(upstream=upstream)
    response = send(client)
    assert response.status_code == 503 and response.json == {"error": "unavailable"}


def test_exception_has_no_token_query_or_error_detail(monkeypatch):
    client, transport = setup()
    def broken(*args, **kwargs):
        raise RuntimeError(TOKEN + " private source")
    monkeypatch.setattr(transport, "post", broken)
    response = send(client)
    assert response.status_code == 503 and response.json == {"error": "unavailable"}


def test_slow_stream_refused(monkeypatch):
    client, transport = setup()
    ticks = iter([0, 1, 9])
    monkeypatch.setattr(bridge.time, "monotonic", lambda: next(ticks))
    response = send(client)
    assert response.status_code == 503
    assert transport.upstream.closed
