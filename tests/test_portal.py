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


def test_guild_build_spec_serves_docs_design_files(portal_client):
    """docs/design/ living docs (e.g. the CoS/Master Craftsman solution
    approach) must resolve at the same /guild/build/spec/ URL as docs/specs/
    component specs, not just render a "not found" placeholder."""
    with portal_client.session_transaction() as sess:
        sess["user"] = {"username": "owner", "tier": "owner"}
    r = portal_client.get(
        "/guild/build/spec/SOLUTION_APPROACH_COS_MASTER_CRAFTSMAN.md"
    )
    assert r.status_code == 200
    assert b"Spec file not found" not in r.data
    assert b"Living Product" in r.data


def test_proxy_nav_hides_owner_workspaces_from_non_owner():
    from minimoi_portal.proxy import _portal_nav_html

    html = _portal_nav_html(
        {"tier": "admin", "display_name": "Admin"},
        "/app/german",
    )
    assert 'href="/dashboard"' in html
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


def test_proxy_nav_has_mobile_safe_workspace_scroller():
    from minimoi_portal.proxy import _portal_nav_html

    html = _portal_nav_html(
        {"tier": "owner", "display_name": "Robert"},
        "/app/german",
    )
    assert 'id="portal-nav-workspaces"' in html
    assert 'class="portal-workspace-link"' in html
    assert "@media (max-width:768px)" in html
    assert "overflow-x:auto" in html
    assert ".portal-nav-account, .portal-nav-signout { display:none; }" in html
