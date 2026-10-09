"""Tests for the production activation of the real Guild floor on minimoi.ai (two switches, exact host, owner guard).

Meant to prove: both switches and the exact host are required; the prototype is never mounted; Master Craftsman is held
off whatever MINIMOI_GUILD_MC says; the owner guard holds; /guild only redirects when MINIMOI_GUILD_PRIMARY is also on.
"""
from __future__ import annotations

import pytest

from floor_helpers import load_portal  # noqa: F401  (pytest fixture)

PROD = "https://minimoi.ai"


def _rules(app, prefix):
    return [r.rule for r in app.url_map.iter_rules() if r.rule.startswith(prefix)]


def test_both_switches_and_the_exact_host_mount_only_the_real_floor(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_PRODUCTION", "1")
    portal = load_portal(next_flag="1", proto_flag="1", base_url=PROD)
    assert portal.module.GUILD_MOUNTS == {"guild_proto": "refused_production", "guild_next": "on"}
    assert _rules(portal.app, "/guild-proto") == []
    assert portal.owner().get("/guild-proto/guild/build").status_code == 404
    assert portal.owner().get("/guild-next/guild/build").status_code == 200


def test_the_owner_guard_holds_in_production(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_PRODUCTION", "1")
    portal = load_portal(next_flag="1", base_url=PROD)
    response = portal.client().get("/guild-next/guild/build")
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    assert portal.guest().get("/guild-next/guild/build").status_code == 302


@pytest.mark.parametrize("next_flag,production_flag,base_url", [
    ("1", None, PROD),                              # the mount switch alone
    (None, "1", PROD),                              # the production switch alone
    ("1", "1", "https://www.minimoi.ai"),           # www is never a production host
    ("1", "1", "https://minimoi.ai.evil.example"),  # host must be exact
    ("1", "1", "https://staging.minimoi.ai"),
    ("1", "1", ""),
])
def test_one_switch_or_a_wrong_host_mounts_nothing(load_portal, monkeypatch, next_flag, production_flag, base_url):
    monkeypatch.delenv("MINIMOI_GUILD_PRODUCTION", raising=False)
    if production_flag:
        monkeypatch.setenv("MINIMOI_GUILD_PRODUCTION", production_flag)
    portal = load_portal(next_flag=next_flag, base_url=base_url)
    assert portal.module.GUILD_MOUNTS["guild_next"] in {"off", "refused_not_staging"}
    assert _rules(portal.app, "/guild-next") == []


def test_the_staging_allowlist_still_cannot_name_production(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_ALLOWED_HOSTS", "minimoi.ai")
    monkeypatch.delenv("MINIMOI_GUILD_PRODUCTION", raising=False)
    portal = load_portal(next_flag="1", base_url=PROD)
    assert portal.module.GUILD_MOUNTS["guild_next"] == "refused_not_staging"


def test_master_craftsman_is_held_off_in_production_whatever_the_environment_says(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_PRODUCTION", "1")
    for name, value in (("MINIMOI_GUILD_MC", "stub"), ("MINIMOI_GUILD_MC_TURNS", "1"), ("MINIMOI_GUILD_MC_STREAM", "1")):
        monkeypatch.setenv(name, value)
    portal = load_portal(next_flag="1", base_url=PROD)
    assert portal.module.GUILD_MOUNTS["guild_next"] == "on"
    services = portal.app.extensions["guild_ui_next"]["services"]
    assert services.mc.kind == "off" and services.mc_turns is False and services.mc_stream is False


def test_guild_landing_redirects_only_when_primary_is_explicitly_on(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_PRODUCTION", "1")
    monkeypatch.delenv("MINIMOI_GUILD_PRIMARY", raising=False)
    portal = load_portal(next_flag="1", base_url=PROD)
    assert portal.owner().get("/guild").status_code == 200            # today's landing stays
    monkeypatch.setenv("MINIMOI_GUILD_PRIMARY", "1")
    portal = load_portal(next_flag="1", base_url=PROD)
    response = portal.owner().get("/guild")
    assert response.status_code == 302 and response.headers["Location"].endswith("/guild-next/")


def test_staging_default_is_unchanged(load_portal, monkeypatch):
    monkeypatch.delenv("MINIMOI_GUILD_PRIMARY", raising=False)
    portal = load_portal(next_flag="1", base_url="https://dev.minimoi.ai")
    assert portal.owner().get("/guild").status_code == 302
