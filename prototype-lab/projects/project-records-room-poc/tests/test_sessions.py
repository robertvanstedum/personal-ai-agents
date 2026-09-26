"""Synthetic schema/API tests: persistent containers do not confer sibling access."""
import json
import sqlite3
from uuid import uuid4

import pytest
from app import create_app
from store import Store, Problem
from conftest import remove_v5_shape


def payload(title="Synthetic meeting"):
    return dict(title=title,purpose="Synthetic discussion",recording_acknowledged=True)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/"private")


def parent(store):
    return store.create_persistent_room("robert",str(uuid4()),dict(title="Shared project",purpose="Public to invited session members"))["result"]["id"]


def session(store,room,title="Synthetic meeting"):
    return store.create_session("robert",str(uuid4()),room,payload(title))["result"]["id"]


def test_quiet_container_and_distinct_sessions(store):
    room=parent(store)
    assert store.rooms("robert")==[]
    assert store.persistent_room("robert",room)["sessions"]==[]
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0]==0
    first,second=session(store,room),session(store,room)
    assert len({room,first,second})==3
    assert len(store.persistent_room("robert",room)["sessions"])==2
    assert store.room("robert",first)["parent_room_id"]==room
    assert store.room("robert",second)["parent_room_id"]==room


def test_sibling_access_not_inherited_and_revocation_hides_parent(store):
    room=parent(store)
    first,second=session(store,room),session(store,room,"Hidden meeting")
    store.add_principal("robert","agent",dict(id="reviewer",label="Reviewer"))
    store.membership("robert","invite",first,dict(actor="reviewer",role="contributor"))
    visible=store.persistent_room("reviewer",room)
    assert [s["id"] for s in visible["sessions"]]==[first]
    assert visible["visible_session_count"]==1
    assert store.persistent_rooms("reviewer")[0]["visible_session_count"]==1
    assert "Hidden meeting" not in json.dumps(visible)
    with pytest.raises(Problem):store.room("reviewer",second)
    with pytest.raises(Problem):store.append("reviewer","bad",second,dict(body="Not invited"))
    store.membership("robert","revoke",first,dict(actor="reviewer",role="remove"))
    assert store.persistent_rooms("reviewer")==[]
    with pytest.raises(Problem):store.persistent_room("reviewer",room)


def test_session_create_owner_scope_capture_and_idempotency(store):
    room=parent(store)
    with pytest.raises(Problem):store.create_session("reviewer","x",room,payload())
    with pytest.raises(Problem):store.create_session("robert","x",room,dict(title="Unrecorded",purpose="No consent"))
    assert store.persistent_room("robert",room)["sessions"]==[]
    first=store.create_session("robert","x",room,payload())
    assert store.create_session("robert","x",room,payload())==first
    with pytest.raises(Problem):store.create_session("robert","x",parent(store),payload())
    with pytest.raises(Problem):store.create_session("robert","missing","missing",payload())


@pytest.mark.parametrize("target",["active","paused"])
def test_closed_session_cannot_reopen(store,target):
    room=parent(store); first=session(store,room)
    store.state("robert","close",first,dict(state="closed",version=1,checkpoint="Ended"))
    with pytest.raises(Problem,match="cannot reopen"):
        store.state("robert","reopen",first,dict(state=target,version=2,checkpoint="Wrong"))
    second=session(store,room)
    assert store.room("robert",first)["state"]=="closed"
    assert store.room("robert",second)["state"]=="active"
    with pytest.raises(Problem):store.append("robert","late",first,dict(body="Late input"))


def test_cross_session_references_and_note_coverage_rejected(store):
    room=parent(store); first,second=session(store,room),session(store,room)
    event=store.append("robert","one",first,dict(body="Session one"))["result"]
    with pytest.raises(Problem):store.append("robert","two",second,dict(body="Wrong reference",reference=event["id"]))
    with pytest.raises(Problem):store.note("robert","note",second,dict(title="Wrong coverage",body="Wrong",source_through_seq=event["seq"]))
    with pytest.raises(Problem):store.link_artifact("robert","link",second,dict(kind="sha256",value="a"*64,revision="v1",label="Test",event_id=event["id"]))


def test_v4_migration_preserves_ids_bytes_receipts_and_access(store):
    opened=store.create_room("robert","legacy-open",payload())
    old=opened["result"]["id"]
    event=store.append("robert","legacy-event",old,dict(body="Original statement"))["result"]
    store.add_principal("robert","agent",dict(id="reviewer",label="Reviewer"))
    store.membership("robert","invite",old,dict(actor="reviewer",role="observer"))
    doc=store.document("robert","doc",old,dict(name="fixture.txt",source_note="Synthetic",base64="aGVsbG8="))["result"]
    link=store.link_artifact("robert","link",old,dict(kind="sha256",value="b"*64,revision="v1",label="Fixture",event_id=event["id"]))
    note=store.note("robert","note",old,dict(title="Summary",body="Original summary",source_through_seq=event["seq"]))
    # Authentic old create-room receipt has no new parent field.
    opened["result"].pop("parent_room_id")
    with store.connect() as db:
        db.execute("UPDATE operations SET response=? WHERE key='legacy-open'",(json.dumps(opened),))
        remove_v5_shape(db)
        db.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
        before={table:[tuple(r) for r in db.execute("SELECT * FROM "+table)] for table in
                ("events","documents","artifact_refs","notes","members","operations")}
    migrated=Store(store.root)
    assert migrated.operation("robert","legacy-open")==opened
    assert migrated.create_room("robert","legacy-open",payload())==opened
    assert migrated.operation("robert","link")==link
    assert migrated.operation("robert","note")==note
    assert migrated.room("reviewer",old)["parent_room_id"]==old
    assert migrated.get_document("reviewer",doc["id"])["content"]==b"hello"
    assert migrated.persistent_room("reviewer",old)["sessions"][0]["id"]==old
    with migrated.connect() as db:
        for table,rows in before.items():assert [tuple(r) for r in db.execute("SELECT * FROM "+table)]==rows
        assert db.execute("PRAGMA foreign_key_check").fetchall()==[]
    assert Store(store.root).room("reviewer",old)==migrated.room("reviewer",old)


def test_v4_migration_failure_atomic_and_retryable(store,monkeypatch):
    old=store.create_room("robert","open",payload())["result"]["id"]
    with store.connect() as db:
        remove_v5_shape(db)
        db.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
    original=sqlite3.connect
    class Interrupted(sqlite3.Connection):
        def execute(self,sql,*args,**kwargs):
            if sql.startswith("CREATE INDEX session_parent"):raise sqlite3.OperationalError("Injected migration interruption")
            return super().execute(sql,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3,"connect",lambda *a,**kw:original(*a,**kw,factory=Interrupted))
        with pytest.raises(sqlite3.OperationalError):Store(store.root)
    with store.connect() as db:
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]=="4"
        assert "parent_room_id" not in [r[1] for r in db.execute("PRAGMA table_info(rooms)")]
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='persistent_rooms'").fetchone()
    assert Store(store.root).room("robert",old)["parent_room_id"]==old


def test_v2_routes_and_legacy_session_alias(store):
    app=create_app(store.root,testing=True); client=app.test_client()
    headers={"Authorization":"Bearer "+store.owner_key,"Idempotency-Key":"room"}
    response=client.post("/api/v2/rooms",json=dict(title="Project",purpose="Synthetic"),headers=headers)
    assert response.status_code==201
    room=response.json["result"]["id"]
    headers["Idempotency-Key"]="session"
    created=client.post(f"/api/v2/rooms/{room}/sessions",json=payload(),headers=headers)
    assert created.status_code==201
    sid=created.json["result"]["id"]
    assert client.get(f"/api/v2/sessions/{sid}",headers=headers).json==client.get(f"/api/v1/rooms/{sid}",headers=headers).json
    assert client.get("/api/v2/rooms",headers=headers).json["rooms"][0]["visible_session_count"]==1
    assert client.get(f"/api/v2/rooms/{room}",headers=headers).json["sessions"][0]["id"]==sid
    assert client.get("/api/v2/rooms").status_code==401
