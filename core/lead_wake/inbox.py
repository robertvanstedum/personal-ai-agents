"""Durable receipt of a lead-wake intent. This is execution bookkeeping, not a task store.

The authenticated adapter supplies resolve(operation_id), which must read the authoritative
outbox/task and enforce environment, principal, scope, charter, current result and fence.
It returns the validated packet or raises StaleWake. No model or owner tool is invoked here.
Lookup fences an absent attempt forever before reporting absence, so a delayed sender cannot
arrive after the source decides it is safe to retry. A receipt proves queued, never delivered.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import stat
import sqlite3
from pathlib import Path
from core.canonical_json import digest


class StaleWake(Exception):
    pass


class Conflict(Exception):
    pass


class Inbox:
    def __init__(self, path, resolve):
        self.path, self.resolve = Path(path), resolve
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            mode = self.path.lstat().st_mode
            if not stat.S_ISREG(mode) or mode & 0o077:
                raise PermissionError('wake inbox must be a private regular file')
        else:
            os.close(fd)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS entries(
                    operation_id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                    packet TEXT NOT NULL, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews(
                    operation_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL,
                    result TEXT, failure TEXT);
                CREATE TABLE IF NOT EXISTS publication_intents(
                    operation_id TEXT PRIMARY KEY, intent TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS absent(
                    operation_id TEXT NOT NULL, attempt INTEGER NOT NULL,
                    PRIMARY KEY(operation_id, attempt));
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _ids(operation_id, attempt):
        if not isinstance(operation_id, str) or not operation_id.startswith('OB-wake-') or len(operation_id) > 80:
            raise ValueError('invalid wake operation')
        if type(attempt) is not int or attempt < 1:
            raise ValueError('invalid attempt')

    @staticmethod
    def _receipt(operation_id, want_digest):
        return {'store': 'lead_wake', 'id': operation_id, 'digest': want_digest, 'edition': '1'}

    def deliver(self, operation_id, payload_digest, *, task_id, kind, attempt):
        self._ids(operation_id, attempt)
        # No helper-supplied prompt/destination is trusted. Resolve again on every delivery.
        packet = self.resolve(operation_id)
        if (packet.get('task_id') != task_id or packet.get('kind') != kind
                or packet.get('lead') not in ('mc', 'cos') or digest(packet) != payload_digest):
            raise Conflict('wake binding differs')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM absent WHERE operation_id=? AND attempt=?', (operation_id, attempt)).fetchone():
                raise StaleWake('attempt already proved absent')
            previous = db.execute('SELECT digest FROM entries WHERE operation_id=?', (operation_id,)).fetchone()
            if previous:
                if previous[0] != payload_digest:
                    raise Conflict('operation id reused')
                return {'receipt': self._receipt(operation_id, payload_digest)}
            db.execute('INSERT INTO entries VALUES(?,?,?,?)',
                       (operation_id, payload_digest, json.dumps(packet, sort_keys=True), 'queued'))
        return {'receipt': self._receipt(operation_id, payload_digest)}

    def lookup(self, operation_id, *, attempt):
        self._ids(operation_id, attempt)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT digest FROM entries WHERE operation_id=?', (operation_id,)).fetchone()
            if row:
                return {'state': 'applied', 'receipt': self._receipt(operation_id, row[0])}
            db.execute('INSERT OR IGNORE INTO absent VALUES(?,?)', (operation_id, attempt))
        return {'state': 'absent'}

    def queued(self):
        with self._db() as db:
            rows = db.execute("SELECT operation_id,packet FROM entries WHERE state='queued' ORDER BY rowid").fetchall()
        ready = []
        for operation_id, raw in rows:
            packet = json.loads(raw)
            try:
                current = self.resolve(operation_id)
            except StaleWake:
                current = None
            if current != packet:
                with self._db() as db:
                    db.execute("UPDATE entries SET state='superseded' WHERE operation_id=? AND state='queued'", (operation_id,))
            else:
                ready.append((operation_id, packet))
        # Consumer must revalidate again at inference and persistence; this read is not a lease or grant.
        return ready

    def claim(self, operation_id, claim_id):
        """One inference attempt. Caller holds the canonical conversation lane and
        has checked the originating turn settled; this claim does not replace that lane.
        An interrupted claim is never returned to queued automatically.
        """
        if not isinstance(claim_id, str) or not 16 <= len(claim_id) <= 128:
            raise ValueError('invalid claim')
        current = self.resolve(operation_id)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT packet,state FROM entries WHERE operation_id=?', (operation_id,)).fetchone()
            if row is None or row[1] != 'queued':
                return False
            if json.loads(row[0]) != current:
                raise StaleWake('wake superseded')
            db.execute('INSERT INTO reviews(operation_id,claim_id) VALUES(?,?)', (operation_id, claim_id))
            db.execute("UPDATE entries SET state='reviewing' WHERE operation_id=?", (operation_id,))
        return True

    def uncertain(self, operation_id, claim_id):
        """Model dispatch may have happened. Preserve the claim, never blindly retry."""
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT claim_id,result FROM reviews WHERE operation_id=?', (operation_id,)).fetchone()
            if row is None or row[0] != claim_id:
                raise Conflict('claim differs')
            if row[1] is None:
                db.execute("UPDATE entries SET state='uncertain' WHERE operation_id=? AND state='reviewing'", (operation_id,))
                db.execute("UPDATE reviews SET failure='inference_unconfirmed' WHERE operation_id=?", (operation_id,))

    def save_review(self, operation_id, claim_id, result):
        """Keep the exact bounded tool-free review before recording its verdict or answer.
        A late provider answer may settle its original uncertain claim; it is not a retry.
        """
        if (not isinstance(result, dict) or set(result) != {'verdict', 'reasons', 'answer'}
                or result['verdict'] not in ('approve', 'changes_requested')
                or any(not isinstance(result[k], str) or not result[k].strip() or len(result[k]) > 8000
                       for k in ('reasons', 'answer'))):
            raise ValueError('invalid review result')
        current = self.resolve(operation_id)
        raw = json.dumps(result, sort_keys=True)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT e.packet,e.state,r.claim_id,r.result FROM entries e JOIN reviews r USING(operation_id) WHERE operation_id=?', (operation_id,)).fetchone()
            if row is None or row[2] != claim_id:
                raise Conflict('claim differs')
            if json.loads(row[0]) != current:
                raise StaleWake('wake superseded')
            if row[3] is not None:
                if row[3] != raw:
                    raise Conflict('review already recorded')
                return
            if row[1] not in ('reviewing', 'uncertain'):
                raise Conflict('review no longer pending')
            db.execute('UPDATE reviews SET result=?,failure=NULL WHERE operation_id=?', (raw, operation_id))
            db.execute("UPDATE entries SET state='reviewed' WHERE operation_id=?", (operation_id,))

    def review(self, operation_id):
        """Read durable output without invoking a model. Not authority to accept or present."""
        with self._db() as db:
            row = db.execute('SELECT result FROM reviews WHERE operation_id=?', (operation_id,)).fetchone()
        return json.loads(row[0]) if row and row[0] is not None else None

    def prepare_publication(self, operation_id, *, conversation_id, created_at):
        """Pin exact canonical content/identity before any external write.

        The host must resolve the original conversation from canonical source,
        hold its lane, and supply its timestamp. No model-selected destination.
        This preparation alone grants no publication authority.
        """
        from datetime import datetime
        if not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 160:
            raise ValueError('invalid publication conversation')
        try:
            timestamp = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            if timestamp.tzinfo is None:
                raise ValueError('timestamp must include timezone')
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError('invalid publication timestamp') from exc
        current = self.resolve(operation_id)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT e.packet,e.state,r.result FROM entries e JOIN reviews r USING(operation_id) WHERE operation_id=?', (operation_id,)).fetchone()
            if row is None or row[2] is None or row[1] not in ('reviewed', 'presented'):
                raise Conflict('durable review required')
            packet, result = json.loads(row[0]), json.loads(row[2])
            if packet != current:
                raise StaleWake('wake superseded')
            intent = {'operation_id': operation_id, 'packet_digest': digest(packet),
                      'result_digest': packet['result_digest'],
                      'review_digest': digest(result),
                      'store': {'mc': 'mc-floor', 'cos': 'cos-history'}[packet['lead']],
                      'id': 'consult:' + operation_id, 'conversation_id': conversation_id,
                      'created_at': created_at, 'text': result['answer']}
            prior = db.execute('SELECT intent FROM publication_intents WHERE operation_id=?', (operation_id,)).fetchone()
            if prior:
                kept = json.loads(prior[0])
                # Timestamp is selected only once; retries reuse it after restart.
                if {k: v for k, v in kept.items() if k != 'created_at'} != {k: v for k, v in intent.items() if k != 'created_at'}:
                    raise Conflict('publication intent changed')
                return kept
            raw = json.dumps(intent, sort_keys=True)
            db.execute('INSERT INTO publication_intents VALUES(?,?)', (operation_id, raw))
            return intent

    def publication(self, operation_id):
        """Read the immutable write intent for canonical reconciliation, never rerender."""
        with self._db() as db:
            row = db.execute('SELECT intent FROM publication_intents WHERE operation_id=?', (operation_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def pending(self):
        """Queued inference or durable review awaiting effects; never replay an uncertain call."""
        with self._db() as db:
            rows = db.execute("SELECT operation_id,packet FROM entries WHERE state IN ('queued','reviewed') ORDER BY rowid").fetchall()
        return [(op, json.loads(raw)) for op, raw in rows]

    def supersede(self, operation_id):
        with self._db() as db:
            db.execute("UPDATE entries SET state='superseded' WHERE operation_id=? AND state!='presented'", (operation_id,))

    def mark_presented(self, operation_id, receipt):
        """Host-only: receipt must name an actually saved canonical answer for this exact result.
        The host must verify the canonical note before calling; an acceptance is not this receipt.
        """
        if (not isinstance(receipt, dict) or set(receipt) != {'store','id','result_digest'}
                or receipt['store'] not in ('mc-floor','cos-history')
                or not isinstance(receipt['id'], str) or not receipt['id']):
            raise ValueError('invalid presentation receipt')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT packet,state FROM entries WHERE operation_id=?', (operation_id,)).fetchone()
            if row is None:
                raise Conflict('wake missing')
            packet = json.loads(row[0])
            if (packet.get('result_digest') != receipt['result_digest']
                    or receipt['store'] != {'mc':'mc-floor','cos':'cos-history'}[packet['lead']]):
                raise Conflict('presentation differs')
            db.execute('CREATE TABLE IF NOT EXISTS presentations(operation_id TEXT PRIMARY KEY, receipt TEXT NOT NULL)')
            raw = json.dumps(receipt, sort_keys=True)
            prior = db.execute('SELECT receipt FROM presentations WHERE operation_id=?', (operation_id,)).fetchone()
            if prior and prior[0] != raw:
                raise Conflict('presentation already recorded')
            db.execute('INSERT OR IGNORE INTO presentations VALUES(?,?)', (operation_id,raw))
            db.execute("UPDATE entries SET state='presented' WHERE operation_id=?", (operation_id,))

    def status(self, operation_id):
        with self._db() as db:
            row = db.execute('SELECT state FROM entries WHERE operation_id=?', (operation_id,)).fetchone()
        return row[0] if row else None

    def budget_blocked(self, operation_id, claim_id):
        """A proven budget refusal before model dispatch is not an unknown charge."""
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT claim_id,result FROM reviews WHERE operation_id=?', (operation_id,)).fetchone()
            if row is None or row[0] != claim_id or row[1] is not None:
                raise Conflict('claim differs')
            db.execute("UPDATE entries SET state='budget_blocked' WHERE operation_id=? AND state='reviewing'", (operation_id,))
            db.execute("UPDATE reviews SET failure='budget_exhausted' WHERE operation_id=?", (operation_id,))
