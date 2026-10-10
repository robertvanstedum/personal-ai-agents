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


def _private_dir(path):
    path=Path(path)
    if path.is_symlink(): raise SystemExit("Output folder cannot be a symlink")
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chmod(path,0o700)
    return path


def _principal(store,db,name,label,kind):
    """A principal whose legacy credential is revoked at once: it signs in only
    with the scoped credential issued next (Rooms R1, ROOMS_R1.md §3.3)."""
    import secrets
    from store import digest, now
    if db.execute("SELECT 1 FROM principals WHERE id=?",(name,)).fetchone(): return False
    token_hash=digest(secrets.token_urlsafe(36).encode())
    db.execute("INSERT INTO principals VALUES(?,?,?,?,?)",(name,label,kind,token_hash,now()))
    store.platform_access.import_legacy(db,dict(id=name,label=label,token_hash=token_hash,created=now()))
    db.execute("UPDATE client_credentials SET revoked=? WHERE id=?",(now(),"legacy:"+name))
    return True


def provision_rooms(store,teammate,label,out,days=90,worker="rooms-worker",card=None,revoke_legacy=False,manual=False):
    """Owner-run, once per teammate: the teammate (membership-scoped), its
    worker (work-scoped service principal), the hosting binding and the
    teammate card. Tokens go only to 0600 files in `out`; nothing secret is
    printed. Refuses if a token file exists. Rooms R2 (ROOMS_R2.md §3.4):
    revoke_legacy revokes the teammate's existing credentials (listed in the
    result); manual creates `<teammate>-manual`, a cardless principal for
    hand-posted notes that can never post as the teammate."""
    from datetime import datetime, timedelta, timezone
    out=_private_dir(out)
    files={teammate:out/f"{teammate}.token",worker:out/f"{worker}.token"}
    if manual: files[teammate+"-manual"]=out/f"{teammate}-manual.token"
    if any(f.exists() for f in files.values()): raise SystemExit("Token files already exist; revoke and remove them first")
    from store import now
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        created={teammate:_principal(store,db,teammate,label,"agent"),
                 worker:_principal(store,db,worker,"Rooms worker" if worker=="rooms-worker" else f"Rooms worker ({worker})","service")}
        if manual: created[teammate+"-manual"]=_principal(store,db,teammate+"-manual",label+" (manual)","agent")
        kinds={r["id"]:r["kind"] for r in db.execute("SELECT id,kind FROM principals WHERE id IN (?,?)",(teammate,worker))}
        revoked=[]
        if revoke_legacy:
            for row in db.execute("""SELECT c.id FROM client_credentials c JOIN client_installations i ON i.id=c.installation
                    WHERE i.principal=? AND c.revoked IS NULL""",(teammate,)).fetchall():
                db.execute("UPDATE client_credentials SET revoked=? WHERE id=?",(now(),row["id"]))
                db.execute("INSERT INTO credential_audit VALUES(?,?,?,?,?)",(str(__import__("uuid").uuid4()),"robert",row["id"],"revoke",now()))
                revoked.append(row["id"])
    if kinds.get(teammate)!="agent" or kinds.get(worker)!="service":
        raise SystemExit("Existing principals have the wrong kind for Rooms")
    store.meetings.put_teammate("robert",teammate,card or {})
    expires=(datetime.now(timezone.utc)+timedelta(days=days)).isoformat()
    issued={}
    requests=[(teammate,dict(scope="membership",operations=["read","post","receipt"])),(worker,dict(scope="work"))]
    if manual:
        requests.append((teammate+"-manual",dict(scope="membership",operations=["read","post","receipt"])))
    for name,request in requests:
        fd=os.open(files[name],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        result=store.platform_access.issue("robert",dict(principal=name,label=f"Rooms {name}",expires_at=expires,**request))
        with os.fdopen(fd,"w") as stream:
            stream.write(result.pop("access_token")+"\n"); stream.flush(); os.fsync(stream.fileno())
        issued[name]=result
    with store.connect() as db:
        db.execute("INSERT OR IGNORE INTO hosted_teammates VALUES(?,?,?)",(issued[worker]["installation_id"],teammate,now()))
    return {"principals_created":created,"credentials":{k:{x:v[x] for x in ("credential_id","installation_id","expires_at","scope")} for k,v in issued.items()},
            "token_files":{k:str(v) for k,v in files.items()},"hosted":{"installation_id":issued[worker]["installation_id"],"teammate":teammate},
            "revoked_legacy_credentials":revoked}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["init","seed","backup","verify","publish-transcript","recover-transcripts","publish-cycle","watch-transcripts","cleanup-transcripts","provision-rooms"])
    parser.add_argument("--teammate",default="mc")
    parser.add_argument("--label",default="Master Craftsman")
    parser.add_argument("--out")
    parser.add_argument("--days",type=int,default=90)
    parser.add_argument("--worker-principal",default="rooms-worker")
    parser.add_argument("--card",help="JSON object of teammate card fields")
    parser.add_argument("--revoke-legacy",action="store_true")
    parser.add_argument("--manual",action="store_true")
    parser.add_argument("--data-dir",required=True)
    parser.add_argument("--session-id")
    parser.add_argument("--interval",type=float,default=5.0)
    parser.add_argument("--duration",type=float,default=3600.0)
    args=parser.parse_args()
    store=Store(args.data_dir)
    if args.command=="init":
        print(json.dumps({"data_directory":str(store.root),"owner_key_file":str(store.root/"owner-key.txt")}))
    elif args.command=="seed": print(json.dumps(seed(store),indent=2))
    elif args.command=="provision-rooms":
        if not args.out: parser.error("--out is required")
        print(json.dumps(provision_rooms(store,args.teammate,args.label,args.out,args.days,worker=args.worker_principal,
            card=json.loads(args.card) if args.card else None,revoke_legacy=args.revoke_legacy,manual=args.manual),indent=2))
    elif args.command=="backup": print(json.dumps(store.backup("robert"),indent=2))
    elif args.command=="publish-transcript":
        if not args.session_id: parser.error("--session-id is required")
        from transcript_publish import publish
        print(json.dumps({"bundle_directory":str(publish(store,"robert",args.session_id)),
                          "automatic_publication":False,"off_device_backup":False}))
    elif args.command=="recover-transcripts":
        from transcript_publish import recover
        print(json.dumps({"recovered":[str(path) for path in recover(store,"robert")]}))
    elif args.command=="publish-cycle":
        from transcript_worker import cycle
        result=cycle(store,"robert")
        print(json.dumps(result,indent=2))
        if result["failures"]: raise SystemExit(1)
    elif args.command=="cleanup-transcripts":
        from transcript_publish import cleanup_scratch
        print(json.dumps(cleanup_scratch(store,"robert"),indent=2))
    elif args.command=="watch-transcripts":
        import signal
        import threading
        from transcript_worker import run
        stop=threading.Event()
        previous={sig:signal.signal(sig,lambda *_:stop.set()) for sig in (signal.SIGINT,signal.SIGTERM)}
        try:
            count=run(store,"robert",interval=args.interval,duration=args.duration,stop_event=stop,
                      on_cycle=lambda report:print(json.dumps(report),flush=True))
            print(json.dumps({"state":"stopped","cycles":count,"off_device_backup":False}))
        finally:
            for sig,handler in previous.items(): signal.signal(sig,handler)
    elif args.command=="verify":
        with store.connect() as db:
            integrity=db.execute("PRAGMA integrity_check").fetchone()[0]
            fks=db.execute("PRAGMA foreign_key_check").fetchall()
            counts={table:db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("rooms","events","documents","operations")}
        print(json.dumps({"integrity":integrity,"foreign_key_errors":len(fks),"counts":counts},indent=2))
        if integrity!="ok" or fks: raise SystemExit(1)


if __name__=="__main__": main()
