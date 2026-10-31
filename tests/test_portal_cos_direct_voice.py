"""The portal for Chief of Staff's direct voice (Spec 165 v0.3 §6.7; A2): one prefix, api/realtime-voice/direct/, for every
route of hers. Its POSTs are CSRF-checked whatever the legacy guard says and only then forwarded with the verified-write
marker, browser X-Minimoi-* stripped; its GETs need the owner only and are never cached; consult and summary have their own
timeouts. Tested through the portal as owner, as non-owner and with forged headers. No backend, provider or network."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent / "guild" / "shop_floor"))
from floor_helpers import load_portal  # noqa: E402,F401  (pytest fixture)

from minimoi_portal import proxy  # noqa: E402

DIRECT = "/app/cos/api/realtime-voice/direct/api/realtime-voice"
SID = "7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f"


def _portal(load_portal, monkeypatch, base_url="https://minimoi.ai", guard="off"):
    monkeypatch.setenv("MINIMOI_COS_WRITE_GUARD", guard)
    monkeypatch.delenv("MINIMOI_COS_MANAGED_REALTIME", raising=False)
    portal = load_portal(base_url=base_url, next_flag=None)
    forwarded = []
    monkeypatch.setattr(portal.module._proxy, "proxy_to", lambda *a, **kw: forwarded.append((a, kw)) or ("{}", 200, {"Content-Type": "application/json"}))
    return portal, forwarded


def _token(client, forwarded):
    client.get("/app/cos/ui/confer")
    token = forwarded[-1][1]["head_meta"]["minimoi-csrf-token"]
    forwarded.clear()
    return token


@pytest.mark.parametrize("route", ["/bootstrap", "/consult", f"/session/{SID}/end", f"/session/{SID}/summary",
                                   f"/session/{SID}/transcript", f"/sessions/{SID}/dismiss"])
def test_every_direct_post_is_csrf_checked_even_with_the_legacy_guard_off_then_marked_verified(load_portal, monkeypatch, route):
    portal, forwarded = _portal(load_portal, monkeypatch)
    client = portal.owner()
    token = _token(client, forwarded)
    assert client.post(DIRECT + route, json={}).status_code == 403 and forwarded == []           # no token
    forged = {"X-Minimoi-Voice-Write-Verified": "1", "X-Minimoi-User-Tier": "owner"}
    assert client.post(DIRECT + route, json={}, headers=forged).status_code == 403 and forwarded == []
    other = {"X-CSRF-Token": token, "Origin": "https://evil.example"}
    assert client.post(DIRECT + route, json={}, headers=other).status_code == 403 and forwarded == []
    answer = client.post(DIRECT + route, json={}, headers={"X-CSRF-Token": token})
    assert answer.status_code == 200 and forwarded[-1][1]["verified_cos_voice_write"] is True
    assert answer.headers["Cache-Control"] == "no-store, private"


def test_a_non_owner_never_reaches_her_voice(load_portal, monkeypatch):
    portal, forwarded = _portal(load_portal, monkeypatch)
    guest = portal.guest()
    for method, url in (("get", DIRECT + "/sessions"), ("post", DIRECT + "/consult"), ("get", DIRECT + "/capabilities")):
        answer = getattr(guest, method)(url, json={} if method == "post" else None)
        assert answer.status_code in (302, 403) and forwarded == []
    anonymous = portal.client()
    assert anonymous.get(DIRECT + "/sessions").status_code == 302 and forwarded == []


def test_her_reads_need_the_owner_only_and_are_never_cached(load_portal, monkeypatch):
    portal, forwarded = _portal(load_portal, monkeypatch)
    client = portal.owner()
    for url in (DIRECT + "/capabilities", DIRECT + "/sessions", DIRECT + f"/sessions/{SID}?conversation_id=owner",
                DIRECT + f"/consult/{SID}/t1?conversation_id=owner"):
        answer = client.get(url)                                          # no token: a read needs none
        assert answer.status_code == 200 and answer.headers["Cache-Control"] == "no-store, private"
        assert forwarded[-1][1].get("verified_cos_voice_write") is not True


def test_other_cos_routes_keep_their_contract(load_portal, monkeypatch):
    portal, forwarded = _portal(load_portal, monkeypatch)
    client = portal.owner()
    _token(client, forwarded)
    assert client.post("/app/cos/ui/send", json={"text": "x"}).status_code == 200                # legacy guard off: as before
    assert forwarded[-1][1].get("verified_cos_voice_write") is False
    answer = client.get("/app/cos/ui/api/notes")
    assert answer.headers.get("Cache-Control") != "no-store, private"


def test_the_marker_is_only_ever_the_portals_and_only_on_her_voice_writes():
    app = Flask(__name__)

    class Backend:
        status_code, headers, content, text = 200, {"content-type": "application/json"}, b"{}", "{}"
    cases = ((DIRECT.removeprefix("/app/cos/") + "/consult", "POST", True, True),
             (DIRECT.removeprefix("/app/cos/") + "/consult", "POST", False, False),       # not verified by the portal
             (DIRECT.removeprefix("/app/cos/") + "/sessions", "GET", True, False),        # a read is never marked
             ("ui/send", "POST", True, False))                                            # not a voice route
    for path, method, verified, expected in cases:
        captured = {}
        with app.test_request_context("/app/cos/" + path, method=method,
                                      headers={"X-Minimoi-Voice-Write-Verified": "1", "X-Minimoi-Auth-Id": "999",
                                               "X-CSRF-Token": "private-token"}), \
                patch("minimoi_portal.proxy.requests.request", side_effect=lambda **kw: captured.update(kw) or Backend()):
            proxy.proxy_to("http://cos:8769", path, "/app/cos", user={"tier": "owner", "auth_id": 3},
                           verified_cos_voice_write=verified)
        assert ("X-Minimoi-Voice-Write-Verified" in captured["headers"]) is expected, (path, method, verified)
        assert captured["headers"]["X-Minimoi-Auth-Id"] == "3"                           # the browser's is stripped
        assert not any(k.lower() == "x-csrf-token" for k in captured["headers"])
    with app.test_request_context("/x", method="POST"), \
            patch("minimoi_portal.proxy.requests.request", side_effect=lambda **kw: captured.update(kw) or Backend()):
        captured.clear()
        proxy.proxy_to("http://cos:8769", DIRECT.removeprefix("/app/cos/") + "/consult", "/app/cos",
                       user={"tier": "guest", "auth_id": 4}, verified_cos_voice_write=True)
        assert "X-Minimoi-Voice-Write-Verified" not in captured["headers"]               # never for a non-owner


def test_her_consult_and_summary_have_their_own_timeouts_and_nothing_else_changes():
    prefix = DIRECT.removeprefix("/app/cos/")
    assert proxy._route_timeout("/app/cos", prefix + "/consult", "POST") == (5, 40)
    assert proxy._route_timeout("/app/cos", f"{prefix}/session/{SID}/summary", "post") == (5, 60)
    for path in (f"{prefix}/session/{SID}/summary/decision", f"{prefix}/session/{SID}/end", prefix + "/bootstrap",
                 f"{prefix}/session/not-a-uuid/summary", "api/realtime-voice/managed/consult"):
        assert proxy._route_timeout("/app/cos", path, "POST") == proxy.DEFAULT_TIMEOUT, path
    assert proxy._route_timeout("/app/cos", prefix + "/consult", "GET") == proxy.DEFAULT_TIMEOUT
    assert proxy._route_timeout("/app/cos", "ui/send", "POST") == (5, 125)
    assert proxy._route_timeout("/app/german", prefix + "/consult", "POST") == proxy.DEFAULT_TIMEOUT


def test_the_portal_reads_no_cos_voice_flag():
    source = (Path(__file__).resolve().parents[1] / "minimoi_portal" / "app.py").read_text()
    for flag in ("MINIMOI_COS_VOICE_DIRECT", "MINIMOI_COS_DIRECT_VOICE_PRODUCTION"):
        assert flag not in source
