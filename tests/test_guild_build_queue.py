"""Build Queue Phase A: a small active queue backed by a complete Build Log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
OWNER = {"username": "owner", "tier": "owner", "display_name": "Robert", "auth_id": 1}


@pytest.fixture(autouse=True)
def _fresh_session(portal_client):
    yield
    with portal_client.session_transaction() as session:
        session.pop("user", None)


def _login_owner(client):
    with client.session_transaction() as session:
        session["user"] = OWNER


@pytest.fixture
def queue_file(tmp_path, monkeypatch):
    import minimoi_portal.app as portal_app

    path = tmp_path / "build_queue.json"
    monkeypatch.setattr(portal_app, "_BQ_PATH", path)

    def write(items):
        path.write_text(json.dumps(items), encoding="utf-8")
        return path

    return write


def _item(item_id, status, title, transitioned="2026-09-01T00:00:00Z"):
    return {
        "id": item_id,
        "spec_title": title,
        "status": status,
        "last_transition_at": transitioned,
    }


def test_tracked_queue_has_unique_ids_and_only_supported_statuses():
    import minimoi_portal.app as portal_app

    items = json.loads(
        (REPO / "data" / "guild" / "build_queue.json").read_text(encoding="utf-8")
    )
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids))
    assert {item["status"] for item in items} <= set(portal_app._BUILD_QUEUE_STATUSES)


def test_queue_renders_only_spec_ready_and_in_build(portal_client, queue_file):
    queue_file([
        _item(1, "spec_ready", "Ready candidate"),
        _item(2, "in_build", "Active build"),
        _item(3, "blocked", "Waiting on a decision"),
        _item(4, "done", "Already shipped"),
        _item(5, "backlog", "Later candidate"),
    ])
    _login_owner(portal_client)

    response = portal_client.get("/guild/build/queue")
    html = response.data.decode()

    assert response.status_code == 200
    assert html.count('<div class="board-col">') == 2
    assert "Ready candidate" in html
    assert "Active build" in html
    assert "Waiting on a decision" not in html
    assert "Already shipped" not in html
    assert "Later candidate" not in html
    assert "Recently Done" not in html
    assert "View full Build Log" in html


def test_queue_orders_newest_first_inside_each_active_status(portal_client, queue_file):
    queue_file([
        _item(1, "in_build", "Older build", "2026-08-01T00:00:00Z"),
        _item(2, "spec_ready", "Older ready", "2026-08-02T00:00:00Z"),
        _item(3, "in_build", "Newer build", "2026-09-02T00:00:00Z"),
        _item(4, "spec_ready", "Newer ready", "2026-09-03T00:00:00Z"),
    ])
    _login_owner(portal_client)

    html = portal_client.get("/guild/build/queue").data.decode()

    assert html.index("Newer ready") < html.index("Older ready")
    assert html.index("Newer build") < html.index("Older build")
    assert html.index("Older ready") < html.index("Newer build")


def test_build_log_exposes_every_lifecycle_filter(portal_client, queue_file):
    queue_file([_item(1, "idea", "An idea")])
    _login_owner(portal_client)

    html = portal_client.get("/guild/build").data.decode()

    for status in (
        "idea", "design", "backlog", "spec_ready", "in_build", "blocked",
        "deferred", "cancelled", "superseded", "done",
    ):
        assert f'/guild/build?status={status}' in html
    assert "/guild/build?status=incomplete" not in html


def test_build_log_defaults_to_active_work_and_keeps_all_history_available(
    portal_client, queue_file
):
    queue_file([
        _item(1, "backlog", "Current backlog"),
        _item(2, "blocked", "Current blocker"),
        _item(3, "done", "Historical completion"),
        _item(4, "cancelled", "Historical cancellation"),
    ])
    _login_owner(portal_client)

    active_html = portal_client.get("/guild/build").data.decode()
    all_html = portal_client.get("/guild/build?status=all").data.decode()

    assert "Current backlog" in active_html
    assert "Current blocker" in active_html
    assert "Historical completion" not in active_html
    assert "Historical cancellation" not in active_html
    assert "Historical completion" in all_html
    assert "Historical cancellation" in all_html


@pytest.mark.parametrize("status", ["backlog", "blocked", "cancelled", "done"])
def test_status_transition_accepts_non_queue_destinations(
    portal_client, queue_file, status
):
    path = queue_file([_item(1, "in_build", "Move me")])
    _login_owner(portal_client)

    response = portal_client.post(
        "/guild/build/items/1/status",
        data={"status": status, "note": "waiting" if status == "blocked" else ""},
    )

    assert response.status_code == 302
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved[0]["status"] == status
    assert saved[0]["blocked_reason"] == ("waiting" if status == "blocked" else None)
