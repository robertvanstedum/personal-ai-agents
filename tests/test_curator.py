def test_curator_health(curator_client):
    r = curator_client.get("/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ok"


def test_curator_daily_loads(curator_client):
    r = curator_client.get("/")
    assert r.status_code in [200, 302]
