"""W7 and S1/S2: with Master Craftsman off, on-record messages are kept as
notes, with time and context, and nothing replies. Off the record keeps
nothing: the server refuses before the store is reached, no row is added,
and the storage layer itself cannot hold an off-record row."""
from __future__ import annotations

import logging
import sqlite3

import pytest

from domains.guild import queue_store as qs

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

API = "/guild-next/api/v1"
HEADER = "Master Craftsman is off · your messages are kept as notes"


def _note(client, token, text, mode="on_record", **extra):
    return client.post(f"{API}/notes", json=keyed(text=text, **extra), headers=write_headers(token, mode=mode))


def test_an_on_record_note_is_kept_with_time_and_context(floored):
    db = floored.extra["floor"]
    client = floored.owner()
    token = floored.csrf(client)
    response = _note(client, token, "Remember the lock timeout on EC2",
                     context={"area": "Build Queue", "item_ref": 12, "page": "item"})
    body = response.get_json()
    assert response.status_code == 200 and body["result"] == "kept" and body["message"] == "Kept as a note"
    note = body["note"]
    assert note["text"] == "Remember the lock timeout on EC2" and note["who"] == "robert"
    assert note["context"] == {"area": "Build Queue", "item_ref": 12, "page": "item"} and note["created_at"]
    rows = db.rows("floor_messages")
    assert len(rows) == 1 and rows[0]["record_mode"] == "on_record" and rows[0]["author"] == "robert"
    listed = client.get(f"{API}/notes").get_json()
    assert [n["text"] for n in listed["notes"]] == ["Remember the lock timeout on EC2"]
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert HEADER in page and "Remember the lock timeout on EC2" in page
    assert "Build Queue · #12 · kept as a note" in page


def test_nothing_replies(floored):
    client = floored.owner()
    token = floored.csrf(client)
    _note(client, token, "hello?")
    notes = client.get(f"{API}/notes").get_json()["notes"]
    assert [n["author_kind"] for n in notes] == ["owner"]


def test_off_the_record_is_refused_before_the_store_and_keeps_nothing(floored, monkeypatch, caplog):
    db = floored.extra["floor"]
    store = floored.app.extensions["guild_ui_next"]["services"].floor

    def boom(*_a, **_k):
        raise AssertionError("the floor store must not be reached off the record")

    for name in ("add_note", "add_postit", "bin_postit", "restore_postit", "set_continue", "_open"):
        monkeypatch.setattr(store, name, boom)
    client = floored.owner()
    token = floored.csrf(client)
    secret = "an off the record thought about item 12"
    with caplog.at_level(logging.DEBUG):
        writes = [
            client.post(f"{API}/notes", json=keyed(text=secret), headers=write_headers(token, mode="off_record")),
            client.post(f"{API}/postits", json=keyed(text=secret), headers=write_headers(token, mode="off_record")),
            client.post(f"{API}/postits/1/bin", json=keyed(), headers=write_headers(token, mode="off_record")),
            client.post(f"{API}/postits/1/restore", json=keyed(), headers=write_headers(token, mode="off_record")),
            client.put(f"{API}/continue", json=keyed(kind="item", ref=12), headers=write_headers(token, mode="off_record")),
        ]
    for response in writes:
        assert response.status_code == 409
        body = response.get_json()
        assert body["error"] == "not_listening"
        assert body["message"] == "Off the record · nothing is kept. Save, notes and post-its are paused."
    monkeypatch.undo()
    assert db.count("floor_messages") == 0 and db.count("floor_postits") == 0
    assert db.count("floor_continue") == 0 and db.count("floor_requests") == 0
    assert secret not in db.all_text()
    assert secret not in caplog.text
    assert floored.journal() == []


def test_the_storage_layer_cannot_hold_an_off_record_row(floor_db):
    conn = floor_db.connect()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO guild.floor_messages (floor, request_id, author, author_kind, author_label, text, "
                     "record_mode, created_at) VALUES ('guild', 'r-offrecord', 'robert', 'owner', 'Robert', 'x', "
                     "'off_record', '2026-09-27T00:00:00+00:00')")
    conn.close()


def test_record_mode_is_required_and_must_agree(floored):
    client = floored.owner()
    token = floored.csrf(client)
    missing = client.post(f"{API}/notes", json=keyed(text="x"), headers={"X-CSRF-Token": token})
    assert missing.status_code == 422
    disagree = client.post(f"{API}/notes", json=keyed(text="x", record_mode="off_record"), headers=write_headers(token))
    assert disagree.status_code == 422
    assert floored.extra["floor"].count("floor_messages") == 0


def test_a_retried_note_is_kept_once(floored):
    db = floored.extra["floor"]
    client = floored.owner()
    token = floored.csrf(client)
    body = {"request_id": "note-retry-01", "text": "only once"}
    first = client.post(f"{API}/notes", json=body, headers=write_headers(token)).get_json()
    again = client.post(f"{API}/notes", json=body, headers=write_headers(token)).get_json()
    assert first["note"]["id"] == again["note"]["id"] and again["repeated"] is True
    other = client.post(f"{API}/notes", json={**body, "text": "changed"}, headers=write_headers(token))
    assert other.status_code == 409 and other.get_json()["error"] == "idempotency_mismatch"
    assert db.count("floor_messages") == 1
    assert client.post(f"{API}/notes", json={"text": "no key"}, headers=write_headers(token)).status_code == 422


def test_note_length_and_emptiness(floored):
    client = floored.owner()
    token = floored.csrf(client)
    assert _note(client, token, "x" * 2000).status_code == 200
    assert _note(client, token, "x" * 2001).status_code == 422
    assert _note(client, token, "   ").status_code == 422


def test_a_save_receipt_becomes_a_platform_line_after_the_receipt(floored):
    db = floored.extra["floor"]
    client = floored.owner()
    token = floored.csrf(client)
    item = next(i for i in qs.QueueStore(str(floored.queue_path)).read_items() if i["id"] == 12)
    saved = client.post(f"{API}/queue/items/12/status",
                        json={"to": "done", "expect_item_digest": qs.item_digest(item), "idempotency_key": "save-key-0001"},
                        headers=write_headers(token)).get_json()
    assert saved["result"] == "saved" and saved["notes_line"] == "ok"
    rows = db.rows("floor_messages")
    assert len(rows) == 1 and rows[0]["author_kind"] == "platform" and rows[0]["author_label"] == "Guild platform"
    assert saved["receipt_id"] in rows[0]["text"] and rows[0]["request_id"] == f"receipt-{saved['receipt_id']}"
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert f"Saved · verified · receipt {saved['receipt_id']}" in page


def test_notes_page_through_older_ones(floored):
    client = floored.owner()
    token = floored.csrf(client)
    for n in range(5):
        _note(client, token, f"note {n}")
    first = client.get(f"{API}/notes?limit=2").get_json()
    assert [n["text"] for n in first["notes"]] == ["note 3", "note 4"] and first["more"] is True
    older = client.get(f"{API}/notes?limit=2&before={first['notes'][0]['id']}").get_json()
    assert [n["text"] for n in older["notes"]] == ["note 1", "note 2"]
