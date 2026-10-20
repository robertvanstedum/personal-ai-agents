"""Durable effect identity and fenced receipts (harness §§3.4,5).

Internal transaction API only: no route, tool, grant compiler or implicit grant.
The caller authenticates scope and rechecks current grants/policy/target/budget
before claiming/dispatching. A lease is concurrency control, never authority.
All methods borrow a non-autocommit psycopg connection; never commit here.

Same-DB domain operations and verification use the SAME borrowed transaction,
so exceptions roll back their effect and receipt together. External sends need
a committed dispatched intent first, provider idempotency/correlation, and a
separate readback. Expired dispatch leases can only reconcile, never resend.
The caller must not perform network effects before dispatch transaction commit.
Reservations are REFERENCES here; grant consumption/budget accounting belongs
in the caller's admission transaction, not in a second authority store.
"""
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets
from uuid import UUID, uuid4

from .context import ActionContext, ActionContractError, _identifier
from .requests import ToolRequest

_MAX_INT = 2**63 - 1
_TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class EffectIntent:
    """Server-assigned effect ID; never accept this object from model arguments.

    Only digests/references are stored. The necessary execution payload must be
    retained separately in scoped operation storage before external dispatch.
    """
    operation_id: str
    request: ToolRequest
    grant_id: str | None = None
    proposal_id: str | None = None
    expected_target_version: str | None = None
    reservation_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if (not _uuid(self.operation_id) or type(self.request) is not ToolRequest
                or self.request.operation_version > _MAX_INT):
            raise ActionContractError('receipt_intent_invalid')
        if self.grant_id is not None and not _identifier(self.grant_id):
            raise ActionContractError('receipt_grant_invalid')
        if self.proposal_id is not None and not _uuid(self.proposal_id):
            raise ActionContractError('receipt_proposal_invalid')
        if self.expected_target_version is not None and not _identifier(self.expected_target_version):
            raise ActionContractError('receipt_target_version_invalid')
        if (type(self.reservation_ids) is not tuple or len(self.reservation_ids) > 20
                or not all(_uuid(x) for x in self.reservation_ids)
                or len(set(self.reservation_ids)) != len(self.reservation_ids)):
            raise ActionContractError('receipt_reservations_invalid')

    @property
    def digest(self):
        return _hash(json.dumps([self.request.operation, self.request.operation_version,
            self.request.arguments_sha256, self.grant_id, self.proposal_id,
            self.expected_target_version, sorted(self.reservation_ids)], separators=(',', ':')))


@dataclass(frozen=True, slots=True, repr=False)
class EffectLease:
    principal_id: str
    environment: str
    operation_id: str
    generation: int
    worker_id: str
    token: str
    mode: str

    def __post_init__(self):
        _scope(self.principal_id, self.environment, self.operation_id)
        if (type(self.generation) is not int or not 0 < self.generation <= _MAX_INT
                or not _identifier(self.worker_id) or type(self.token) is not str
                or not _TOKEN.fullmatch(self.token) or self.mode not in ('execute', 'reconcile')):
            raise ActionContractError('receipt_lease_invalid')


def _scope(principal, environment, operation_id):
    if (not _identifier(principal) or type(environment) is not str
            or environment not in ('dev', 'prod') or not _uuid(operation_id)):
        raise ActionContractError('receipt_scope_invalid')
    return principal, environment, operation_id


def _cursor(conn):
    if conn.autocommit:
        raise ActionContractError('receipt_transaction_required')
    from psycopg2.extras import RealDictCursor
    return conn.cursor(cursor_factory=RealDictCursor)


class ReceiptStore:
    @staticmethod
    def _row(cur, scope):
        cur.execute('''SELECT * FROM guild.mc_action_receipts
            WHERE principal_id=%s AND environment=%s AND operation_id=%s FOR UPDATE''', scope)
        row = cur.fetchone()
        if row is None:
            raise ActionContractError('receipt_not_found')
        # Read time AFTER waiting for the row lock, not transaction start time.
        cur.execute('SELECT clock_timestamp() AS now')
        return row, cur.fetchone()['now']

    @staticmethod
    def _event(cur, scope, kind):
        cur.execute('''UPDATE guild.mc_action_receipts SET event_sequence=event_sequence+1,
            updated_at=clock_timestamp() WHERE principal_id=%s AND environment=%s AND operation_id=%s
            RETURNING event_sequence,state,generation,cancel_requested,lease_mode''', scope)
        row = cur.fetchone()
        # No argument text, upstream body, context reference or lease secret.
        payload = {k: row[k] for k in ('state', 'generation', 'cancel_requested', 'lease_mode')}
        cur.execute('''INSERT INTO guild.mc_action_events
            (principal_id,environment,operation_id,sequence,event_type,payload)
            VALUES (%s,%s,%s,%s,%s,%s::jsonb)''', scope + (row['event_sequence'], kind, json.dumps(payload)))

    @staticmethod
    def _public(row):
        fields = ('receipt_id', 'operation_id', 'state', 'generation', 'cancel_requested',
                  'evidence_refs', 'verified_at', 'event_sequence')
        return {key: row[key] for key in fields}

    def read(self, conn, principal, environment, operation_id):
        with _cursor(conn) as cur:
            row, _ = self._row(cur, _scope(principal, environment, operation_id))
            return self._public(row)

    def prepare(self, conn, context: ActionContext, intent: EffectIntent):
        if type(context) is not ActionContext or type(intent) is not EffectIntent:
            raise ActionContractError('receipt_intent_invalid')
        scope = _scope(context.principal_id, context.environment, intent.operation_id)
        with _cursor(conn) as cur:
            cur.execute('''INSERT INTO guild.mc_action_receipts
                (principal_id,environment,operation_id,receipt_id,actor_id,conversation_id,turn_id,work_id,
                 capability,capability_version,request_digest,intent_digest,grant_id,proposal_id,
                 policy_version,role_version,expected_target_version,reservation_ids)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (principal_id,environment,operation_id) DO NOTHING RETURNING operation_id''',
                scope + (str(uuid4()), context.actor_id, context.conversation_id, context.turn_id,
                    context.work_id, intent.request.operation, intent.request.operation_version,
                    intent.request.arguments_sha256, intent.digest, intent.grant_id, intent.proposal_id,
                    context.policy_version, context.role_version, intent.expected_target_version,
                    json.dumps(intent.reservation_ids)))
            inserted = cur.fetchone() is not None
            row, now = self._row(cur, scope)
            if context.has_expired(now):
                raise ActionContractError('receipt_context_expired')
            if row['intent_digest'] != intent.digest:
                raise ActionContractError('receipt_operation_reused')
            if inserted:
                self._event(cur, scope, 'prepared')
            row, _ = self._row(cur, scope)
            return self._public(row)

    def claim(self, conn, principal, environment, operation_id, worker_id, *, mode='execute', seconds=30):
        scope = _scope(principal, environment, operation_id)
        if (not _identifier(worker_id) or mode not in ('execute', 'reconcile')
                or type(seconds) is not int or not 1 <= seconds <= 30):
            raise ActionContractError('receipt_claim_invalid')
        with _cursor(conn) as cur:
            row, now = self._row(cur, scope)
            if row['state'] in ('verified', 'failed', 'cancelled'):
                raise ActionContractError('receipt_terminal')
            if row['lease_until'] is not None and row['lease_until'] > now:
                raise ActionContractError('receipt_busy')
            if mode == 'execute' and (row['state'] != 'prepared' or row['cancel_requested']):
                raise ActionContractError('receipt_reconciliation_required')
            if mode == 'reconcile' and row['state'] not in ('dispatched', 'unknown'):
                raise ActionContractError('receipt_not_dispatched')
            if row['generation'] == _MAX_INT:
                raise ActionContractError('receipt_generation_exhausted')
            token = secrets.token_urlsafe(32)
            generation = row['generation'] + 1
            if mode == 'reconcile' and row['state'] == 'dispatched':
                cur.execute('''UPDATE guild.mc_action_receipts SET state='unknown'
                    WHERE principal_id=%s AND environment=%s AND operation_id=%s''', scope)
                self._event(cur, scope, 'unknown')
            cur.execute('''UPDATE guild.mc_action_receipts SET generation=%s,lease_hash=%s,
                lease_until=clock_timestamp()+%s*interval '1 second',lease_mode=%s,worker_id=%s
                WHERE principal_id=%s AND environment=%s AND operation_id=%s''',
                (generation, _hash(token), seconds, mode, worker_id) + scope)
            self._event(cur, scope, 'claimed')
            return EffectLease(*scope, generation, worker_id, token, mode)

    def _leased(self, cur, lease):
        if type(lease) is not EffectLease:
            raise ActionContractError('receipt_lease_invalid')
        scope = _scope(lease.principal_id, lease.environment, lease.operation_id)
        row, now = self._row(cur, scope)
        if (row['lease_hash'] is None or not hmac.compare_digest(row['lease_hash'], _hash(lease.token))
                or row['generation'] != lease.generation or row['worker_id'] != lease.worker_id
                or row['lease_mode'] != lease.mode or row['lease_until'] <= now):
            raise ActionContractError('receipt_lease_stale')
        return scope, row

    def dispatch(self, conn, lease):
        with _cursor(conn) as cur:
            scope, row = self._leased(cur, lease)
            if lease.mode != 'execute' or row['state'] != 'prepared' or row['cancel_requested']:
                raise ActionContractError('receipt_dispatch_refused')
            cur.execute('''UPDATE guild.mc_action_receipts SET state='dispatched'
                WHERE principal_id=%s AND environment=%s AND operation_id=%s''', scope)
            self._event(cur, scope, 'dispatched')

    def finish(self, conn, lease, state, *, evidence_refs=()):
        """Verified/failed requires committed evidence IDs from a trusted verifier.

        For local writes dispatch, domain change, readback and finish are in one
        transaction. Unknown retains all reservation links; it never permits a
        retry. A cancelled dispatched operation still needs effect readback.
        Evidence authorization/meaning is enforced by the registered verifier,
        not by accepting arbitrary user text as an ID.
        """
        if (state not in ('verified', 'failed', 'unknown') or type(evidence_refs) is not tuple
                or len(evidence_refs) > 32 or not all(_uuid(x) for x in evidence_refs)
                or len(set(evidence_refs)) != len(evidence_refs)
                or (state != 'unknown' and not evidence_refs)):
            raise ActionContractError('receipt_result_invalid')
        with _cursor(conn) as cur:
            scope, row = self._leased(cur, lease)
            if row['state'] not in ('dispatched', 'unknown'):
                raise ActionContractError('receipt_result_transition_invalid')
            cur.execute('''UPDATE guild.mc_action_receipts SET state=%s,evidence_refs=%s::jsonb,
                verified_at=CASE WHEN %s='verified' THEN clock_timestamp() ELSE NULL END,
                lease_hash=NULL,lease_until=NULL,lease_mode=NULL,worker_id=NULL
                WHERE principal_id=%s AND environment=%s AND operation_id=%s''',
                (state, json.dumps(evidence_refs), state) + scope)
            self._event(cur, scope, state)

    def cancel(self, conn, principal, environment, operation_id):
        scope = _scope(principal, environment, operation_id)
        with _cursor(conn) as cur:
            row, _ = self._row(cur, scope)
            if row['state'] in ('verified', 'failed', 'cancelled') or row['cancel_requested']:
                return self._public(row)
            if row['state'] == 'prepared':
                cur.execute('''UPDATE guild.mc_action_receipts SET state='cancelled',cancel_requested=true,
                    lease_hash=NULL,lease_until=NULL,lease_mode=NULL,worker_id=NULL
                    WHERE principal_id=%s AND environment=%s AND operation_id=%s''', scope)
                kind = 'cancelled'
            else:
                cur.execute('''UPDATE guild.mc_action_receipts SET cancel_requested=true
                    WHERE principal_id=%s AND environment=%s AND operation_id=%s''', scope)
                kind = 'cancel_requested'
            self._event(cur, scope, kind)
            row, _ = self._row(cur, scope)
            return self._public(row)
