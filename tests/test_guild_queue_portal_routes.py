"""B1 (a): the portal's existing status Save and metadata edit go through the
queue store, and the redirect shows success or conflict honestly."""
from __future__ import annotations

import html as _html
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from domains.guild import queue_store as qs

REPO = Path(__file__).resolve().parent.parent
ARCHIVE_POSTS: list = []
OWNER = {"username": "owner", "tier": "owner", "display_name": "Robert", "auth_id": 1}


def _items():
    return [
        {"id": 1, "spec_title": "Ready one", "status": "spec_ready", "summary": "s1",
         "last_transition_at": "2026-09-01T00:00:00Z"},
        {"id": 2, "spec_title": "Building two", "status": "in_build", "summary": "s2",
         "spec_file": "spec_two.md", "last_transition_at": "2026-09-02T00:00:00Z"},
    ]


@pytest.fixture(autouse=True)
def _fresh_session(portal_client):
    yield
    with portal_client.session_transaction() as session:
        session.clear()


@pytest.fixture
def live(tmp_path, monkeypatch, portal_client):
    import minimoi_portal.app as portal_app
    folder = tmp_path / "runtime" / "guild"
    folder.mkdir(parents=True)
    path = folder / "build_queue.json"
    path.write_bytes(qs.serialize(_items()))
    monkeypatch.setattr(portal_app, "_GUILD_QUEUE_PATH", str(path))
    monkeypatch.setattr(qs, "_running_in_container", lambda: False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(portal_app._requests, "post",
                        lambda *a, **k: ARCHIVE_POSTS.append((a, k)))
    ARCHIVE_POSTS.clear()
    with portal_client.session_transaction() as session:
        session["user"] = OWNER
    return path


def form_fields(page: str, action: str) -> dict:
    """The hidden fields of the form posting to `action`, as rendered."""
    start = page.index(f'action="{action}"')
    end = page.index("</form>", start)
    chunk = page[start:end]
    return {name: _html.unescape(value) for name, value in
            re.findall(r'<input type="hidden" name="(\w+)"[^>]*value="([^"]*)"', chunk)}


def journal(path):
    raw = (path.parent / qs.JOURNAL_NAME).read_text()
    return [json.loads(l) for l in raw.splitlines() if l.strip()]


def result_of(response):
    assert response.status_code == 302
    return {k: v[0] for k, v in parse_qs(urlparse(response.headers["Location"]).query).items()}


def test_queue_page_renders_digest_csrf_and_idempotency_fields(portal_client, live):
    page = portal_client.get("/guild/build/queue").data.decode()
    fields = form_fields(page, "/guild/build/items/1/status")
    assert fields["expect_item_digest"] == qs.item_digest(_items()[0])
    assert fields["csrf_token"] and fields["idempotency_key"]
    assert form_fields(page, "/guild/build/items/1/edit")["expect_item_digest"] == fields["expect_item_digest"]
    log_page = portal_client.get("/guild/build").data.decode()
    assert form_fields(log_page, "/guild/build/items/1/edit")["expect_item_digest"] == fields["expect_item_digest"]
    assert "Save is off" not in page


def test_status_save_goes_through_the_store_and_shows_the_receipt(portal_client, live):
    page = portal_client.get("/guild/build/queue").data.decode()
    fields = form_fields(page, "/guild/build/items/1/status")
    response = portal_client.post("/guild/build/items/1/status",
                                  data={**fields, "status": "in_build", "note": ""},
                                  headers={"Referer": "http://localhost/guild/build/queue"})
    result = result_of(response)
    assert result["save"] == "saved" and result["item"] == "1"
    assert re.fullmatch(r"q-\d{8}T\d{6}Z-[0-9a-f]{6}", result["receipt"])
    assert urlparse(response.headers["Location"]).path == "/guild/build/queue"
    assert json.loads(live.read_text())[0]["status"] == "in_build"
    lines = journal(live)
    assert [l["kind"] for l in lines] == ["intent", "completed"]
    assert lines[0]["via"] == "legacy" and lines[0]["principal"] == "owner"
    assert lines[1]["receipt_id"] == result["receipt"] and lines[1]["audit"] == "skipped"
    shown = portal_client.get(response.headers["Location"]).data.decode()
    assert f"Saved · verified · receipt {result['receipt']}" in shown


def test_status_save_conflict_is_shown_and_nothing_is_lost(portal_client, live):
    page = portal_client.get("/guild/build/queue").data.decode()
    fields = form_fields(page, "/guild/build/items/1/status")
    # Another tab edits the same item first.
    other = portal_client.post("/guild/build/items/1/edit",
                               data={**form_fields(page, "/guild/build/items/1/edit"),
                                     "summary": "from the other tab"})
    assert result_of(other)["save"] == "saved"
    after_other = live.read_bytes()
    response = portal_client.post("/guild/build/items/1/status",
                                  data={**fields, "status": "done"},
                                  headers={"Referer": "http://localhost/guild/build/queue"})
    result = result_of(response)
    assert result["save"] == "conflict" and "receipt" not in result
    assert live.read_bytes() == after_other
    shown = portal_client.get(response.headers["Location"]).data.decode()
    assert "#1 changed since you opened it" in shown
    assert ARCHIVE_POSTS == []


def test_edit_goes_through_the_store(portal_client, live):
    page = portal_client.get("/guild/build").data.decode()
    fields = form_fields(page, "/guild/build/items/2/edit")
    response = portal_client.post("/guild/build/items/2/edit",
                                  data={**fields, "spec_title": "Renamed", "summary": "",
                                        "github_issue": "#7"},
                                  headers={"Referer": "http://localhost/guild/build?status=all"})
    result = result_of(response)
    assert result["save"] == "saved" and result["receipt"].startswith("q-")
    assert parse_qs(urlparse(response.headers["Location"]).query)["status"] == ["all"]
    item = json.loads(live.read_text())[1]
    assert (item["spec_title"], item["summary"], item["github_issue"]) == ("Renamed", "s2", "#7")
    assert journal(live)[0]["op"] == "edit"


def test_missing_digest_or_csrf_changes_nothing(portal_client, live):
    before = live.read_bytes()
    page = portal_client.get("/guild/build/queue").data.decode()
    fields = form_fields(page, "/guild/build/items/1/status")
    no_digest = {k: v for k, v in fields.items() if k != "expect_item_digest"}
    assert result_of(portal_client.post("/guild/build/items/1/status",
                                        data={**no_digest, "status": "done"}))["save"] == "stale"
    no_csrf = {k: v for k, v in fields.items() if k != "csrf_token"}
    assert result_of(portal_client.post("/guild/build/items/1/status",
                                        data={**no_csrf, "status": "done"}))["save"] == "csrf"
    assert result_of(portal_client.post("/guild/build/items/1/edit",
                                        data={**no_csrf, "summary": "x"}))["save"] == "csrf"
    assert live.read_bytes() == before


def test_redirect_never_echoes_user_text_or_leaves_the_site(portal_client, live):
    page = portal_client.get("/guild/build/queue").data.decode()
    fields = form_fields(page, "/guild/build/items/1/status")
    response = portal_client.post("/guild/build/items/1/status",
                                  data={**fields, "status": "blocked", "note": "<script>x</script>"},
                                  headers={"Referer": "https://evil.example/elsewhere"})
    location = response.headers["Location"]
    assert "script" not in location and "evil.example" not in location
    assert urlparse(location).path == "/guild/build" and not urlparse(location).netloc


def test_done_save_still_asks_dev_agent_to_archive_after_the_lock(portal_client, live):
    page = portal_client.get("/guild/build/queue").data.decode()
    fields = form_fields(page, "/guild/build/items/2/status")
    assert result_of(portal_client.post("/guild/build/items/2/status",
                                        data={**fields, "status": "done"}))["save"] == "saved"
    assert ARCHIVE_POSTS and ARCHIVE_POSTS[0][1]["json"] == {"spec_file": "spec_two.md"}


def test_audit_failure_is_logged_and_shown_not_swallowed(portal_client, live, monkeypatch, caplog):
    import minimoi_portal.app as portal_app

    def broken(*_args):
        raise RuntimeError("database unreachable")
    monkeypatch.setattr(portal_app, "_queue_audit_insert", broken)
    page = portal_client.get("/guild/build/queue").data.decode()
    fields = form_fields(page, "/guild/build/items/1/status")
    response = portal_client.post("/guild/build/items/1/status", data={**fields, "status": "in_build"})
    result = result_of(response)
    assert result["save"] == "saved" and result["audit"] == "failed"
    assert "audit insert failed" in caplog.text
    shown = portal_client.get(response.headers["Location"]).data.decode()
    assert "history not recorded" in shown


def test_unset_queue_path_reads_the_marked_repository_copy_and_refuses_saves(
        portal_client, live, monkeypatch):
    import minimoi_portal.app as portal_app
    monkeypatch.setattr(portal_app, "_GUILD_QUEUE_PATH", None)
    repo_copy = REPO / "data" / "guild" / "build_queue.json"
    before = repo_copy.read_bytes()
    page = portal_client.get("/guild/build").data.decode()
    assert "Save is off: GUILD_QUEUE_PATH is not set." in page
    assert "Showing the repository copy, read-only." in page
    item = json.loads(before)[0]
    with portal_client.session_transaction() as session:
        session["queue_csrf"] = "t"
    response = portal_client.post(f"/guild/build/items/{item['id']}/status",
                                  data={"csrf_token": "t", "status": "done",
                                        "expect_item_digest": qs.item_digest(item)})
    assert result_of(response)["save"] == "refused"
    assert repo_copy.read_bytes() == before


def test_unverified_save_shows_a_check_that_can_be_marked(portal_client, live, monkeypatch):
    import os

    def replace_then_corrupt(src, dst):
        os.replace(src, dst)
        with open(dst, "ab") as stream:
            stream.write(b" ")
    monkeypatch.setattr(qs, "_replace", replace_then_corrupt)
    page = portal_client.get("/guild/build/queue").data.decode()
    fields = form_fields(page, "/guild/build/items/1/status")
    response = portal_client.post("/guild/build/items/1/status", data={**fields, "status": "in_build"})
    assert result_of(response)["save"] == "uncertain"
    monkeypatch.undo()
    monkeypatch.setattr(qs, "_running_in_container", lambda: False)
    import minimoi_portal.app as portal_app
    monkeypatch.setattr(portal_app, "_GUILD_QUEUE_PATH", str(live))
    shown = portal_client.get(response.headers["Location"]).data.decode()
    assert "Save not verified — check #1." in shown
    op_id = re.search(r'/guild/build/checks/([0-9a-f]{32})/checked', shown).group(1)
    csrf = form_fields(shown, f"/guild/build/checks/{op_id}/checked")["csrf_token"]
    assert result_of(portal_client.post(f"/guild/build/checks/{op_id}/checked",
                                        data={"csrf_token": csrf}))["save"] == "checked"
    assert "Save not verified" not in portal_client.get("/guild/build/queue").data.decode()


def test_no_plain_queue_writer_is_left_in_the_portal():
    import minimoi_portal.app as portal_app
    assert not hasattr(portal_app, "_save_build_queue")
    source = (REPO / "minimoi_portal" / "app.py").read_text()
    assert "_BQ_PATH.write_text" not in source
    assert source.count("_queue_store().save_status(") == 1
    assert source.count("_queue_store().edit_metadata(") == 1
