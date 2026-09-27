"""One malformed queue row never takes the floor down (review F2).

A row with a field of the wrong type is kept, marked unknown and excluded
from the counts, like a row with an unrecognised status; every page and
/api/v1/floor still answer 200, and the other rows render as before.
"""
from __future__ import annotations

import copy

import pytest

from minimoi_portal.guild_ui.adapters import by_recent, normalize
from minimoi_portal.guild_ui.adapters.contract import live_ok
from minimoi_portal.guild_ui.needs import needs_you
from floor_helpers import QUEUE_ITEMS
from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)

PAGES = ["/guild-next/guild/build", "/guild-next/guild/build/bench", "/guild-next/guild/build/queue",
         "/guild-next/guild/build/items/12", "/guild-next/guild/build/items/55", "/guild-next/guild/operate"]
API = ["/guild-next/api/v1/floor", "/guild-next/api/v1/queue", "/guild-next/api/v1/queue/items/55"]

BAD_FIELDS = [
    ("last_transition_at", 5), ("last_transition_at", 1.5), ("last_transition_at", ["2026-09-01"]),
    ("spec_title", ["a", "list"]), ("spec_title", 7), ("spec_title", {"t": 1}),
    ("summary", {"a": 1}), ("summary", 3), ("spec_file", 3), ("blocked_reason", 9),
    ("github_issue", True), ("github_issue", 1.5), ("github_issue", ["1"]),
]


def _with_bad_row(field, value, status="blocked"):
    row = {"id": 55, "spec_title": "Bad row", "status": status, "blocked_reason": "x",
           "last_transition_at": "2026-09-23T10:00:00+00:00"}
    row[field] = value
    return copy.deepcopy(QUEUE_ITEMS) + [row]


def _all_answer(client):
    for url in PAGES:
        response = client.get(url)
        assert response.status_code == 200, (url, response.status_code)
    for url in API:
        response = client.get(url)
        assert response.status_code == 200, (url, response.status_code)
        assert response.is_json, url


def test_the_review_repro_an_integer_transition_time_on_a_blocked_row(load_portal):
    """The review's repro: before the fix this was an HTML 500 on /api/v1/floor
    and on the floor, queue and bench pages."""
    items = copy.deepcopy(QUEUE_ITEMS)
    next(i for i in items if i["id"] == 31)["last_transition_at"] = 5
    items.append({"id": 32, "spec_title": "Also blocked", "status": "blocked", "blocked_reason": "r",
                  "last_transition_at": "2026-09-24T10:00:00+00:00"})
    portal = load_portal(items=items)
    client = portal.owner()
    for url in PAGES[:4] + ["/guild-next/guild/build/items/31", "/guild-next/guild/operate"] + API[:2]:
        assert client.get(url).status_code == 200, url
    floor = client.get("/guild-next/api/v1/floor").get_json()
    tags = [(n["tag"], n["item_id"]) for n in floor["needs"]["items"]]
    assert ("Decide", 32) in tags and ("Unknown", 31) in tags
    assert floor["needs"]["status"] == "ok" and floor["needs"]["total"] == 2
    queue_light = {l["id"]: l for l in floor["lights"]}["build_queue"]
    assert queue_light["state"] == "red"   # #32 is still blocked
    assert any("1 unknown row" in line for line in queue_light["detail"])
    row = next(i for i in client.get("/guild-next/api/v1/queue").get_json()["items"] if i["id"] == 31)
    assert row["status_known"] is False and row["status"] is None
    assert row["status_label"] == "unknown row (bad last_transition_at)"
    assert row["last_transition_at"] == ""


@pytest.mark.parametrize("field,value", BAD_FIELDS)
def test_a_bad_field_makes_only_that_row_unknown(load_portal, field, value):
    portal = load_portal(items=_with_bad_row(field, value))
    client = portal.owner()
    _all_answer(client)
    items = {i["id"]: i for i in client.get("/guild-next/api/v1/queue").get_json()["items"]}
    assert items[55]["status_known"] is False and items[55]["status"] is None
    assert field in items[55]["field_problems"]
    assert items[55]["status_label"].startswith("unknown row (bad ")
    for key in ("title", "summary", "spec_file", "github_issue", "last_transition_at", "blocked_reason"):
        assert isinstance(items[55][key], str), key
    assert all(items[n]["status_known"] for n in (7, 12, 31, 40))
    queue_page = client.get("/guild-next/guild/build/queue").get_data(as_text=True)
    assert 'data-unknown-row data-item-id="55"' in queue_page
    assert 'data-queue-card data-item-id="12"' in queue_page and 'data-queue-card data-item-id="7"' in queue_page
    item_page = client.get("/guild-next/guild/build/items/55").get_data(as_text=True)
    assert "unknown row (bad " in item_page
    floor = client.get("/guild-next/api/v1/floor").get_json()
    assert floor["queue"]["active"] == 2 and floor["queue"]["status"] == "ok"
    assert any(n["tag"] == "Unknown" and n["item_id"] == 55 for n in floor["needs"]["items"]) \
        or floor["needs"]["total"] > len(floor["needs"]["items"])


@pytest.mark.parametrize("status", ["in_build", "spec_ready"])
def test_a_bad_active_row_is_not_counted_and_the_board_sorts(load_portal, status):
    portal = load_portal(items=_with_bad_row("last_transition_at", 20260923, status=status))
    client = portal.owner()
    _all_answer(client)
    assert client.get("/guild-next/api/v1/floor").get_json()["queue"]["active"] == 2


def test_the_sorts_are_total_over_mixed_types():
    raw = [{"last_transition_at": v} for v in (5, None, "2026-09-01", 1.5, ["x"], True, "")]
    assert [by_recent(r) for r in sorted(raw, key=by_recent)]   # never raises
    rows = [{"id": n, "status": "blocked", "status_known": True, "title": "t", "blocked_reason": "",
             "last_transition_at": v} for n, v in enumerate((5, "2026-09-01", None, 2.5))]
    needs = needs_you(live_ok(rows, "q"), live_ok([], "j"))
    assert needs["status"] == "ok" and needs["total"] == 4


def test_normalize_always_returns_text_for_display_fields():
    row = normalize({"id": 1, "status": "in_build", "spec_title": None, "spec_file": None,
                     "github_issue": 239, "last_transition_at": None})
    assert row["status_known"] is True and row["title"] == "(untitled)" and row["github_issue"] == "239"
    assert row["last_transition_at"] == "" and row["field_problems"] == []
