"""Registration fails closed (spec §8.3) and a failed mount answers 503, never a
fallback (review S9): JSON under /api/, a short page otherwise, still behind
the owner guard, and never the portal's catch-all login redirect or a 405."""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from minimoi_portal import guild_mounts
from minimoi_portal.guild_ui import GuildBindingError, register_guild_ui
from minimoi_portal.guild_ui.services import build_services

from floor_helpers import write_queue
from floor_helpers import load_portal  # noqa: F401  (pytest fixtures)

REPO = Path(__file__).resolve().parents[3]


def _assert_503_json(response):
    assert response.status_code == 503
    assert response.get_json()["error"] == "unavailable"
    assert "Location" not in response.headers


@pytest.mark.parametrize("queue", [None, "inside_code_tree", "missing_folder"])
def test_failed_registration_answers_503_json_for_the_api(load_portal, tmp_path, queue):
    if queue == "inside_code_tree":
        path = REPO / "data" / "guild" / "build_queue.json"
    elif queue == "missing_folder":
        path = tmp_path / "nope" / "guild" / "build_queue.json"
    else:
        path = None
    portal = load_portal(queue=path or False)
    assert portal.module.GUILD_MOUNTS["guild_next"] == "unavailable"
    owner = portal.owner()
    _assert_503_json(owner.get("/guild-next/api/v1/floor"))
    _assert_503_json(owner.post("/guild-next/api/v1/queue/items/12/status", json={}))
    _assert_503_json(owner.delete("/guild-next/api/v1/anything"))
    page = owner.get("/guild-next/guild/build")
    assert page.status_code == 503 and b"Guild next is unavailable" in page.data
    root = owner.get("/guild-next/")
    assert root.status_code == 503


def test_failed_registration_still_guards_json_401_403(load_portal):
    portal = load_portal(queue=False)
    anonymous = portal.client().get("/guild-next/api/v1/floor")
    assert anonymous.status_code == 401 and anonymous.get_json()["error"] == "not_signed_in"
    assert "Location" not in anonymous.headers
    guest = portal.guest().post("/guild-next/api/v1/floor", json={})
    assert guest.status_code == 403 and guest.get_json()["error"] == "not_allowed"
    page = portal.client().get("/guild-next/guild/build")
    assert page.status_code == 302 and "/login" in page.headers["Location"]


def test_other_portal_routes_survive_a_failed_mount(load_portal):
    portal = load_portal(queue=False, proto_flag="1")
    owner = portal.owner()
    assert owner.get("/health").status_code == 200
    assert owner.get("/guild-proto/guild/build").status_code == 200
    assert "update_build_status" in portal.app.view_functions


def test_a_failing_prototype_mount_answers_503(load_portal, monkeypatch, tmp_path):
    monkeypatch.setattr(guild_mounts, "PROTO_DIR", tmp_path / "missing")
    monkeypatch.delitem(__import__("sys").modules, guild_mounts.PROTO_MODULE, raising=False)
    portal = load_portal(proto_flag="1")
    assert portal.module.GUILD_MOUNTS["guild_proto"] == "unavailable"
    assert portal.owner().get("/guild-proto/guild/build").status_code == 503
    assert portal.owner().get("/guild-next/guild/build").status_code == 200


def _services(tmp_path, **kw):
    path = write_queue(tmp_path / "s" / "guild" / "build_queue.json")
    return build_services(queue_path=str(path), **kw)


def _register(app, **overrides):
    kwargs = dict(owner_guard=lambda f: f, current_user=lambda: None, url_prefix="/guild-next",
                  blueprint_name="guild_ui_next", services=None)
    kwargs.update(overrides)
    return register_guild_ui(app, **kwargs)


def test_register_refuses_each_missing_piece(tmp_path, monkeypatch):
    from domains.guild import queue_store as qs
    monkeypatch.setattr(qs, "_running_in_container", lambda: False)
    good = _services(tmp_path)
    cases = [
        dict(prototype=True, services=good),
        dict(owner_guard=None, services=good),
        dict(current_user=None, services=good),
        dict(url_prefix="", services=good),
        dict(url_prefix="guild-next", services=good),
        dict(blueprint_name="guild ui", services=good),
        dict(routes=("floor", "improve"), services=good),
        dict(routes=("floor", "landing"), services=good),
        dict(services=None),
        dict(services=build_services(queue_path=None)),
        dict(services=build_services(queue_path=str(REPO / "data" / "guild" / "build_queue.json"))),
    ]
    for case in cases:
        app = Flask("t")
        with pytest.raises(GuildBindingError):
            _register(app, **case)
        assert not app.blueprints, case
        assert "guild_ui_next" not in app.extensions, case


def test_register_twice_in_one_app_is_refused(tmp_path, monkeypatch):
    from domains.guild import queue_store as qs
    monkeypatch.setattr(qs, "_running_in_container", lambda: False)
    app = Flask("t")
    _register(app, services=_services(tmp_path))
    with pytest.raises(GuildBindingError, match="already registered"):
        _register(app, services=_services(tmp_path))


def test_registration_needs_no_database_and_no_queue_file_contents(tmp_path, monkeypatch):
    """S9: ready() checks configuration only. A missing database or an unreadable
    queue file is a run-time *unknown*, not a registration failure."""
    from domains.guild import queue_store as qs
    monkeypatch.setattr(qs, "_running_in_container", lambda: False)
    folder = tmp_path / "state" / "guild"
    folder.mkdir(parents=True)
    services = build_services(queue_path=str(folder / "build_queue.json"),
                              database_url=lambda: "postgresql://nobody@127.0.0.1:1/none")
    assert services.ready() == []
    app = Flask("t")
    _register(app, services=services)
    assert "guild_ui_next" in app.blueprints
