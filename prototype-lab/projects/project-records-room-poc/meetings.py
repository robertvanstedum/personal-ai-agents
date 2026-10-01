"""Rooms R1: meetings with server-hosted teammates (Master Craftsman first).

Specification: docs/specs/minimoi-connected-work/ROOMS_R1.md (v0.5.1).

Records owns scheduling, turn state and the only write path. A sibling worker
claims bounded turns over HTTP with a work-scoped credential and posts each
reply as the teammate, with the teammate's own membership-scoped credential.
No model is called here.

Schema: new tables only. No existing table gains a column and the store's
schema_version is not bumped, so the previous Records image starts and runs on
a migrated database (rollback keeps every accepted record; spec §3.2, §7).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from uuid import uuid4

from store import Problem, canonical, now, string


SCHEMA = """
CREATE TABLE IF NOT EXISTS room_generations(
    room TEXT PRIMARY KEY,generation TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meetings(
    room TEXT PRIMARY KEY,kind TEXT NOT NULL,brief TEXT NOT NULL,facilitator TEXT,
    language TEXT,cursor INTEGER NOT NULL,remaining INTEGER NOT NULL,max_turns INTEGER NOT NULL,
    window_s INTEGER NOT NULL,window_expires TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS member_rsvp(
    room TEXT NOT NULL,actor TEXT NOT NULL,rsvp TEXT NOT NULL,rsvp_at TEXT,rsvp_reason TEXT,
    PRIMARY KEY(room,actor));
CREATE TABLE IF NOT EXISTS presence(
    scope TEXT NOT NULL,actor TEXT NOT NULL,kind TEXT NOT NULL,observed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,source TEXT NOT NULL,PRIMARY KEY(scope,actor,kind));
CREATE TABLE IF NOT EXISTS credential_scopes(
    credential_id TEXT PRIMARY KEY,scope TEXT NOT NULL,operations TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hosted_teammates(
    installation_id TEXT NOT NULL,principal TEXT NOT NULL,created TEXT NOT NULL,
    PRIMARY KEY(installation_id,principal));
CREATE TABLE IF NOT EXISTS turns(
    id TEXT PRIMARY KEY,room TEXT NOT NULL,generation TEXT NOT NULL,addressee TEXT NOT NULL,
    trigger_seq INTEGER NOT NULL,attempt INTEGER NOT NULL,state TEXT NOT NULL,claimed_by TEXT,
    claim_id TEXT,lease_until TEXT,budget_reserved INTEGER NOT NULL DEFAULT 0,created TEXT NOT NULL,
    expires TEXT NOT NULL,snapshot_through_seq INTEGER,result TEXT,disposition TEXT,stop_ack TEXT,
    updated TEXT NOT NULL,UNIQUE(room,addressee,trigger_seq,attempt));
CREATE INDEX IF NOT EXISTS turns_room ON turns(room,addressee,state);
CREATE TABLE IF NOT EXISTS teammates(
    principal TEXT PRIMARY KEY,host TEXT NOT NULL,connector TEXT NOT NULL,tools_profile TEXT NOT NULL,
    max_turn_s INTEGER NOT NULL,max_turns_per_meeting INTEGER NOT NULL,max_concurrency INTEGER NOT NULL,
    billing_route TEXT NOT NULL,auto_accept INTEGER NOT NULL,rsvp_timeout_s INTEGER NOT NULL,
    proven_at TEXT,last_failure_at TEXT,created TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS routing_notes(
    room TEXT NOT NULL,trigger_seq INTEGER NOT NULL,addressee TEXT NOT NULL,label TEXT NOT NULL,
    disposition TEXT NOT NULL,created TEXT NOT NULL,PRIMARY KEY(room,trigger_seq,addressee));
INSERT OR IGNORE INTO meta VALUES('rooms_r1_schema','1');
"""

ACTIVE = ("claimed", "running", "cancel_requested", "recovering")   # hold the (room, addressee) slot
TERMINAL = ("committed", "cancelled", "superseded", "expired", "failed", "abandoned")
LEASE_S = 60
TURN_TTL_S = 600
ANSWERING_TTL_S = 90
HERE_TTL_S = 90
REACHABLE_TTL_S = 180
READYZ_FRESH_S = 60
SNAPSHOT_LIMIT = 40
DEFAULT_MAX_TURNS = 20
DEFAULT_WINDOW_S = 3600
PROOF_WINDOW_S = 900
PROOF_QUESTION = ("Proof turn: please introduce yourself in one or two sentences and say what you can "
                  "and cannot do in this meeting.")
SNAPSHOT_KINDS = ("message", "proposal", "decision", "task", "task_update", "checkpoint", "document")
MENTION = re.compile(r"(?<![\w@.])@([A-Za-z][A-Za-z0-9_-]{0,59})")
HEX32 = re.compile(r"[0-9a-f]{32}")


def stamp(delta_s=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


def response_key(addressee, turn_id):
    return f"{addressee}-response:{turn_id}"


def ensure_schema(db):
    db.executescript(SCHEMA)
    # Every room present at migration gets a generation (the fence, spec §3.2).
    for row in db.execute("SELECT id FROM rooms WHERE id NOT IN (SELECT room FROM room_generations)").fetchall():
        db.execute("INSERT INTO room_generations VALUES(?,?,?)", (row["id"], str(uuid4()), now()))


def generation(db, room):
    """The room's current generation; created on first use for rooms made later
    (including by an older image after a rollback)."""
    row = db.execute("SELECT generation FROM room_generations WHERE room=?", (room,)).fetchone()
    if row:
        return row["generation"]
    value = str(uuid4())
    db.execute("INSERT INTO room_generations VALUES(?,?,?)", (room, value, now()))
    return value


def regenerate(db, room):
    value = str(uuid4())
    db.execute("INSERT INTO room_generations VALUES(?,?,?) ON CONFLICT(room) DO UPDATE SET "
               "generation=excluded.generation,updated=excluded.updated", (room, value, now()))
    return value


def scope_of(db, credential_id):
    if not credential_id:
        return None
    row = db.execute("SELECT scope,operations FROM credential_scopes WHERE credential_id=?", (credential_id,)).fetchone()
    return {"scope": row["scope"], "operations": json.loads(row["operations"])} if row else None


def rsvp_of(db, room, actor):
    """A member without a row reads accepted (members added before R1)."""
    row = db.execute("SELECT rsvp,rsvp_at,rsvp_reason FROM member_rsvp WHERE room=? AND actor=?", (room, actor)).fetchone()
    return dict(row) if row else {"rsvp": "accepted", "rsvp_at": None, "rsvp_reason": None}


def accepted_member(db, room, actor, contributor=True):
    member = db.execute("SELECT role FROM members WHERE room=? AND actor=?", (room, actor)).fetchone()
    if not member or (contributor and member["role"] != "contributor"):
        return False
    return rsvp_of(db, room, actor)["rsvp"] == "accepted"


def membership_credential_valid(db, principal):
    row = db.execute("""SELECT 1 FROM client_credentials c JOIN client_installations i ON i.id=c.installation
        JOIN credential_scopes s ON s.credential_id=c.id
        WHERE i.principal=? AND s.scope='membership' AND c.revoked IS NULL AND (c.expires IS NULL OR c.expires>?)""",
                     (principal, now())).fetchone()
    return bool(row)


def card(db, principal):
    row = db.execute("SELECT * FROM teammates WHERE principal=?", (principal,)).fetchone()
    return dict(row) if row else None


def hosted(db, principal):
    return bool(db.execute("SELECT 1 FROM hosted_teammates WHERE principal=?", (principal,)).fetchone())


def label_of(db, principal):
    row = db.execute("SELECT label FROM principals WHERE id=?", (principal,)).fetchone()
    return row["label"] if row else principal


def fresh(db, scope, actor, kind):
    return bool(db.execute("SELECT 1 FROM presence WHERE scope=? AND actor=? AND kind=? AND expires_at>?",
                           (scope, actor, kind, now())).fetchone())


def set_presence(db, scope, actor, kind, ttl, source):
    db.execute("""INSERT INTO presence VALUES(?,?,?,?,?,?) ON CONFLICT(scope,actor,kind) DO UPDATE SET
        observed_at=excluded.observed_at,expires_at=excluded.expires_at,source=excluded.source""",
               (scope, actor, kind, now(), stamp(ttl), source))


def clear_presence(db, scope, actor, kind):
    db.execute("DELETE FROM presence WHERE scope=? AND actor=? AND kind=?", (scope, actor, kind))


def set_turn(db, turn_id, **values):
    values["updated"] = now()
    columns = ",".join(f"{k}=?" for k in values)
    db.execute(f"UPDATE turns SET {columns} WHERE id=?", (*values.values(), turn_id))


def turn_row(db, turn_id):
    row = db.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
    if not row:
        raise Problem("Turn not found", 404)
    return dict(row)


def proven_or_proof(db, room, principal):
    record = card(db, principal)
    if record and record["proven_at"]:
        return True
    meeting = db.execute("SELECT kind FROM meetings WHERE room=?", (room,)).fetchone()
    return bool(meeting and meeting["kind"] == "proof")


def sweep(db):
    """Records-owned lease expiry (spec §3.4). Runs inside every meeting route."""
    moment = now()
    for row in db.execute("SELECT id,state,room,addressee FROM turns WHERE state IN "
                          "('claimed','running','recovering','cancel_requested') AND lease_until<?", (moment,)).fetchall():
        if row["state"] == "cancel_requested":
            set_turn(db, row["id"], state="cancelled", stop_ack="none", disposition="fenced_without_ack")
        else:
            set_turn(db, row["id"], state="uncertain", disposition="lease_expired")
        clear_presence(db, row["room"], row["addressee"], "answering")
    db.execute("UPDATE turns SET state='expired',disposition='not_answered_busy',updated=? "
               "WHERE state='queued' AND expires<=?", (moment, moment))


class Meetings:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            ensure_schema(db)

    # ── hooks called by Store inside its own write transactions ──────────────
    def on_state(self, db, room, new_state, reason):
        """Pause/resume/close/stop: new generation; fence every unfinished turn."""
        regenerate(db, room)
        db.execute("UPDATE turns SET state='cancelled',disposition=?,updated=? WHERE room=? AND state='queued'",
                   (reason, now(), room))
        db.execute("UPDATE turns SET state='cancel_requested',disposition=?,updated=? WHERE room=? "
                   "AND state IN ('claimed','running')", (reason, now(), room))
        db.execute("UPDATE turns SET state='cancelled',disposition='paused_during_recovery',updated=? "
                   "WHERE room=? AND state='recovering'", (now(), room))

    def on_membership(self, db, room, target, role):
        """Removal or downgrade fences that teammate's unfinished turns (spec §3.3)."""
        if role in {"remove", "observer"}:
            self.fence_principal(db, target, "membership_ended", room)
            if role == "remove":
                db.execute("DELETE FROM member_rsvp WHERE room=? AND actor=?", (room, target))

    def fence_principal(self, db, principal, reason, room=None):
        where, args = ("AND room=?", (room,)) if room else ("", ())
        db.execute(f"UPDATE turns SET state='cancelled',disposition=?,updated=? WHERE addressee=? AND state='queued' {where}",
                   (reason, now(), principal, *args))
        db.execute(f"UPDATE turns SET state='cancel_requested',disposition=?,updated=? WHERE addressee=? "
                   f"AND state IN ('claimed','running','recovering') {where}", (reason, now(), principal, *args))

    def on_revoke(self, db, principal):
        self.fence_principal(db, principal, "credential_invalid")

    def fence_append(self, db, actor, room, payload, credential_scope):
        """The write fence (spec §3.5). Returns the turn being committed, or None
        for an ordinary (human) post. Raises 409 with no row otherwise."""
        origin = payload.get("origin") or {}
        agent = ((credential_scope or {}).get("scope") == "membership" or "turn_id" in payload
                 or (origin.get("mode") == "agent_response" and card(db, actor) is not None))
        if not agent:
            return None    # people, and the existing CoS connector path (no card), are unchanged (M6)
        turn_id, claim_id = payload.get("turn_id"), payload.get("claim_id")
        if not isinstance(turn_id, str) or not isinstance(claim_id, str):
            raise Problem("An agent reply must name its claimed turn", 409)
        sweep(db)
        row = db.execute("SELECT * FROM turns WHERE id=? AND room=?", (turn_id, room)).fetchone()
        expected = payload.get("expected_context")
        if (not row or row["addressee"] != actor or row["claim_id"] != claim_id
                or row["state"] not in ("running", "recovering") or not row["lease_until"] or row["lease_until"] <= now()
                or row["generation"] != generation(db, room)
                or not db.execute("SELECT 1 FROM hosted_teammates WHERE installation_id=? AND principal=?",
                                  (row["claimed_by"], actor)).fetchone()
                or not isinstance(expected, dict) or set(expected) != {"generation", "trigger_seq"}
                or expected["generation"] != row["generation"] or expected["trigger_seq"] != row["trigger_seq"]):
            raise Problem("The meeting moved on before this reply arrived; it was not added (stale_turn)", 409)
        if payload.get("kind", "message") != "message":
            raise Problem("A teammate turn posts a message", 409)
        if not HEX32.fullmatch(str(origin.get("execution_id", ""))):
            raise Problem("A teammate reply carries its 32-hex caller correlation as origin.execution_id", 409)
        return dict(row)

    def commit(self, db, turn, event, key, payload):
        """Inside append's transaction: the only commit of a turn."""
        origin = payload.get("origin") or {}
        usage = payload.get("usage_evidence") or {}
        execution = {"turn_id": turn["id"], "claim_id": turn["claim_id"], "attempt": turn["attempt"],
                     "coordinating_installation": turn["claimed_by"],
                     "caller_correlation": origin.get("execution_id"), "upstream_execution_id": None,
                     "usage_evidence_status": "reported" if usage.get("status") == "reported" else "none"}
        for name in ("prompt_tokens", "completion_tokens"):
            if type(usage.get(name)) is int and usage[name] >= 0 and execution["usage_evidence_status"] == "reported":
                execution[name] = usage[name]
        result = {"event_id": event["id"], "event_seq": event["seq"], "operation_key": key, "execution": execution,
                  "model": None}
        set_turn(db, turn["id"], state="committed", result=canonical(result), disposition="answered")
        clear_presence(db, turn["room"], turn["addressee"], "answering")
        record = card(db, turn["addressee"])
        if record and not record["proven_at"]:
            meeting = db.execute("SELECT kind FROM meetings WHERE room=?", (turn["room"],)).fetchone()
            if meeting and meeting["kind"] == "proof":
                db.execute("UPDATE teammates SET proven_at=?,updated=? WHERE principal=?",
                           (now(), now(), turn["addressee"]))

    def on_human_message(self, db, room, event, target=None):
        """Schedule from Robert's message (spec §3.6). Deterministic; no model."""
        meeting = db.execute("SELECT * FROM meetings WHERE room=?", (room,)).fetchone()
        if not meeting:
            return
        meeting = dict(meeting)
        addressees, notes = self.route(db, room, event["body"], target, meeting)
        for principal, display, disposition in notes:
            db.execute("INSERT OR IGNORE INTO routing_notes VALUES(?,?,?,?,?,?)",
                       (room, event["seq"], principal, display, disposition, now()))
        for principal in addressees:
            self.queue_turn(db, room, principal, event["seq"], 1)
        db.execute("UPDATE meetings SET cursor=MAX(cursor,?),updated=? WHERE room=?", (event["seq"], now(), room))

    def route(self, db, room, body, target, meeting):
        names = []
        if target:
            names.append(target)
        for match in MENTION.finditer(body or ""):
            names.append(match.group(1))
        if not names:
            names = [meeting["facilitator"]] if meeting["facilitator"] else []
        addressees, notes, seen = [], [], set()
        for raw in names:
            principal = self.resolve(db, room, raw)
            key = principal or raw.lower()
            if key in seen:
                continue
            seen.add(key)
            if principal == "robert":
                continue
            if not principal or not card(db, principal) or not hosted(db, principal):
                notes.append((key, label_of(db, principal) if principal else raw, "no_connector"))
                continue
            member = db.execute("SELECT role FROM members WHERE room=? AND actor=?", (room, principal)).fetchone()
            rsvp = rsvp_of(db, room, principal)
            if not member:
                notes.append((principal, label_of(db, principal), "not_invited"))
            elif member["role"] != "contributor" or rsvp["rsvp"] == "declined":
                notes.append((principal, label_of(db, principal), "not_joined"))
            elif rsvp["rsvp"] == "invited" and not proven_or_proof(db, room, principal):
                notes.append((principal, label_of(db, principal), "not_proven"))
            else:
                addressees.append(principal)    # accepted, or proven and about to accept: the turn waits
        return addressees, notes

    @staticmethod
    def resolve(db, room, raw):
        value = raw.strip().lower()
        for row in db.execute("SELECT id,label FROM principals").fetchall():
            labels = {row["id"].lower(), row["label"].strip().lower(), row["label"].strip().lower().replace(" ", "")}
            if value in labels or value == row["label"].split()[0].lower():
                return row["id"]
        return None

    def queue_turn(self, db, room, principal, trigger_seq, attempt):
        """At most one queued turn per (room, addressee): a newer one supersedes it."""
        db.execute("UPDATE turns SET state='superseded',disposition='newer_message',updated=? "
                   "WHERE room=? AND addressee=? AND state='queued'", (now(), room, principal))
        turn = str(uuid4())
        db.execute("""INSERT INTO turns(id,room,generation,addressee,trigger_seq,attempt,state,created,expires,updated)
            VALUES(?,?,?,?,?,?,'queued',?,?,?)""", (turn, room, generation(db, room), principal, trigger_seq,
                                                     attempt, now(), stamp(TURN_TTL_S), now()))
        return turn

    # ── owner routes ─────────────────────────────────────────────────────────
    def ensure_meeting(self, db, room, kind="meeting", facilitator=None, max_turns=DEFAULT_MAX_TURNS,
                       window_s=DEFAULT_WINDOW_S):
        if db.execute("SELECT 1 FROM meetings WHERE room=?", (room,)).fetchone():
            if facilitator:
                db.execute("UPDATE meetings SET facilitator=COALESCE(facilitator,?),updated=? WHERE room=?",
                           (facilitator, now(), room))
            return
        session = db.execute("SELECT title,purpose FROM rooms WHERE id=?", (room,)).fetchone()
        cursor = db.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE room=?", (room,)).fetchone()[0]
        brief = canonical({"title": session["title"], "purpose": session["purpose"]})
        db.execute("INSERT INTO meetings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (room, kind, brief, facilitator, None, cursor, max_turns, max_turns, window_s, stamp(window_s),
                    now(), now()))

    def invite(self, actor, key, room, payload):
        """Owner-only; the only creator of teammate-card membership (M6)."""
        self.store.owner(actor)
        target = string(payload.get("actor"), "Teammate", 60)
        role = payload.get("role", "contributor")
        if role not in {"contributor", "observer"}:
            raise Problem("Invalid teammate role")

        def action(db):
            current = self.store.access(db, actor, room, True)
            if current["state"] == "closed":
                raise Problem("Closed sessions take no new participants; continue the conversation instead", 409)
            record = card(db, target)
            if not record:
                raise Problem("Only a teammate with a card can be invited here", 404)
            db.execute("INSERT INTO members VALUES(?,?,?) ON CONFLICT(room,actor) DO UPDATE SET role=excluded.role",
                       (room, target, role))
            self.ensure_meeting(db, room, facilitator=target)
            existing = db.execute("SELECT rsvp FROM member_rsvp WHERE room=? AND actor=?", (room, target)).fetchone()
            if not existing or existing["rsvp"] != "accepted":
                state, reason = "invited", None
                if not proven_or_proof(db, room, target):
                    reason = "not_proven"
                elif record["auto_accept"] and fresh(db, "*", target, "reachable"):
                    state = "accepted"
                db.execute("""INSERT INTO member_rsvp VALUES(?,?,?,?,?) ON CONFLICT(room,actor) DO UPDATE SET
                    rsvp=excluded.rsvp,rsvp_at=excluded.rsvp_at,rsvp_reason=excluded.rsvp_reason""",
                           (room, target, state, now(), reason))    # rsvp_at = when invited, or when accepted
            event = self.store._event(db, room, actor, "membership", f"{target}: invited as {role}", target)
            return {"event": event, "rsvp": rsvp_of(db, room, target)}
        return self.store.mutate(actor, key, {"op": "invite", "room": room, "payload": payload}, action, room)

    def rsvp(self, actor, key, room, payload):
        state = payload.get("state")
        if state not in {"accepted", "declined"}:
            raise Problem("Answer accepted or declined")
        target = actor
        if actor == "robert":
            target = string(payload.get("actor"), "Teammate", 60)
            if state != "declined":
                raise Problem("The owner can only decline on a teammate's behalf", 403)

        def action(db):
            self.store.access(db, actor, room)
            row = db.execute("SELECT rsvp FROM member_rsvp WHERE room=? AND actor=?", (room, target)).fetchone()
            if not row:
                raise Problem("No invitation to answer", 404)
            if row["rsvp"] == state:
                return rsvp_of(db, room, target)
            if row["rsvp"] != "invited":
                raise Problem("This invitation was already answered", 409)
            if state == "accepted" and not proven_or_proof(db, room, target):
                db.execute("UPDATE member_rsvp SET rsvp_reason='not_proven' WHERE room=? AND actor=?", (room, target))
                raise Problem("This teammate is not yet proven; Prove first", 409)
            db.execute("UPDATE member_rsvp SET rsvp=?,rsvp_at=?,rsvp_reason=? WHERE room=? AND actor=?",
                       (state, now(), None if state == "accepted" else "owner_declined", room, target))
            self.store._event(db, room, actor, "membership", f"{target}: {state}", target)
            return rsvp_of(db, room, target)
        return self.store.mutate(actor, key, {"op": "rsvp", "room": room, "payload": payload}, action, room, write=False)

    def presence_here(self, actor, room, payload):
        if payload.get("kind") != "here":
            raise Problem("Only 'here' is reported by a browser")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.store.access(db, actor, room)
            kind = db.execute("SELECT kind FROM principals WHERE id=?", (actor,)).fetchone()
            if not kind or kind["kind"] != "human":
                raise Problem("Only a person reports 'here'", 403)
            set_presence(db, room, actor, "here", HERE_TTL_S, "browser")
        return {"kind": "here", "expires_in_s": HERE_TTL_S}

    def status(self, actor, room):
        """Owner (or a reading member) view: participants, turns, routing notes."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sweep(db)
            current = self.store.access(db, actor, room)
            meeting = db.execute("SELECT * FROM meetings WHERE room=?", (room,)).fetchone()
            members = db.execute("""SELECT p.id,p.label,p.kind,m.role FROM members m JOIN principals p ON p.id=m.actor
                WHERE m.room=? ORDER BY p.created""", (room,)).fetchall()
            participants = []
            for m in members:
                record = card(db, m["id"])
                if m["kind"] != "human" and not record:
                    continue    # the strip lists teammate cards and people only (S4)
                rsvp = rsvp_of(db, room, m["id"])
                if (rsvp["rsvp"] == "invited" and record and rsvp["rsvp_at"]
                        and rsvp["rsvp_at"] <= stamp(-record["rsvp_timeout_s"])):
                    rsvp = {**rsvp, "rsvp": "no_response"}    # derived at read time, never stored
                participants.append({"id": m["id"], "label": m["label"], "kind": m["kind"], "role": m["role"],
                                     **rsvp, "reach": self.reach(db, room, m["id"], m["kind"], record),
                                     "proven_at": record["proven_at"] if record else None,
                                     "teammate": bool(record)})
            turns = [self.public_turn(db, dict(r)) for r in db.execute(
                "SELECT * FROM turns WHERE room=? ORDER BY created DESC LIMIT 20", (room,)).fetchall()]
            notes = [dict(r) for r in db.execute(
                "SELECT * FROM routing_notes WHERE room=? ORDER BY trigger_seq DESC LIMIT 20", (room,)).fetchall()]
            return {"room": room, "state": current["state"], "generation": generation(db, room),
                    "meeting": self.public_meeting(dict(meeting)) if meeting else None,
                    "participants": participants, "turns": turns, "routing_notes": notes}

    @staticmethod
    def public_meeting(m):
        return {k: m[k] for k in ("kind", "facilitator", "remaining", "max_turns", "window_expires", "cursor")}

    def public_turn(self, db, t):
        result = json.loads(t["result"]) if t["result"] else None
        out = {k: t[k] for k in ("id", "addressee", "trigger_seq", "attempt", "state", "created", "expires",
                                 "disposition", "stop_ack")}
        out["event_id"] = result["event_id"] if result else None
        out["answered_earlier"] = False
        if result:
            later = db.execute("""SELECT 1 FROM events WHERE room=? AND actor='robert' AND kind='message'
                AND origin IS NULL AND seq>? AND seq<?""", (t["room"], t["trigger_seq"], result["event_seq"])).fetchone()
            out["answered_earlier"] = bool(later)
        return out

    def reach(self, db, room, principal, kind, record):
        if kind == "human":
            return {"state": "here" if fresh(db, room, principal, "here") else "away", "reason": None}
        if fresh(db, room, principal, "answering"):
            return {"state": "answering", "reason": None}
        if fresh(db, "*", principal, "reachable"):
            return {"state": "reachable", "reason": None}
        if not record or not hosted(db, principal):
            return {"state": "away", "reason": "no connector in Rooms yet"}
        if not record["proven_at"]:
            return {"state": "away", "reason": "not yet proven — Prove first"}
        failed = db.execute("""SELECT disposition FROM turns WHERE addressee=? AND state IN ('failed','uncertain')
            ORDER BY updated DESC LIMIT 1""", (principal,)).fetchone()
        last_commit = db.execute("SELECT MAX(updated) FROM turns WHERE addressee=? AND state='committed'",
                                 (principal,)).fetchone()[0]
        if failed and record["last_failure_at"] and (not last_commit or record["last_failure_at"] > last_commit):
            busy = failed["disposition"] == "relay_busy"
            return {"state": "away", "reason": ("last reply failed (relay busy) — Retry or Prove again" if busy
                                                else "last reply failed — Retry or Prove again")}
        return {"state": "away", "reason": "its worker has not checked in recently"}

    def owner_cancel(self, actor, key, room, turn_id):
        self.store.owner(actor)

        def action(db):
            self.store.access(db, actor, room)
            sweep(db)
            t = turn_row(db, turn_id)
            if t["room"] != room:
                raise Problem("Turn not found", 404)
            if t["state"] == "queued":
                set_turn(db, turn_id, state="cancelled", disposition="continued_without")
            elif t["state"] in ("claimed", "running", "recovering"):
                set_turn(db, turn_id, state="cancel_requested", disposition="continued_without")
            return self.public_turn(db, turn_row(db, turn_id))
        return self.store.mutate(actor, key, {"op": "turn_cancel", "room": room, "turn": turn_id}, action, room)

    def owner_retry(self, actor, key, room, turn_id, payload):
        self.store.owner(actor)
        if payload.get("confirm") is not True:
            raise Problem("Confirm the new attempt: it may use a second turn")

        def action(db):
            current = self.store.access(db, actor, room, True)
            sweep(db)
            t = turn_row(db, turn_id)
            if t["room"] != room:
                raise Problem("Turn not found", 404)
            if t["state"] not in ("failed", "cancelled", "expired", "uncertain"):
                raise Problem("Only a failed, cancelled, expired or unresolved turn can be tried again", 409)
            if current["state"] != "active":
                raise Problem("The meeting is not active", 409)
            if t["state"] == "uncertain":
                set_turn(db, turn_id, state="abandoned", disposition="replaced_by_new_attempt")
            top = db.execute("SELECT MAX(attempt) FROM turns WHERE room=? AND addressee=? AND trigger_seq=?",
                             (room, t["addressee"], t["trigger_seq"])).fetchone()[0]
            new = self.queue_turn(db, room, t["addressee"], t["trigger_seq"], top + 1)
            return self.public_turn(db, turn_row(db, new))
        return self.store.mutate(actor, key, {"op": "turn_retry", "room": room, "turn": turn_id}, action, room)

    def stop(self, actor, key, room):
        """Stop all replies: pause the session and fence every unfinished turn."""
        self.store.owner(actor)

        def action(db):
            current = self.store.access(db, actor, room, True)
            if current["state"] != "active":
                raise Problem("Only an active meeting can be stopped", 409)
            db.execute("UPDATE rooms SET state='paused',version=version+1 WHERE id=?", (room,))
            self.on_state(db, room, "paused", "stopped_by_owner")
            event = self.store._event(db, room, actor, "state_change", "active → paused. Stop: all replies stopped.")
            return {"state": "paused", "version": current["version"] + 1, "event": event}
        return self.store.mutate(actor, key, {"op": "stop", "room": room}, action, room)

    def continue_conversation(self, actor, key, room):
        """A closed session continues as a new session under the same room."""
        self.store.owner(actor)

        def action(db):
            current = self.store.access(db, actor, room)
            if current["state"] != "closed":
                raise Problem("Only a closed conversation is continued; this one is still open", 409)
            closing = db.execute("""SELECT id FROM events WHERE room=? AND kind='state_change'
                ORDER BY seq DESC LIMIT 1""", (room,)).fetchone()
            session_id, timestamp = str(uuid4()), now()
            title = current["title"] if current["title"].endswith("(continued)") else current["title"] + " (continued)"
            db.execute("INSERT INTO rooms VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (session_id, title[:160], current["purpose"], current["mode"], "active", 1, "robert",
                        timestamp, timestamp, current["parent_room_id"]))
            db.execute("INSERT INTO members VALUES(?,?,?)", (session_id, actor, "contributor"))
            self.store._event(db, session_id, actor, "session_opened",
                              f"Recorded working session opened, continuing session {room}"
                              + (f" from checkpoint {closing['id']}" if closing else "") + ". " + current["purpose"])
            generation(db, session_id)
            old = db.execute("SELECT * FROM meetings WHERE room=?", (room,)).fetchone()
            if old and old["kind"] == "meeting":
                self.ensure_meeting(db, session_id, facilitator=old["facilitator"])
                for m in db.execute("""SELECT m.actor,m.role FROM members m JOIN teammates t ON t.principal=m.actor
                        WHERE m.room=?""", (room,)).fetchall():
                    db.execute("INSERT INTO members VALUES(?,?,?)", (session_id, m["actor"], m["role"]))
                    state = "invited"
                    reason = None if proven_or_proof(db, session_id, m["actor"]) else "not_proven"
                    db.execute("INSERT INTO member_rsvp VALUES(?,?,?,?,?)", (session_id, m["actor"], state, now(), reason))
                    self.store._event(db, session_id, actor, "membership", f"{m['actor']}: invited as {m['role']}",
                                      m["actor"])
            db.execute("UPDATE persistent_rooms SET updated=? WHERE id=?", (timestamp, current["parent_room_id"]))
            return {"session_id": session_id, "continues": room, "checkpoint_ref": closing["id"] if closing else None}
        return self.store.mutate(actor, key, {"op": "continue", "room": room}, action, room)

    def teammates(self, actor):
        self.store.owner(actor)
        with self.store.connect() as db:
            out = []
            for row in db.execute("SELECT t.*,p.label FROM teammates t JOIN principals p ON p.id=t.principal "
                                  "ORDER BY t.created").fetchall():
                record = dict(row)
                record["connected"] = hosted(db, record["principal"])
                record["reachable"] = fresh(db, "*", record["principal"], "reachable")
                record["auto_accept"] = bool(record["auto_accept"])
                out.append(record)
            return out

    def put_teammate(self, actor, principal, payload):
        self.store.owner(actor)
        allowed = {"host", "connector", "tools_profile", "max_turn_s", "max_turns_per_meeting", "max_concurrency",
                   "billing_route", "auto_accept", "rsvp_timeout_s"}
        if set(payload) - allowed:
            raise Problem("Unknown teammate card fields")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            kind = db.execute("SELECT kind FROM principals WHERE id=?", (principal,)).fetchone()
            if not kind or kind["kind"] != "agent":
                raise Problem("A teammate card belongs to an agent participant", 404)
            current = card(db, principal) or {"host": "staging", "connector": "rooms-worker",
                "tools_profile": "mc-session-status-only", "max_turn_s": 120, "max_turns_per_meeting": DEFAULT_MAX_TURNS,
                "max_concurrency": 1, "billing_route": "mc capped gateway key (staging)", "auto_accept": 1,
                "rsvp_timeout_s": 120, "proven_at": None, "last_failure_at": None, "created": now()}
            for name, value in payload.items():
                if name in {"max_turn_s", "max_turns_per_meeting", "max_concurrency", "rsvp_timeout_s"}:
                    if type(value) is not int or not 1 <= value <= 3600:
                        raise Problem(f"{name} must be a whole number from 1 to 3600")
                elif name == "auto_accept":
                    value = int(bool(value))
                else:
                    value = string(value, name, 200)
                current[name] = value
            db.execute("""INSERT INTO teammates VALUES(:principal,:host,:connector,:tools_profile,:max_turn_s,
                :max_turns_per_meeting,:max_concurrency,:billing_route,:auto_accept,:rsvp_timeout_s,:proven_at,
                :last_failure_at,:created,:updated) ON CONFLICT(principal) DO UPDATE SET host=excluded.host,
                connector=excluded.connector,tools_profile=excluded.tools_profile,max_turn_s=excluded.max_turn_s,
                max_turns_per_meeting=excluded.max_turns_per_meeting,max_concurrency=excluded.max_concurrency,
                billing_route=excluded.billing_route,auto_accept=excluded.auto_accept,
                rsvp_timeout_s=excluded.rsvp_timeout_s,updated=excluded.updated""",
                       {**current, "principal": principal, "updated": now()})
            return card(db, principal)

    def prove(self, actor, key, principal, payload):
        """Owner-only, idempotent: one owner-approved proof turn (spec §3.9, M1)."""
        self.store.owner(actor)
        if payload.get("confirm") is not True:
            raise Problem("Confirm the proof: it uses one paid turn")

        def action(db):
            record = card(db, principal)
            if not record:
                raise Problem("No teammate card for that participant", 404)
            if not hosted(db, principal):
                raise Problem("No connector hosts this teammate yet", 409)
            label = label_of(db, principal)
            room, timestamp = str(uuid4()), now()
            title = f"Proof: {label} {timestamp[:10]}"
            purpose = f"One owner-approved proof turn for {label}: a single question, a single reply."
            db.execute("INSERT INTO persistent_rooms VALUES(?,?,?,?,?)", (room, title, purpose, timestamp, timestamp))
            db.execute("INSERT INTO rooms VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (room, title, purpose, "meeting", "active", 1, "robert", timestamp, timestamp, room))
            db.execute("INSERT INTO members VALUES(?,?,?)", (room, actor, "contributor"))
            self.store._event(db, room, actor, "session_opened", "Recorded working session opened. " + purpose)
            generation(db, room)
            self.ensure_meeting(db, room, kind="proof", facilitator=principal, max_turns=1, window_s=PROOF_WINDOW_S)
            db.execute("INSERT INTO members VALUES(?,?,?)", (room, principal, "contributor"))
            db.execute("INSERT INTO member_rsvp VALUES(?,?,?,?,?)", (room, principal, "invited", now(), None))
            self.store._event(db, room, actor, "membership", f"{principal}: invited as contributor (proof)", principal)
            event = self.store._event(db, room, actor, "message", PROOF_QUESTION)
            self.on_human_message(db, room, event)
            return {"proof_room": room, "principal": principal, "state": "queued"}
        return self.store.mutate(actor, key, {"op": "prove", "principal": principal}, action)

    def proof_status(self, actor, principal):
        self.store.owner(actor)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sweep(db)
            record = card(db, principal)
            if not record:
                raise Problem("No teammate card for that participant", 404)
            row = db.execute("""SELECT m.room FROM meetings m WHERE m.kind='proof' AND m.facilitator=?
                ORDER BY m.created DESC LIMIT 1""", (principal,)).fetchone()
            turn = None
            if row:
                t = db.execute("SELECT * FROM turns WHERE room=? ORDER BY created DESC LIMIT 1", (row["room"],)).fetchone()
                turn = self.public_turn(db, dict(t)) if t else None
            return {"principal": principal, "proven_at": record["proven_at"],
                    "proof_room": row["room"] if row else None, "turn": turn}

    # ── worker routes (work-scoped credential; spec §3.4) ────────────────────
    @staticmethod
    def require_work(db, auth):
        scope = scope_of(db, auth.get("credential_id"))
        if not scope or scope["scope"] != "work":
            raise Problem("This route needs a work-scoped worker credential", 403)
        return [r["principal"] for r in db.execute(
            "SELECT principal FROM hosted_teammates WHERE installation_id=?", (auth["installation_id"],)).fetchall()]

    def bound_turn(self, db, auth, turn_id, bound, claim_id=None, require_lease=True):
        t = turn_row(db, turn_id)
        if t["addressee"] not in bound or t["claimed_by"] not in (None, auth["installation_id"]):
            raise Problem("Turn not found", 404)
        if claim_id is not None and t["claim_id"] != claim_id:
            raise Problem("That claim was replaced or never existed (stale_claim)", 409)
        if require_lease and t["state"] not in TERMINAL and t["state"] != "uncertain" and (
                not t["lease_until"] or t["lease_until"] <= now()):
            raise Problem("The lease expired (stale_claim)", 409)
        return t

    def admission(self, db, t):
        """Dispatch admission D (spec §3.4). The failing check's name, or None."""
        room = db.execute("SELECT state FROM rooms WHERE id=?", (t["room"],)).fetchone()
        meeting = db.execute("SELECT * FROM meetings WHERE room=?", (t["room"],)).fetchone()
        if not room or room["state"] != "active":
            return "meeting_not_active"
        if t["generation"] != generation(db, t["room"]):
            return "stale_generation"
        if not accepted_member(db, t["room"], t["addressee"]):
            return "membership_ended"
        if not membership_credential_valid(db, t["addressee"]):
            return "credential_invalid"
        if t["expires"] <= now():
            return "turn_expired"
        if not meeting or meeting["window_expires"] <= now():
            return "window_expired"
        if t["budget_reserved"] != 1:
            return "no_reservation"
        if not proven_or_proof(db, t["room"], t["addressee"]):
            return "not_proven"
        return None

    def hosted_view(self, auth):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sweep(db)
            bound = self.require_work(db, auth)
            invites, uncertain = [], []
            for principal in bound:
                for r in db.execute("""SELECT room FROM member_rsvp WHERE actor=? AND rsvp='invited'""", (principal,)):
                    invites.append({"room": r["room"], "principal": principal,
                                    "proven": proven_or_proof(db, r["room"], principal)})
                for r in db.execute("""SELECT id,room,claim_id FROM turns WHERE addressee=? AND state='uncertain'
                        AND claimed_by=?""", (principal, auth["installation_id"])):
                    uncertain.append({"turn_id": r["id"], "room": r["room"], "prior_claim_id": r["claim_id"],
                                      "principal": principal})
            return {"teammates": bound, "pending_invites": invites, "uncertain_turns": uncertain}

    def mark_reachable(self, auth, principal, payload):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sweep(db)
            bound = self.require_work(db, auth)
            if principal not in bound:
                raise Problem("Teammate not hosted by this worker", 404)
            try:
                checked = datetime.fromisoformat(payload.get("readyz_at", ""))
                if checked.tzinfo is None:
                    raise ValueError
            except (TypeError, ValueError):
                raise Problem("readyz_at must be a timezone-aware time")
            record = card(db, principal)
            age = (datetime.now(timezone.utc) - checked).total_seconds()
            failed_after_commit = db.execute("""SELECT 1 FROM turns WHERE addressee=? AND state IN ('failed','uncertain')
                AND updated > COALESCE((SELECT MAX(updated) FROM turns WHERE addressee=? AND state='committed'),'')""",
                                             (principal, principal)).fetchone()
            reasons = []
            if not record or not record["proven_at"]:
                reasons.append("not_proven")
            if not 0 <= age <= READYZ_FRESH_S:
                reasons.append("readyz_stale")
            if failed_after_commit:
                reasons.append("failure_since_last_commit")
            if reasons:
                clear_presence(db, "*", principal, "reachable")
                return {"reachable": False, "reasons": reasons}
            set_presence(db, "*", principal, "reachable", REACHABLE_TTL_S, "worker_readyz")
            return {"reachable": True, "expires_in_s": REACHABLE_TTL_S}

    def claim(self, auth, payload):
        wanted = payload.get("addressees")
        if not isinstance(wanted, list) or not all(isinstance(x, str) for x in wanted):
            raise Problem("Name the addressees to claim for")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sweep(db)
            bound = self.require_work(db, auth)
            names = [x for x in wanted if x in bound]
            if not names:
                return {"turn": None}
            marks = ",".join("?" * len(names))
            for row in db.execute(f"SELECT * FROM turns WHERE state='queued' AND addressee IN ({marks}) "
                                  "ORDER BY created", names).fetchall():
                t = dict(row)
                if db.execute(f"SELECT 1 FROM turns WHERE room=? AND addressee=? AND state IN "
                              f"({','.join('?' * len(ACTIVE))})", (t["room"], t["addressee"], *ACTIVE)).fetchone():
                    continue    # the slot is busy; this one waits
                if rsvp_of(db, t["room"], t["addressee"])["rsvp"] == "invited" and \
                        proven_or_proof(db, t["room"], t["addressee"]):
                    continue    # acceptance pending on the worker's next pass
                reason = self.claim_refusal(db, t)
                if reason:
                    set_turn(db, t["id"], state="cancelled", disposition=reason)
                    continue
                claim_id = str(uuid4())
                db.execute("UPDATE meetings SET remaining=remaining-1,updated=? WHERE room=?", (now(), t["room"]))
                set_turn(db, t["id"], state="claimed", claimed_by=auth["installation_id"], claim_id=claim_id,
                         lease_until=stamp(LEASE_S), budget_reserved=1, snapshot_through_seq=t["trigger_seq"] - 1)
                return {"turn": self.work_view(db, turn_row(db, t["id"]), with_snapshot=True)}
            return {"turn": None}

    def claim_refusal(self, db, t):
        room = db.execute("SELECT state FROM rooms WHERE id=?", (t["room"],)).fetchone()
        if not room or room["state"] != "active":
            return "meeting_not_active"
        if t["generation"] != generation(db, t["room"]):
            return "stale_generation"
        if t["expires"] <= now():
            return "turn_expired"
        meeting = dict(db.execute("SELECT * FROM meetings WHERE room=?", (t["room"],)).fetchone())
        if meeting["window_expires"] <= now():
            if meeting["kind"] == "proof":
                return "window_expired"
            # A rolling hour: the next claim after the window starts a new one (spec §3.6 budget).
            db.execute("UPDATE meetings SET remaining=max_turns,window_expires=?,updated=? WHERE room=?",
                       (stamp(meeting["window_s"]), now(), t["room"]))
            meeting = dict(db.execute("SELECT * FROM meetings WHERE room=?", (t["room"],)).fetchone())
        if meeting["remaining"] <= 0:
            return "budget_exhausted"
        if not accepted_member(db, t["room"], t["addressee"]):
            return "membership_ended"
        if not membership_credential_valid(db, t["addressee"]):
            return "credential_invalid"
        if not proven_or_proof(db, t["room"], t["addressee"]):
            return "not_proven"
        return None

    def work_view(self, db, t, with_snapshot=False):
        out = {k: t[k] for k in ("id", "room", "addressee", "trigger_seq", "attempt", "state", "claim_id",
                                 "lease_until", "generation", "expires")}
        out["response_key"] = response_key(t["addressee"], t["id"])
        if with_snapshot:
            out.update(self.snapshot(db, t))
        return out

    def snapshot(self, db, t):
        """Bounded, attributed context at claim: through trigger_seq-1; the trigger once (spec §3.6)."""
        accepted = [r["actor"] for r in db.execute("SELECT actor FROM members WHERE room=?", (t["room"],))
                    if rsvp_of(db, t["room"], r["actor"])["rsvp"] == "accepted"]
        marks = ",".join("?" * len(accepted)) or "''"
        kinds = ",".join("?" * len(SNAPSHOT_KINDS))
        rows = db.execute(f"""SELECT e.seq,e.id,e.actor,e.kind,e.body,e.created,p.label FROM events e
            JOIN principals p ON p.id=e.actor WHERE e.room=? AND e.seq<? AND e.actor IN ({marks})
            AND e.kind IN ({kinds}) ORDER BY e.seq DESC""",
                          (t["room"], t["trigger_seq"], *accepted, *SNAPSHOT_KINDS)).fetchall()
        kept = list(reversed(rows[:SNAPSHOT_LIMIT]))
        trigger = db.execute("SELECT body FROM events WHERE room=? AND seq=?", (t["room"], t["trigger_seq"])).fetchone()
        session = db.execute("SELECT title,purpose FROM rooms WHERE id=?", (t["room"],)).fetchone()
        meeting = db.execute("SELECT * FROM meetings WHERE room=?", (t["room"],)).fetchone()
        participants = [{"id": r["id"], "label": r["label"], "kind": r["kind"]} for r in db.execute(
            """SELECT p.id,p.label,p.kind FROM members m JOIN principals p ON p.id=m.actor WHERE m.room=?""",
            (t["room"],)) if r["id"] in accepted]
        return {"brief": {"title": session["title"], "purpose": session["purpose"],
                          "facilitator": meeting["facilitator"] if meeting else None,
                          "facilitator_label": label_of(db, meeting["facilitator"]) if meeting and meeting["facilitator"] else None,
                          "language": (meeting["language"] if meeting else None) or "the language Robert writes in",
                          "participants": participants, "kind": meeting["kind"] if meeting else "meeting"},
                "transcript": [{"seq": r["seq"], "record_id": r["id"], "speaker_id": r["actor"],
                                "speaker": r["label"], "kind": r["kind"],
                                "text": r["body"] if r["kind"] != "document" else "(shared a file: " + r["body"] + ")",
                                "created": r["created"]} for r in kept],
                "coverage": {"through_seq": t["trigger_seq"] - 1, "included": len(kept),
                             "omitted": max(0, len(rows) - len(kept))},
                "trigger": {"seq": t["trigger_seq"], "text": trigger["body"] if trigger else ""}}

    def start(self, auth, turn_id, payload):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sweep(db)
            bound = self.require_work(db, auth)
            t = self.bound_turn(db, auth, turn_id, bound, payload.get("claim_id"))
            if t["state"] != "claimed":
                return {"state": t["state"], "dispatch": False}
            failing = self.admission(db, t)
            if failing:
                # Nothing was ever sent for this turn: it ends here (the slot stays consumed, M4).
                set_turn(db, turn_id, state="cancelled", disposition=failing)
                return {"state": "cancelled", "dispatch": False, "reason": failing}
            set_turn(db, turn_id, state="running", lease_until=stamp(LEASE_S))
            set_presence(db, t["room"], t["addressee"], "answering", ANSWERING_TTL_S, "turn_start")
            return {"state": "running", "dispatch": True, "generation": t["generation"]}

    def heartbeat(self, auth, turn_id, payload):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sweep(db)
            bound = self.require_work(db, auth)
            t = self.bound_turn(db, auth, turn_id, bound, payload.get("claim_id"), require_lease=False)
            if t["state"] in TERMINAL or t["state"] == "uncertain":
                return {"state": t["state"], "dispatch": False}
            if not t["lease_until"] or t["lease_until"] <= now():
                return {"state": t["state"], "dispatch": False}
            set_turn(db, turn_id, lease_until=stamp(LEASE_S))
            if t["state"] in ("running", "recovering"):
                set_presence(db, t["room"], t["addressee"], "answering", ANSWERING_TTL_S, "heartbeat")
            out = {"state": t["state"], "generation": generation(db, t["room"]), "dispatch": False}
            if payload.get("intent") == "dispatch" and t["state"] == "running":
                failing = self.admission(db, t)
                if failing:
                    set_turn(db, turn_id, state="cancel_requested", disposition=failing)
                    return {"state": "cancel_requested", "dispatch": False, "reason": failing}
                out["dispatch"] = True
            return out

    def fail(self, auth, turn_id, payload):
        outcome = payload.get("outcome")
        if outcome not in {"failed", "uncertain"}:
            raise Problem("Report failed or uncertain")
        reason = payload.get("reason") or outcome
        if not isinstance(reason, str) or not re.fullmatch(r"[a-z_]{1,40}", reason):
            raise Problem("A short reason code is required")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sweep(db)
            bound = self.require_work(db, auth)
            t = self.bound_turn(db, auth, turn_id, bound, payload.get("claim_id"), require_lease=False)
            if t["state"] == "committed":
                raise Problem("This turn already committed its reply", 409)
            if t["state"] == "cancel_requested":
                set_turn(db, turn_id, state="cancelled", stop_ack="worker",
                         disposition=(t["disposition"] or "cancelled"))
            elif t["state"] in ("claimed", "running"):
                set_turn(db, turn_id, state=outcome, disposition=reason)
                db.execute("UPDATE teammates SET last_failure_at=?,updated=? WHERE principal=?",
                           (now(), now(), t["addressee"]))
                clear_presence(db, "*", t["addressee"], "reachable")
            elif t["state"] == "recovering":
                set_turn(db, turn_id, state="uncertain", disposition=reason)
            clear_presence(db, t["room"], t["addressee"], "answering")
            return {"state": turn_row(db, turn_id)["state"]}

    def cancel_ack(self, auth, turn_id, payload):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sweep(db)
            bound = self.require_work(db, auth)
            t = self.bound_turn(db, auth, turn_id, bound, payload.get("claim_id"), require_lease=False)
            if t["state"] == "cancel_requested":
                late = bool(payload.get("late_output"))
                set_turn(db, turn_id, state="cancelled", stop_ack="worker",
                         disposition=(t["disposition"] or "cancelled") + (":late_output_discarded" if late else ""))
                clear_presence(db, t["room"], t["addressee"], "answering")
            return {"state": turn_row(db, turn_id)["state"]}

    def recover(self, auth, turn_id, payload):
        prior = payload.get("prior_claim_id")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sweep(db)
            bound = self.require_work(db, auth)
            t = self.bound_turn(db, auth, turn_id, bound, require_lease=False)
            if t["state"] != "uncertain":
                return {"state": t["state"]}
            if not prior or prior != t["claim_id"]:
                raise Problem("That was not this turn's last claim (stale_claim)", 409)
            receipt = db.execute("SELECT response FROM operations WHERE actor=? AND key=?",
                                 (t["addressee"], response_key(t["addressee"], t["id"]))).fetchone()
            if receipt:
                event_id = json.loads(receipt["response"])["result"]["id"]
                seq = db.execute("SELECT seq FROM events WHERE id=?", (event_id,)).fetchone()["seq"]
                set_turn(db, turn_id, state="committed", disposition="answered",
                         result=canonical({"event_id": event_id, "event_seq": seq,
                                           "operation_key": response_key(t["addressee"], t["id"]),
                                           "execution": None, "model": None}))
                return {"state": "committed"}
            if t["generation"] != generation(db, t["room"]):
                set_turn(db, turn_id, state="cancelled", disposition="stale_generation:late_output_retained")
                return {"state": "cancelled", "reason": "stale_generation"}
            if not accepted_member(db, t["room"], t["addressee"]) or not membership_credential_valid(db, t["addressee"]):
                set_turn(db, turn_id, state="cancelled", disposition="membership_ended:late_output_retained")
                return {"state": "cancelled", "reason": "membership_ended"}
            if db.execute(f"SELECT 1 FROM turns WHERE room=? AND addressee=? AND id<>? AND state IN "
                          f"({','.join('?' * len(ACTIVE))})", (t["room"], t["addressee"], t["id"], *ACTIVE)).fetchone():
                return {"state": "uncertain", "wait": True}
            claim_id = str(uuid4())
            set_turn(db, turn_id, state="recovering", claim_id=claim_id, lease_until=stamp(LEASE_S))
            return {"state": "recovering", "turn": self.work_view(db, turn_row(db, turn_id))}
