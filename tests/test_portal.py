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
