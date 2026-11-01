"""In flight's two CoS-app routes (Review 06, slice 3): owner only, writes only with the portal's verified marker, one
store command per page action, retries replayed, and a plain "not recorded" answer when the store isn't configured."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import Flask, request

from domains.cos.in_flight import NOT_CONFIGURED, create_blueprint
from domains.cos.store import StoreWriter

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 4, 18, 0, tzinfo=timezone.utc)            # a Wednesday
OWNER = {"X-Minimoi-User-Tier": "owner", "X-Owner": "1"}
WRITE = {**OWNER, "X-Verified": "1"}


def _app(writer, now=NOW):
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda: writer, is_owner=lambda: request.headers.get("X-Owner") == "1",
                                            write_verified=lambda: request.headers.get("X-Verified") == "1",
                                            clock=lambda: now))
    return app.test_client()


@pytest.fixture
def writer(tmp_path):
    return StoreWriter(tmp_path / "store", "dev")


@pytest.fixture
def client(writer):
    return _app(writer)


def post(client, op, n, at="2026-11-04T17:59:00.000Z", headers=WRITE, **body):
    return client.post(f"/ui/api/in-flight/{op}", json={"op_id": f"op-{n:08d}", "at": at, **body}, headers=headers)


def test_add_change_comment_and_close_through_the_page(client, writer):
    added = post(client, "open", 1, title="  Send the walkthrough  ", priority=1)
    assert added.status_code == 200 and added.json["outcome"] == "accepted"
    row = added.json["row"]
    item = row["item_id"]
    assert item == "IF-op-00000001" and row["title"] == "Send the walkthrough" and row["priority"] == 1
    assert (row["kind"], row["status"], row["with"], row["opened_by"], row["title_provenance"]) == ("follow-up", "open", "robert", "robert", "approved")
    assert post(client, "update", 2, item_id=item, status="doing").json["row"]["status"] == "doing"
    assert post(client, "update", 3, item_id=item, priority=3).json["row"]["priority"] == 3
    commented = post(client, "comment", 4, item_id=item, text="Drafts are ready").json["row"]
    assert commented["latest_comment"]["text"] == "Drafts are ready"
    assert commented["comments"][0]["ref"] == {"type": "cos.owner_ui", "id": "op-00000004"}
    # Stored as Robert's page action: approved, citing it; actor robert, seat null.
    journal = (writer.root / "journal" / "journal.jsonl").read_text()
    assert '"kind": "in_flight.comment"' in journal or '"kind":"in_flight.comment"' in journal
    table = client.get("/ui/api/in-flight", headers=OWNER).json
    assert table["state"] == "ok" and [r["item_id"] for r in table["open"]] == [item] and table["earlier"] == []
    closed = post(client, "close", 5, item_id=item)
    assert closed.json["row"]["outcome"] == "done" and closed.json["row"]["closed_at"] == "2026-11-04T17:59:00+00:00"
    table = client.get("/ui/api/in-flight", headers=OWNER).json
    assert table["open"] == []
    assert [(w["label"], w["count"]) for w in table["earlier"]] == [("This week", 1)]
    assert table["earlier"][0]["week_start"] == "2026-11-02"
    # Closed is closed: a change after it is refused in plain words.
    late = post(client, "update", 6, item_id=item, priority=1)
    assert late.status_code == 409 and "Reload" in late.json["message"]


def test_a_retry_replays_and_never_writes_twice(client, writer):
    first = post(client, "open", 7, title="Once")
    again = post(client, "open", 7, title="Once")
    assert again.status_code == 200 and again.json["replayed"] is True
    assert len(writer.docs("cos.in_flight")) == 1
    changed = post(client, "open", 7, title="Different words")
    assert changed.status_code == 409 and changed.json["code"] == "command_id_reused"
    assert first.json["row"]["title"] == "Once"


def test_open_rows_sort_by_priority_then_last_moved_and_earlier_folds_by_week(client, writer):
    for n, (title, p, at) in enumerate([("a", 2, "2026-11-04T10:00:00Z"), ("b", 1, "2026-11-04T09:00:00Z"),
                                       ("c", 2, "2026-11-04T12:00:00Z"), ("d", 3, "2026-11-04T08:00:00Z")], start=10):
        post(client, "open", n, at=at, title=title, priority=p)
    last_week = _app(writer, datetime(2026, 10, 29, 12, 30, tzinfo=timezone.utc))
    post(last_week, "open", 20, at="2026-10-29T11:00:00Z", title="last week")
    post(last_week, "close", 21, at="2026-10-29T12:00:00Z", item_id="IF-op-00000020")
    post(client, "open", 22, at="2026-11-04T07:00:00Z", title="long ago")
    post(client, "close", 23, at="2026-11-03T23:30:00Z", item_id="IF-op-00000022")
    table = client.get("/ui/api/in-flight", headers=OWNER).json
    assert [r["title"] for r in table["open"]] == ["b", "c", "a", "d"]
    assert [(w["label"], w["count"]) for w in table["earlier"]] == [("This week", 1), ("Last week", 1)]


def test_the_page_input_is_checked_before_anything_is_written(client, writer):
    assert post(client, "open", 30, title="   ").status_code == 422
    assert post(client, "open", 31, title="x", priority=5).status_code == 422
    assert post(client, "open", 32, title="x", status="finished").status_code == 422
    assert post(client, "open", 33, title="x", kind="chore").status_code == 422
    assert post(client, "update", 34, item_id="IF-nothing").status_code == 422
    assert post(client, "comment", 35, item_id="../etc").status_code == 400
    assert post(client, "open", 36, at="2026-10-01T00:00:00Z", title="old").status_code == 400
    assert post(client, "open", 37, at="yesterday", title="x").status_code == 400
    assert client.post("/ui/api/in-flight/open", json={"op_id": "x", "at": "2026-11-04T17:59:00Z"}, headers=WRITE).status_code == 400
    assert client.post("/ui/api/in-flight/delete", json={}, headers=WRITE).status_code == 404
    assert writer.docs("cos.in_flight") == []


def test_owner_only_and_writes_need_the_portals_marker(client, writer):
    assert client.get("/ui/api/in-flight").status_code == 403
    assert post(client, "open", 40, headers={}, title="x").status_code == 403
    no_marker = post(client, "open", 41, headers=OWNER, title="x")
    assert no_marker.status_code == 403 and no_marker.json["code"] == "csrf"
    assert writer.docs("cos.in_flight") == []


def test_without_the_store_the_page_says_so_and_nothing_is_invented():
    client = _app(None)
    table = client.get("/ui/api/in-flight", headers=OWNER)
    assert table.status_code == 200 and table.json == {"state": "not_configured", "message": NOT_CONFIGURED}
    refused = post(client, "open", 50, title="x")
    assert refused.status_code == 503 and refused.json["code"] == "not_configured"


def test_the_cos_app_wires_the_routes_and_the_tab():
    source = (ROOT / "domains/cos/chief_of_staff.py").read_text()
    assert '"flight"' in source.split("_VALID_TABS = ", 1)[1].split("\n", 1)[0]
    assert "_in_flight_blueprint(" in source and "lambda: _cos_store_writer" in source
    assert '"cos.in_flight"' in (ROOT / "domains/cos/store/api.py").read_text()
    template = (ROOT / "domains/cos/templates/cos_ui.html").read_text()
    assert 'data-tab="flight"' in template and 'id="tab-flight"' in template and "cos-in-flight.js" in template


def test_the_portal_guards_and_marks_in_flight_writes():
    app = (ROOT / "minimoi_portal/app.py").read_text()
    assert 'in_flight = path.strip("/").startswith("ui/api/in-flight/")' in app
    assert "managed_voice or direct_voice or in_flight" in app
    assert '_COS_OWNER_WRITES = ("ui/api/in-flight/",)' in (ROOT / "minimoi_portal/proxy.py").read_text()
