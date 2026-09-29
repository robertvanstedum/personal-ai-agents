"""C18: the prototype at /guild-proto and the real floor at /guild-next in one
portal, each with its own module, blueprint, configuration, links and
templates (review S10: the prototype is loaded by path under its own name)."""
from __future__ import annotations

import re
import sys

from minimoi_portal import guild_mounts
from floor_helpers import load_portal  # noqa: F401  (pytest fixtures)


def test_both_mount_and_each_serves_its_own_pages(load_portal):
    portal = load_portal(next_flag="1", proto_flag="1")
    assert portal.module.GUILD_MOUNTS == {"guild_proto": "on", "guild_next": "on"}
    owner = portal.owner()
    proto = owner.get("/guild-proto/guild/build").get_data(as_text=True)
    real = owner.get("/guild-next/guild/build").get_data(as_text=True)
    assert "Prototype" in proto and "simulated" in proto
    assert "Master Craftsman is off" not in proto
    assert "Master Craftsman is off" in real
    assert "Prototype" not in real.replace("Prototype Lab", "") and "simulated" not in real.lower()


def test_modules_blueprints_and_config_are_distinct(load_portal):
    portal = load_portal(next_flag="1", proto_flag="1")
    app = portal.app
    assert "guild_ui" in app.blueprints and "guild_ui_next" in app.blueprints
    assert app.extensions["guild_ui"] is not app.extensions["guild_ui_next"]
    assert app.extensions["guild_ui"]["url_prefix"] == "/guild-proto"
    assert app.extensions["guild_ui_next"]["url_prefix"] == "/guild-next"
    assert app.extensions["guild_ui"]["prototype"] is True
    proto_module = sys.modules[guild_mounts.PROTO_MODULE]
    real_module = sys.modules["minimoi_portal.guild_ui"]
    assert proto_module is not real_module
    assert "prototype-lab" in proto_module.__file__ and "minimoi_portal" in real_module.__file__
    assert app.blueprints["guild_ui"].import_name == guild_mounts.PROTO_MODULE
    assert app.blueprints["guild_ui_next"].import_name == "minimoi_portal.guild_ui"
    # Neither is a top-level ``guild_ui`` module that a later import could confuse.
    top = sys.modules.get("guild_ui")
    assert top is None or top not in (proto_module, real_module)


def test_every_link_and_asset_stays_under_its_own_prefix(load_portal):
    portal = load_portal(next_flag="1", proto_flag="1")
    owner = portal.owner()
    for prefix in ("/guild-proto", "/guild-next"):
        body = owner.get(f"{prefix}/guild/build").get_data(as_text=True)
        other = "/guild-next" if prefix == "/guild-proto" else "/guild-proto"
        assert other not in body
        for url in re.findall(r'(?:href|src)="(/guild[^"]*)"', body):
            if url == "/guild" or url.startswith("/guild/"):
                continue  # the portal bar, and the legacy Build Log and Operate links, on purpose
            assert url.startswith(prefix), (prefix, url)
        css = re.search(r'href="([^"]+components\.css)"', body).group(1)
        assert owner.get(css).status_code == 200


def test_templates_do_not_shadow_each_other_whichever_mounts_first(load_portal, monkeypatch):
    """Flask searches blueprint template folders in registration order; the two
    packages use distinct folders (guild/ui_* and guild_floor/*), so order
    does not matter. Mount the real floor first this time."""
    original = guild_mounts.mount_all

    def reversed_order(app, *, environ, owner_guard, current_user, **kw):
        nxt = guild_mounts.mount_guild_next(app, environ=environ, owner_guard=owner_guard,
                                            current_user=current_user, **kw)
        proto = guild_mounts.mount_guild_proto(app, environ=environ, owner_guard=owner_guard,
                                               current_user=current_user)
        return {"guild_proto": proto, "guild_next": nxt}

    monkeypatch.setattr(guild_mounts, "mount_all", reversed_order)
    portal = load_portal(next_flag="1", proto_flag="1")
    owner = portal.owner()
    assert "Prototype" in owner.get("/guild-proto/guild/build").get_data(as_text=True)
    assert "Master Craftsman is off" in owner.get("/guild-next/guild/build").get_data(as_text=True)
    monkeypatch.setattr(guild_mounts, "mount_all", original)


def test_storage_namespaces_differ(load_portal):
    portal = load_portal(next_flag="1", proto_flag="1")
    body = portal.owner().get("/guild-next/guild/build").get_data(as_text=True)
    assert '"storage_ns": "guild.guild-next"' in body
    assert "guild.guild-proto" not in body


def test_the_prototype_mount_uses_prototype_data_and_the_portal_guard(load_portal):
    portal = load_portal(next_flag=None, proto_flag="1")
    assert portal.app.extensions["guild_ui"]["sources"].mode == "sample"
    anonymous = portal.client().get("/guild-proto/guild/build")
    assert anonymous.status_code == 302 and "/login" in anonymous.headers["Location"]
