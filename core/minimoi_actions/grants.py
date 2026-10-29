"""Finite exact-request grants, independent of memory or model self-approval.

This is persistence/enforcement of a TRUSTED compiler/owner-control result,
not an intent inference component. No tool or HTTP handler exposes issue().
The issuer must authenticate retained owner turns, resolve authorized resources,
and compile the exact canonical domain request under a reviewed template.
Quoted/pasted content and lessons cannot mint these objects. Natural-language
compilation and broader field-constrained templates are separate work, except
one narrow template: delegate.card (host runtime spec v0.5 R4.3-c, N1). One exact
owner approval of a card covers that card's enumerated steps, one operation each,
each named by its step template digest; a step's request is exactly that digest
plus input artifact digests, the only free field (card_step_template).

Borrow the binding transaction: binding/session -> grant -> receipt lock order.
prepare_effect consumes cardinality and prepares its receipt atomically. At the
actual dispatch boundary recheck_execution rechecks revocation/current policy,
scope, effect identity and expiry; it leaves its grant row lock held until commit.
Receipt execution transitions require GrantExecution and repeat that check.
Run the local domain operation in that transaction.
For an external send commit dispatch first, then reconcile all uncertainty.

These grants cover ZERO-SPEND product actions only. Paid capabilities and their
shared spend admission are deliberately refused until the budget seam exists.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import uuid
from types import MappingProxyType

from .context import ActionContext, ActionContractError, _aware, _identifier
from .receipts import EffectIntent, ReceiptStore, _cursor, _uuid

_DIGEST = re.compile('[0-9a-f]{64}')
_CAPABILITY = re.compile(r'[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+')
# R4.3-c (N1): the one capability whose single exact approval covers several operations, a card's enumerated steps.
CARD_CAPABILITY = 'delegate.card'
_CARD_STEP = {'step_template', 'input_artifacts'}
_ARTIFACT_REF = re.compile(r'[A-Za-z0-9._:-]{1,64}')


def card_step_template(request):
    """The step template digest a delegate.card request names, or None unless the request is exactly a compiled card
    step: {step_template: <digest>, input_artifacts: [{ref, sha256}, ...]} with at most 16 artifacts. The artifact
    digests are its only free field; which digests may fill it is the authority's to check (its own records)."""
    try:
        args = request.arguments
    except (AttributeError, ValueError):
        return None
    if type(args) is not dict or set(args) != _CARD_STEP:
        return None
    template, arts = args['step_template'], args['input_artifacts']
    if (type(template) is not str or not _DIGEST.fullmatch(template) or type(arts) is not list or len(arts) > 16
            or not all(type(a) is dict and set(a) == {'ref', 'sha256'} and type(a['ref']) is str
                       and _ARTIFACT_REF.fullmatch(a['ref']) and type(a['sha256']) is str
                       and _DIGEST.fullmatch(a['sha256']) for a in arts)):
        return None
    return template


def card_step_operation_id(approved_operation_id, step_template):
    """Each enumerated step of an approved card has one operation id, derived from the approval: covered once."""
    return str(uuid.uuid5(uuid.UUID(approved_operation_id), step_template))


@dataclass(frozen=True, slots=True)
class CapabilityRule:
    """Operator-installed catalogue, never supplied by a model or grant issuer."""
    version: int
    exact_approval_required: bool
    zero_spend: bool

    def __post_init__(self):
        if (type(self.version) is not int or not 1 <= self.version < 2**63
                or type(self.exact_approval_required) is not bool or type(self.zero_spend) is not bool):
            raise ActionContractError('grant_catalogue_invalid')


@dataclass(frozen=True, slots=True, repr=False)
class GrantDefinition:
    grant_id: str
    principal_id: str
    environment: str
    conversation_id: str
    owner_turn_ids: tuple[str, ...]
    actor_ids: tuple[str, ...]
    policy_version: str
    capability: str
    capability_version: int
    argument_digests: tuple[str, ...]
    max_operations: int
    expires_at: datetime
    work_id: str | None = None
    proposal_id: str | None = None
    approved_operation_id: str | None = None
    approval_expires_at: datetime | None = None
    expected_target_version: str | None = None

    def __post_init__(self):
        if (not _uuid(self.grant_id) or not all(_identifier(x) for x in
                (self.principal_id, self.conversation_id, self.policy_version))
                or type(self.environment) is not str or self.environment not in ('dev', 'prod')
                or type(self.capability) is not str or len(self.capability) > 128
                or not _CAPABILITY.fullmatch(self.capability)
                or type(self.capability_version) is not int or not 1 <= self.capability_version < 2**63
                or type(self.max_operations) is not int or not 1 <= self.max_operations <= 100
                or not _aware(self.expires_at)):
            raise ActionContractError('grant_definition_invalid')
        for values, limit in ((self.owner_turn_ids, 32), (self.actor_ids, 16)):
            if (type(values) is not tuple or not 1 <= len(values) <= limit
                    or not all(_identifier(x) for x in values) or len(set(values)) != len(values)):
                raise ActionContractError('grant_provenance_invalid')
        if (type(self.argument_digests) is not tuple or not 1 <= len(self.argument_digests) <= 100
                or not all(type(x) is str and _DIGEST.fullmatch(x) for x in self.argument_digests)
                or len(set(self.argument_digests)) != len(self.argument_digests)):
            raise ActionContractError('grant_constraints_invalid')
        if any(x is not None and not _identifier(x) for x in (self.work_id, self.expected_target_version)):
            raise ActionContractError('grant_scope_invalid')
        exact = (self.proposal_id, self.approved_operation_id, self.approval_expires_at)
        card = self.capability == CARD_CAPABILITY
        if card and (self.work_id is None or any(x is None for x in exact)):
            raise ActionContractError('grant_approval_invalid')   # one exact approval of one card (R4.3-c, R4.3-f)
        if any(x is not None for x in exact):
            # The exact-approval rule: one operation on one exact request. A card's approval covers its enumerated
            # steps instead: one operation per step template digest, at most 100 (R4.3-c).
            if (not _uuid(self.proposal_id) or not _uuid(self.approved_operation_id)
                    or not _aware(self.approval_expires_at)
                    or (self.max_operations != len(self.argument_digests) if card
                        else self.max_operations != 1 or len(self.argument_digests) != 1)):
                raise ActionContractError('grant_approval_invalid')

    @property
    def encoded(self):
        doc = asdict(self)
        doc['expires_at'] = self.expires_at.astimezone(timezone.utc).isoformat()
        if self.approval_expires_at is not None:
            doc['approval_expires_at'] = self.approval_expires_at.astimezone(timezone.utc).isoformat()
        for field in ('owner_turn_ids', 'actor_ids', 'argument_digests'):
            doc[field] = sorted(doc[field])
        return json.dumps(doc, sort_keys=True, separators=(',', ':'), ensure_ascii=True)

    @property
    def digest(self):
        return hashlib.sha256(self.encoded.encode()).hexdigest()


def _decode(value):
    try:
        value = dict(value)
        for field in ('owner_turn_ids', 'actor_ids', 'argument_digests'):
            value[field] = tuple(value[field])
        for field in ('expires_at', 'approval_expires_at'):
            if value[field] is not None:
                value[field] = datetime.fromisoformat(value[field])
        return GrantDefinition(**value)
    except (ValueError, KeyError, TypeError):
        raise ActionContractError('grant_record_invalid') from None


@dataclass(frozen=True, slots=True, repr=False)
class GrantExecution:
    """Trusted dispatch inputs, revalidated by every receipt transition.

    Not an authority cache: each check reads current durable grant state and
    holds its lock. Current policy is supplied by the trusted request adapter.
    Scope and intent are bound before acquiring the receipt lock.
    """
    store: 'GrantStore'
    context: ActionContext
    intent: EffectIntent
    current_policy_version: str

    def check(self, conn, scope):
        if (type(self.store) is not GrantStore or type(self.context) is not ActionContext
                or type(self.intent) is not EffectIntent
                or scope != (self.context.principal_id,self.context.environment,self.intent.operation_id)):
            raise ActionContractError('receipt_execution_scope_mismatch')
        self.store.recheck_execution(conn,self.context,self.intent,
            current_policy_version=self.current_policy_version)


class GrantStore:
    def __init__(self, catalogue=None):
        rules = dict(catalogue or {})
        if any(type(k) is not str or not _CAPABILITY.fullmatch(k) or type(v) is not CapabilityRule
               for k, v in rules.items()):
            raise ActionContractError('grant_catalogue_invalid')
        self._catalogue = MappingProxyType(rules)

    def _rule(self, grant):
        rule = self._catalogue.get(grant.capability)
        if rule is None or rule.version != grant.capability_version:
            raise ActionContractError('grant_capability_unavailable')
        if not rule.zero_spend:
            raise ActionContractError('grant_spend_not_supported')
        if rule.exact_approval_required and grant.proposal_id is None:
            raise ActionContractError('grant_exact_approval_required')

    @staticmethod
    def _locked(cur, grant_id, principal, environment):
        if (not _uuid(grant_id) or not _identifier(principal)
                or type(environment) is not str or environment not in ('dev', 'prod')):
            raise ActionContractError('grant_scope_invalid')
        cur.execute('''SELECT * FROM guild.mc_action_grants WHERE grant_id=%s
            AND principal_id=%s AND environment=%s FOR UPDATE''', (grant_id, principal, environment))
        row = cur.fetchone()
        if row is None:
            raise ActionContractError('grant_not_found')
        grant = _decode(row['definition'])
        if (grant.digest != row['definition_digest'] or grant.grant_id != grant_id
                or grant.principal_id != principal or grant.environment != environment
                or grant.expires_at != row['expires_at'] or grant.max_operations != row['max_operations']):
            raise ActionContractError('grant_record_invalid')
        cur.execute('SELECT clock_timestamp() AS now')
        return row, grant, cur.fetchone()['now']

    def issue(self, conn, grant: GrantDefinition):
        """Only the authenticated owner control / trusted policy compiler calls.

        Immutable ID replay cannot alter or reactivate a grant. No automatic
        lifetime renewal. Conversation grants <=24h; exact approval <=15min.
        An explicit work grant may have a separately owner-approved duration.
        """
        if type(grant) is not GrantDefinition:
            raise ActionContractError('grant_definition_invalid')
        self._rule(grant)
        with _cursor(conn) as cur:
            cur.execute('SELECT clock_timestamp() AS now')
            now = cur.fetchone()['now']
            if (grant.expires_at <= now or (grant.work_id is None and grant.expires_at > now+timedelta(hours=24))
                    or (grant.approval_expires_at is not None
                        and not now < grant.approval_expires_at <= now+timedelta(minutes=15))):
                raise ActionContractError('grant_lifetime_invalid')
            cur.execute('''INSERT INTO guild.mc_action_grants
                (grant_id,principal_id,environment,definition,definition_digest,expires_at,max_operations)
                VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s) ON CONFLICT DO NOTHING''',
                (grant.grant_id, grant.principal_id, grant.environment, grant.encoded,
                 grant.digest, grant.expires_at, grant.max_operations))
            row, persisted, _ = self._locked(cur, grant.grant_id, grant.principal_id, grant.environment)
            if persisted.digest != grant.digest:
                raise ActionContractError('grant_id_reused')
            if row['revoked_at'] is not None:
                raise ActionContractError('grant_revoked')

    def _check(self, row, grant, now, context, intent, current_policy_version):
        self._rule(grant)
        if row['revoked_at'] is not None:
            raise ActionContractError('grant_revoked')
        if context.has_expired(now) or grant.expires_at <= now:
            raise ActionContractError('grant_expired')
        if (not _identifier(current_policy_version) or current_policy_version != context.policy_version
                or current_policy_version != grant.policy_version):
            raise ActionContractError('grant_policy_changed')
        if (context.actor_id not in grant.actor_ids or intent.grant_id not in context.grant_ids
                or context.conversation_id != grant.conversation_id or context.work_id != grant.work_id):
            raise ActionContractError('grant_context_mismatch')
        card = grant.capability == CARD_CAPABILITY      # a card step is covered by its template digest (R4.3-c)
        covered = card_step_template(intent.request) if card else intent.request.arguments_sha256
        if (intent.request.operation != grant.capability or intent.request.operation_version != grant.capability_version
                or covered not in grant.argument_digests
                or intent.expected_target_version != grant.expected_target_version
                or intent.proposal_id != grant.proposal_id):
            raise ActionContractError('grant_effect_not_covered')
        if intent.reservation_ids:
            raise ActionContractError('grant_spend_not_supported')
        if grant.proposal_id is not None:
            approved = card_step_operation_id(grant.approved_operation_id, covered) if card \
                else grant.approved_operation_id
            if intent.operation_id != approved:
                raise ActionContractError('grant_approval_operation_mismatch')
            # A card's approval window bounds its admission, the first of its operations; later steps are that
            # admitted card's own.
            if grant.approval_expires_at <= now and not (card and row['consumed_operations'] > 0):
                raise ActionContractError('grant_approval_expired')

    @staticmethod
    def _inputs(context, intent):
        if type(context) is not ActionContext or type(intent) is not EffectIntent or not _uuid(intent.grant_id):
            raise ActionContractError('grant_request_invalid')

    def prepare_effect(self, conn, context, intent, *, current_policy_version):
        self._inputs(context, intent)
        with _cursor(conn) as cur:
            row, grant, now = self._locked(cur, intent.grant_id, context.principal_id, context.environment)
            self._check(row, grant, now, context, intent, current_policy_version)
            cur.execute('''SELECT intent_digest FROM guild.mc_action_grant_consumptions
                WHERE grant_id=%s AND operation_id=%s''', (grant.grant_id, intent.operation_id))
            consumed = cur.fetchone()
            if consumed is not None and consumed['intent_digest'] != intent.digest:
                raise ActionContractError('grant_operation_reused')
            if consumed is None and row['consumed_operations'] >= grant.max_operations:
                raise ActionContractError('grant_exhausted')
            receipt = ReceiptStore().prepare(conn, context, intent)
            if consumed is None:
                cur.execute('''INSERT INTO guild.mc_action_grant_consumptions
                    (grant_id,principal_id,environment,operation_id,intent_digest) VALUES (%s,%s,%s,%s,%s)''',
                    (grant.grant_id, context.principal_id, context.environment, intent.operation_id, intent.digest))
                cur.execute('''UPDATE guild.mc_action_grants SET consumed_operations=consumed_operations+1
                    WHERE grant_id=%s''', (grant.grant_id,))
            return receipt

    def recheck_execution(self, conn, context, intent, *, current_policy_version):
        """Call at execution, not only admission; transaction keeps the grant locked.

        No exception for an expired exact approval merely because a receipt was
        prepared earlier. Recovery reads of already-dispatched effects use the
        reconciler's separate read authority, never a renewed mutation grant.
        """
        self._inputs(context, intent)
        with _cursor(conn) as cur:
            row, grant, now = self._locked(cur, intent.grant_id, context.principal_id, context.environment)
            self._check(row, grant, now, context, intent, current_policy_version)
            cur.execute('''SELECT intent_digest FROM guild.mc_action_grant_consumptions
                WHERE grant_id=%s AND principal_id=%s AND environment=%s AND operation_id=%s''',
                (grant.grant_id, context.principal_id, context.environment, intent.operation_id))
            consumed = cur.fetchone()
            if consumed is None or consumed['intent_digest'] != intent.digest:
                raise ActionContractError('grant_effect_not_prepared')

    # Work items admitted under a grant (host runtime spec v0.5 R4.3): the delegation authority's admission and its
    # later rechecks. Both return a fixed code, or None, and hold the grant row lock until the caller commits.
    def covers_item(self, conn, principal, environment, grant_id, *, subject_digest, capability_prefix,
                    work_ids=()):
        """Read only: None while the grant is current (valid record, catalogue rule, not revoked, not expired) and
        covers the item: a capability under ``capability_prefix``, ``subject_digest`` among its argument digests,
        and, when the grant names a work id, one of ``work_ids``. Never uses an operation."""
        with _cursor(conn) as cur:
            return self._item_refusal(cur, principal, environment, grant_id, subject_digest, capability_prefix,
                                      work_ids)[0]

    def use_for_item(self, conn, principal, environment, grant_id, *, subject_digest, capability_prefix,
                     work_ids=()):
        """covers_item, an exact approval's window still open, then one of the grant's operations used, in the
        caller's transaction (grant_exhausted when none is left). The caller uses it once per newly written item, in
        that item's transaction, so a retry of the same item uses nothing more."""
        with _cursor(conn) as cur:
            code, row, grant, now = self._item_refusal(cur, principal, environment, grant_id, subject_digest,
                                                       capability_prefix, work_ids)
            if code is not None:
                return code
            if (grant.approval_expires_at is not None and grant.approval_expires_at <= now
                    and not (grant.capability == CARD_CAPABILITY and row['consumed_operations'] > 0)):
                return 'grant_approval_expired'       # a card's window bounds its first step's admission (R4.3-c)
            if row['consumed_operations'] >= grant.max_operations:
                return 'grant_exhausted'
            cur.execute('''UPDATE guild.mc_action_grants SET consumed_operations=consumed_operations+1
                WHERE grant_id=%s''', (grant.grant_id,))
            return None

    def _item_refusal(self, cur, principal, environment, grant_id, subject_digest, capability_prefix, work_ids):
        if (type(subject_digest) is not str or not _DIGEST.fullmatch(subject_digest)
                or type(capability_prefix) is not str or not capability_prefix.endswith('.')
                or type(work_ids) is not tuple or not all(_identifier(w) for w in work_ids)):
            raise ActionContractError('grant_request_invalid')
        try:
            row, grant, now = self._locked(cur, grant_id, principal, environment)
            self._rule(grant)
        except ActionContractError as exc:
            return str(exc), None, None, None
        if row['revoked_at'] is not None:
            code = 'grant_revoked'
        elif grant.expires_at <= now:
            code = 'grant_expired'
        elif not grant.capability.startswith(capability_prefix) or subject_digest not in grant.argument_digests:
            code = 'grant_effect_not_covered'
        elif grant.work_id is not None and grant.work_id not in work_ids:
            code = 'grant_context_mismatch'
        else:
            code = None
        return code, row, grant, now

    def revoke(self, conn, principal, environment, grant_id):
        """Authenticated owner/cancellation control only; replay is harmless."""
        with _cursor(conn) as cur:
            self._locked(cur, grant_id, principal, environment)
            cur.execute('''UPDATE guild.mc_action_grants SET revoked_at=COALESCE(revoked_at,clock_timestamp())
                WHERE grant_id=%s''', (grant_id,))
