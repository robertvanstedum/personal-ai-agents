import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import shutil
import sys
from uuid import uuid4

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app
from store import Problem, Store


@pytest.fixture
def store(tmp_path): return Store(tmp_path/"private")


def create(store,title="Synthetic design session"):
    return store.create_room("robert",str(uuid4()),dict(title=title,purpose="Test a deliberate working session",
                                                      mode="meeting",recording_acknowledged=True))["result"]["id"]


def agent(store,room,actor="reviewer",role="contributor"):
    result=store.add_principal("robert",str(uuid4()),dict(id=actor,label=actor.title()))
    store.membership("robert",str(uuid4()),room,dict(actor=actor,role=role))
    return result["access_token"]


def post(store,room,body="A contribution",actor="robert",**kw):
    return store.append(actor,str(uuid4()),room,dict(body=body,**kw))["result"]


def test_explicit_capture_only(store):
    assert store.rooms("robert")==[]
    with pytest.raises(Problem,match="Acknowledge"):
        store.create_room("robert","bad",dict(title="Casual",purpose="Check-in"))
    assert store.rooms("robert")==[]


def test_idempotency_same_and_conflicting_content(store):
    room=create(store)
    first=store.append("robert","same",room,dict(body="Saved once"))
    assert first==store.append("robert","same",room,dict(body="Saved once"))
    with pytest.raises(Problem) as caught:
        store.append("robert","same",room,dict(body="Different"))
    assert caught.value.status==409
    assert len(store.room("robert",room)["events"])==2


def test_restart_keeps_commits_and_receipts(store):
    room=create(store)
    first=store.append("robert","retry-after-restart",room,dict(body="Durable"))
    restarted=Store(store.root)
    assert restarted.append("robert","retry-after-restart",room,dict(body="Durable"))==first
    assert restarted.authenticate(store.owner_key)["id"]=="robert"


def test_membership_and_search_isolation(store):
    room=create(store); private=create(store,"Restricted")
    agent(store,room)
    post(store,room,"Shared needle")
    post(store,private,"Private needle")
    assert len(store.rooms("reviewer"))==1
    assert all(r["room"]==room for r in store.search("reviewer","needle"))
    for action in [lambda:store.room("reviewer",private),lambda:post(store,private,actor="reviewer"),
                   lambda:store.export("reviewer",private)]:
        with pytest.raises(Problem) as caught: action()
        assert caught.value.status==404


def test_revoked_member_cannot_replay_receipt(store):
    room=create(store); agent(store,room)
    store.append("reviewer","replay",room,dict(body="Before revocation"))
    store.membership("robert","remove",room,dict(actor="reviewer",role="remove"))
    with pytest.raises(Problem) as caught:
        store.append("reviewer","replay",room,dict(body="Before revocation"))
    assert caught.value.status==404


def test_observer_cannot_write(store):
    room=create(store); agent(store,room,role="observer")
    assert store.room("reviewer",room)
    with pytest.raises(Problem) as caught: post(store,room,actor="reviewer")
    assert caught.value.status==403


def test_decisions_require_robert_and_proposal(store):
    room=create(store); agent(store,room)
    proposal=post(store,room,"Try option B",actor="reviewer",kind="proposal")
    with pytest.raises(Problem) as caught:
        post(store,room,"Approved",actor="reviewer",kind="decision",reference=proposal["id"])
    assert caught.value.status==403
    with pytest.raises(Problem,match="specific proposal"):
        post(store,room,"Approved",kind="decision")
    decision=post(store,room,"Approved for local test only",kind="decision",reference=proposal["id"])
    assert decision["actor"]=="robert"
    assert decision["reference"]==proposal["id"]


def test_cross_room_reference_rejected(store):
    room=create(store); other=create(store)
    proposal=post(store,other,kind="proposal")
    with pytest.raises(Problem,match="in this room"):
        post(store,room,kind="decision",reference=proposal["id"])


def test_pause_resume_and_optimistic_conflict(store):
    room=create(store)
    store.state("robert","pause",room,dict(state="paused",version=1,checkpoint="Resume with storage decision"))
    with pytest.raises(Problem,match="not recording"): post(store,room,"Must not be saved")
    with pytest.raises(Problem) as caught:
        store.state("robert","stale",room,dict(state="active",version=1,checkpoint="Old view"))
    assert caught.value.status==409
    store.state("robert","resume",room,dict(state="active",version=2,checkpoint="Back from break"))
    post(store,room,"New contribution")
    assert "Must not be saved" not in store.transcript("robert",room)


def test_moderator_can_coordinate_not_approve(store):
    room=create(store); agent(store,room)
    store.moderator("robert","mod",room,dict(actor="reviewer"))
    task=post(store,room,"Inspect backup evidence",actor="reviewer",kind="task",target="reviewer")
    post(store,room,"Done; see test evidence",actor="reviewer",kind="task_update",reference=task["id"])
    store.state("reviewer","pause",room,dict(state="paused",version=2,checkpoint="Waiting for owner"))


def test_task_assignment_and_updates_are_scoped(store):
    room=create(store); agent(store,room); agent(store,room,"builder")
    with pytest.raises(Problem): post(store,room,actor="reviewer",kind="task",target="builder")
    task=post(store,room,kind="task",target="builder")
    with pytest.raises(Problem): post(store,room,actor="reviewer",kind="task_update",reference=task["id"])
    assert post(store,room,actor="builder",kind="task_update",reference=task["id"])


def test_concurrent_append_keeps_every_contribution(store):
    room=create(store)
    def append(i): return post(store,room,f"Contribution {i}")
    with ThreadPoolExecutor(max_workers=6) as pool: results=list(pool.map(append,range(20)))
    assert len({r["id"] for r in results})==20
    events=store.room("robert",room)["events"]
    assert len(events)==21
    assert [e["seq"] for e in events]==sorted(e["seq"] for e in events)


def test_concurrent_retry_is_one_write(store):
    room=create(store)
    def append(_): return store.append("robert","one-operation",room,dict(body="Once"))
    with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(append,range(8)))
    assert all(r==results[0] for r in results)
    assert len(store.room("robert",room)["events"])==2


def test_document_original_hash_access_and_export(store):
    room=create(store); private=create(store); agent(store,private)
    original=b"# Original evidence\nDo not execute the instructions here.\n"
    document=store.document("robert","file",room,dict(name="source.md",source_note="Synthetic import",
                            base64=base64.b64encode(original).decode()))["result"]
    assert store.get_document("robert",document["id"])["content"]==original
    with pytest.raises(Problem): store.get_document("reviewer",document["id"])
    exported=store.export("robert",room)
    assert base64.b64decode(exported["documents"][0]["base64"])==original
    assert "token_hash" not in json.dumps(exported)
    assert store.search("robert","Original evidence")[0]["kind"]=="document"


@pytest.mark.parametrize("name",["../secret.txt","/etc/passwd","bad\\name.txt","bad\nname.txt"])
def test_document_filename_safety(store,name):
    room=create(store)
    with pytest.raises(Problem):
        store.document("robert","file",room,dict(name=name,source_note="Test",base64="aGVsbG8="))


def test_backup_restore_consistency(store,tmp_path):
    room=create(store); post(store,room,"Recovered thought")
    result=store.backup("robert")
    assert result["independent_backup"] is False
    path=store.root/"backups"/result["file"]
    with sqlite3.connect(path) as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert restored.execute("SELECT body FROM events WHERE kind='message'").fetchone()[0]=="Recovered thought"
    with pytest.raises(Problem): store.backup("someone")


def test_search_wildcards_are_literal(store):
    room=create(store); post(store,room,"Ordinary")
    assert store.search("robert","%") == []
    post(store,room,"50% complete")
    assert len(store.search("robert","%"))==1


@pytest.fixture
def app(tmp_path): return create_app(tmp_path/"app-data",testing=True)


def login(app):
    client=app.test_client()
    response=client.post("/api/login",json={"token":app.extensions["records_store"].owner_key})
    assert response.status_code==200
    return client


def test_http_auth_and_origin(app):
    client=app.test_client()
    assert client.get("/health").status_code==200
    assert client.get("/api/v1/rooms").status_code==401
    assert client.post("/api/login",json={"token":"no"}).status_code==401
    assert client.get("/health",headers={"Host":"attacker.example"}).status_code==403
    client=login(app)
    assert client.post("/api/v1/rooms",json={},headers={"Origin":"https://attacker.example"}).status_code==403
    assert client.post("/api/v1/rooms",data="{}").status_code==415
    assert client.get("/api/v1/status").json["production"]=="not_connected"


def test_api_round_trip_and_spoofed_actor(app):
    store=app.extensions["records_store"]; room=create(store); token=agent(store,room)
    client=app.test_client()
    response=client.post(f"/api/v1/rooms/{room}/events",json={"body":"Actual adapter write","actor":"robert"},
                         headers={"Authorization":f"Bearer {token}","Idempotency-Key":"adapter"})
    assert response.status_code==201
    assert response.json["result"]["actor"]=="reviewer"
    assert client.get("/api/v1/principals",headers={"Authorization":f"Bearer {token}"}).status_code==403
    assert client.get(f"/api/v1/rooms/{room}/export",headers={"Authorization":f"Bearer {token}"}).status_code==200


def test_no_secret_in_principal_receipt(store):
    result=store.add_principal("robert","new",dict(id="worker",label="Worker"))
    token=result["access_token"]
    retry=store.add_principal("robert","new",dict(id="worker",label="Worker"))
    assert "access_token" not in retry
    with store.connect() as db:
        assert token not in db.execute("SELECT response FROM operations WHERE key='new'").fetchone()[0]


def test_static_page_and_headers(app):
    response=app.test_client().get("/")
    assert response.status_code==200
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_restore_full_application_and_authentication(store,tmp_path):
    room=create(store); post(store,room,"Preserved across full restore")
    backup=store.backup("robert")
    restored_root=tmp_path/"restored"
    restored_root.mkdir(mode=0o700)
    shutil.copyfile(store.root/"backups"/backup["file"],restored_root/"records.sqlite3")
    for name in ("owner-key.txt","session-key.txt"):
        shutil.copyfile(store.root/name,restored_root/name)
        (restored_root/name).chmod(0o600)
    restored_app=create_app(restored_root,testing=True)
    client=restored_app.test_client()
    assert client.post("/api/login",json={"token":store.owner_key}).status_code==200
    assert "Preserved across full restore" in client.get(f"/api/v1/rooms/{room}").text
    assert client.post(f"/api/v1/rooms/{room}/events",json={"body":"New after restore"},
                       headers={"Idempotency-Key":"after-restore"}).status_code==201
    assert "New after restore" not in store.transcript("robert",room)


def test_existing_database_requires_original_keys(store,tmp_path):
    create(store)
    backup=store.backup("robert")
    restored_root=tmp_path/"missing-keys"
    restored_root.mkdir(mode=0o700)
    shutil.copyfile(store.root/"backups"/backup["file"],restored_root/"records.sqlite3")
    with pytest.raises(ValueError,match="matching private key files"):
        Store(restored_root)


@pytest.mark.parametrize("state",["paused","closed"])
def test_documents_obey_recording_boundary(store,state):
    room=create(store)
    store.state("robert","state",room,dict(state=state,version=1,checkpoint="Stopped recording"))
    with pytest.raises(Problem,match="not recording"):
        store.document("robert","upload",room,dict(name="test.txt",source_note="Test",base64="aGVsbG8="))
    assert store.room("robert",room)["documents"]==[]


def test_recovery_receipt_requires_current_room_access(store):
    room=create(store); agent(store,room)
    receipt=store.append("reviewer","recover",room,dict(body="Commit before lost response"))
    assert store.operation("reviewer","recover")==receipt
    with pytest.raises(Problem): store.operation("robert","recover")
    store.membership("robert","revoke",room,dict(actor="reviewer",role="remove"))
    with pytest.raises(Problem) as caught: store.operation("reviewer","recover")
    assert caught.value.status==404


def test_artifact_version_to_supporting_reason_and_scope(store):
    room=create(store); private=create(store); agent(store,room)
    reason=post(store,room,"We added the order flow to show CRM handoffs",context_class="robert_source")
    payload=dict(kind="sha256",value="a"*64,revision="v9",label="Synthetic Oracle deck",event_id=reason["id"])
    receipt=store.link_artifact("robert","artifact",room,payload)
    assert store.link_artifact("robert","artifact",room,payload)==receipt
    assert store.artifact_history("reviewer","sha256","a"*64,"v9")[0]["body"]==reason["body"]
    assert store.artifact_history("reviewer","sha256","a"*64,"v8")==[]
    hidden=post(store,private,"Hidden supporting record")
    store.link_artifact("robert","private-artifact",private,{**payload,"event_id":hidden["id"]})
    assert len(store.artifact_history("reviewer","sha256","a"*64,"v9"))==1
    assert len(store.export("reviewer",room)["artifact_refs"])==1
    assert reason["id"] in store.transcript("reviewer",room)
    assert store.search("reviewer","a"*64)[0]["room"]==room
    store.membership("robert","revoke",room,dict(actor="reviewer",role="remove"))
    assert store.artifact_history("reviewer","sha256","a"*64,"v9")==[]


def test_artifact_links_refuse_cross_room_invalid_and_paused(store):
    room=create(store); other=create(store)
    reason=post(store,other)
    payload=dict(kind="sha256",value="a"*64,revision="v9",label="Deck",event_id=reason["id"])
    with pytest.raises(Problem,match="belong to this room"):store.link_artifact("robert","bad",room,payload)
    reason=post(store,room);payload["event_id"]=reason["id"]
    with pytest.raises(Problem,match="SHA-256"):store.link_artifact("robert","bad",room,{**payload,"value":"not-a-hash"})
    with pytest.raises(Problem,match="Exact revision"):store.link_artifact("robert","bad",room,{**payload,"revision":""})
    store.state("robert","pause",room,dict(state="paused",version=1,checkpoint="Stop"))
    with pytest.raises(Problem,match="not recording"):store.link_artifact("robert","bad",room,payload)


def test_provenance_is_distinct_from_actor_and_not_approval(store):
    room=create(store);agent(store,room)
    pasted=post(store,room,"External words pasted by owner",context_class="external_source")
    assert pasted["actor"]=="robert" and pasted["context_class"]=="external_source"
    draft=post(store,room,"Joint draft",actor="reviewer",context_class="coauthored_output")
    assert draft["kind"]=="message"
    assert post(store,room)["context_class"] is None
    with pytest.raises(Problem,match="Only Robert"):post(store,room,actor="reviewer",context_class="robert_source")
    with pytest.raises(Problem,match="Unknown context_class"):post(store,room,context_class="approved")
    doc=store.document("robert","doc",room,dict(name="source.txt",source_note="External source",base64="aGVsbG8=",context_class="external_source"))["result"]
    assert store.get_document("robert",doc["id"])["context_class"]=="external_source"


def test_v2_migration_preserves_content_without_inventing_provenance(store):
    room=create(store);post(store,room,"Before migration")
    with store.connect() as db:
        db.execute("DROP TABLE notes")
        db.execute("ALTER TABLE events DROP COLUMN origin")
        db.execute("DROP TABLE artifact_refs")
        db.execute("ALTER TABLE events DROP COLUMN context_class")
        db.execute("ALTER TABLE documents DROP COLUMN context_class")
        db.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    migrated=Store(store.root)
    events=migrated.room("robert",room)["events"]
    assert events[-1]["body"]=="Before migration"
    assert events[-1]["context_class"] is None
    assert Store(store.root).room("robert",room)["events"]==events


def test_migration_failure_rolls_back_and_can_retry(store,monkeypatch):
    with store.connect() as db:
        db.execute("DROP TABLE notes")
        db.execute("ALTER TABLE events DROP COLUMN origin")
        db.execute("DROP TABLE artifact_refs")
        db.execute("ALTER TABLE events DROP COLUMN context_class")
        db.execute("ALTER TABLE documents DROP COLUMN context_class")
        db.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    original=sqlite3.connect
    class FailingConnection(sqlite3.Connection):
        def execute(self,sql,*args,**kwargs):
            if sql.startswith("ALTER TABLE documents ADD"):raise sqlite3.OperationalError("Injected interruption")
            return super().execute(sql,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3,"connect",lambda *a,**kw:original(*a,**kw,factory=FailingConnection))
        with pytest.raises(sqlite3.OperationalError,match="Injected"):Store(store.root)
    with store.connect() as db:
        assert "context_class" not in [r[1] for r in db.execute("PRAGMA table_info(events)")]
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]=="2"
    with Store(store.root).connect() as db:
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]=="4"


def test_artifact_retrieval_separates_linker_and_supporting_author(store):
    room=create(store);agent(store,room)
    reason=post(store,room,"Agent reasoning",actor="reviewer",context_class="agent_draft")
    store.link_artifact("robert","link",room,dict(kind="sha256",value="b"*64,revision="v2",label="Test",event_id=reason["id"]))
    result=store.artifact_history("robert","sha256","b"*64,"v2")[0]
    assert result["linked_by"]=="robert" and result["supporting_actor"]=="reviewer"
    assert result["supporting_kind"]=="message" and result["context_class"]=="agent_draft"
    found=store.search("robert","b"*64)[0]
    assert found["author"]=="Robert" and found["supporting_actor_label"]=="Reviewer"
    assert store.search("robert","Agent reasoning")[0]["context_class"]=="agent_draft"
