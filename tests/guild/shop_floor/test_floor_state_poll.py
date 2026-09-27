"""GET /floor with an ETag: 304 only when nothing changed, and a source that
starts failing always changes the tag (review N4)."""
from __future__ import annotations

from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)


def test_etag_304_when_unchanged_and_new_tag_when_the_queue_changes(staging):
    client = staging.owner()
    first = client.get("/guild-next/api/v1/floor")
    tag = first.headers["ETag"]
    again = client.get("/guild-next/api/v1/floor", headers={"If-None-Match": tag})
    assert again.status_code == 304
    staging.queue_path.write_text('[{"id": 1, "spec_title": "x", "status": "in_build"}]')
    changed = client.get("/guild-next/api/v1/floor", headers={"If-None-Match": tag})
    assert changed.status_code == 200 and changed.headers["ETag"] != tag


def test_a_failing_source_is_never_hidden_by_304(staging):
    client = staging.owner()
    tag = client.get("/guild-next/api/v1/floor").headers["ETag"]
    staging.queue_path.write_text("garbage")
    response = client.get("/guild-next/api/v1/floor", headers={"If-None-Match": tag})
    assert response.status_code == 200
    assert {l["id"]: l for l in response.get_json()["lights"]}["build_queue"]["state"] == "unknown"


def test_every_value_carries_observed_at(staging):
    floor = staging.owner().get("/guild-next/api/v1/floor").get_json()
    assert floor["observed_at"].endswith("+00:00")
    for light in floor["lights"]:
        assert light["observed_at"]
    assert floor["needs"]["observed_at"] and floor["briefing"]["observed_at"]


def test_continue_notes_and_postits_say_they_arrive_next(staging):
    floor = staging.owner().get("/guild-next/api/v1/floor").get_json()
    for zone in ("continue", "postits", "notes"):
        assert floor[zone]["state"] == "not_connected" and floor[zone]["text"]
    assert floor["mc_state"] == "off"
