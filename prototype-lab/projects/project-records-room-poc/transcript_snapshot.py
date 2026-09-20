"""Owner-only consistent transcript capture. Explicit call; no live migration.

Adds export metadata tables on first authorized call, within the same SQLite
transaction as capture. Existing event/note/receipt rows are never rewritten.
Revisions identify observed export snapshots, not every intervening mutation.
Automatic publication scheduling is a separate integration, not provided here.
"""
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4, uuid5, UUID

from transcript_format import VERSION, validate


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def capture(store, actor, session_id):
    store.owner(actor)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        session = store.access(db, actor, session_id)
        db.execute("CREATE TABLE IF NOT EXISTS transcript_origin(id INTEGER PRIMARY KEY CHECK(id=1), source_id TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS transcript_snapshots(session TEXT PRIMARY KEY REFERENCES rooms(id), revision INTEGER NOT NULL, fingerprint TEXT NOT NULL, snapshot_at TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO transcript_origin VALUES(1,?)", (str(uuid4()),))
        source = db.execute("SELECT source_id FROM transcript_origin WHERE id=1").fetchone()[0]
        UUID(source)
        parent = dict(db.execute("SELECT * FROM persistent_rooms WHERE id=?", (session["parent_room_id"],)).fetchone())
        events = [dict(r) for r in db.execute("SELECT * FROM events WHERE room=? ORDER BY seq,id", (session_id,))]
        notes = [dict(r) for r in db.execute("SELECT * FROM notes WHERE room=? ORDER BY created,id", (session_id,))]
        refs = [dict(r) for r in db.execute("SELECT * FROM artifact_refs WHERE room=? ORDER BY id", (session_id,))]
        documents = [dict(r) for r in db.execute("SELECT id,name,sha256,actor FROM documents WHERE room=? ORDER BY id", (session_id,))]
        members = [dict(r) for r in db.execute("SELECT actor,role FROM members WHERE room=? ORDER BY actor", (session_id,))]
        actors = {r["actor"] for r in events + notes} | {r["actor"] for r in members}
        participants = []
        for actor_id in sorted(actors):
            principal = db.execute("SELECT id,label,kind FROM principals WHERE id=?", (actor_id,)).fetchone()
            participants.append(dict(participant_id=principal["id"], display_name=principal["label"], kind=principal["kind"]))
        kinds = dict(session_opened="lifecycle", state_change="lifecycle", decision="recorded_decision",
                     task="assignment", document="document_filed", artifact_link="artifact_linked")
        ids = {e["id"] for e in events}
        records = []
        for event in events:
            origin = json.loads(event["origin"]) if event["origin"] else {}
            # Historical display labels were not captured: use stable identity,
            # not today's label disguised as a historical quote.
            records.append(dict(record_id=event["id"], seq=event["seq"],
                kind=kinds.get(event["kind"], event["kind"]), speaker_id=event["actor"],
                submitted_by=event["actor"], speaker_label=event["actor"], text=event["body"],
                source_created_at=None, ingested_at=utc(event["created"]),
                agent_id=origin.get("agent_id"), model=None,
                source_application=origin.get("source_application"),
                material_class=event["context_class"], origin_assurance="declared" if origin else "unknown",
                reply_to_record_id=event["reference"] if event["reference"] in ids else None,
                corrects_record_id=None, context_through_seq=None))
            if origin.get("material_type") in {"transcript","handoff"}:
                records[-1]["imported_source"]={key:origin[key] for key in
                    ("declared_speaker","coverage","source_ordinal","source_record_ids","material_type") if key in origin}
                records[-1]["source_created_at"]=origin.get("source_created_at")
        note_by_id = {n["id"]: n for n in notes}
        def version(note):
            seen, current, count = set(), note, 1
            while current["supersedes"] is not None:
                if current["id"] in seen: raise ValueError("Cyclic note ancestry")
                seen.add(current["id"])
                current = note_by_id[current["supersedes"]]
                count += 1
            return count
        mapped_notes = [dict(note_id=n["id"], version=version(n), kind=n["kind"], author_id=n["actor"],
            created_at=utc(n["created"]), text=n["body"], source_through_seq=n["source_through_seq"],
            source_record_ids=[e["id"] for e in events if e["seq"] <= n["source_through_seq"]],
            supersedes=n["supersedes"]) for n in notes]
        references = [dict(reference_id=r["id"], kind=r["kind"], source_record_id=r["event_id"],
            source_note_id=None, target=r["value"], target_revision=r["revision"],
            sha256=r["sha256"], label=r["label"]) for r in refs]
        for document in documents:
            event = next((e for e in events if e["reference"] == document["id"] and e["kind"] == "document"), None)
            references.append(dict(reference_id=str(uuid5(UUID(source), "document:" + document["id"])),
                kind="document", source_record_id=event["id"] if event else None, source_note_id=None,
                target="records-document:" + document["id"], target_revision=None,
                sha256=document["sha256"], label=document["name"]))
        seqs = [e["seq"] for e in events]
        unknown = ["raw_transcript.source_created_at", "raw_transcript.speaker_label"]
        if session["state"] == "closed": unknown.append("session.closed_at")
        data = dict(schema_version=VERSION, source_instance_id=source, source_revision=0,
            through_seq=max(seqs, default=0),
            coverage=dict(scope="authorized_owner_full", accepted_submissions_only=True,
                gaps=[dict(after_seq=a, before_seq=b) for a,b in zip(seqs,seqs[1:]) if b>a+1],
                unknown_fields=unknown, omissions=["Attachment bytes are separate protected sources."]),
            room=dict(room_id=parent["id"], title=parent["title"]),
            session=dict(session_id=session_id, title=session["title"], purpose=session["purpose"],
                state=session["state"], opened_at=utc(session["created"]), closed_at=None),
            participants=participants, raw_transcript=records, notes=mapped_notes, references=references)
        validate(data)
        # Include authorization projection even if it doesn't change message text.
        material = json.dumps([data,members], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        fingerprint = hashlib.sha256(material.encode()).hexdigest()
        prior = db.execute("SELECT * FROM transcript_snapshots WHERE session=?", (session_id,)).fetchone()
        if prior and prior["fingerprint"] == fingerprint:
            revision, snapshot_at = prior["revision"], prior["snapshot_at"]
        else:
            revision = prior["revision"] + 1 if prior else 1
            snapshot_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            db.execute("INSERT INTO transcript_snapshots VALUES(?,?,?,?) ON CONFLICT(session) DO UPDATE SET revision=excluded.revision,fingerprint=excluded.fingerprint,snapshot_at=excluded.snapshot_at",
                       (session_id,revision,fingerprint,snapshot_at))
        data["source_revision"] = revision
        return data, snapshot_at
