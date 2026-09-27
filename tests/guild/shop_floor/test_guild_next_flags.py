"""/guild-next and /guild-proto exist only when their switches are set.

Unset (production): nothing is registered and both prefixes answer 404 to the
owner. Set: owner only, through the portal's own guard. docker-compose.prod.yml
never sets either switch.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)

REPO = Path(__file__).resolve().parents[3]
PAGES = ["/guild-next/guild/build", "/guild-next/guild/build/queue", "/guild-next/guild/operate",
         "/guild-proto/guild/build"]


def _rules(app, prefix):
    return [r.rule for r in app.url_map.iter_rules() if r.rule.startswith(prefix)]


@pytest.mark.parametrize("value", [None, "", "0", "off", "false", "no", "2"])
def test_switch_off_registers_nothing_and_the_owner_gets_404(load_portal, value):
    portal = load_portal(next_flag=value, proto_flag=value)
    assert portal.module.GUILD_MOUNTS == {"guild_proto": "off", "guild_next": "off"}
    assert _rules(portal.app, "/guild-next") == [] and _rules(portal.app, "/guild-proto") == []
    assert "guild_ui_next" not in portal.app.blueprints and "guild_ui" not in portal.app.blueprints
    client = portal.owner()
    for url in PAGES + ["/guild-next/api/v1/floor", "/guild-proto/guild/ui-assets/tokens.css"]:
        assert client.get(url).status_code == 404, url


def test_the_shared_portal_client_has_no_guild_mounts(portal_client):
    app = portal_client.application
    assert not [r for r in app.url_map.iter_rules() if r.rule.startswith(("/guild-next", "/guild-proto"))]


@pytest.mark.parametrize("value", ["1", "true", "on", "YES"])
def test_switch_on_serves_the_owner(load_portal, value):
    portal = load_portal(next_flag=value, proto_flag=value)
    assert portal.module.GUILD_MOUNTS == {"guild_proto": "on", "guild_next": "on"}
    client = portal.owner()
    for url in PAGES:
        assert client.get(url).status_code == 200, url


def test_pages_are_owner_only_through_the_portal_guard(staging):
    anonymous = staging.client()
    response = anonymous.get("/guild-next/guild/build")
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    guest = staging.guest()
    response = guest.get("/guild-next/guild/build")
    assert response.status_code == 302 and "owner_required" in response.headers["Location"]
    asset = anonymous.get("/guild-next/guild/ui-assets/components.css")
    assert asset.status_code == 302


def test_only_one_switch_mounts_only_its_prefix(load_portal):
    portal = load_portal(next_flag="1", proto_flag=None)
    assert portal.module.GUILD_MOUNTS == {"guild_proto": "off", "guild_next": "on"}
    assert portal.owner().get("/guild-proto/guild/build").status_code == 404
    portal = load_portal(next_flag=None, proto_flag="1")
    assert portal.module.GUILD_MOUNTS == {"guild_proto": "on", "guild_next": "off"}
    assert portal.owner().get("/guild-next/guild/build").status_code == 404
    assert portal.owner().get("/guild-proto/guild/build").status_code == 200


def test_production_compose_sets_neither_switch():
    text = (REPO / "docker-compose.prod.yml").read_text()
    assert "MINIMOI_GUILD_NEXT" not in text
    assert "MINIMOI_GUILD_PROTO" not in text
    # and nothing that would switch them on by another spelling
    assert not re.search(r"GUILD_(NEXT|PROTO)\s*[:=]", text)


def test_the_portal_image_keeps_the_prototype_folder_for_the_staging_mount():
    ignore = (REPO / ".dockerignore").read_text().splitlines()
    assert not any(line.strip().startswith("prototype-lab") for line in ignore)


@pytest.mark.parametrize("base_url", [
    "https://minimoi.ai", "https://www.minimoi.ai/", "https://MINIMOI.AI",   # production
    "", None, "not a url", "https://10.0.0.5", "https://staging.minimoi.ai",  # empty or unknown
    "https://dev.minimoi.ai.evil.example", "https://minimoi.ai/dev.minimoi.ai",
])
def test_switches_are_refused_off_a_staging_origin(load_portal, monkeypatch, base_url):
    """Review F8: an allowlist, not a production blocklist. /opt/minimoi/.env is
    shared by every production service, so a stray switch must not expose
    /guild-next or /guild-proto anywhere but a staging origin; an empty or
    unfamiliar BASE_URL is not staging."""
    monkeypatch.delenv("BASE_URL", raising=False)
    monkeypatch.delenv("MINIMOI_GUILD_ALLOWED_HOSTS", raising=False)
    portal = load_portal(next_flag="1", proto_flag="1", base_url=base_url)
    assert portal.module.GUILD_MOUNTS == {"guild_proto": "refused_not_staging",
                                          "guild_next": "refused_not_staging"}
    assert _rules(portal.app, "/guild-next") == [] and _rules(portal.app, "/guild-proto") == []
    assert portal.owner().get("/guild-next/guild/build").status_code == 404


@pytest.mark.parametrize("base_url", ["https://dev.minimoi.ai", "http://localhost:5001", "http://127.0.0.1:5001",
                                      "https://DEV.minimoi.ai/"])
def test_switches_work_on_a_staging_origin(load_portal, monkeypatch, base_url):
    monkeypatch.delenv("MINIMOI_GUILD_ALLOWED_HOSTS", raising=False)
    portal = load_portal(next_flag="1", proto_flag="1", base_url=base_url)
    assert portal.module.GUILD_MOUNTS == {"guild_proto": "on", "guild_next": "on"}
    assert _rules(portal.app, "/guild-next")


def test_an_explicit_allowed_host_override_admits_only_that_host(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_ALLOWED_HOSTS", " staging.example.test , other.test")
    portal = load_portal(next_flag="1", base_url="https://staging.example.test")
    assert portal.module.GUILD_MOUNTS["guild_next"] == "on"
    portal = load_portal(next_flag="1", base_url="https://minimoi.ai")
    assert portal.module.GUILD_MOUNTS["guild_next"] == "refused_not_staging"


def test_production_compose_sets_no_allowed_hosts_override():
    text = (REPO / "docker-compose.prod.yml").read_text()
    assert "MINIMOI_GUILD_ALLOWED_HOSTS" not in text
