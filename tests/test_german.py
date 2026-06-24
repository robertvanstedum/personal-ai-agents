def test_german_health(german_client):
    r = german_client.get("/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ok"


def test_gesprache_loads(german_client):
    r = german_client.get("/gesprache")
    assert r.status_code in [200, 302]


def test_personas_api_loads(german_client):
    r = german_client.get("/api/personas")
    assert r.status_code == 200
    assert isinstance(r.get_json(), list)


def test_lesen_loads(german_client):
    r = german_client.get("/lesen")
    assert r.status_code in [200, 302]
