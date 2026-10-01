"""W10 for the floor store (B2, review S9): when the floor database is down,
not migrated, or not configured, the notes, post-its and Continue zones say
"unavailable", never empty and never zero; writes are refused with a plain
reason; Save and the rest of the floor keep working; and registration never
needs the database."""
from __future__ import annotations

import pytest
from flask import Flask

from domains.guild import queue_store as qs
from minimoi_portal.guild_ui import register_guild_ui
from minimoi_portal.guild_ui.services import build_services

from floor_helpers import write_headers, write_queue
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import DownFloor, SqliteFloor, attach, keyed

API = "/guild-next/api/v1"


def _down(load_portal):
    portal = load_portal()
    store = DownFloor()
    attach(portal, store)
    return portal, store


def test_floor_zones_say_unavailable_never_empty_or_zero(load_portal):
    portal, _ = _down(load_portal)
    client = portal.owner()
    floor = client.get(f"{API}/floor").get_json()
    assert floor["postits"]["state"] == "unavailable"
    assert floor["postits"]["shown"] is None and floor["postits"]["active_total"] is None
    assert floor["postits"]["bin_total"] is None and floor["postits"]["more"] is None
    assert floor["continue"]["state"] == "unavailable" and floor["continue"]["target"] is None
    assert floor["notes"]["state"] == "unavailable" and floor["notes"]["recent"] is None
    assert floor["mc_header"] == "Master Craftsman is off · notes unavailable, nothing you send is kept"
    assert floor["floor_store"]["status"] == "unknown"
    # Everything else on the floor is still live.
    assert {l["id"]: l for l in floor["lights"]}["build_queue"]["state"] != "unknown"


def test_pages_say_unavailable_and_show_no_zero(load_portal):
    portal, _ = _down(load_portal)
    client = portal.owner()
    floor = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert "Continue unavailable — treat as unknown" in floor          # the context rail shows it, never hides it
    assert "Conversation unavailable — treat as unknown. Nothing you send is kept." in floor
    for zero in ("0 on the board", "Bin (0)", "No post-its on the board", "Nothing to continue"):
        assert zero not in floor, zero
    # Guild 1.1 slice 1: post-its live on the wall (the Workbench), which says they are unavailable.
    wall = client.get("/guild-next/guild/build/bench").get_data(as_text=True)
    assert "Post-its unavailable — add and remove are paused" in wall
    assert 'data-postit-input maxlength="280" autocomplete="off" placeholder="Add a post-it" disabled' in wall
    for zero in ("0 on the board", "No post-its on the board"):
        assert zero not in wall, zero
    board = client.get("/guild-next/guild/build/postits").get_data(as_text=True)
    assert "Post-its unavailable — add and remove are paused" in board and "Bin unavailable — treat as unknown" in board
    assert "The bin is empty" not in board


def test_reads_answer_unknown_not_empty_lists(load_portal):
    portal, _ = _down(load_portal)
    client = portal.owner()
    postits = client.get(f"{API}/postits").get_json()
    assert postits["available"] is False and postits["postits"] is None and postits["bin_total"] is None
    assert postits["status"] == "unknown" and postits["message"]
    bin_ = client.get(f"{API}/postits/bin").get_json()
    assert bin_["bin"] is None and bin_["total"] is None
    notes = client.get(f"{API}/notes").get_json()
    assert notes["notes"] is None and notes["message"].startswith("Conversation unavailable")
    cont = client.get(f"{API}/continue").get_json()
    assert cont["continue"] is None and cont["state"] == "unavailable"


def test_writes_are_refused_with_a_plain_reason(load_portal):
    portal, _ = _down(load_portal)
    client = portal.owner()
    token = portal.csrf(client)
    answers = {
        "note": client.post(f"{API}/notes", json=keyed(text="x"), headers=write_headers(token)),
        "postit": client.post(f"{API}/postits", json=keyed(text="x"), headers=write_headers(token)),
        "bin": client.post(f"{API}/postits/1/bin", json=keyed(), headers=write_headers(token)),
        "continue": client.put(f"{API}/continue", json=keyed(kind="item", ref=12), headers=write_headers(token)),
    }
    for name, response in answers.items():
        assert response.status_code == 503, name
        assert response.get_json()["error"] == "unavailable", name
    assert answers["note"].get_json()["message"] == "Not saved — notes unavailable"
    assert answers["postit"].get_json()["message"] == "Post-its unavailable — nothing was changed"


def test_save_still_works_and_reports_the_thread_line_failed(load_portal):
    portal, _ = _down(load_portal)
    client = portal.owner()
    token = portal.csrf(client)
    item = next(i for i in qs.QueueStore(str(portal.queue_path)).read_items() if i["id"] == 12)
    body = client.post(f"{API}/queue/items/12/status",
                       json={"to": "done", "expect_item_digest": qs.item_digest(item), "idempotency_key": "down-save-01"},
                       headers=write_headers(token)).get_json()
    assert body["result"] == "saved" and body["verified"] is True and body["notes_line"] == "failed"


def test_an_unmigrated_database_is_unavailable_too(load_portal, tmp_path):
    portal = load_portal()
    attach(portal, SqliteFloor(tmp_path / "empty", migrate=False).store())
    floor = portal.owner().get(f"{API}/floor").get_json()
    assert floor["postits"]["state"] == "unavailable" and floor["postits"]["active_total"] is None


def test_not_configured_says_so(staging_without_db):
    floor = staging_without_db.owner().get(f"{API}/floor").get_json()
    assert floor["postits"]["reason"] == "not_configured"
    assert floor["postits"]["text"] == "Post-its unavailable — the floor database is not configured on this portal"


def test_a_store_outage_changes_the_etag(load_portal, tmp_path):
    portal = load_portal()
    db = SqliteFloor(tmp_path / "f")
    attach(portal, db.store())
    client = portal.owner()
    tag = client.get(f"{API}/floor").headers["ETag"]
    attach(portal, DownFloor())
    response = client.get(f"{API}/floor", headers={"If-None-Match": tag})
    assert response.status_code == 200 and response.get_json()["postits"]["state"] == "unavailable"


def test_registration_never_touches_the_floor_database(tmp_path, monkeypatch):
    monkeypatch.setattr(qs, "_running_in_container", lambda: False)
    down = DownFloor()
    queue = write_queue(tmp_path / "guild" / "build_queue.json")
    services = build_services(queue_path=str(queue), floor=down)
    assert services.ready() == []
    app = Flask("reg")
    register_guild_ui(app, owner_guard=lambda f: f, current_user=lambda: None, url_prefix="/guild-next",
                      blueprint_name="guild_ui_next", services=services)
    assert down.attempts == 0 and "guild_ui_next" in app.blueprints


@pytest.fixture
def staging_without_db(load_portal):
    return load_portal()
