"""/guild-next, /guild-next/ and /guild-next/guild land on the Shop floor
(Robert, 2026-09-29: opening dev.minimoi.ai/guild-next/ after signing in said
"not found"). Owner-guarded like every page."""
from __future__ import annotations

import pytest

from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)

ROOTS = ["/guild-next", "/guild-next/", "/guild-next/guild", "/guild-next/guild/"]


@pytest.mark.parametrize("path", ROOTS)
def test_the_mount_root_lands_the_owner_on_the_shop_floor(staging, path):
    r = staging.owner().get(path)
    assert r.status_code in (301, 302, 303, 307, 308), (path, r.status_code)
    assert r.headers["Location"].endswith("/guild-next/guild/build")
    page = staging.owner().get(path, follow_redirects=True)
    assert page.status_code == 200 and 'data-page="floor"' in page.get_data(as_text=True)


@pytest.mark.parametrize("path", ROOTS)
def test_a_guest_or_a_signed_out_visitor_gets_the_guard_not_the_floor(staging, path):
    for client in (staging.guest(), staging.client()):
        r = client.get(path)
        assert r.status_code in (302, 403), (path, r.status_code)
        assert not r.headers.get("Location", "").endswith("/guild-next/guild/build")
