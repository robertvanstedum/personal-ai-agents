def test_portal_health(portal_client):
    r = portal_client.get("/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ok"


def test_login_page_loads(portal_client):
    r = portal_client.get("/login")
    assert r.status_code == 200


def test_protected_route_redirects_unauthenticated(portal_client):
    r = portal_client.get("/guild")
    assert r.status_code in [302, 401]


def test_german_proxy_reachable_or_redirects(portal_client):
    r = portal_client.get("/app/german")
    assert r.status_code in [200, 302]


def test_curator_proxy_reachable_or_redirects(portal_client):
    r = portal_client.get("/app/curator")
    assert r.status_code in [200, 302]


def test_proxy_nav_hides_owner_workspaces_from_non_owner():
    from minimoi_portal.proxy import _portal_nav_html

    html = _portal_nav_html(
        {"tier": "admin", "display_name": "Admin"},
        "/app/german",
    )
    assert 'href="/"' in html
    assert 'href="/app/curator"' in html
    assert 'href="/app/german"' in html
    assert 'href="/app/portuguese"' in html
    assert 'href="/guild"' not in html
    assert 'href="/app/cos"' not in html


def test_proxy_nav_shows_owner_workspaces_to_owner():
    from minimoi_portal.proxy import _portal_nav_html

    html = _portal_nav_html(
        {"tier": "owner", "display_name": "Robert"},
        "/app/cos",
    )
    assert 'href="/guild"' in html
    assert 'href="/app/cos"' in html
