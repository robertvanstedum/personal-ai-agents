"""CoS web chat (/app/cos) uses the Shop floor's write guard.

Writes through the portal need the per-session token (X-CSRF-Token), a
same-origin Sec-Fetch-Site, a matching Origin, and the expected content type
(JSON; multipart for the audio upload). The token reaches Confer in its page
bootstrap. A refused write never reaches cos-scheduler. Reads, identity
forwarding, Telegram and the voice paths' own authentication are unchanged.
The backend is mocked: no network, no model call.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
OWNER = {"username": "robert", "tier": "owner", "display_name": "Robert", "auth_id": 1}
GUEST = {"username": "guest_ab12cd34", "tier": "guest", "display_name": "Guest"}
KEY = "cos_web_csrf"

CONFER_HTML = (b'<!DOCTYPE html><html><head><meta charset="utf-8">'
               b'<meta name="minimoi-csrf-token" content="from-the-backend">'
               b'<title>Confer</title></head><body><div id="chat-log"></div></body></html>')


class _Resp:
    def __init__(self, status=200, content=b'{"reply": "ok"}', ctype="application/json"):
        self.status_code = status
        self.content = content
        self.text = content.decode()
        self.headers = {"content-type": ctype}


@pytest.fixture(autouse=True)
def _signed_out_after(portal_client):
    yield
    with portal_client.session_transaction() as sess:
        sess.pop("user", None)
        sess.pop(KEY, None)


@pytest.fixture
def backend(monkeypatch):
    """Record every request the portal would forward to cos-scheduler."""
    import minimoi_portal.proxy as proxy_mod
    calls = []

    def fake_request(method, url, headers=None, data=None, allow_redirects=False, timeout=30):
        calls.append({"method": method, "url": url, "headers": dict(headers or {}), "data": data})
        if method == "GET" and "/ui" in url and "/api/" not in url:
            return _Resp(content=CONFER_HTML, ctype="text/html; charset=utf-8")
        return _Resp()

    monkeypatch.setattr(proxy_mod.requests, "request", fake_request)
    return calls


def _login(client, user=OWNER):
    with client.session_transaction() as sess:
        sess["user"] = dict(user)


def _open_confer(client):
    """Open Confer as the browser does; return the token from its bootstrap."""
    page = client.get("/app/cos/ui/confer")
    assert page.status_code == 200
    found = re.findall(r'<meta content="([^"]+)" name="minimoi-csrf-token"/?>', page.get_data(as_text=True))
    assert len(found) == 1, page.get_data(as_text=True)
    return found[0]


def _same_origin(token, **extra):
    return {"X-CSRF-Token": token, "Sec-Fetch-Site": "same-origin", "Origin": "http://localhost", **extra}


# ── the token in Confer's page bootstrap ───────────────────────────────────────

def test_confer_gets_this_sessions_token_and_never_the_backends(portal_client, backend):
    _login(portal_client)
    token = _open_confer(portal_client)
    with portal_client.session_transaction() as sess:
        assert sess[KEY] == token and len(token) >= 40
    assert "from-the-backend" not in portal_client.get("/app/cos/ui/confer").get_data(as_text=True)
    assert _open_confer(portal_client) == token                            # stable for the session
    root = portal_client.get("/app/cos/").get_data(as_text=True)
    assert f'content="{token}"' in root                                      # /app/cos opens Confer too


def test_the_guild_and_cos_tokens_are_separate(portal_client, backend):
    _login(portal_client)
    token = _open_confer(portal_client)
    with portal_client.session_transaction() as sess:
        assert sess.get("guild_floor_csrf") != token


# ── forged requests are refused and never reach cos-scheduler ─────────────────

def _forged_cases(token):
    good = _same_origin(token)
    return [
        ("ui/send", {"json": {"text": "hi"}, "headers": {}}, "token"),
        ("ui/send", {"json": {"text": "hi"}, "headers": {**good, "X-CSRF-Token": "wrong"}}, "token"),
        ("ui/send", {"json": {"text": "hi"}, "headers": {**good, "Sec-Fetch-Site": "same-site"}}, "cross-site"),
        ("ui/send", {"json": {"text": "hi"}, "headers": {**good, "Sec-Fetch-Site": "cross-site"}}, "cross-site"),
        ("ui/send", {"json": {"text": "hi"}, "headers": {**good, "Origin": "https://elsewhere.example"}}, "other origin"),
        ("ui/send", {"data": '{"text": "hi"}', "content_type": "text/plain", "headers": good}, "not JSON"),
        ("ui/send", {"data": {"text": "hi"}, "headers": good}, "not JSON"),                       # a form post
        ("chat", {"json": {"message": "hi"}, "headers": {}}, "token"),
        ("event", {"json": {"type": "x"}, "headers": {"Sec-Fetch-Site": "cross-site"}}, "cross-site"),
        ("ui/transcribe", {"data": {"audio": (io.BytesIO(b"RIFF"), "a.webm")}, "headers": {}}, "token"),
        ("ui/transcribe", {"data": {"audio": (io.BytesIO(b"RIFF"), "a.webm")},
                           "headers": {**good, "Sec-Fetch-Site": "cross-site"}}, "cross-site"),
        ("ui/transcribe", {"json": {"audio": "x"}, "headers": good}, "not multipart"),
        ("api/realtime-voice/confer/bootstrap", {"data": '{"provider": "openai"}', "content_type": "text/plain",
                                                  "headers": good}, "not JSON"),
        ("api/realtime-voice/confer/bootstrap", {"json": {"provider": "openai"}, "headers": {}}, "token"),
    ]


def test_forged_writes_are_refused_before_cos_scheduler(portal_client, backend):
    _login(portal_client)
    token = _open_confer(portal_client)
    backend.clear()
    for path, kw, why in _forged_cases(token):
        r = portal_client.post(f"/app/cos/{path}", **kw)
        body = r.get_json()
        assert r.status_code == 403, (path, why, r.status_code)
        assert body["error"] == "csrf" and f"({why})" in body["message"] and body["reply"] == body["message"], (path, why)
    assert backend == [], "a refused write must never reach cos-scheduler"


def test_a_token_from_another_session_is_refused(portal_client, backend):
    _login(portal_client)
    token = _open_confer(portal_client)
    with portal_client.session_transaction() as sess:
        sess[KEY] = "a-different-sessions-token-" + "x" * 20
    backend.clear()
    r = portal_client.post("/app/cos/ui/send", json={"text": "hi"}, headers=_same_origin(token))
    assert r.status_code == 403 and backend == []


def test_non_owners_are_still_turned_away_first(portal_client, backend):
    for user in (None, GUEST):
        if user:
            _login(portal_client, user)
        r = portal_client.post("/app/cos/ui/send", json={"text": "hi"}, headers={"X-CSRF-Token": "x"})
        assert r.status_code == 302
    assert backend == []


# ── same-origin writes with the token go through, as before ───────────────────

def test_text_send_with_the_token_is_forwarded_with_the_portals_identity(portal_client, backend):
    _login(portal_client)
    token = _open_confer(portal_client)
    backend.clear()
    body = {"text": "What is on today?", "channel": "html_text", "conversation_id": "owner", "request_id": "r1"}
    r = portal_client.post("/app/cos/ui/send", json=body, headers=_same_origin(token))
    assert r.status_code == 200 and r.get_json() == {"reply": "ok"}
    [call] = backend
    assert call["method"] == "POST" and call["url"].endswith("/ui/send")
    assert json.loads(call["data"]) == body
    assert call["headers"]["X-Minimoi-Auth-Id"] == "1" and call["headers"]["X-Minimoi-User-Tier"] == "owner"


def test_voice_send_and_bootstrap_keep_their_identity_path(portal_client, backend):
    """The voice paths still authenticate as today: cos-scheduler resolves the
    user from the portal's X-Minimoi-Auth-Id, which the guard leaves as is."""
    _login(portal_client)
    token = _open_confer(portal_client)
    backend.clear()
    r1 = portal_client.post("/app/cos/api/realtime-voice/confer/bootstrap", json={"provider": "openai"},
                            headers=_same_origin(token))
    r2 = portal_client.post("/app/cos/ui/send", json={"text": "note it", "channel": "html_voice",
                                                     "speech_output": False}, headers=_same_origin(token))
    assert r1.status_code == 200 and r2.status_code == 200
    assert [c["url"].rsplit("/", 1)[-1] for c in backend] == ["bootstrap", "send"]
    assert all(c["headers"]["X-Minimoi-Auth-Id"] == "1" for c in backend) and len(backend) == 2


def test_transcribe_multipart_with_the_token_is_forwarded(portal_client, backend):
    _login(portal_client)
    token = _open_confer(portal_client)
    backend.clear()
    r = portal_client.post("/app/cos/ui/transcribe", data={"audio": (io.BytesIO(b"RIFFdata"), "a.webm")},
                           headers=_same_origin(token))
    assert r.status_code == 200
    [call] = backend
    assert call["headers"]["Content-Type"].startswith("multipart/form-data") and b"RIFFdata" in call["data"]


def test_the_public_base_url_is_an_accepted_origin_and_a_bare_client_with_the_token_passes(portal_client, backend,
                                                                                           monkeypatch):
    import minimoi_portal.config as cfg
    monkeypatch.setattr(cfg, "BASE_URL", "https://portal.example")
    _login(portal_client)
    token = _open_confer(portal_client)
    backend.clear()
    ok_tunnel = portal_client.post("/app/cos/ui/send", json={"text": "a"},
                                   headers=_same_origin(token, Origin="https://portal.example"))
    ok_bare = portal_client.post("/app/cos/ui/send", json={"text": "b"}, headers={"X-CSRF-Token": token})
    assert ok_tunnel.status_code == 200 and ok_bare.status_code == 200 and len(backend) == 2


def test_reads_need_no_token(portal_client, backend):
    _login(portal_client)
    for path in ("/app/cos/ui/api/notes", "/app/cos/status", "/app/cos/api/realtime-voice/confer/capabilities"):
        assert portal_client.get(path).status_code == 200
    assert len(backend) == 3


# ── header forwarding is unchanged ─────────────────────────────────────────────

SPOOFED = {"X-Minimoi-Auth-Id": "999", "X-Minimoi-User-Tier": "owner", "X-Minimoi-Username": "someone",
           "X-Minimoi-Display-Name": "Someone", "x-minimoi-anything": "x"}


def test_client_sent_minimoi_headers_never_reach_cos_scheduler(portal_client, backend):
    _login(portal_client)
    token = _open_confer(portal_client)
    backend.clear()
    portal_client.get("/app/cos/ui/api/notes", headers=SPOOFED)
    portal_client.post("/app/cos/ui/send", json={"text": "hi"}, headers={**_same_origin(token), **SPOOFED})
    portal_client.post("/app/cos/ui/transcribe", data={"audio": (io.BytesIO(b"R"), "a.webm")},
                       headers={**_same_origin(token), **SPOOFED})
    assert len(backend) == 3
    for call in backend:
        sent = {k.lower(): v for k, v in call["headers"].items()}
        assert sent["x-minimoi-auth-id"] == "1" and sent["x-minimoi-user-tier"] == "owner"
        assert sent["x-minimoi-username"] == "robert" and sent["x-minimoi-display-name"] == "Robert"
        assert "x-minimoi-anything" not in sent


def test_forward_headers_strips_client_identity_and_adds_the_portals(portal_client):
    from minimoi_portal.app import app
    from minimoi_portal.proxy import _forward_headers
    with app.test_request_context("/app/cos/ui", headers={**SPOOFED, "Connection": "keep-alive",
                                                          "X-Demo-Role": "admin", "Accept": "text/html"}):
        plain = _forward_headers(OWNER)
        stripped = _forward_headers(OWNER, ("x-demo-",))
        anonymous = _forward_headers(None)
    assert {k: v for k, v in plain.items() if k.lower().startswith("x-minimoi-")} == {
        "X-Minimoi-User-Tier": "owner", "X-Minimoi-Display-Name": "Robert", "X-Minimoi-Auth-Id": "1",
        "X-Minimoi-Username": "robert"}
    assert "Connection" not in plain and "Host" not in plain and plain["Accept"] == "text/html"
    assert plain["X-Demo-Role"] == "admin" and "X-Demo-Role" not in stripped
    assert not [k for k in anonymous if k.lower().startswith("x-minimoi-")]


def test_proxy_to_uses_the_one_header_function():
    source = (REPO / "minimoi_portal/proxy.py").read_text()
    body = source[source.index("def proxy_to("):]
    assert "fwd_headers = _forward_headers(user, strip_header_prefixes)" in body
    assert 'startswith("x-minimoi-")' not in body


# ── Confer sends the token; Telegram and direct callers are untouched ─────────

def test_confer_sends_the_token_on_every_same_origin_write_and_nowhere_else():
    page = (REPO / "domains/cos/templates/cos_ui.html").read_text()
    assert "document.querySelector('meta[name=\"minimoi-csrf-token\"]')" in page
    assert "method !== 'GET' && method !== 'HEAD' && url.origin === window.location.origin" in page
    assert "headers.set('X-CSRF-Token', CSRF_TOKEN);" in page
    # The shared voice controller is untouched (German and Portuguese use it too).
    assert "X-CSRF-Token" not in (REPO / "core/realtime_voice/static/realtime-voice-controller.js").read_text()


def test_telegram_and_service_callers_do_not_go_through_the_portal():
    bot = (REPO / "core/telegram/telegram_cos_bot.py").read_text()
    assert "from domains.cos.chief_of_staff import _chat as cos_chat" in bot and "/app/cos" not in bot
    cos = (REPO / "domains/cos/chief_of_staff.py").read_text()
    assert "X-CSRF-Token" not in cos and "csrf" not in cos.lower()          # cos-scheduler itself is unchanged
