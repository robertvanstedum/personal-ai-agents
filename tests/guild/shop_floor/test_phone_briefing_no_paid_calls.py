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


@pytest.fixture
def no_outbound(monkeypatch):
    real = socket.create_connection
    attempts = []

    def guarded(address, *args, **kwargs):
        host = address[0]
        if host not in ("127.0.0.1", "localhost", "::1"):
            attempts.append(address)
            raise OSError("outbound connections are blocked in this test")
        return real(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", guarded)
    return attempts


def test_every_page_and_api_call_works_with_outbound_connections_blocked(load_portal, no_outbound):
    portal = load_portal(ops_url="http://ops.invalid:8768/status")
    client = portal.owner()
    before = set(sys.modules)
    urls = ["/guild-next/guild/build", "/guild-next/guild/build/bench", "/guild-next/guild/build/queue",
            "/guild-next/guild/build/items/12", "/guild-next/guild/operate",
            "/guild-next/api/v1/session", "/guild-next/api/v1/floor", "/guild-next/api/v1/queue",
            "/guild-next/api/v1/queue/items/12", "/guild-next/api/v1/queue/items/12/history"]
    for url in urls:
        assert client.get(url).status_code == 200, url
    new = set(sys.modules) - before
    assert not [m for m in new if m.split(".")[0] in {p.split(".")[0] for p in PROVIDER_MODULES}]
    # The only outbound attempt is the configured Operations probe, and it failed closed to unknown.
    assert all(addr[0] == "ops.invalid" for addr in no_outbound)
