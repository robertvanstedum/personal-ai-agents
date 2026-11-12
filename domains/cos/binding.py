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

from contextlib import contextmanager

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
  claim_id TEXT, room_generation TEXT, trigger_seq INTEGER, approval_generation INTEGER,
  source_ref TEXT, runtime_instance TEXT NOT NULL, epoch TEXT NOT NULL, run_id TEXT UNIQUE, expires REAL NOT NULL,
  created REAL NOT NULL, revoked REAL, revoked_reason TEXT, read_only INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS bindings_live ON bindings(supersession_key) WHERE revoked IS NULL;
CREATE TABLE IF NOT EXISTS stops(turn_id TEXT PRIMARY KEY, at REAL NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calls(
  turn_id TEXT NOT NULL, tool_call_id TEXT NOT NULL, tool TEXT NOT NULL, at TEXT NOT NULL, seq INTEGER NOT NULL,
  command_id TEXT, receipt_id TEXT, PRIMARY KEY(turn_id, tool_call_id));
CREATE TABLE IF NOT EXISTS served_runs(
  run_id TEXT PRIMARY KEY, at TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, api TEXT, harness TEXT);
CREATE TABLE IF NOT EXISTS run_answers(run_id TEXT PRIMARY KEY, at TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS return_runtime_settlements(instance_id TEXT PRIMARY KEY, evidence_ref TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS terminal_audit_runs(run_id TEXT PRIMARY KEY, receipt TEXT NOT NULL, valid INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS terminal_runs(run_id TEXT PRIMARY KEY, receipt TEXT NOT NULL, valid INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS runtime_proof_keys(instance_id TEXT PRIMARY KEY, public_key TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS depth_continuations(turn_id TEXT PRIMARY KEY, old_run TEXT UNIQUE NOT NULL,
  nonce TEXT NOT NULL, state TEXT NOT NULL, new_run TEXT);
CREATE TABLE IF NOT EXISTS native_calls(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, turn_id TEXT, run_id TEXT, tool_call_id TEXT,
  tool TEXT NOT NULL, target TEXT, bound INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS turn_words(turn_id TEXT PRIMARY KEY, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mc_deliveries(
  delivery_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, conversation_id TEXT, state TEXT NOT NULL, receipt TEXT,
  at TEXT NOT NULL);
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
    room_generation: str | None = None
    trigger_seq: int | None = None
    approval_generation: int | None = None
    # B4: the CoS history turn this owner turn was saved as ({conversation_id, turn_id}), or None.
    source_ref: dict | None = None
    # A spoken lookup, run without his yes: only READ_OPERATIONS pass (tool_listener).
    read_only: bool = False


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
        # Only a folder made here is tightened. On production this file sits in the CoS store, whose mode the deploy sets
        # (cos_prod.sh prepare): the file itself stays 0600 either way.
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        db = self._db()
        db.executescript(_SCHEMA)
        # A spoken lookup's turn (Robert, 8 Nov: "I don't need you to ask me for permission to read things, maybe just
        # to write things"): its bound tools may only read. Added to a file made before the column existed.
        if "read_only" not in {row["name"] for row in db.execute("PRAGMA table_info(bindings)")}:
            db.execute("ALTER TABLE bindings ADD COLUMN read_only INTEGER NOT NULL DEFAULT 0")
        # #507: the runtime's trace id for the run, the same id the gateway's usage record carries as correlation_id.
        if "trace_id" not in {row["name"] for row in db.execute("PRAGMA table_info(served_runs)")}:
            db.execute("ALTER TABLE served_runs ADD COLUMN trace_id TEXT")

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

    def register_instance(self, instance_id: str, public_key=None) -> bool:
        """The plugin's gateway process. A new one becomes current and retires the old for good, revoking every
        binding on another instance; a retired instance is refused (no last-arrival-wins)."""
        if not isinstance(instance_id, str) or not INSTANCE_ID.fullmatch(instance_id):
            raise NotBound("instance_invalid")
        from core.model_routes.runtime_proof import valid_public_key
        if public_key is not None and not valid_public_key(public_key):
            raise NotBound("instance_key_invalid")
        with self._tx() as db:
            current = self._state(db, "instance")
            if current == instance_id:
                if public_key is not None:
                    key = db.execute("SELECT public_key FROM runtime_proof_keys WHERE instance_id=?", (instance_id,)).fetchone()
                    if key is None or key["public_key"] != public_key:
                        raise NotBound("instance_key_changed")
                return False
            seen = db.execute("SELECT 1 FROM instances WHERE instance_id=?", (instance_id,)).fetchone()
            if seen is not None:
                raise NotBound("instance_retired")
            now = self._clock()
            if current is not None:
                db.execute("UPDATE instances SET retired=? WHERE instance_id=?", (now, current))
            db.execute("INSERT INTO instances VALUES(?,?,NULL)", (instance_id, now))
            if public_key is not None:
                db.execute("INSERT INTO runtime_proof_keys VALUES(?,?)", (instance_id, public_key))
            db.execute("INSERT INTO state VALUES('instance',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (instance_id,))
            db.execute("UPDATE bindings SET revoked=?, revoked_reason='runtime_replaced' "
                       "WHERE revoked IS NULL AND runtime_instance<>?", (now, instance_id))
            return True

    # ── turn lifecycle ───────────────────────────────────────────────────────
    def record(self, turn_id: str, *, principal: str, origin: str, scope: str, audience: str, agent_entry: str,
               session_user: str, lifetime_s: float, owner_request_id: str | None = None,
               supersession_key: str | None = None, claim_id: str | None = None,
               room_generation: str | None = None, trigger_seq: int | None = None,
               approval_generation: int | None = None, source_ref: dict | None = None,
               owner_words: str | None = None, read_only: bool = False) -> bool:
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
        if scope == "meeting" and (not isinstance(claim_id, str) or not claim_id
                                   or not isinstance(room_generation, str) or not room_generation):
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
                trigger_seq, approval_generation, source_ref, runtime_instance, epoch, run_id, expires, created,
                revoked, revoked_reason, read_only) VALUES(?,?,?,?,'cos','cos',?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,?)""",
                       (turn_id, self.environment, principal, origin, agent_entry, session_user, scope, audience,
                        owner_request_id, supersession_key, claim_id, room_generation, trigger_seq,
                        approval_generation, json.dumps(source_ref, sort_keys=True) if source_ref else None,
                        instance, epoch, now + lifetime_s, now,
                        now if stop else None, stop["reason"] if stop else None, 1 if read_only is True else 0))
            if isinstance(owner_words, str) and owner_words:      # a Telegram turn has no history record to read them from
                db.execute("INSERT OR REPLACE INTO turn_words VALUES(?,?)", (turn_id, owner_words))
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

    @staticmethod
    def _ran(db, turn_id: str, run_id: str) -> bool:
        """Did this run do anything we were told of? An admitted call, a native call, or a completed model call: a tool can
        only start after a model call completed, so a run with none of the three has not started one."""
        return bool(db.execute("SELECT 1 FROM calls WHERE turn_id=? LIMIT 1", (turn_id,)).fetchone()
                    or db.execute("SELECT 1 FROM native_calls WHERE run_id=? LIMIT 1", (run_id,)).fetchone()
                    or db.execute("SELECT 1 FROM served_runs WHERE run_id=? LIMIT 1", (run_id,)).fetchone())

    def quiet(self, turn_id: str, run_id: str) -> bool:
        """True only when this turn's binding holds exactly this run and it did nothing we were told of."""
        with self._tx() as db:
            row = db.execute("SELECT run_id FROM bindings WHERE turn_id=?", (turn_id,)).fetchone()
            return row is not None and row["run_id"] == run_id and not self._ran(db, turn_id, run_id)

    def record_terminal_audit(self, receipt: dict) -> bool:
        """Signed termination is an audit fact even after Stop; never authority.

        A separate transaction preserves the fact when normal tool/depth binding
        checks subsequently reject a stopped, expired or replaced owner turn.
        """
        from core.model_routes.runtime_proof import verified
        with self._tx() as db:
            key=db.execute('SELECT public_key FROM runtime_proof_keys WHERE instance_id=?',(receipt.get('instance'),)).fetchone()
            if key is None or not verified(receipt,key['public_key']):return False
            row=db.execute('SELECT * FROM bindings WHERE run_id=?',(receipt['run'],)).fetchone()
            if (row is None or row['environment']!=self.environment or row['runtime_instance']!=receipt['instance']
                    or row['agent_entry']!=receipt['agent']
                    or receipt['session']!='agent:'+row['agent_entry']+':openai-user:'+row['session_user']):
                return False
            raw=json.dumps(receipt,sort_keys=True)
            prior=db.execute('SELECT receipt,valid FROM terminal_audit_runs WHERE run_id=?',(receipt['run'],)).fetchone()
            if prior and prior['receipt']!=raw:
                db.execute('UPDATE terminal_audit_runs SET valid=0 WHERE run_id=?',(receipt['run'],))
                return False
            db.execute('INSERT OR IGNORE INTO terminal_audit_runs(run_id,receipt) VALUES(?,?)',(receipt['run'],raw))
            return prior is None or bool(prior['valid'])

    def record_terminal(self, receipt: dict) -> bool:
        from core.model_routes.runtime_proof import valid_receipt, verified
        if not valid_receipt(receipt):
            return False
        # Audit receipt survives a later authorization refusal without reopening tools.
        self.record_terminal_audit(receipt)
        with self._tx() as db:
            key = db.execute("SELECT public_key FROM runtime_proof_keys WHERE instance_id=?", (receipt["instance"],)).fetchone()
            if key is None or not verified(receipt, key["public_key"]):
                return False
            row = db.execute("SELECT * FROM bindings WHERE run_id=?", (receipt["run"],)).fetchone()
            if row is None:
                return False
            user = receipt["session"].removeprefix(f"agent:{receipt['agent']}:openai-user:")
            # The HTTP answer can finish just before agent_end's callback arrives.
            # Keep that signed audit evidence after a normal close, without
            # reopening the binding. Stop/replacement/expiry still refuse it;
            # prepare_depth and tool admission always check the original row.
            audit_row = row
            if row["revoked"] is not None and row["revoked_reason"] == "turn_ended":
                if db.execute("SELECT 1 FROM stops WHERE turn_id=?", (row["turn_id"],)).fetchone():
                    raise NotBound("stopped")
                audit_row = dict(row, revoked=None)
            self._check(db, audit_row, instance_id=receipt["instance"], agent_id=receipt["agent"], session_user=user)
            raw = json.dumps(receipt, sort_keys=True)
            prior = db.execute("SELECT receipt FROM terminal_runs WHERE run_id=?", (receipt["run"],)).fetchone()
            if prior and prior["receipt"] != raw:
                db.execute("UPDATE terminal_runs SET valid=0 WHERE run_id=?", (receipt["run"],))
                return False
            db.execute("INSERT OR IGNORE INTO terminal_runs(run_id,receipt) VALUES(?,?)", (receipt["run"], raw))
            return True

    def prepare_depth(self, turn_id: str, run_id: str, nonce: str) -> bool:
        """Consume the one continuation and retire quick BEFORE the next send.

        A prepared row after a crash is uncertain, never permission to send again.
        """
        from core.model_routes.runtime_proof import unused_completed
        if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
            return False
        with self._tx() as db:
            row = db.execute("SELECT * FROM bindings WHERE turn_id=? AND run_id=?", (turn_id, run_id)).fetchone()
            evidence = db.execute("SELECT receipt FROM terminal_runs WHERE run_id=? AND valid=1", (run_id,)).fetchone()
            if row is None or evidence is None:
                return False
            receipt = json.loads(evidence["receipt"])
            if not unused_completed(receipt) or row["origin"] != "owner":
                return False
            self._check(db, row, instance_id=receipt["instance"], agent_id=receipt["agent"],
                        session_user=receipt["session"].removeprefix(f"agent:{receipt['agent']}:openai-user:"))
            if (db.execute("SELECT 1 FROM calls WHERE turn_id=?", (turn_id,)).fetchone()
                    or db.execute("SELECT 1 FROM native_calls WHERE run_id=?", (run_id,)).fetchone()):
                return False
            inserted = db.execute("INSERT OR IGNORE INTO depth_continuations VALUES(?,?,?,'prepared',NULL)",
                                  (turn_id, run_id, nonce)).rowcount
            if not inserted:
                return False
            db.execute("UPDATE bindings SET run_id=NULL WHERE turn_id=?", (turn_id,))
            return True

    def attach_depth(self, turn_id: str, run_id: str) -> bool:
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            return False
        with self._tx() as db:
            row = db.execute("SELECT * FROM bindings WHERE turn_id=? AND run_id IS NULL", (turn_id,)).fetchone()
            attempt = db.execute("SELECT old_run FROM depth_continuations WHERE turn_id=? AND state='prepared'", (turn_id,)).fetchone()
            if row is None or attempt is None or attempt["old_run"] == run_id:
                return False
            self._check(db, row, instance_id=row["runtime_instance"], agent_id=row["agent_entry"], session_user=row["session_user"])
            db.execute("UPDATE bindings SET run_id=? WHERE turn_id=?", (run_id, turn_id))
            db.execute("UPDATE depth_continuations SET state='attached',new_run=? WHERE turn_id=?", (run_id, turn_id))
            return True

    def move_run(self, turn_id: str, old_run: str, new_run: str) -> bool:
        """Plan failover only: the plan run ended quiet, and the API run takes over this binding. The one exception to
        "never moved". A late call from the old run then finds no binding. False changes nothing, and the new run's tools
        are refused."""
        if not isinstance(new_run, str) or not RUN_ID.fullmatch(new_run):
            raise NotBound("run_invalid")
        with self._tx() as db:
            row = db.execute("SELECT run_id, revoked FROM bindings WHERE turn_id=?", (turn_id,)).fetchone()
            if (row is None or row["run_id"] != old_run or row["revoked"] is not None or self._ran(db, turn_id, old_run)
                    or db.execute("SELECT 1 FROM bindings WHERE run_id=?", (new_run,)).fetchone()):
                return False
            db.execute("UPDATE bindings SET run_id=? WHERE turn_id=?", (new_run, turn_id))
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
                       row["room_generation"], row["trigger_seq"], row["approval_generation"],
                       json.loads(row["source_ref"]) if row["source_ref"] else None, bool(row["read_only"]))

    @contextmanager
    def hold_live(self, binding, *, tool_call_id, instance_id, session_user):
        """Serialize a private document dispatch with stop/runtime replacement.
        A stop committed first refuses; a dispatched call is allowed to settle.
        No identity or grant is taken from document arguments.
        """
        with self._tx():
            current = self.resolve(run_id=binding.run_id, tool_call_id=tool_call_id,
                                   instance_id=instance_id, agent_id=binding.agent_entry,
                                   session_user=session_user, wait_s=0)
            if current != binding:
                raise NotBound("binding_changed")
            yield

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

    def record_served(self, *, run_id, fields: dict) -> bool:
        """The model call this exact run completed, as the runtime reported it. Evidence only; the last call of a run wins."""
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            return False
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO served_runs(run_id, at, provider, model, api, harness, trace_id) VALUES(?,?,?,?,?,?,?)",
                       (run_id, _iso(self._clock()), fields["provider"], fields["model"], fields.get("api"), fields.get("harness"),
                        fields.get("trace")))
        return True

    def record_answer(self, *, run_id, text) -> bool:
        """The run's final assistant step, as the runtime reported it at the run's end: the answer without the narration
        between tool steps. Evidence only, for the reply's text; the last report of a run wins."""
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id) or not isinstance(text, str) or not text.strip():
            return False
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO run_answers(run_id, at, text) VALUES(?,?,?)", (run_id, _iso(self._clock()), text[:40_000]))
        return True

    def answer_for_run(self, run_id: str) -> str | None:
        with self._tx() as db:
            row = db.execute("SELECT text FROM run_answers WHERE run_id=?", (run_id,)).fetchone()
        return row["text"] if row else None

    def run_report(self, run_id: str) -> tuple[str | None, bool, bool]:
        """(this run's final step or None, whether its end receipt has arrived, whether this host records end receipts)."""
        with self._tx() as db:
            row = db.execute("SELECT text FROM run_answers WHERE run_id=?", (run_id,)).fetchone()
            ended = db.execute("SELECT 1 FROM terminal_runs WHERE run_id=? UNION SELECT 1 FROM terminal_audit_runs WHERE run_id=?",
                               (run_id, run_id)).fetchone() is not None
            receipts = ended or db.execute("SELECT 1 FROM terminal_runs LIMIT 1").fetchone() is not None
        return (row["text"] if row else None), ended, receipts

    def served_for_turn(self, turn_id: str) -> dict | None:
        """The evidence for the run attached to this turn, or None. Never the selected route."""
        with self._tx() as db:
            row = db.execute("SELECT s.provider, s.model, s.api, s.harness FROM bindings b JOIN served_runs s ON s.run_id = b.run_id "
                             "WHERE b.turn_id=?", (turn_id,)).fetchone()
        return {k: row[k] for k in ("provider", "model", "api", "harness") if row[k]} if row else None

    def trace_for_turn(self, turn_id: str) -> str | None:
        """The runtime trace id of the run attached to this turn (#507): usage records with this correlation_id are its
        model calls."""
        with self._tx() as db:
            row = db.execute("SELECT s.trace_id FROM bindings b JOIN served_runs s ON s.run_id = b.run_id WHERE b.turn_id=?",
                             (turn_id,)).fetchone()
        return row["trace_id"] if row and row["trace_id"] else None

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

    def owner_words(self, turn_id: str) -> str | None:
        """Robert's words for a turn that has no history record (Telegram), as the signed request carried them."""
        row = self._db().execute("SELECT text FROM turn_words WHERE turn_id=?", (turn_id,)).fetchone()
        return row["text"] if row else None

    # ── one send to MC per CoS turn (domains/cos/mc_send.py) ─────────────────
    def delivery(self, delivery_id: str) -> dict | None:
        row = self._db().execute("SELECT * FROM mc_deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()
        if row is None:
            return None
        return {"delivery_id": row["delivery_id"], "turn_id": row["turn_id"], "conversation_id": row["conversation_id"],
                "state": row["state"], "receipt": json.loads(row["receipt"]) if row["receipt"] else None}

    def claim_delivery(self, delivery_id: str, turn_id: str, conversation_id: str | None) -> bool:
        """Recorded before the POST. False: this delivery already exists, so nothing is posted."""
        with self._tx() as db:
            cur = db.execute("INSERT OR IGNORE INTO mc_deliveries VALUES(?,?,?,'sending',NULL,?)",
                             (delivery_id, turn_id, conversation_id, _iso(self._clock())))
            return cur.rowcount == 1

    def finish_delivery(self, delivery_id: str, state: str, receipt: dict) -> None:
        if state not in ("sent", "refused", "unknown"):
            raise ValueError("unknown delivery state")
        with self._tx() as db:
            db.execute("UPDATE mc_deliveries SET state=?, receipt=? WHERE delivery_id=? AND state='sending'",
                       (state, json.dumps(receipt, sort_keys=True), delivery_id))

    def trace(self, turn_id: str) -> dict:
        """Everything recorded for one turn: the binding, its admitted calls with receipts, and native calls."""
        db = self._db()
        row = db.execute("SELECT * FROM bindings WHERE turn_id=?", (turn_id,)).fetchone()
        calls = [dict(r) for r in db.execute("SELECT * FROM calls WHERE turn_id=? ORDER BY seq", (turn_id,))]
        native = [dict(r) for r in db.execute("SELECT * FROM native_calls WHERE turn_id=? ORDER BY id", (turn_id,))]
        return {"binding": dict(row) if row else None, "calls": calls, "native_calls": native}


def dumps_trace(trace: dict) -> str:
    return json.dumps(trace, sort_keys=True, default=str)


class TurnBinder:
    """The adapter's side of the binding (step 3): one binding per turn, from the turn's own facts.

    ``binding`` is what the caller verified, never what a request body or model said:
    - owner turns: {"origin": "owner" | "service:mc" | "service:telegram", "principal": ..., "owner_request_id": ...},
      taken from the F3 signed assertion of the request that started the turn;
    - meeting turns: {"origin": "meeting", "principal": ..., "audience": <room>, "claim_id": ..., "room_generation":
      ..., "trigger_seq": ..., "approval_generation": ...}, from the Records snapshot (step 4).
    No binding, or an incomplete one, means no binding is recorded: every bound tool call of that turn is refused.
    A closed meeting room never gets one.
    """

    def __init__(self, store: BindingStore, *, lifetime_s: float = 900):
        self.store = store
        self.lifetime_s = lifetime_s

    def begin(self, *, turn_id, session_user, agent_entry, conversation_id, meeting_scope, binding) -> str | None:
        if not isinstance(binding, dict) or not isinstance(turn_id, str) or not TURN_ID.fullmatch(turn_id):
            return None
        origin = binding.get("origin")
        principal = binding.get("principal")
        if meeting_scope is not None:
            if meeting_scope != "owner_private" or origin != "meeting":
                return None
            args = dict(scope="meeting", audience=binding.get("audience"), supersession_key=None,
                        claim_id=binding.get("claim_id"), room_generation=binding.get("room_generation"),
                        trigger_seq=binding.get("trigger_seq"), approval_generation=binding.get("approval_generation"))
        else:
            if origin not in ("owner", "service:mc", "service:telegram"):
                return None
            args = dict(scope="owner", audience=str(conversation_id),
                        # Review S6: a newer turn supersedes only an older turn from the same origin in the same
                        # conversation, so a Telegram or MC message never cuts off a running Confer turn's tools.
                        supersession_key=f"{self.store.environment}:{origin}:{conversation_id}")
        try:
            live = self.store.record(turn_id, principal=principal, origin=origin, agent_entry=agent_entry,
                                     session_user=session_user, lifetime_s=self.lifetime_s,
                                     owner_request_id=binding.get("owner_request_id"),
                                     source_ref=binding.get("history") if origin == "owner" else None,
                                     owner_words=binding.get("owner_words") if origin == "service:telegram" else None,
                                     read_only=binding.get("read_only") is True and meeting_scope is None, **args)
        except NotBound:
            return None
        return turn_id if live else None

    def attach(self, turn_id: str, run_id: str) -> bool:
        try:
            return self.store.attach_run(turn_id, run_id)
        except NotBound:
            return False

    def quiet(self, turn_id: str, run_id: str, *, grace_s: float = 0.0) -> bool:
        if grace_s:
            time.sleep(grace_s)
        return self.store.quiet(turn_id, run_id)

    def move(self, turn_id: str, old_run: str, new_run: str) -> bool:
        try:
            return self.store.move_run(turn_id, old_run, new_run)
        except NotBound:
            return False

    def served(self, turn_id: str) -> dict | None:
        return self.store.served_for_turn(turn_id)

    def answer(self, run_id: str) -> str | None:
        return self.store.answer_for_run(run_id)

    def run_report(self, run_id: str):
        return self.store.run_report(run_id)

    def prepare_depth(self, turn_id: str, run_id: str, nonce: str, wait_s: float = 5.0) -> bool:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                if self.store.prepare_depth(turn_id, run_id, nonce):
                    return True
            except NotBound:
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def attach_depth(self, turn_id: str, run_id: str) -> bool:
        try:
            return self.store.attach_depth(turn_id, run_id)
        except NotBound:
            return False

    def end(self, turn_id: str) -> None:
        self.store.end(turn_id)

    def stop(self, turn_id: str, reason: str = "owner_stop") -> bool:
        return self.store.revoke(turn_id, reason)


def helper_review_settled(store, source_ref, principal):
    """History failure/recovery is not proof of runtime termination.

    ``principal`` is one owner principal or a tuple of the owner's account ids:
    CoS records an owner turn under the portal account that signed it (a number
    when the owner has an auth.users row), not under the task service's name.

    Only signed terminal receipts already verified at ingress settle attached
    runs. A missing run identity or proof keeps the return queued. Include all
    owner runs so a restarted process cannot ignore an older orphaned run.
    """
    if store is None:return False
    with store._tx() as db:
        def terminal(run):
            audit=db.execute('SELECT valid FROM terminal_audit_runs WHERE run_id=?',(run,)).fetchone()
            if audit is not None:return bool(audit['valid'])
            return db.execute('SELECT 1 FROM terminal_runs WHERE run_id=? AND valid=1',(run,)).fetchone() is not None
        accounts=(principal,) if isinstance(principal,str) else tuple(principal)
        if not accounts:return False
        rows=db.execute("SELECT source_ref,run_id,turn_id,runtime_instance FROM bindings WHERE principal IN (%s) AND origin='owner'"%','.join('?'*len(accounts)),accounts).fetchall()
        found=False
        for row in rows:
            source=json.loads(row['source_ref']) if row['source_ref'] else None
            if (isinstance(source,dict) and source_ref.get('store')=='cos-history'
                    and source_ref.get('id')==str(source.get('conversation_id'))+':'+str(source.get('turn_id'))):found=True
            if db.execute('SELECT 1 FROM return_runtime_settlements WHERE instance_id=?',(row['runtime_instance'],)).fetchone():
                continue
            if row['run_id'] is None or not terminal(row['run_id']):
                return False
            old=db.execute('SELECT old_run,state,new_run FROM depth_continuations WHERE turn_id=?',(row['turn_id'],)).fetchone()
            if old and (old['state']!='attached' or old['new_run']!=row['run_id']
                        or not terminal(old['old_run'])):
                return False
        return found


def reconcile_retired_return_runtime(store, instance_id, *, evidence_ref):
    """Operator-only migration after independently proving an old runtime stopped.

    No HTTP/model route calls this function. Retirement alone is insufficient:
    the operator records a durable deployment/stop verification evidence ref.
    This does not manufacture a signed model receipt or a successful answer.
    """
    if not re.fullmatch(r'[0-9a-f]{32}',instance_id) or not isinstance(evidence_ref,str) or not 8<=len(evidence_ref)<=300:
        raise ValueError('retired instance and operator evidence required')
    with store._tx() as db:
        row=db.execute('SELECT retired FROM instances WHERE instance_id=?',(instance_id,)).fetchone()
        if row is None or row['retired'] is None:
            raise ValueError('cannot reconcile current runtime')
        existing=db.execute('SELECT evidence_ref FROM return_runtime_settlements WHERE instance_id=?',(instance_id,)).fetchone()
        if existing and existing['evidence_ref']!=evidence_ref:
            raise ValueError('settlement evidence changed')
        db.execute('INSERT OR IGNORE INTO return_runtime_settlements VALUES(?,?)',(instance_id,evidence_ref))
