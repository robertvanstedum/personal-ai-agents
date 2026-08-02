def test_german_health(german_client):
    r = german_client.get("/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ok"


def test_german_health_reports_commit_and_dirty_state(german_client):
    """Dev-drift fix (2026-08-02): /health used to report a commit SHA
    with no way to tell if the running worktree actually matched it. A
    dirty tree next to a clean-looking SHA is exactly how the original
    drift went unnoticed. Both fields must be present -- 'dirty' being
    None is only acceptable when there's no git repo to ask at all, and
    then 'commit' must also be None (never a SHA with an unknown dirty
    state)."""
    r = german_client.get("/health")
    data = r.get_json()
    assert "commit" in data
    assert "dirty" in data
    if data["commit"] is not None:
        assert data["dirty"] is not None


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
