"""API answers stay JSON on unexpected errors, and a 304 needs a real ETag
match (review F11)."""
from __future__ import annotations

import pytest

from minimoi_portal.guild_ui import floor_state

from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)


def _boom(*_a, **_k):
    raise RuntimeError("unexpected")


def test_an_unexpected_api_error_is_json_500(staging, monkeypatch):
    monkeypatch.setattr(floor_state, "compute", _boom)
    response = staging.owner().get("/guild-next/api/v1/floor")
    assert response.status_code == 500 and response.is_json
    body = response.get_json()
    assert body["error"] == "server_error" and body["message"]
    assert "Traceback" not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"
    assert "Content-Security-Policy" in response.headers


def test_the_guard_still_runs_before_the_view(staging, monkeypatch):
    monkeypatch.setattr(floor_state, "compute", _boom)
    response = staging.client().get("/guild-next/api/v1/floor")
    assert response.status_code == 401 and response.get_json()["error"] == "not_signed_in"


def test_http_errors_and_page_errors_keep_their_usual_answers(staging, monkeypatch):
    client = staging.owner()
    assert client.get("/guild-next/api/v1/nope").get_json()["error"] == "not_found"
    monkeypatch.setattr(floor_state, "compute", _boom)
    staging.app.config["PROPAGATE_EXCEPTIONS"] = False
    page = client.get("/guild-next/guild/build")
    assert page.status_code == 500 and not page.is_json


def test_if_none_match_star_is_not_a_match(staging):
    client = staging.owner()
    first = client.get("/guild-next/api/v1/floor")
    tag = first.headers["ETag"].strip('"')
    star = client.get("/guild-next/api/v1/floor", headers={"If-None-Match": "*"})
    assert star.status_code == 200 and star.get_json()["lights"]
    other = client.get("/guild-next/api/v1/floor", headers={"If-None-Match": '"not-the-tag"'})
    assert other.status_code == 200
    assert client.get("/guild-next/api/v1/floor", headers={"If-None-Match": f'"{tag}"'}).status_code == 304
    assert client.get("/guild-next/api/v1/floor", headers={"If-None-Match": f'"x", W/"{tag}"'}).status_code == 304
