import json
from uuid import uuid4
import pytest
from store import Store, Problem
from transcript_snapshot import capture
from transcript_format import render


@pytest.fixture
def source(tmp_path):
    store = Store(tmp_path / "records")
    room = store.create_room("robert", "open", dict(title="Synthetic", purpose="Test only", mode="meeting", recording_acknowledged=True))["result"]["id"]
    return store, room


def test_capture_stable_revision_receipts_and_notes(source):
    store, room = source
    saved = store.append("robert", "line", room, dict(body="Exact\nmultiline"))
    first, stamp = capture(store,"robert",room)
    assert capture(Store(store.root),"robert",room) == (first,stamp)
    assert store.operation("robert","line") == saved
    note = store.note("robert", "note", room, dict(title="Notes", body="Separate", source_through_seq=saved["result"]["seq"]))
    second, stamp2 = capture(store,"robert",room)
    assert second["source_revision"] == first["source_revision"] + 1
    assert second["notes"][0]["note_id"] == note["result"]["id"]
    assert json.loads(render(second,snapshot_at=stamp2)["transcript.json"])["raw_transcript"][1]["text"] == "Exact\nmultiline"


def test_owner_only_and_attachment_bytes_excluded(source):
    store,room = source
    store.add_principal("robert","agent",dict(id="cos-dev",label="CoS"))
    store.membership("robert","member",room,dict(actor="cos-dev",role="contributor"))
    with pytest.raises(Problem): capture(store,"cos-dev",room)
    store.document("robert","doc",room,dict(name="a.txt",source_note="Synthetic",base64="aGVsbG8="))
    data,stamp = capture(store,"robert",room)
    output=render(data,snapshot_at=stamp)
    assert b"aGVsbG8=" not in output["transcript.json"]
    assert data["references"][0]["kind"] == "document"


def test_membership_change_advances_revision(source):
    store,room=source
    store.add_principal("robert","agent",dict(id="cos-dev",label="CoS"))
    store.membership("robert","member",room,dict(actor="cos-dev",role="contributor"))
    first,_=capture(store,"robert",room)
    store.membership("robert","remove",room,dict(actor="cos-dev",role="remove"))
    second,_=capture(store,"robert",room)
    assert second["source_revision"] > first["source_revision"]


def test_closed_legacy_timestamp_is_unknown_not_guessed(source):
    store,room=source
    store.state("robert","close",room,dict(state="closed",version=1,checkpoint="Done"))
    data,stamp=capture(store,"robert",room)
    assert data["session"]["closed_at"] is None
    assert "session.closed_at" in data["coverage"]["unknown_fields"]
    assert b"Publication: final" in render(data,snapshot_at=stamp)["transcript.md"]


def test_capture_failure_rolls_back_export_metadata(source):
    store,room=source
    with store.connect() as db:
        db.execute("UPDATE events SET kind='unsupported' WHERE room=?",(room,))
    with pytest.raises(Exception): capture(store,"robert",room)
    with store.connect() as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='transcript_origin'").fetchone() is None


def test_origin_survives_sqlite_snapshot_restore(source,tmp_path):
    import shutil
    import sqlite3
    store,room=source
    expected=capture(store,"robert",room)
    target=tmp_path/"restored"
    target.mkdir(mode=0o700)
    for name in ("owner-key.txt","session-key.txt"):
        shutil.copy2(store.root/name,target/name)
    with store.connect() as db, sqlite3.connect(target/"records.sqlite3") as dest:
        db.backup(dest)
    assert capture(Store(target),"robert",room)==expected
