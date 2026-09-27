"""Phone Type always rendered (C6, S14); the opening briefing is rules only
(decision B, S1a); nothing calls a model, a paid provider or any other host."""
from __future__ import annotations

import re
import socket
import sys
from pathlib import Path

import pytest

from minimoi_portal.guild_ui.briefing import LABEL, opening_briefing
from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)

PACKAGE = Path(__file__).resolve().parents[3] / "minimoi_portal" / "guild_ui"


def test_type_is_rendered_on_every_page_and_hold_to_talk_never(staging):
    client = staging.owner()
    for url in ("/guild-next/guild/build", "/guild-next/guild/build/bench", "/guild-next/guild/build/queue",
                "/guild-next/guild/build/items/12", "/guild-next/guild/operate"):
        body = client.get(url).get_data(as_text=True)
        assert re.search(r'<button[^>]*data-mc-type[^>]*>Type</button>', body), url
        assert 'id="mc-input"' in body
        assert "Hold to talk" not in body and "data-mc-voice" not in body


def test_phone_css_never_hides_the_composer():
    css = (PACKAGE / "static" / "components.css").read_text()
    phone = css[css.index("@media (max-width: 640px)"):]
    phone = phone[:phone.index("\n}\n")]
    assert ".mc-composer { display: none; }" not in phone
    assert ".mc-composer { display: flex; }" in phone
    assert ".mc-phone-bar { display: flex;" in phone


def test_send_is_disabled_and_says_notes_are_not_connected(staging):
    body = staging.owner().get("/guild-next/guild/build").get_data(as_text=True)
    assert re.search(r'<button type="submit" class="btn" data-mc-send disabled', body)
    assert "Notes are not connected yet. Nothing you type is sent or kept." in body
    assert "file this" not in body.lower()


def test_the_briefing_is_one_rules_only_line(staging):
    client = staging.owner()
    floor = client.get("/guild-next/api/v1/floor").get_json()
    briefing = floor["briefing"]
    assert briefing["label"] == "platform rules" and briefing["display_label"] == LABEL
    assert "\n" not in briefing["text"] and briefing["text"].startswith("As of ")
    assert "1 needs you (Decide #31)" in briefing["text"]
    lights = {l["id"]: l for l in floor["lights"]}
    assert f"Queue {lights['build_queue']['word']}" in briefing["text"]
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert f'data-briefing-label>{LABEL}<' in page.replace("&middot;", "·")
    assert "data-briefing-text" in page


def test_the_briefing_is_a_pure_function_of_lights_and_needs():
    lights = [{"id": "build_queue", "short": "Queue", "word": "OK", "state": "green", "source": "live", "rule": "queue"},
              {"id": "systems", "short": "Systems", "word": "Unknown", "state": "unknown", "source": "live", "rule": "systems"},
              {"id": "usage", "short": "Usage", "word": "Unknown", "state": "unknown", "source": "not_instrumented",
               "rule": "not_instrumented"}]
    needs = {"status": "ok", "total": 0, "items": []}
    one = opening_briefing(lights, needs, "2026-09-27T10:42:00+00:00")
    assert one["text"] == "As of 10:42 UTC · nothing needs you · Queue OK · Systems unknown · 1 not instrumented"
    assert opening_briefing(lights, needs, "2026-09-27T10:42:00+00:00") == one


PROVIDER_MODULES = ("anthropic", "openai", "litellm", "xai_sdk", "groq", "google.generativeai", "mistralai", "cohere")


def test_the_package_imports_no_model_provider():
    for path in PACKAGE.rglob("*.py"):
        text = path.read_text()
        for name in PROVIDER_MODULES:
            assert not re.search(rf"^\s*(import|from)\s+{re.escape(name)}\b", text, re.M), (path, name)


class OutboundBlocked(OSError):
    """Raised by the guard in place of any outbound connection or name lookup."""


@pytest.fixture
def no_outbound(monkeypatch):
    """Block outbound network at the socket layer that requests (urllib3),
    urllib and http.client all go through (review F4): every name lookup
    (socket.getaddrinfo) and every connect (socket.socket.connect /
    connect_ex, socket.create_connection) is recorded and refused. Nothing is
    allowed through, not even loopback: the Flask test client needs no socket.
    (psycopg2 connects through libpq in C, below Python sockets; the history
    read is kept off it by leaving DATABASE_URL unset, which load_portal does.)"""
    attempts = []

    def refuse(kind, target):
        attempts.append((kind, target))
        raise OutboundBlocked(f"outbound {kind} to {target!r} is blocked in this test")

    def getaddrinfo(host, port, *args, **kwargs):
        refuse("getaddrinfo", (host, port))

    def connect(self, address):
        refuse("connect", address)

    def connect_ex(self, address):
        refuse("connect", address)

    def create_connection(address, *args, **kwargs):
        refuse("create_connection", address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    return attempts


def assert_no_outbound(attempts):
    assert attempts == [], f"outbound network attempted: {attempts}"


URLS = ["/guild-next/guild/build", "/guild-next/guild/build/bench", "/guild-next/guild/build/queue",
        "/guild-next/guild/build/items/12", "/guild-next/guild/operate",
        "/guild-next/api/v1/session", "/guild-next/api/v1/floor", "/guild-next/api/v1/queue",
        "/guild-next/api/v1/queue/items/12", "/guild-next/api/v1/queue/items/12/history"]
OPS_URL = "http://ops.invalid:8768/status"


def _mock_probe(portal):
    """The Systems probe to the configured Operations URL is the one call the
    floor may make; here it is mocked, so the page makes no network call at all."""
    calls = []

    class Answer:
        status_code = 200

        def json(self):
            from datetime import datetime, timezone
            return {"state": "running", "last_checkin": datetime.now(timezone.utc).isoformat(),
                    "open_escalations": 0}

    def fake_get(url, timeout):
        calls.append(url)
        return Answer()

    portal.app.extensions["guild_ui_next"]["services"].systems._get = fake_get
    return calls


def test_the_floor_briefing_and_ask_make_no_outbound_call(load_portal, no_outbound):
    """W3: rendering every page, the floor state, the rules briefing and the
    data behind Ask (the explain card is built in the browser from /floor)
    makes zero outbound calls; the only permitted call, the Systems probe, is
    mocked and goes only to the configured Operations URL."""
    portal = load_portal(ops_url=OPS_URL)
    probe_calls = _mock_probe(portal)
    client = portal.owner()
    before = set(sys.modules)
    for url in URLS:
        assert client.get(url).status_code == 200, url
    floor = client.get("/guild-next/api/v1/floor").get_json()
    assert floor["briefing"]["label"] == "platform rules"
    systems = {l["id"]: l for l in floor["lights"]}["systems"]
    assert systems["state"] == "green" and systems["detail"]    # what Ask explains
    new = set(sys.modules) - before
    assert not [m for m in new if m.split(".")[0] in {p.split(".")[0] for p in PROVIDER_MODULES}]
    assert probe_calls and set(probe_calls) == {OPS_URL}
    assert_no_outbound(no_outbound)


def test_meta_the_guard_catches_requests_urllib_and_http_client(no_outbound):
    """The guard is real: each client library's attempt is seen and refused."""
    import http.client
    import urllib.request

    import requests

    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get("http://127.0.0.2:9/status", timeout=1)
    with pytest.raises(OSError):
        urllib.request.urlopen("http://api.anthropic.com/v1/messages", timeout=1)
    with pytest.raises(OSError):
        conn = http.client.HTTPConnection("api.x.ai", 80, timeout=1)
        conn.request("GET", "/")
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", 9), timeout=1)
    kinds = {kind for kind, _ in no_outbound}
    targets = " ".join(repr(t) for _, t in no_outbound)
    assert "127.0.0.2" in targets and "api.anthropic.com" in targets and "api.x.ai" in targets
    assert kinds & {"getaddrinfo", "connect"}
    with pytest.raises(AssertionError):
        assert_no_outbound(no_outbound)


def test_meta_an_unmocked_probe_makes_the_no_outbound_test_fail(load_portal, no_outbound):
    """Prove the assertion above can fail: with the real probe (requests) and an
    address outside any allowlist, the attempt is recorded, the light fails
    closed to unknown, and assert_no_outbound raises."""
    portal = load_portal(ops_url="http://127.0.0.2:8768/status")
    floor = portal.owner().get("/guild-next/api/v1/floor")
    assert floor.status_code == 200
    systems = {l["id"]: l for l in floor.get_json()["lights"]}["systems"]
    assert systems["state"] == "unknown" and systems["reason"] == "Operations agent unreachable"
    assert any("127.0.0.2" in repr(target) for _, target in no_outbound)
    with pytest.raises(AssertionError):
        assert_no_outbound(no_outbound)
