"""#262: the portal's generic proxy answers a slow backend honestly. A read
timeout is a 504 (JSON for a JSON caller, HTML otherwise), never an unhandled
500; CoS turn routes wait past the backend's own 120 s bound. No network."""
from __future__ import annotations

import pytest
import requests

OWNER = {"username": "robert", "tier": "owner", "display_name": "Robert", "auth_id": 1}


@pytest.fixture(autouse=True)
def _signed_out_after(portal_client):
    yield
    with portal_client.session_transaction() as sess:
        sess.pop("user", None)


@pytest.fixture
def backend(monkeypatch):
    import minimoi_portal.proxy as proxy_mod
    calls = []
    state = {"raise": None}

    class Resp:
        status_code, content, text = 200, b'{"reply": "ok"}', '{"reply": "ok"}'
        headers = {"content-type": "application/json"}

    def fake_request(method, url, headers=None, data=None, allow_redirects=False, timeout=None):
        calls.append({"method": method, "url": url, "timeout": timeout})
        if state["raise"] is not None:
            raise state["raise"]
        return Resp()

    monkeypatch.setattr(proxy_mod.requests, "request", fake_request)
    return calls, state


def _login(client):
    with client.session_transaction() as sess:
        sess["user"] = dict(OWNER)


TOKEN = "t" * 43


def _write_headers(client) -> dict:
    """A valid session token for CoS's web write guard when the portal has it
    (#267: COS_CSRF_SESSION_KEY); nothing extra on a portal without it."""
    import minimoi_portal.app as portal_app
    key = getattr(portal_app, "COS_CSRF_SESSION_KEY", None)
    if key is None:
        return {}
    with client.session_transaction() as sess:
        sess[key] = TOKEN
    return {"X-CSRF-Token": TOKEN}


def test_a_slow_cos_turn_is_an_honest_504_in_json_for_confer(portal_client, backend):
    calls, state = backend
    _login(portal_client)
    state["raise"] = requests.exceptions.ReadTimeout("read timed out")
    r = portal_client.post("/app/cos/ui/send", json={"text": "hi"}, headers=_write_headers(portal_client))
    body = r.get_json()
    assert r.status_code == 504 and body["error"] == "timeout"
    assert "may still finish" in body["message"] and "nothing was retried" in body["message"]
    assert body["reply"] == body["message"]                                   # Confer shows data.reply
    assert calls[0]["timeout"] == (5, 125)                                    # past Agent A's own 120 s


def test_a_slow_page_is_an_honest_504_in_html(portal_client, backend):
    calls, state = backend
    _login(portal_client)
    state["raise"] = requests.exceptions.ReadTimeout("read timed out")
    r = portal_client.get("/app/cos/ui/confer", headers={"Accept": "text/html"})
    assert r.status_code == 504 and r.content_type.startswith("text/html")
    assert "Still working" in r.get_data(as_text=True) and calls[0]["timeout"] == (5, 30)


def test_other_request_errors_are_a_502_not_a_500(portal_client, backend):
    calls, state = backend
    _login(portal_client)
    state["raise"] = requests.exceptions.ChunkedEncodingError("broken")
    r = portal_client.post("/app/cos/chat", json={"message": "hi"}, headers=_write_headers(portal_client))
    assert r.status_code == 502 and r.get_json()["error"] == "backend_error"
    assert calls[0]["timeout"] == (5, 125)


def test_the_connection_refused_answer_is_unchanged(portal_client, backend):
    calls, state = backend
    _login(portal_client)
    state["raise"] = requests.exceptions.ConnectionError("refused")
    r = portal_client.get("/app/cos/ui/confer")
    assert r.status_code == 503 and "Backend unavailable" in r.get_data(as_text=True)


def test_route_timeouts():
    from minimoi_portal.proxy import DEFAULT_TIMEOUT, _route_timeout
    assert _route_timeout("/app/cos", "ui/send", "POST") == (5, 125)
    assert _route_timeout("/app/cos", "/chat/", "post") == (5, 125)
    assert _route_timeout("/app/cos", "ui/send", "GET") == DEFAULT_TIMEOUT
    assert _route_timeout("/app/cos", "ui/transcribe", "POST") == DEFAULT_TIMEOUT
    assert _route_timeout("/app/curator", "ui/send", "POST") == DEFAULT_TIMEOUT
    from domains.cos.backends import openclaw_backend
    assert _route_timeout("/app/cos", "ui/send", "POST")[1] > openclaw_backend._TURN_TIMEOUT_SECONDS
