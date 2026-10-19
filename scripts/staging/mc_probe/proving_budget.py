"""Durable $25 Chicago-day admission ledger for the isolated dev proving run.

D23: the cap is a conservative runaway guard. Each reservation is a conservative
per-turn estimate (sized for tool loops), not a bound the gateway enforces; the
gateway's own usage reports stay authoritative. This ledger does not itself
constrain model tokens, tool loops, or other callers.

A reservation counts against the Chicago day it was made on, for good, unless
correlated receipts settle it (or the portal answered without a model: see
settle_platform). An unresolved reservation from just before midnight also
counts against the new day for CARRY, longer than any turn can still run.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from contextlib import contextmanager
from pathlib import Path
import json
import os
import re
import sqlite3
from zoneinfo import ZoneInfo

CAP_MICRO = 25_000_000
CARRY = timedelta(minutes=15)   # > the driver's 180 s request timeout, with slack
COLUMNS = ['id', 'day', 'bound', 'charged', 'state', 'receipts', 'reserved_us']

class BudgetRefused(RuntimeError):
    pass


def micro(value):
    if isinstance(value, bool) or value is None:
        raise BudgetRefused('cost_missing_or_invalid')
    try:
        value = Decimal(str(value))
        if not value.is_finite() or value < 0:
            raise BudgetRefused('cost_invalid')
        return int((value * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    except (ValueError, InvalidOperation):
        raise BudgetRefused('cost_invalid') from None


class Budget:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        if self.path.stat().st_mode & 0o077:
            raise BudgetRefused('budget_file_must_be_private')
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS stop_flags (reason TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, day TEXT NOT NULL, bound INTEGER NOT NULL, charged INTEGER NOT NULL, state TEXT NOT NULL, receipts TEXT, reserved_us INTEGER NOT NULL)')
            if [row[1] for row in db.execute('PRAGMA table_info(reservations)')] != COLUMNS:
                raise BudgetRefused('ledger_schema_outdated_use_a_new_ledger')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, request_id, upper_bound_usd, *, at=None):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', request_id):
            raise BudgetRefused('invalid_request_id')
        bound = micro(upper_bound_usd)
        if not 0 < bound <= CAP_MICRO:
            raise BudgetRefused('positive_bounded_reservation_required')
        when = at or datetime.now(timezone.utc)
        if when.tzinfo is None:
            raise BudgetRefused('timezone_required')
        day = when.astimezone(ZoneInfo('America/Chicago')).date().isoformat()
        at_us = _epoch_us(when)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM stop_flags').fetchone():
                raise BudgetRefused('loop_stopped_requires_operator_reconciliation')
            if db.execute('SELECT 1 FROM reservations WHERE id=?', (request_id,)).fetchone():
                raise BudgetRefused('duplicate_request_requires_reconciliation')
            # Everything charged to this day, plus any request from an earlier
            # day that is unresolved and recent enough to still be running.
            used = db.execute("SELECT COALESCE(SUM(charged),0) FROM reservations WHERE day=? OR "
                              "(state='reserved' AND reserved_us>=?)",
                              (day, at_us - CARRY // timedelta(microseconds=1))).fetchone()[0]
            if used + bound > CAP_MICRO:
                raise BudgetRefused('daily_cap_reached')
            db.execute('INSERT INTO reservations VALUES (?,?,?,?,?,?,?)', (request_id, day, bound, bound, 'reserved', None, at_us))
        return {'admitted':True, 'reservation_id':request_id, 'cap_usd':25,
                'day_spent_before_usd':used/1_000_000, 'reserved_usd':bound/1_000_000,
                'day':day, 'decided_utc':when.astimezone(timezone.utc).isoformat()}

    def settle(self, request_id, actual_usd, receipt_ids):
        if actual_usd is None:
            return {'usd':None, 'source':'unresolved_gateway_receipts', 'receipt_ids':[]}
        cost = micro(actual_usd)
        if not isinstance(receipt_ids, list) or not receipt_ids or any(not isinstance(r,str) or not r for r in receipt_ids) or len(set(receipt_ids)) != len(receipt_ids):
            raise BudgetRefused('correlated_receipt_ids_required')
        receipts = json.dumps(sorted(receipt_ids))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT bound,charged,state,receipts FROM reservations WHERE id=?', (request_id,)).fetchone()
            if row is None:
                raise BudgetRefused('reservation_missing')
            if row[2] == 'settled':
                if row[1] != cost or row[3] != receipts:
                    raise BudgetRefused('settlement_conflict')
            else:
                for existing, in db.execute('SELECT receipts FROM reservations WHERE receipts IS NOT NULL'):
                    if set(json.loads(existing)) & set(receipt_ids):
                        raise BudgetRefused('receipt_reused')
                if cost > row[0]:
                    db.execute('INSERT OR IGNORE INTO stop_flags VALUES (?)', ('enforced_bound_breached',))
                    db.commit()
                    raise BudgetRefused('enforced_bound_breached_stop_loop')
                # The charge stays on the day the request was admitted.
                db.execute("UPDATE reservations SET charged=?,state='settled',receipts=? WHERE id=?", (cost,receipts,request_id))
        return {'usd':cost/1_000_000,'source':'gateway_receipts','receipt_ids':sorted(receipt_ids)}

    def settle_platform(self, request_id, answer):
        """Release a reservation the portal answered itself, with no model call.

        Only the journaled answer of a completed turn qualifies: HTTP 200 with
        status 'answered' and backend_kind 'platform' (Board, Library, Register
        and owner-action commands). Anything else, including a job start, keeps
        its reservation."""
        if not (isinstance(answer, dict) and answer.get('status') == 'answered'
                and answer.get('backend_kind') == 'platform' and not answer.get('error')):
            raise BudgetRefused('platform_answer_required')
        receipts = json.dumps(['platform:' + str(request_id)])
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT charged,state,receipts FROM reservations WHERE id=?', (request_id,)).fetchone()
            if row is None:
                raise BudgetRefused('reservation_missing')
            if row[1] == 'settled':
                if row[0] != 0 or row[2] != receipts:
                    raise BudgetRefused('settlement_conflict')
            else:
                db.execute("UPDATE reservations SET charged=0,state='settled',receipts=? WHERE id=?", (receipts, request_id))
        return {'usd': 0.0, 'source': 'platform_answer', 'receipt_ids': ['platform:' + str(request_id)]}


def _epoch_us(when):
    delta = when.astimezone(timezone.utc) - datetime.fromtimestamp(0, timezone.utc)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
