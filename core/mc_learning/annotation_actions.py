"""Annotation DB-intent/file/receipt bridge, disabled until explicitly wired.

Only authenticated owner-control/trusted intent compilation may use prepare.
An annotation template includes server-issued ULID/time, exact target revision
and owner attribution. Quoted text, model arguments and lessons cannot create
this authority. A finite exact approval is required; no grant issuer lives here.

Transactions: prepare (payload + grant consumption + receipt); begin (durable
dispatch); COMMIT; execute in a new transaction (current grants + file readback
+ evidence + receipt). A DB transaction-id barrier refuses file writes before
dispatch commit. If a file lands but its receipt transaction rolls back, only
reconcile may verify that exact already-published record. Reconcile does not
write/repair files or renew a grant. Pending publication needs explicit current
mutation authority; uncertainty never causes an automatic re-dispatch.

Methods borrow the caller's connection and never commit. The caller must roll
back any exception and preserve its retry pointer. No application DB is chosen.
"""
from uuid import uuid4

from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.grants import GrantStore, GrantExecution
from core.minimoi_actions.receipts import EffectIntent, EffectLease, ReceiptStore, _cursor, _uuid
from core.workshop_journal.errors import InvalidInput
from .annotation_shelf import AnnotationShelf
from .corrections import Annotation
from .statement_journal import _encode, _hash, _date

CAPABILITY = 'learning.annotate_statement'
_DUMMY = '00000000-0000-4000-8000-000000000000'


def _context(cur, context, *, transaction=False):
    if type(context) is not ActionContext or context.privacy_mode not in ('recorded','on_record'):
        raise ActionContractError('annotation_action_context_refused')
    cur.execute('SELECT clock_timestamp() AS now'+(', txid_current() AS transaction_id' if transaction else ''))
    row = cur.fetchone()
    if context.has_expired(row['now']):
        raise ActionContractError('annotation_action_context_expired')
    return row


def _annotation(payload):
    try:
        return Annotation(1,_encode(payload))
    except (InvalidInput,ValueError,TypeError):
        raise ActionContractError('annotation_action_payload_invalid') from None


class AnnotationActions:
    def __init__(self, grants, shelf):
        if type(grants) is not GrantStore or type(shelf) is not AnnotationShelf:
            raise ActionContractError('annotation_action_config_invalid')
        self.grants,self.shelf,self.receipts = grants,shelf,ReceiptStore()

    def _template(self, context, intent):
        if (type(context) is not ActionContext or type(intent) is not EffectIntent
                or intent.request.operation != CAPABILITY or intent.request.operation_version != 1
                or not _uuid(intent.proposal_id) or intent.expected_target_version is None):
            raise ActionContractError('annotation_exact_owner_action_required')
        payload = intent.request.arguments
        if 'owner_receipt_id' in payload:
            raise ActionContractError('annotation_receipt_is_server_assigned')
        annotation = _annotation(payload|{'owner_receipt_id':_DUMMY})
        p = annotation.data
        if ((p['principal_id'],p['environment'],p['scope_id']) !=
                (context.principal_id,context.environment,self.shelf.journal.scope)
                or (context.principal_id,context.environment) !=
                (self.shelf.journal.principal,self.shelf.journal.environment)
                or p['actor_id'] != context.principal_id
                or p['base_revision'] != intent.expected_target_version
                or annotation.digest != intent.request.arguments_sha256):
            raise ActionContractError('annotation_action_binding_refused')
        return payload

    def prepare(self, conn, context, intent, *, current_policy_version):
        payload = self._template(context,intent)
        with _cursor(conn) as cur:
            now = _context(cur,context)['now']
            if _date(payload['recorded_at']) > now:
                raise ActionContractError('annotation_action_time_invalid')
        receipt = self.grants.prepare_effect(conn,context,intent,current_policy_version=current_policy_version)
        payload['owner_receipt_id'] = str(receipt['receipt_id'])
        with _cursor(conn) as cur:
            cur.execute('''INSERT INTO guild.mc_owner_annotation_intents
                (principal_id,environment,operation_id,receipt_id,payload,payload_digest,evidence_id)
                VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s) ON CONFLICT DO NOTHING''',
                (context.principal_id,context.environment,intent.operation_id,str(receipt['receipt_id']),
                 _encode(payload).decode(),intent.request.arguments_sha256,str(uuid4())))
            saved,_,_ = self._load(cur,context,intent.operation_id,intent=intent)
            if saved['payload'] != payload:
                raise ActionContractError('annotation_action_intent_conflict')
        return receipt

    def _load(self, cur, context, operation_id, *, intent=None):
        if not _uuid(operation_id):
            raise ActionContractError('annotation_action_id_invalid')
        scope = (context.principal_id,context.environment,operation_id)
        # All callers acquire any grant lock BEFORE this receipt lock.
        receipt,now = self.receipts._row(cur,scope)
        clock = _context(cur,context,transaction=True)
        cur.execute('''SELECT * FROM guild.mc_owner_annotation_intents
            WHERE principal_id=%s AND environment=%s AND operation_id=%s FOR UPDATE''',scope)
        saved = cur.fetchone()
        if saved is None:
            raise ActionContractError('annotation_action_not_prepared')
        annotation = _annotation(saved['payload']); p = annotation.data
        if (str(saved['receipt_id']) != str(receipt['receipt_id']) or p['owner_receipt_id'] != str(receipt['receipt_id'])
                or saved['payload_digest'] != annotation.digest or receipt['request_digest'] != annotation.digest
                or receipt['capability'] != CAPABILITY or receipt['capability_version'] != 1
                or receipt['proposal_id'] is None or p['actor_id'] != context.principal_id
                or (p['principal_id'],p['environment'],p['scope_id']) !=
                   (context.principal_id,context.environment,self.shelf.journal.scope)
                or (context.principal_id,context.environment) !=
                   (self.shelf.journal.principal,self.shelf.journal.environment)
                or receipt['expected_target_version'] != p['base_revision']
                or _date(p['recorded_at']) > now):
            raise ActionContractError('annotation_action_evidence_invalid')
        if intent is not None and (receipt['intent_digest'] != intent.digest
                                   or annotation.digest != intent.request.arguments_sha256):
            raise ActionContractError('annotation_action_intent_conflict')
        return saved,receipt,clock

    def begin(self, conn, context, intent, worker_id, *, current_policy_version):
        self._template(context,intent)
        self.grants.recheck_execution(conn,context,intent,current_policy_version=current_policy_version)
        with _cursor(conn) as cur:
            saved,_,clock = self._load(cur,context,intent.operation_id,intent=intent)
        authority = GrantExecution(self.grants,context,intent,current_policy_version)
        lease = self.receipts.claim(conn,context.principal_id,context.environment,intent.operation_id,worker_id,authority=authority)
        self.receipts.dispatch(conn,lease,authority=authority)
        with _cursor(conn) as cur:
            cur.execute('''UPDATE guild.mc_owner_annotation_intents SET dispatch_transaction=%s
                WHERE principal_id=%s AND environment=%s AND operation_id=%s''',
                (clock['transaction_id'],context.principal_id,context.environment,intent.operation_id))
        return lease

    def _execution(self, conn, context, intent, lease, policy):
        if (type(lease) is not EffectLease or lease.mode != 'execute'
                or (lease.principal_id,lease.environment,lease.operation_id) !=
                   (context.principal_id,context.environment,intent.operation_id)):
            raise ActionContractError('annotation_execution_lease_invalid')
        self.grants.recheck_execution(conn,context,intent,current_policy_version=policy)
        with _cursor(conn) as cur:
            saved,receipt,clock = self._load(cur,context,intent.operation_id,intent=intent)
            self.receipts._leased(cur,lease)
        if receipt['state'] != 'dispatched' or receipt['cancel_requested']:
            raise ActionContractError('annotation_dispatch_refused')
        if saved['dispatch_transaction'] is None or saved['dispatch_transaction'] == clock['transaction_id']:
            raise ActionContractError('annotation_dispatch_commit_required')
        return saved,clock['now']

    def execute(self, conn, context, intent, lease, *, current_policy_version, can_read_statement):
        self._template(context,intent)
        saved,now = self._execution(conn,context,intent,lease,current_policy_version)
        def authorize(ctx, payload, digest):
            current,_ = self._execution(conn,ctx,intent,lease,current_policy_version)
            return current['payload'] == payload and current['payload_digest'] == digest
        annotation = self.shelf.publish(_encode(saved['payload']),context=context,now=now,
            can_read_statement=can_read_statement,authorize_owner_annotation=authorize)
        return self._finish(conn,lease,saved,annotation,
            authority=GrantExecution(self.grants,context,intent,current_policy_version))

    def reconcile(self, conn, context, lease, *, can_read_statement):
        if (type(context) is not ActionContext or type(lease) is not EffectLease or lease.mode != 'reconcile'
                or (context.principal_id,context.environment) != (lease.principal_id,lease.environment)):
            raise ActionContractError('annotation_reconcile_lease_invalid')
        with _cursor(conn) as cur:
            saved,receipt,clock = self._load(cur,context,lease.operation_id)
            self.receipts._leased(cur,lease)
        if receipt['state'] not in ('dispatched','unknown'):
            raise ActionContractError('annotation_reconcile_state_invalid')
        snapshot = self.shelf.snapshot(context=context,now=clock['now'],can_read_statement=can_read_statement)
        annotation = next((a for a in snapshot.annotations if a.data == saved['payload']),None)
        if annotation is None:
            raise ActionContractError('annotation_publication_unverified')
        return self._finish(conn,lease,saved,annotation)

    def _finish(self, conn, lease, saved, annotation, *, authority=None):
        if annotation.data != saved['payload'] or annotation.digest != saved['payload_digest']:
            raise ActionContractError('annotation_readback_mismatch')
        with _cursor(conn) as cur:
            cur.execute('''UPDATE guild.mc_owner_annotation_intents
                SET verified_sequence=%s,verified_payload_hash=%s
                WHERE principal_id=%s AND environment=%s AND operation_id=%s''',
                (annotation.sequence,_hash(annotation.encoded),lease.principal_id,lease.environment,lease.operation_id))
        self.receipts.finish(conn,lease,'verified',evidence_refs=(str(saved['evidence_id']),),authority=authority)
        return self.receipts.read(conn,lease.principal_id,lease.environment,lease.operation_id)

    def verify_completed(self, conn, context, payload, digest, sequence):
        """Projection callback: scoped, exact, read-only proof; no grant renewal.

        Current source access is a separate projection requirement. DB outage
        raises rather than falsely showing zero confirmed annotations.
        """
        if type(context) is not ActionContext or type(sequence) is not int or sequence < 1:
            return False
        annotation = _annotation(payload); p = annotation.data
        if (annotation.digest != digest or p['owner_receipt_id'] is None
                or (p['principal_id'],p['environment'],p['scope_id']) !=
                   (context.principal_id,context.environment,self.shelf.journal.scope)
                or p['actor_id'] != context.principal_id):
            return False
        with _cursor(conn) as cur:
            _context(cur,context)
            cur.execute('''SELECT i.*,r.state,r.request_digest,r.capability,r.capability_version,
                    r.expected_target_version,r.evidence_refs,r.verified_at,r.proposal_id
                FROM guild.mc_owner_annotation_intents i JOIN guild.mc_action_receipts r
                  ON (r.principal_id,r.environment,r.operation_id,r.receipt_id)=
                     (i.principal_id,i.environment,i.operation_id,i.receipt_id)
                WHERE i.principal_id=%s AND i.environment=%s AND i.receipt_id=%s''',
                (context.principal_id,context.environment,p['owner_receipt_id']))
            row = cur.fetchone()
        return bool(row and row['payload'] == p and row['payload_digest'] == digest
            and row['request_digest'] == digest and row['capability'] == CAPABILITY and row['capability_version'] == 1
            and row['state'] == 'verified' and row['verified_at'] is not None and row['proposal_id'] is not None
            and row['expected_target_version'] == p['base_revision'] and row['verified_sequence'] == sequence
            and row['verified_payload_hash'] == _hash(annotation.encoded)
            and str(row['evidence_id']) in row['evidence_refs'])
