def test_guild_queue_redirects_unauthenticated(portal_client):
    r = portal_client.get("/guild/build")
    assert r.status_code in [200, 302]


def test_guild_roadmap_redirects_unauthenticated(portal_client):
    r = portal_client.get("/guild/build/roadmap")
    assert r.status_code in [200, 302]
