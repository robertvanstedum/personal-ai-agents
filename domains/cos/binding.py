"""CoS per-turn tool binding (F2 design v0.3, D7): which live turn, if any, owns a tool call.

cos-scheduler records a binding when it hands a turn to the agent (``record``), attaches the OpenClaw run id from the
opening stream chunk (``attach_run``), and revokes it on Stop, supersession, turn end, a runtime replacement or a
scheduler restart. The bound listener resolves every call from the host-sealed run and tool-call ids (``resolve``),
never from model arguments, then admits it (``admit``) under the per-turn quota. A delayed call from an earlier run
finds that run's revoked binding and is refused, even while a later turn of the same conversation is live.

Storage is a writer-private SQLite file (0600, directory 0700) that only the single cos-scheduler process opens; no
path that holds it may be mounted into an agent container. Every change runs in ``BEGIN IMMEDIATE``, so revoke versus
admit, the quota count, run-id uniqueness and instance retirement are each atomic. No write transaction is held while
waiting for a run to be attached.

Robert's Q1 decision (v0.3 §4): owner turns keep OpenClaw's native write and exec. ``log_native`` records each such
call against its binding (or as unbound) and never blocks it. That record is observation, not runtime attestation.

This is the MC pattern (``core/mc_tools/binding.py``) reshaped for CoS: no Postgres ledger, so the turn's live state
lives here, and meeting turns carry the Records claim and generations that the step 4 monitor re-checks.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from domains.cos.store.actions_v2 import BoundCall

SCOPES = ("owner", "meeting")
ORIGINS = ("owner", "service:mc", "service:telegram", "meeting")
RUN_ID = re.compile(r"chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TOOL_CALL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
INSTANCE_ID = re.compile(r"[0-9a-f]{32}")
AGENT_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
TURN_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
RUN_WAIT_S = 2.0
_POLL_S = 0.05

_SCHEMA = """
CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS instances(instance_id TEXT PRIMARY KEY, registered REAL NOT NULL, retired REAL);
CREATE TABLE IF NOT EXISTS bindings(
  turn_id TEXT PRIMARY KEY, environment TEXT NOT NULL, principal TEXT NOT NULL, origin TEXT NOT NULL,
  seat TEXT NOT NULL, actor TEXT NOT NULL, agent_entry TEXT NOT NULL, session_user TEXT NOT NULL,
  scope TEXT NOT NULL, audience TEXT NOT NULL, owner_request_id TEXT, supersession_key TEXT,
  claim_id TEXT, room_generation INTEGER, trigger_seq INTEGER, approval_generation INTEGER,
  runtime_instance TEXT NOT NULL, epoch TEXT NOT NULL, run_id TEXT UNIQUE, expires REAL NOT NULL,
  created REAL NOT NULL, revoked REAL, revoked_reason TEXT);
CREATE INDEX IF NOT EXISTS bindings_live ON bindings(supersession_key) WHERE revoked IS NULL;
CREATE TABLE IF NOT EXISTS stops(turn_id TEXT PRIMARY KEY, at REAL NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calls(
  turn_id TEXT NOT NULL, tool_call_id TEXT NOT NULL, tool TEXT NOT NULL, at TEXT NOT NULL, seq INTEGER NOT NULL,
  command_id TEXT, receipt_id TEXT, PRIMARY KEY(turn_id, tool_call_id));
CREATE TABLE IF NOT EXISTS native_calls(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, turn_id TEXT, run_id TEXT, tool_call_id TEXT,
  tool TEXT NOT NULL, target TEXT, bound INTEGER NOT NULL);
"""


class NotBound(Exception):
    """No live turn owns this call. ``code`` is fixed vocabulary, never request contents."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Binding:
    turn_id: str
    environment: str
    principal: str
    origin: str
    scope: str
    audience: str
    owner_request_id: str | None
    run_id: str
    agent_entry: str
    claim_id: str | None = None
    room_generation: int | None = None
    trigger_seq: int | None = None
    approval_generation: int | None = None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="microseconds")


class BindingStore:
    """One per cos-scheduler process. Thread-safe: one SQLite connection per thread."""

    def __init__(self, path, environment: str, *, clock=time.time):
        if environment not in ("dev", "prod"):
            raise ValueError("environment must be dev or prod")
        self.path = Path(path)
        self.environment = environment
        self._clock = clock
        self._local = threading.local()
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        self._db().executescript(_SCHEMA)

    # ── plumbing ─────────────────────────────────────────────────────────────
    def _db(self) -> sqlite3.Connection:
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=True)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            self._local.db = db
        return db

    class _Tx:
        def __init__(self, db):
            self.db = db

        def __enter__(self):
            self.db.execute("BEGIN IMMEDIATE")
            return self.db

        def __exit__(self, kind, *_):
            self.db.execute("ROLLBACK" if kind else "COMMIT")
            return False

    def _tx(self):
        return self._Tx(self._db())

    @staticmethod
    def _state(db, key):
        row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    # ── process lifecycle ────────────────────────────────────────────────────
    def start_epoch(self, epoch: str) -> None:
        """This scheduler process's epoch becomes current; every binding from another epoch is revoked."""
        if not isinstance(epoch, str) or not epoch:
            raise ValueError("epoch required")
        with self._tx() as db:
            db.execute("INSERT INTO state VALUES('epoch',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (epoch,))
            db.execute("UPDATE bindings SET revoked=?, revoked_reason='scheduler_restarted' "
                       "WHERE revoked IS NULL AND epoch<>?", (self._clock(), epoch))

    def register_instance(self, instance_id: str) -> bool:
        """The plugin's gateway process. A new one becomes current and retires the old for good, revoking every
        binding on another instance; a retired instance is refused (no last-arrival-wins)."""
        if not isinstance(instance_id, str) or not INSTANCE_ID.fullmatch(instance_id):
            raise NotBound("instance_invalid")
        with self._tx() as db:
            current = self._state(db, "instance")
            if current == instance_id:
                return False
            seen = db.execute("SELECT 1 FROM instances WHERE instance_id=?", (instance_id,)).fetchone()
            if seen is not None:
                raise NotBound("instance_retired")
            now = self._clock()
            if current is not None:
                db.execute("UPDATE instances SET retired=? WHERE instance_id=?", (now, current))
            db.execute("INSERT INTO instances VALUES(?,?,NULL)", (instance_id, now))
            db.execute("INSERT INTO state VALUES('instance',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (instance_id,))
            db.execute("UPDATE bindings SET revoked=?, revoked_reason='runtime_replaced' "
                       "WHERE revoked IS NULL AND runtime_instance<>?", (now, instance_id))
            return True

    # ── turn lifecycle ───────────────────────────────────────────────────────
    def record(self, turn_id: str, *, principal: str, origin: str, scope: str, audience: str, agent_entry: str,
               session_user: str, lifetime_s: float, owner_request_id: str | None = None,
               supersession_key: str | None = None, claim_id: str | None = None,
               room_generation: int | None = None, trigger_seq: int | None = None,
               approval_generation: int | None = None) -> bool:
        """The binding for a turn being handed to the agent. True when it is live; False when there is no current
        runtime instance or epoch (tools stay refused), or a Stop already reached the turn. A newer binding with the
        same supersession key revokes the older ones (reason 'superseded')."""
        if (not isinstance(turn_id, str) or not TURN_ID.fullmatch(turn_id) or origin not in ORIGINS
                or scope not in SCOPES or not isinstance(agent_entry, str) or not AGENT_ID.fullmatch(agent_entry)
                or not isinstance(principal, str) or not principal or not isinstance(audience, str) or not audience
                or not isinstance(session_user, str) or not session_user or not 0 < lifetime_s <= 3600):
            raise NotBound("turn_invalid")
        if (scope == "meeting") != (origin == "meeting"):
            raise NotBound("turn_invalid")
        if scope == "meeting" and (not claim_id or type(room_generation) is not int):
            raise NotBound("turn_invalid")
        with self._tx() as db:
            instance, epoch = self._state(db, "instance"), self._state(db, "epoch")
            if instance is None or epoch is None:
                return False
            now = self._clock()
            existing = db.execute("SELECT * FROM bindings WHERE turn_id=?", (turn_id,)).fetchone()
            if existing is not None:
                return existing["revoked"] is None and existing["session_user"] == session_user
            if supersession_key:
                db.execute("UPDATE bindings SET revoked=?, revoked_reason='superseded' "
                           "WHERE revoked IS NULL AND supersession_key=? AND environment=?",
                           (now, supersession_key, self.environment))
            stop = db.execute("SELECT reason FROM stops WHERE turn_id=?", (turn_id,)).fetchone()
            db.execute("""INSERT INTO bindings(turn_id, environment, principal, origin, seat, actor, agent_entry,
                session_user, scope, audience, owner_request_id, supersession_key, claim_id, room_generation,
                trigger_seq, approval_generation, runtime_instance, epoch, run_id, expires, created, revoked,
                revoked_reason) VALUES(?,?,?,?,'cos','cos',?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?)""",
                       (turn_id, self.environment, principal, origin, agent_entry, session_user, scope, audience,
                        owner_request_id, supersession_key, claim_id, room_generation, trigger_seq,
                        approval_generation, instance, epoch, now + lifetime_s, now,
                        now if stop else None, stop["reason"] if stop else None))
            return stop is None

    def attach_run(self, turn_id: str, run_id: str) -> bool:
        """The run the adapter opened for this turn, from its opening chunk. Attached once, never moved, never shared.
        A revoked turn still gets its run attached, so a late call is told 'stopped' rather than 'not bound'."""
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise NotBound("run_invalid")
        with self._tx() as db:
            row = db.execute("SELECT run_id FROM bindings WHERE turn_id=?", (turn_id,)).fetchone()
            if row is None:
                return False
            if row["run_id"] is not None:
                return row["run_id"] == run_id
            if db.execute("SELECT 1 FROM bindings WHERE run_id=?", (run_id,)).fetchone():
                return False
            db.execute("UPDATE bindings SET run_id=? WHERE turn_id=?", (run_id, turn_id))
            return True

    def revoke(self, turn_id: str, reason: str = "owner_stop") -> bool:
        """Stop (or any revocation). Recorded against the turn first, so a binding written later starts revoked.
        True when a binding existed (now revoked). Admissions already past ``admit`` complete; nothing new passes."""
        if reason not in ("owner_stop", "meeting_stop", "turn_ended", "admission_lost"):
            raise ValueError("unknown revoke reason")
        with self._tx() as db:
            now = self._clock()
            if reason != "turn_ended":
                db.execute("INSERT OR IGNORE INTO stops VALUES(?,?,?)", (turn_id, now, reason))
            cur = db.execute("UPDATE bindings SET revoked=coalesce(revoked,?), "
                             "revoked_reason=coalesce(revoked_reason,?) WHERE turn_id=?", (now, reason, turn_id))
            return cur.rowcount == 1

    def end(self, turn_id: str) -> None:
        """The turn's ``finally``: the binding is dead from now on."""
        self.revoke(turn_id, "turn_ended")

    # ── calls ────────────────────────────────────────────────────────────────
    def resolve(self, *, run_id, tool_call_id, instance_id, agent_id, session_user, wait_s: float = RUN_WAIT_S,
                monotonic=time.monotonic, sleep=time.sleep) -> Binding:
        """The live turn of exactly this run, or NotBound. Waits up to ``wait_s`` for the run to be attached, holding
        no transaction while it waits. Identity comes only from the sealed ids and the stored binding."""
        if (not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id)
                or not isinstance(tool_call_id, str) or not TOOL_CALL_ID.fullmatch(tool_call_id)
                or not isinstance(instance_id, str) or not INSTANCE_ID.fullmatch(instance_id)
                or not isinstance(agent_id, str) or not isinstance(session_user, str)):
            raise NotBound("run_missing")
        deadline = monotonic() + max(0.0, wait_s)
        db = self._db()
        while True:
            row = db.execute("SELECT * FROM bindings WHERE run_id=?", (run_id,)).fetchone()
            if row is not None or monotonic() >= deadline:
                break
            sleep(_POLL_S)
        if row is None:
            raise NotBound("run_unattached")
        return self._check(db, row, instance_id=instance_id, agent_id=agent_id, session_user=session_user)

    def _check(self, db, row, *, instance_id, agent_id, session_user) -> Binding:
        if row["agent_entry"] != agent_id or row["session_user"] != session_user \
                or row["environment"] != self.environment:
            raise NotBound("session_invalid")
        if row["revoked"] is not None:
            raise NotBound({"runtime_replaced": "runtime_replaced", "scheduler_restarted": "scheduler_restarted",
                            "superseded": "superseded", "turn_ended": "turn_ended"}
                           .get(row["revoked_reason"], "stopped"))
        if row["expires"] <= self._clock():
            raise NotBound("expired")
        if row["runtime_instance"] != instance_id or row["runtime_instance"] != self._state(db, "instance"):
            raise NotBound("runtime_replaced")
        if row["epoch"] != self._state(db, "epoch"):
            raise NotBound("scheduler_restarted")
        return Binding(row["turn_id"], row["environment"], row["principal"], row["origin"], row["scope"],
                       row["audience"], row["owner_request_id"], row["run_id"], row["agent_entry"], row["claim_id"],
                       row["room_generation"], row["trigger_seq"], row["approval_generation"])

    def admit(self, binding: Binding, *, tool: str, tool_call_id: str, instance_id: str, session_user: str,
              limit: int) -> BoundCall:
        """Admit one resolved call: re-checks the binding inside the write transaction (so a Stop that committed
        first wins), records the call once (a retried host call id is the same admission, same time) and enforces
        the per-turn quota for the tool. Returns the identity the tool server uses (``actions_v2.BoundCall``)."""
        if not isinstance(tool, str) or not tool or not TOOL_CALL_ID.fullmatch(tool_call_id or "") or limit < 1:
            raise NotBound("call_invalid")
        with self._tx() as db:
            row = db.execute("SELECT * FROM bindings WHERE turn_id=?", (binding.turn_id,)).fetchone()
            if row is None or row["run_id"] != binding.run_id:
                raise NotBound("run_unattached")
            self._check(db, row, instance_id=instance_id, agent_id=binding.agent_entry, session_user=session_user)
            seen = db.execute("SELECT tool, at, seq FROM calls WHERE turn_id=? AND tool_call_id=?",
                              (binding.turn_id, tool_call_id)).fetchone()
            if seen is not None:
                if seen["tool"] != tool:
                    raise NotBound("call_reused")
                at = seen["at"]
            else:
                used = db.execute("SELECT count(*) AS n FROM calls WHERE turn_id=? AND tool=?",
                                  (binding.turn_id, tool)).fetchone()["n"]
                if used >= limit:
                    raise NotBound("quota_exceeded")
                at = _iso(self._clock())
                seq = db.execute("SELECT coalesce(max(seq),0)+1 AS n FROM calls WHERE turn_id=?",
                                 (binding.turn_id,)).fetchone()["n"]
                db.execute("INSERT INTO calls VALUES(?,?,?,?,?,NULL,NULL)",
                           (binding.turn_id, tool_call_id, tool, at, seq))
        return BoundCall(environment=self.environment, scope=binding.scope,
                         call_id=f"{binding.run_id}:{tool_call_id}", at=datetime.fromisoformat(at))

    def record_receipt(self, binding: Binding, tool_call_id: str, *, command_id: str, receipt_id: str) -> None:
        """The store's receipt for an admitted call: the durable link from a CoS store row to its turn."""
        with self._tx() as db:
            db.execute("UPDATE calls SET command_id=coalesce(command_id,?), receipt_id=coalesce(receipt_id,?) "
                       "WHERE turn_id=? AND tool_call_id=?", (command_id, receipt_id, binding.turn_id, tool_call_id))

    def log_native(self, *, run_id, tool_call_id, tool: str, target: str | None) -> bool:
        """Record one native OpenClaw call (write, edit, exec) in an owner turn, bound or not. Never refuses the call
        (Robert, Q1): this is an observation log. True when the run belongs to a known binding."""
        run = run_id if isinstance(run_id, str) and RUN_ID.fullmatch(run_id) else None
        call = tool_call_id if isinstance(tool_call_id, str) and TOOL_CALL_ID.fullmatch(tool_call_id) else None
        name = tool if isinstance(tool, str) and tool else "unknown"
        text = None if target is None else str(target)[:500]
        with self._tx() as db:
            row = db.execute("SELECT turn_id FROM bindings WHERE run_id=?", (run,)).fetchone() if run else None
            db.execute("INSERT INTO native_calls(at, turn_id, run_id, tool_call_id, tool, target, bound) "
                       "VALUES(?,?,?,?,?,?,?)", (_iso(self._clock()), row["turn_id"] if row else None, run, call,
                                                 name[:64], text, 1 if row else 0))
        return row is not None

    def trace(self, turn_id: str) -> dict:
        """Everything recorded for one turn: the binding, its admitted calls with receipts, and native calls."""
        db = self._db()
        row = db.execute("SELECT * FROM bindings WHERE turn_id=?", (turn_id,)).fetchone()
        calls = [dict(r) for r in db.execute("SELECT * FROM calls WHERE turn_id=? ORDER BY seq", (turn_id,))]
        native = [dict(r) for r in db.execute("SELECT * FROM native_calls WHERE turn_id=? ORDER BY id", (turn_id,))]
        return {"binding": dict(row) if row else None, "calls": calls, "native_calls": native}


def dumps_trace(trace: dict) -> str:
    return json.dumps(trace, sort_keys=True, default=str)
