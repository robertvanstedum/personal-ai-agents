"""Durable, room-scoped records. No model execution or external services."""
from __future__ import annotations

import base64
from contextlib import contextmanager, closing
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid4())


def digest(value: bytes):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# Same vocabulary as Mini-moi Work; classification does not grant approval.
CONTEXT_CLASSES = {"robert_source", "external_source", "agent_draft", "coauthored_output"}


def contribution_origin(payload):
    """Caller-declared provenance, never a credential or proof of agent execution."""
    value = payload.get("origin")
    if value is None:
        return None
    allowed = {"source_application", "agent_id", "runtime", "execution_id", "mode"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise Problem("Unknown contribution origin fields")
    result = {key: string(item, "Origin " + key, 200) for key, item in value.items()}
    if result.get("mode") not in {"human", "relay", "platform_acknowledgement", "agent_response"}:
        raise Problem("Origin requires a known contribution mode")
    if not result.get("source_application"):
        raise Problem("Origin requires source_application")
    if result["mode"] == "agent_response" and not all(result.get(k) for k in ("agent_id", "runtime", "execution_id")):
        raise Problem("Agent response origin requires agent, runtime and execution identifiers")
    return result


def context_class(payload, actor):
    value = payload.get("context_class")
    if value is not None and value not in CONTEXT_CLASSES:
        raise Problem("Unknown context_class")
    if value == "robert_source" and actor != "robert":
        raise Problem("Only Robert may classify a contribution as his own source", 403)
    return value  # Missing metadata is explicitly unknown, never inferred from uploader.


class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def string(value, name, maximum=12000, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise Problem(f"{name} must contain {minimum}–{maximum} characters")
    return value.strip()


class Store:
    def __init__(self, root):
        self.root = Path(root).expanduser().absolute()
        if self.root.is_symlink():
            raise ValueError("Data directory cannot be a symlink")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.stat().st_mode & 0o077:
            raise ValueError("Data directory must be owner-private (chmod 700)")
        self.path = self.root / "records.sqlite3"
        if self.path.exists() and any(not (self.root/name).is_file() for name in ("owner-key.txt","session-key.txt")):
            raise ValueError("Existing database requires its matching private key files; restore them separately")
        self.owner_key = self._secret("owner-key.txt")
        self.session_key = self._secret("session-key.txt")
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT OR IGNORE INTO meta VALUES('schema_version','1');
                CREATE TABLE IF NOT EXISTS principals(
                    id TEXT PRIMARY KEY,label TEXT NOT NULL,kind TEXT NOT NULL,
                    token_hash TEXT UNIQUE NOT NULL,created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS rooms(
                    id TEXT PRIMARY KEY,title TEXT NOT NULL,purpose TEXT NOT NULL,
                    mode TEXT NOT NULL,state TEXT NOT NULL,version INTEGER NOT NULL,
                    moderator TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS members(
                    room TEXT REFERENCES rooms(id),actor TEXT REFERENCES principals(id),
                    role TEXT NOT NULL,PRIMARY KEY(room,actor));
                CREATE TABLE IF NOT EXISTS events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,
                    room TEXT REFERENCES rooms(id),actor TEXT REFERENCES principals(id),
                    kind TEXT NOT NULL,body TEXT NOT NULL,target TEXT,reference TEXT,
                    created TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_room ON events(room,seq);
                CREATE TABLE IF NOT EXISTS documents(
                    id TEXT PRIMARY KEY,room TEXT REFERENCES rooms(id),
                    actor TEXT REFERENCES principals(id),name TEXT NOT NULL,
                    mime TEXT NOT NULL,sha256 TEXT NOT NULL,content BLOB NOT NULL,
                    text_content TEXT,created TEXT NOT NULL,source_note TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations(
                    actor TEXT NOT NULL,key TEXT NOT NULL,request_hash TEXT NOT NULL,
                    response TEXT NOT NULL,created TEXT NOT NULL,PRIMARY KEY(actor,key));
            """)
            db.execute("BEGIN IMMEDIATE")
            version=db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
            if version not in {"1","2","3","4","5"}:
                raise ValueError("Unsupported schema version")
            if version=="1":
                db.execute("ALTER TABLE operations ADD COLUMN room TEXT REFERENCES rooms(id)")
                db.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
                version="2"
            if version=="2":
                db.execute("ALTER TABLE events ADD COLUMN context_class TEXT")
                db.execute("ALTER TABLE documents ADD COLUMN context_class TEXT")
                db.execute("""CREATE TABLE artifact_refs(
                    id TEXT PRIMARY KEY,room TEXT NOT NULL REFERENCES rooms(id),
                    event_id TEXT NOT NULL REFERENCES events(id),actor TEXT NOT NULL REFERENCES principals(id),
                    kind TEXT NOT NULL,value TEXT NOT NULL,revision TEXT NOT NULL,
                    sha256 TEXT,label TEXT NOT NULL,created TEXT NOT NULL)""")
                db.execute("CREATE INDEX artifact_lookup ON artifact_refs(kind,value,revision)")
                db.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
                version="3"
            if version=="3":
                db.execute("ALTER TABLE events ADD COLUMN origin TEXT")
                db.execute("""CREATE TABLE notes(
                    id TEXT PRIMARY KEY,room TEXT NOT NULL REFERENCES rooms(id),
                    actor TEXT NOT NULL REFERENCES principals(id),kind TEXT NOT NULL,
                    title TEXT NOT NULL,body TEXT NOT NULL,source_through_seq INTEGER NOT NULL,
                    supersedes TEXT REFERENCES notes(id),created TEXT NOT NULL,
                    context_class TEXT,origin TEXT)""")
                db.execute("CREATE INDEX notes_room ON notes(room,created)")
                db.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
                version="4"
            if version=="4":
                # Legacy `rooms` rows remain session identities: every foreign key,
                # receipt and external URL keeps its original meaning.
                db.execute("""CREATE TABLE persistent_rooms(
                    id TEXT PRIMARY KEY,title TEXT NOT NULL,purpose TEXT NOT NULL,
                    created TEXT NOT NULL,updated TEXT NOT NULL)""")
                db.execute("ALTER TABLE rooms ADD COLUMN parent_room_id TEXT REFERENCES persistent_rooms(id)")
                db.execute("INSERT INTO persistent_rooms SELECT id,title,purpose,created,updated FROM rooms")
                db.execute("UPDATE rooms SET parent_room_id=id")
                db.execute("CREATE INDEX session_parent ON rooms(parent_room_id)")
                db.execute("""CREATE TRIGGER session_parent_required BEFORE INSERT ON rooms
                    WHEN NEW.parent_room_id IS NULL BEGIN
                    SELECT RAISE(ABORT,'Session requires a parent room'); END""")
                db.execute("""CREATE TRIGGER session_parent_immutable BEFORE UPDATE OF parent_room_id ON rooms
                    WHEN NEW.parent_room_id IS NOT OLD.parent_room_id BEGIN
                    SELECT RAISE(ABORT,'Session parent cannot change'); END""")
                db.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
            db.execute("INSERT OR IGNORE INTO principals VALUES(?,?,?,?,?)",
                       ("robert", "Robert", "human", digest(self.owner_key.encode()), now()))
            owner=db.execute("SELECT token_hash FROM principals WHERE id='robert'").fetchone()
            if owner[0]!=digest(self.owner_key.encode()):
                raise ValueError("Owner access key does not match this database")
        os.chmod(self.path, 0o600)

    def _secret(self, name):
        path = self.root / name
        if path.is_symlink():
            raise ValueError("Credential file cannot be a symlink")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if path.stat().st_mode & 0o077:
                raise ValueError("Credential files must be owner-private")
            return path.read_text().strip()
        value = secrets.token_urlsafe(36)
        with os.fdopen(fd, "w") as stream:
            stream.write(value + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return value

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def authenticate(self, token):
        if not token or not isinstance(token, str):
            return None
        with self.connect() as db:
            row = db.execute("SELECT id,label,kind FROM principals WHERE token_hash=?",
                             (digest(token.encode()),)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def owner(actor):
        if actor != "robert":
            raise Problem("Only Robert can perform this operation", 403)

    def access(self, db, actor, room, write=False):
        row = db.execute("SELECT * FROM rooms WHERE id=?", (room,)).fetchone()
        member = db.execute("SELECT role FROM members WHERE room=? AND actor=?", (room, actor)).fetchone()
        if not row or (actor != "robert" and not member):
            raise Problem("Room not found", 404)
        if write and actor != "robert" and member["role"] != "contributor":
            raise Problem("This participant has read-only access", 403)
        return dict(row)

    def mutate(self, actor, key, request, action, room=None, write=True):
        key = string(key, "Idempotency-Key", 160)
        request_hash = digest(canonical(request).encode())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if room:
                self.access(db, actor, room, write)
            else:
                self.owner(actor)
            old = db.execute("SELECT * FROM operations WHERE actor=? AND key=?", (actor, key)).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise Problem("Idempotency key already used for different content", 409)
                return json.loads(old["response"])
            result = action(db)
            response = {"result": result, "receipt": {"id": uid(), "actor": actor,
                        "committed_at": now(), "durability": "local_sqlite", "production": "not_connected"}}
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?)",
                       (actor, key, request_hash, canonical(response), now(), room))
            return response

    def operation(self, actor, key):
        with self.connect() as db:
            row=db.execute("SELECT response,room FROM operations WHERE actor=? AND key=?",(actor,key)).fetchone()
            if not row: raise Problem("No committed receipt found yet",404)
            if row["room"]: self.access(db,actor,row["room"])
            elif actor!="robert": raise Problem("Receipt not available",404)
            return json.loads(row["response"])

    def _event(self, db, room, actor, kind, body, target=None, reference=None, provenance=None, origin=None):
        record = dict(id=uid(), room=room, actor=actor, kind=kind, body=body,
                      target=target, reference=reference, created=now(),context_class=provenance,origin=origin)
        cursor = db.execute("""INSERT INTO events(id,room,actor,kind,body,target,reference,created,context_class,origin)
                            VALUES(:id,:room,:actor,:kind,:body,:target,:reference,:created,:context_class,:origin)""",
                            {**record,"origin":canonical(origin) if origin is not None else None})
        record["seq"] = cursor.lastrowid
        db.execute("UPDATE rooms SET updated=? WHERE id=?", (record["created"], room))
        return record

    def create_room(self, actor, key, payload):
        title = string(payload.get("title"), "Title", 160)
        purpose = string(payload.get("purpose"), "Purpose", 2400)
        mode = payload.get("mode", "conversation")
        if mode not in {"conversation", "meeting", "bridge"}:
            raise Problem("Unknown session mode")
        if payload.get("recording_acknowledged") is not True:
            raise Problem("Acknowledge this deliberate session's recording scope")

        def action(db):
            room, timestamp = uid(), now()
            db.execute("INSERT INTO persistent_rooms VALUES(?,?,?,?,?)",(room,title,purpose,timestamp,timestamp))
            db.execute("INSERT INTO rooms VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (room,title,purpose,mode,"active",1,"robert",timestamp,timestamp,room))
            db.execute("INSERT INTO members VALUES(?,?,?)", (room,actor,"contributor"))
            self._event(db,room,actor,"session_opened", "Recorded working session opened. " + purpose)
            return dict(db.execute("SELECT * FROM rooms WHERE id=?",(room,)).fetchone())
        return self.mutate(actor,key,{"op":"create_room","payload":payload},action)

    def create_persistent_room(self, actor, key, payload):
        """Create a quiet container, not a recording session or an agent task."""
        title=string(payload.get("title"),"Title",160)
        purpose=string(payload.get("purpose"),"Purpose",2400)
        def action(db):
            timestamp=now()
            record=dict(id=uid(),title=title,purpose=purpose,created=timestamp,updated=timestamp)
            db.execute("INSERT INTO persistent_rooms VALUES(:id,:title,:purpose,:created,:updated)",record)
            return record
        return self.mutate(actor,key,{"op":"create_persistent_room","payload":payload},action)

    def create_session(self, actor, key, parent, payload):
        # Owner-only opening in this slice. Membership is never inherited.
        self.owner(actor)
        title=string(payload.get("title"),"Title",160)
        purpose=string(payload.get("purpose"),"Purpose",2400)
        mode=payload.get("mode","meeting")
        if not isinstance(mode,str) or mode not in {"conversation","meeting","bridge"}:
            raise Problem("Unknown session mode")
        if payload.get("recording_acknowledged") is not True:
            raise Problem("Acknowledge this deliberate session's recording scope")
        def action(db):
            if not db.execute("SELECT 1 FROM persistent_rooms WHERE id=?",(parent,)).fetchone():
                raise Problem("Room not found",404)
            session_id,timestamp=uid(),now()
            db.execute("INSERT INTO rooms VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (session_id,title,purpose,mode,"active",1,"robert",timestamp,timestamp,parent))
            db.execute("INSERT INTO members VALUES(?,?,?)",(session_id,actor,"contributor"))
            self._event(db,session_id,actor,"session_opened","Recorded working session opened. "+purpose)
            db.execute("UPDATE persistent_rooms SET updated=? WHERE id=?",(timestamp,parent))
            return dict(db.execute("SELECT * FROM rooms WHERE id=?",(session_id,)).fetchone())
        return self.mutate(actor,key,{"op":"create_session","parent":parent,"payload":payload},action)

    def persistent_rooms(self, actor):
        with self.connect() as db:
            # Shared membership is session-scoped. Do not leak sibling counts.
            return [dict(r) for r in db.execute("""SELECT p.id,p.title,p.purpose,p.created,
                (SELECT COUNT(*) FROM rooms s WHERE s.parent_room_id=p.id AND
                 (?='robert' OR EXISTS(SELECT 1 FROM members m WHERE m.room=s.id AND m.actor=?))) AS visible_session_count
                FROM persistent_rooms p WHERE ?='robert' OR EXISTS(
                 SELECT 1 FROM rooms s JOIN members m ON m.room=s.id
                 WHERE s.parent_room_id=p.id AND m.actor=?) ORDER BY p.created,p.id""",(actor,actor,actor,actor))]

    def persistent_room(self, actor, parent):
        with self.connect() as db:
            db.execute("BEGIN")
            record=db.execute("SELECT id,title,purpose,created FROM persistent_rooms WHERE id=?",(parent,)).fetchone()
            sessions=[dict(r) for r in db.execute("""SELECT s.* FROM rooms s WHERE s.parent_room_id=? AND
                (?='robert' OR EXISTS(SELECT 1 FROM members m WHERE m.room=s.id AND m.actor=?))
                ORDER BY s.created,s.id""",(parent,actor,actor))]
            if not record or (actor!="robert" and not sessions):
                raise Problem("Room not found",404)
            return {**dict(record),"sessions":sessions,"visible_session_count":len(sessions)}

    def rooms(self, actor):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT r.*,
                (SELECT COUNT(*) FROM events e WHERE e.room=r.id) AS event_count
                FROM rooms r WHERE ?='robert' OR EXISTS
                (SELECT 1 FROM members m WHERE m.room=r.id AND m.actor=?)
                ORDER BY updated DESC""", (actor,actor))]

    def principals(self, actor):
        self.owner(actor)
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,label,kind,created FROM principals ORDER BY created")]

    def add_principal(self, actor, key, payload):
        name = string(payload.get("id"),"Participant ID",60)
        if not re.fullmatch(r"[a-z][a-z0-9_-]*",name) or name == "robert":
            raise Problem("Use a unique lowercase participant ID")
        label = string(payload.get("label"),"Participant label",100)
        token = secrets.token_urlsafe(36)

        def action(db):
            if db.execute("SELECT 1 FROM principals WHERE id=?",(name,)).fetchone():
                raise Problem("Participant already exists",409)
            db.execute("INSERT INTO principals VALUES(?,?,?,?,?)",(name,label,"agent",digest(token.encode()),now()))
            # Do not persist a bearer token in the operations receipt.
            return {"id":name,"label":label,"kind":"agent"}
        result = self.mutate(actor,key,{"op":"principal","payload":payload},action)
        # For a retry, return no invalid newly generated credential.
        if self.authenticate(token):
            result = {**result,"access_token":token}
        return result

    def membership(self, actor, key, room, payload):
        self.owner(actor)
        target = string(payload.get("actor"),"Participant",60)
        role = payload.get("role","contributor")
        if role not in {"contributor","observer","remove"} or target == "robert":
            raise Problem("Invalid membership change")

        def action(db):
            if not db.execute("SELECT 1 FROM principals WHERE id=?",(target,)).fetchone():
                raise Problem("Unknown participant",404)
            if role == "remove":
                db.execute("DELETE FROM members WHERE room=? AND actor=?",(room,target))
                db.execute("UPDATE rooms SET moderator='robert' WHERE id=? AND moderator=?",(room,target))
            else:
                db.execute("INSERT INTO members VALUES(?,?,?) ON CONFLICT(room,actor) DO UPDATE SET role=excluded.role",
                           (room,target,role))
            return self._event(db,room,actor,"membership",f"{target}: {role}",target)
        return self.mutate(actor,key,{"op":"membership","room":room,"payload":payload},action,room)

    def room(self, actor, room, after=0):
        with self.connect() as db:
            # One snapshot: state, membership and transcript must describe the
            # same instant when a model response is conditioned on this read.
            db.execute("BEGIN")
            data = self.access(db,actor,room)
            data["contribution_guard"] = {
                "version": data["version"],
                "last_seq": db.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE room=?",(room,)).fetchone()[0],
            }
            data["events"] = [dict(r) for r in db.execute("""SELECT e.*,p.label AS actor_label,p.kind AS actor_kind
                FROM events e JOIN principals p ON p.id=e.actor WHERE e.room=? AND e.seq>? ORDER BY e.seq""",(room,after))]
            for event in data["events"]:
                event["origin"] = json.loads(event["origin"]) if event["origin"] else None
            data["notes"] = [dict(r) for r in db.execute("""SELECT n.*,p.label AS actor_label
                FROM notes n JOIN principals p ON p.id=n.actor WHERE n.room=? ORDER BY n.created,n.id""",(room,))]
            for note in data["notes"]:
                note["origin"] = json.loads(note["origin"]) if note["origin"] else None
            data["origin_assurance"] = "Caller-declared metadata; actor alone is authenticated. Runtime execution is not attested."
            data["decision_authority"] = "Session decision evidence; formal artifact dispositions remain in Work. No Work write is performed."
            data["members"] = [dict(r) for r in db.execute("""SELECT p.id,p.label,p.kind,m.role,
                (SELECT MAX(created) FROM events WHERE room=m.room AND actor=p.id) AS last_contribution
                FROM members m JOIN principals p ON p.id=m.actor WHERE m.room=? ORDER BY p.created""",(room,))]
            data["documents"] = [dict(r) for r in db.execute("""SELECT id,name,mime,sha256,created,source_note,context_class,
                length(content) AS size,actor FROM documents WHERE room=? ORDER BY created""",(room,))]
            data["artifact_refs"] = [dict(r) for r in db.execute("SELECT * FROM artifact_refs WHERE room=? ORDER BY created",(room,))]
            return data

    def state(self, actor, key, room, payload):
        def action(db):
            current = self.access(db,actor,room,True)
            if actor not in {"robert",current["moderator"]}:
                raise Problem("Only the owner or moderator can change session state",403)
            if payload.get("version") != current["version"]:
                raise Problem("Session changed; refresh before applying your update",409)
            state = payload.get("state")
            if state not in {"active","paused","closed"}:
                raise Problem("Unknown session state")
            if state == current["state"]:
                raise Problem("Session already has that state",409)
            if current["state"] == "closed":
                raise Problem("Closed sessions cannot reopen; open a new session in the room",409)
            body = string(payload.get("checkpoint"),"Closing/resumption checkpoint",2400)
            db.execute("UPDATE rooms SET state=?,version=version+1 WHERE id=?",(state,room))
            event = self._event(db,room,actor,"state_change",f"{current['state']} → {state}. {body}")
            return {"state":state,"version":current["version"]+1,"event":event}
        return self.mutate(actor,key,{"op":"state","room":room,"payload":payload},action,room)

    def moderator(self, actor, key, room, payload):
        self.owner(actor)
        def action(db):
            target=payload.get("actor")
            member=db.execute("SELECT role FROM members WHERE room=? AND actor=?",(room,target)).fetchone()
            if not member or member["role"] != "contributor":
                raise Problem("Moderator must be a contributing member")
            db.execute("UPDATE rooms SET moderator=?,version=version+1 WHERE id=?",(target,room))
            return self._event(db,room,actor,"moderator",f"Moderator assigned: {target}",target)
        return self.mutate(actor,key,{"op":"moderator","room":room,"payload":payload},action,room)

    def append(self, actor, key, room, payload):
        kind=payload.get("kind","message")
        if kind not in {"message","checkpoint","proposal","decision","task","task_update"}:
            raise Problem("Unsupported contribution type")
        body=string(payload.get("body"),"Contribution",16000)
        provenance=context_class(payload,actor)
        origin=contribution_origin(payload)
        target,reference=payload.get("target") or None,payload.get("reference") or None
        if target is not None: target=string(target,"Target",60)
        if reference is not None: reference=string(reference,"Reference",100)
        expected=payload.get("expected_context")
        if expected is not None:
            if (not isinstance(expected,dict) or set(expected)!={"version","last_seq"}
                    or any(type(expected[k]) is not int or expected[k]<0 for k in expected)):
                raise Problem("Invalid expected_context guard")

        def action(db):
            current=self.access(db,actor,room,True)
            if current["state"] != "active":
                raise Problem("Session is not recording; resume before contributing",409)
            if expected is not None:
                latest=db.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE room=?",(room,)).fetchone()[0]
                if expected!={"version":current["version"],"last_seq":latest}:
                    raise Problem("Room changed while response was prepared; review current context before a new turn",409)
            if target and not db.execute("SELECT 1 FROM members WHERE room=? AND actor=?",(room,target)).fetchone():
                raise Problem("Target is not a participant")
            ref=db.execute("SELECT * FROM events WHERE id=? AND room=?",(reference,room)).fetchone() if reference else None
            if reference and not ref: raise Problem("Reference must identify an event in this room")
            if kind == "decision":
                self.owner(actor)
                if not ref or ref["kind"] != "proposal": raise Problem("Confirm a specific proposal")
            if kind == "task":
                if actor not in {"robert",current["moderator"]}: raise Problem("Only the owner or moderator assigns work",403)
                if not target: raise Problem("Assign the task to a participant")
            if kind == "task_update":
                if not ref or ref["kind"] != "task": raise Problem("Reference a task")
                if actor not in {"robert",ref["target"]}: raise Problem("Only the assignee or owner updates this task",403)
            return self._event(db,room,actor,kind,body,target,reference,provenance,origin)
        return self.mutate(actor,key,{"op":"append","room":room,"payload":payload},action,room)

    def note(self, actor, key, room, payload):
        """Append an interpretation of a fixed transcript prefix, including after closure."""
        kind=payload.get("kind", "meeting_notes")
        if not isinstance(kind, str) or kind not in {"meeting_notes", "decision_summary", "next_steps", "executive_brief"}:
            raise Problem("Unknown note type")
        title=string(payload.get("title"), "Note title", 200)
        body=string(payload.get("body"), "Note body", 32000)
        through=payload.get("source_through_seq")
        if type(through) is not int or through < 1:
            raise Problem("Note requires a transcript source_through_seq")
        supersedes=payload.get("supersedes")
        if supersedes is not None: supersedes=string(supersedes, "Earlier note", 100)
        provenance=context_class(payload,actor)
        origin=contribution_origin(payload)
        def action(db):
            if not db.execute("SELECT 1 FROM events WHERE room=? AND seq=?",(room,through)).fetchone():
                raise Problem("Source sequence must identify a record in this room")
            if supersedes and not db.execute("SELECT 1 FROM notes WHERE id=? AND room=? AND kind=?",
                                            (supersedes,room,kind)).fetchone():
                raise Problem("Earlier note must have the same room and note type")
            record=dict(id=uid(),room=room,actor=actor,kind=kind,title=title,body=body,
                        source_through_seq=through,supersedes=supersedes,created=now(),context_class=provenance,origin=origin)
            db.execute("""INSERT INTO notes VALUES(:id,:room,:actor,:kind,:title,:body,:source_through_seq,
                :supersedes,:created,:context_class,:origin)""",
                {**record,"origin":canonical(origin) if origin is not None else None})
            self._event(db,room,actor,"note_filed",f"Filed {kind}: {title}",reference=record["id"])
            return record
        return self.mutate(actor,key,{"op":"note","room":room,"payload":payload},action,room)

    def document(self, actor, key, room, payload):
        name=string(payload.get("name"),"Filename",200)
        if Path(name).name != name or "\\" in name or any(ord(c)<32 for c in name):
            raise Problem("Use a filename, not a path")
        note=string(payload.get("source_note"),"Source/provenance note",2400)
        provenance=context_class(payload,actor)
        try:
            encoded=payload.get("base64","")
            if not isinstance(encoded,str) or len(encoded)>2_800_000: raise ValueError()
            content=base64.b64decode(encoded,validate=True)
        except (ValueError,TypeError):
            raise Problem("Invalid or oversized file")
        if not content or len(content)>2_000_000: raise Problem("File must contain 1–2,000,000 bytes")
        mime="text/plain" if name.lower().endswith((".txt",".md",".csv",".json")) else "application/octet-stream"
        text_content=None
        if mime=="text/plain":
            try: text_content=content.decode("utf-8")
            except UnicodeError: mime="application/octet-stream"

        def action(db):
            if self.access(db,actor,room,True)["state"] != "active":
                raise Problem("Session is not recording; resume before filing a source",409)
            record=dict(id=uid(),room=room,actor=actor,name=name,mime=mime,sha256=digest(content),created=now(),source_note=note,context_class=provenance)
            db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (record["id"],room,actor,name,mime,record["sha256"],content,text_content,record["created"],note,provenance))
            self._event(db,room,actor,"document",f"Filed source: {name}",reference=record["id"])
            return record
        return self.mutate(actor,key,{"op":"document","room":room,"payload":payload},action,room)

    def link_artifact(self, actor, key, room, payload):
        kind=payload.get("kind")
        if kind not in {"sha256","git_commit","spec_path","work_artifact"}:
            raise Problem("Unknown artifact reference kind")
        value=string(payload.get("value"),"Artifact identifier",1200)
        revision=string(payload.get("revision"),"Exact revision",200)
        label=string(payload.get("label"),"Artifact label",160)
        event_id=string(payload.get("event_id"),"Supporting record",100)
        checksum=payload.get("sha256") or None
        if kind=="sha256": checksum=value
        if checksum is not None and (not isinstance(checksum,str) or not re.fullmatch(r"[0-9a-f]{64}",checksum)):
            raise Problem("SHA-256 must be 64 lowercase hexadecimal characters")
        if kind=="git_commit" and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}",revision):
            raise Problem("Use a full git commit hash as the revision")
        def action(db):
            if self.access(db,actor,room,True)["state"]!="active":
                raise Problem("Session is not recording; resume before linking evidence",409)
            if not db.execute("SELECT 1 FROM events WHERE id=? AND room=?",(event_id,room)).fetchone():
                raise Problem("Supporting record must belong to this room")
            record=dict(id=uid(),room=room,event_id=event_id,actor=actor,kind=kind,value=value,
                        revision=revision,sha256=checksum,label=label,created=now())
            db.execute("INSERT INTO artifact_refs VALUES(:id,:room,:event_id,:actor,:kind,:value,:revision,:sha256,:label,:created)",record)
            self._event(db,room,actor,"artifact_link",f"Linked artifact: {label} · revision {revision}",reference=event_id)
            return record
        return self.mutate(actor,key,{"op":"artifact_link","room":room,"payload":payload},action,room)

    def artifact_history(self, actor, kind, value, revision):
        """Exact, authorized lookup only; never fetch or open the identifier."""
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT a.*,a.actor AS linked_by,e.body,e.context_class,r.title,
                e.actor AS supporting_actor,p.label AS supporting_actor_label,
                e.kind AS supporting_kind,e.created AS supporting_created
                FROM artifact_refs a JOIN events e ON e.id=a.event_id JOIN rooms r ON r.id=a.room
                JOIN principals p ON p.id=e.actor
                WHERE a.kind=? AND a.value=? AND a.revision=? AND (?='robert' OR EXISTS
                (SELECT 1 FROM members m WHERE m.room=a.room AND m.actor=?)) ORDER BY a.created""",
                (kind,value,revision,actor,actor))]

    def get_document(self, actor, doc_id):
        with self.connect() as db:
            row=db.execute("SELECT * FROM documents WHERE id=?",(doc_id,)).fetchone()
            if not row: raise Problem("Document not found",404)
            self.access(db,actor,row["room"])
            return dict(row)

    def search(self, actor, query):
        query=string(query,"Search",160)
        pattern="%"+query.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%"
        with self.connect() as db:
            results=[]
            for row in db.execute("""SELECT n.id,n.room,r.title,n.kind,n.body,n.created,p.label AS author,n.context_class
                FROM notes n JOIN rooms r ON r.id=n.room JOIN principals p ON p.id=n.actor
                WHERE (n.title LIKE ? ESCAPE '\\' OR n.body LIKE ? ESCAPE '\\') AND
                (?='robert' OR EXISTS(SELECT 1 FROM members m WHERE m.room=n.room AND m.actor=?))
                ORDER BY n.created DESC LIMIT 40""",(pattern,pattern,actor,actor)):
                result=dict(row);result["body"]=result["body"][:500];results.append(result)
            for row in db.execute("""SELECT e.id,e.room,r.title,e.kind,e.body,e.created,p.label AS author,e.context_class
                FROM events e JOIN rooms r ON r.id=e.room JOIN principals p ON p.id=e.actor
                WHERE e.body LIKE ? ESCAPE '\\' AND (?='robert' OR EXISTS
                (SELECT 1 FROM members m WHERE m.room=e.room AND m.actor=?)) ORDER BY e.seq DESC LIMIT 60""",(pattern,actor,actor)):
                result=dict(row); result["body"]=result["body"][:500]; results.append(result)
            for row in db.execute("""SELECT d.id,d.room,r.title,'document' AS kind,d.name AS body,d.created,p.label AS author,d.context_class
                FROM documents d JOIN rooms r ON r.id=d.room JOIN principals p ON p.id=d.actor
                WHERE (d.name LIKE ? ESCAPE '\\' OR d.text_content LIKE ? ESCAPE '\\' OR d.source_note LIKE ? ESCAPE '\\')
                AND (?='robert' OR EXISTS(SELECT 1 FROM members m WHERE m.room=d.room AND m.actor=?))
                ORDER BY d.created DESC LIMIT 40""",(pattern,pattern,pattern,actor,actor)):
                results.append(dict(row))
            for row in db.execute("""SELECT a.id,a.room,r.title,'artifact_link' AS kind,
                a.label || ' · ' || a.revision || ' · ' || e.body AS body,a.created,p.label AS author,
                e.context_class,a.actor AS linked_by,e.actor AS supporting_actor,
                ep.label AS supporting_actor_label,e.kind AS supporting_kind,e.created AS supporting_created
                FROM artifact_refs a JOIN events e ON e.id=a.event_id JOIN rooms r ON r.id=a.room
                JOIN principals p ON p.id=a.actor JOIN principals ep ON ep.id=e.actor WHERE
                (a.value LIKE ? ESCAPE '\\' OR a.label LIKE ? ESCAPE '\\' OR a.revision LIKE ? ESCAPE '\\')
                AND (?='robert' OR EXISTS(SELECT 1 FROM members m WHERE m.room=a.room AND m.actor=?))
                ORDER BY a.created DESC LIMIT 40""",(pattern,pattern,pattern,actor,actor)):
                results.append(dict(row))
            return results

    def export(self, actor, room):
        record=self.room(actor,room)
        record["format"]="minimoi.room.v1"
        record["exported_at"]=now()
        record["capture_coverage"]="Only contributions and sources explicitly submitted to this application"
        for doc in record["documents"]:
            source=self.get_document(actor,doc["id"])
            doc["base64"]=base64.b64encode(source["content"]).decode()
        return record

    def transcript(self, actor, room):
        record=self.room(actor,room)
        lines=[f"# {record['title']}","",f"Purpose: {record['purpose']}",f"Session: {room}",
               f"Mode: {record['mode']} · State: {record['state']}",
               "Coverage: only contributions explicitly submitted here. Imported documents remain separate sources.",
               "Authority: session decisions are evidence; formal artifact dispositions remain in Work.",
               "Meeting notes are separate derived documents, included in JSON export; they do not replace this transcript.",""]
        for event in record["events"]:
            lines.extend([f"## {event['created']} — {event['actor_label']} [{event['kind']}]",
                          f"Record: {event['id']} · provenance: {event['context_class'] or 'not classified'}",
                          f"Declared origin (not runtime attestation): {canonical(event['origin']) if event['origin'] else 'not supplied'}",event["body"],""])
        lines.extend(["## Artifact-version links", ""])
        for ref in record["artifact_refs"]:
            lines.extend([f"{ref['label']} [{ref['kind']}] — {ref['value']}",
                          f"Revision: {ref['revision']} · SHA-256: {ref['sha256'] or 'not provided'}",
                          f"Supporting record: {ref['event_id']} · linked by: {ref['actor']}", ""])
        return "\n".join(lines)

    def backup(self, actor):
        self.owner(actor)
        directory=self.root/"backups"; directory.mkdir(mode=0o700,exist_ok=True)
        name=f"records-{uid()}.sqlite3"
        destination=directory/name
        with self.connect() as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise Problem("Backup integrity verification failed",500)
        os.chmod(destination,0o600)
        return {"file":name,"sha256":digest(destination.read_bytes()),"created":now(),
                "scope":"same_disk_test_backup","independent_backup":False,
                "note":"Database backup contains credential hashes; original access keys are stored separately."}
