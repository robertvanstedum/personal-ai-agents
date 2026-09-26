"""Owner-only Guild page for the Rooms UI preview (package U, simulated fixtures only).

The portal serves the prototype's static preview from the image. These checks keep
it owner-only, keep its asset paths inside the Guild route, keep the live HTTP
adapter out, and prove every module the page imports resolves under the new base.
"""
from __future__ import annotations

import json
import re

import pytest

OWNER = {"username": "owner", "tier": "owner", "display_name": "Robert", "auth_id": 1}
GUEST = {"username": "guest_ab12cd34", "tier": "guest", "display_name": "Guest"}
PAGE = "/guild/rooms-preview/"
ASSETS = "/guild/rooms-preview/assets/"


@pytest.fixture(autouse=True)
def _signed_out(portal_client):
    """The shared session-scoped client keeps cookies: start and leave every test signed out."""
    with portal_client.session_transaction() as sess:
        sess.clear()
    yield
    with portal_client.session_transaction() as sess:
        sess.clear()


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user"] = user


@pytest.mark.parametrize("path", [PAGE, ASSETS + "main.js", ASSETS + "fixtures/overview.normal.json"])
def test_signed_out_is_sent_to_login(portal_client, path):
    response = portal_client.get(path)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


@pytest.mark.parametrize("path", [PAGE, ASSETS + "main.js"])
def test_non_owner_is_sent_to_login(portal_client, path):
    _login(portal_client, GUEST)
    response = portal_client.get(path)
    assert response.status_code == 302
    assert "owner_required" in response.headers["Location"]


def test_owner_gets_the_simulated_preview_with_guild_asset_paths(portal_client):
    _login(portal_client, OWNER)
    response = portal_client.get(PAGE)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert ASSETS + "main.js" in html and ASSETS + "v2.css" in html
    assert "/static/v2/" not in html
    assert "adapter_http" not in html
    assert response.headers["Cache-Control"].startswith("no-store")


def test_trailing_slash_redirect(portal_client):
    _login(portal_client, OWNER)
    response = portal_client.get("/guild/rooms-preview")
    assert response.status_code in (301, 308)
    assert response.headers["Location"].endswith(PAGE)


def test_every_module_the_page_imports_is_served(portal_client):
    _login(portal_client, OWNER)
    pending, seen = ["main.js"], set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        response = portal_client.get(ASSETS + name)
        assert response.status_code == 200, name
        assert "javascript" in response.headers["Content-Type"], name
        base = name.rsplit("/", 1)[0] + "/" if "/" in name else ""
        for target in re.findall(r"""(?:import|from)\s*['"](\./[^'"]+|\.\./[^'"]+)['"]""", response.get_data(as_text=True)):
            parts = (base + target).split("/")
            resolved = []
            for part in parts:
                if part == "..":
                    resolved.pop()
                elif part not in (".", ""):
                    resolved.append(part)
            pending.append("/".join(resolved))
    assert "adapter_fixture.js" in seen and "views/overview.js" in seen
    assert "adapter_http.js" not in seen


def test_fixtures_are_served_and_marked_simulated(portal_client):
    _login(portal_client, OWNER)
    response = portal_client.get(ASSETS + "fixtures/overview.normal.json")
    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True))["simulated"] is True


def test_fixture_base_is_relative_to_the_module(portal_client):
    _login(portal_client, OWNER)
    source = portal_client.get(ASSETS + "adapter_fixture.js").get_data(as_text=True)
    assert "new URL('./fixtures/', import.meta.url)" in source
    assert "'/static/v2/fixtures/'" not in source
    # Owner-only hosting authorizes fixture reads with the session cookie (same origin only);
    # "omit" made every fixture request bounce to the login page.
    assert "credentials: 'same-origin'" in source and "credentials: 'omit'" not in source
    assert "response.redirected" in source


@pytest.mark.parametrize("path", [
    ASSETS + "adapter_http.js",
    ASSETS + "%2e%2e/%2e%2e/%2e%2e/%2e%2e/minimoi_portal/app.py",
    ASSETS + "..%2f..%2f..%2f..%2fminimoi_portal%2fapp.py",
    ASSETS + "does-not-exist.js",
])
def test_live_adapter_traversal_and_unknown_files_are_refused(portal_client, path):
    _login(portal_client, OWNER)
    assert portal_client.get(path).status_code == 404
