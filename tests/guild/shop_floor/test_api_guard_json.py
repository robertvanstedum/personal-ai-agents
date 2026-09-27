"""API calls get JSON 401/403, never a redirect or HTML (spec §5.2, W9)."""
from __future__ import annotations

import pytest
from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)

CALLS = [
    ("GET", "/guild-next/api/v1/session"),
    ("GET", "/guild-next/api/v1/floor"),
    ("GET", "/guild-next/api/v1/queue"),
    ("GET", "/guild-next/api/v1/queue/items/12"),
    ("GET", "/guild-next/api/v1/queue/items/12/history"),
    ("POST", "/guild-next/api/v1/queue/items/12/status"),
    ("POST", "/guild-next/api/v1/queue/journal/" + "a" * 32 + "/checked"),
    ("GET", "/guild-next/api/v1/no/such/thing"),
    ("DELETE", "/guild-next/api/v1/queue"),
]


def _assert_json(response, status, error):
    assert response.status_code == status
    assert "Location" not in response.headers
    assert response.mimetype == "application/json"
    body = response.get_json()
    assert body["error"] == error and body["message"]
    assert "<html" not in response.get_data(as_text=True).lower()


@pytest.mark.parametrize("method,url", CALLS)
def test_anonymous_gets_json_401(staging, method, url):
    response = staging.client().open(url, method=method, json={})
    _assert_json(response, 401, "not_signed_in")


@pytest.mark.parametrize("method,url", CALLS)
def test_guest_gets_json_403(staging, method, url):
    response = staging.guest().open(url, method=method, json={})
    _assert_json(response, 403, "not_allowed")


def test_owner_gets_json_and_unknown_paths_are_json_404(staging):
    client = staging.owner()
    session = client.get("/guild-next/api/v1/session")
    assert session.status_code == 200 and session.get_json()["mc_state"] == "off"
    assert session.get_json()["user"]["tier"] == "owner"
    _assert_json(client.get("/guild-next/api/v1/no/such/thing"), 404, "not_found")
    _assert_json(client.get("/guild-next/api/v1/queue/items/999"), 404, "not_found")


def test_guest_write_changes_nothing(staging):
    before = staging.queue_path.read_bytes()
    staging.guest().post("/guild-next/api/v1/queue/items/12/status", json={"to": "done"})
    assert staging.queue_path.read_bytes() == before and staging.journal() == []
