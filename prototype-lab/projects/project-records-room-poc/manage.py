"""Local owner utilities. No production service is contacted."""
import argparse
import base64
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from store import Store, Problem


def seed(store):
    """Only synthetic records; idempotent re-running does not invent duplicates."""
    manifest=store.root/"fixture-manifest.json"
    if manifest.exists():
        return json.loads(manifest.read_text())
    reviewer=store.add_principal("robert","fixture-reviewer-v1",{
        "id":"example-reviewer","label":"Example reviewer (fixture)"})
    if "access_token" in reviewer:
        path=store.root/"example-reviewer-key.txt"
        with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),"w") as stream:
            stream.write(reviewer["access_token"]+"\n")
    rooms={}
    examples=[
        ("conversation","Thinking together — a private session","Synthetic fixture: explore a question with one selected participant. This is not a captured real conversation."),
        ("bridge","Deployment bridge — rehearsal","Synthetic fixture: practice a moderated check-in. No deployment, outage, or command execution is taking place."),
        ("meeting","Records foundation — test room","Synthetic fixture: explore how a working discussion becomes a durable record. All sample statements and decisions are examples, not real approvals.")]
    for mode,title,purpose in examples:
        room=store.create_room("robert",f"fixture-room-{mode}-v1",dict(title=title,purpose=purpose,mode=mode,recording_acknowledged=True))["result"]["id"]
        rooms[mode]=room
        store.membership("robert",f"fixture-member-{mode}-v1",room,dict(actor="example-reviewer",role="contributor"))
    room=rooms["meeting"]
    store.append("robert","fixture-message-v1",room,dict(body="Synthetic opening: the question is how we preserve the reasoning without turning every casual exchange into a permanent record."))
    proposal=store.append("example-reviewer","fixture-proposal-v1",room,dict(kind="proposal",body="Synthetic proposal: preserve the complete available transcript of an explicitly opened working session. Keep summaries separate and link decisions to their source proposals."))["result"]
    def guarded(actor,key,payload):
        try: return store.operation(actor,key)
        except Problem as error:
            if error.status!=404: raise
        return store.append(actor,key,room,{**payload,"expected_context":store.room(actor,room)["contribution_guard"]})
    guarded("robert","fixture-decision-v1",dict(kind="decision",body="Synthetic owner decision for this example only: use explicit session boundaries. This is not a production or implementation approval.",reference=proposal["id"]))
    task=guarded("robert","fixture-task-v1",dict(kind="task",body="Synthetic assignment: inspect whether a paused session rejects new transcript messages.",target="example-reviewer"))["result"]
    guarded("example-reviewer","fixture-task-update-v1",dict(kind="task_update",body="Fixture response: the test scenario should verify that rejected messages never appear in an export.",reference=task["id"]))
    store.append("robert","fixture-checkpoint-v1",room,dict(kind="checkpoint",body="Example checkpoint\nEstablished: originals and interpretations remain distinct.\nOpen: how live agent adapters join the meeting.\nNext: test pause/resume, export, and retrieval."))
    content=b"# Synthetic source\n\nPurposeful working sessions are preserved. Casual greetings are excluded.\n\nThis document is a test fixture, not an approved architecture.\n"
    store.document("robert","fixture-source-v1",room,dict(name="synthetic-session-principles.md",source_note="Generated synthetic fixture for this local proof; contains no imported personal dialogue.",base64=base64.b64encode(content).decode()))
    store.append("robert","fixture-bridge-v1",rooms["bridge"],dict(kind="checkpoint",body="Rehearsal only\nImpact: no real users affected.\nKnown: this is a test bridge, not an outage.\nOwner: Robert.\nNext: practice an executive check-in with source-linked contributions."))
    manifest.write_text(json.dumps(rooms,indent=2))
    os.chmod(manifest,0o600)
    return rooms


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["init","seed","backup","verify","publish-transcript","recover-transcripts"])
    parser.add_argument("--data-dir",required=True)
    parser.add_argument("--session-id")
    args=parser.parse_args()
    store=Store(args.data_dir)
    if args.command=="init":
        print(json.dumps({"data_directory":str(store.root),"owner_key_file":str(store.root/"owner-key.txt")}))
    elif args.command=="seed": print(json.dumps(seed(store),indent=2))
    elif args.command=="backup": print(json.dumps(store.backup("robert"),indent=2))
    elif args.command=="publish-transcript":
        if not args.session_id: parser.error("--session-id is required")
        from transcript_publish import publish
        print(json.dumps({"bundle_directory":str(publish(store,"robert",args.session_id)),
                          "automatic_publication":False,"off_device_backup":False}))
    elif args.command=="recover-transcripts":
        from transcript_publish import recover
        print(json.dumps({"recovered":[str(path) for path in recover(store,"robert")]}))
    elif args.command=="verify":
        with store.connect() as db:
            integrity=db.execute("PRAGMA integrity_check").fetchone()[0]
            fks=db.execute("PRAGMA foreign_key_check").fetchall()
            counts={table:db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("rooms","events","documents","operations")}
        print(json.dumps({"integrity":integrity,"foreign_key_errors":len(fks),"counts":counts},indent=2))
        if integrity!="ok" or fks: raise SystemExit(1)


if __name__=="__main__": main()
