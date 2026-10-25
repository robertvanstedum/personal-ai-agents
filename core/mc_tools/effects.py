"""Every tool effect runs through C06 receipts under a C07 grant (design §4).

Two kinds of authority, both issued by trusted platform code and never by the model:

* a **turn grant**: the tool service issues it for exactly one proposal-class request, owned by the
  live ledger turn and expiring with its lease. The owner's turn authorizes MC to *ask*;
* an **exact-approval grant**: issued only by the owner-command path when the owner replies Confirm
  in the same conversation, for the exact request and target MC proposed.

``Effect`` walks a receipt from prepare to finish. A local database effect runs all steps in one
transaction. An effect outside the database (a file ledger, an account store, a job) commits its
dispatched receipt first and finishes in a second transaction, and an uncertain outcome is
``unknown``, never retried.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.grants import CapabilityRule, GrantDefinition, GrantExecution, GrantStore
from core.minimoi_actions.receipts import EffectIntent, EffectLease, ReceiptStore, _cursor
from core.minimoi_actions.requests import ToolRequest

from .binding import TurnBinding

# Proposals: the owner's live turn is enough (zero spend, no exact approval).
PROPOSAL_CAPABILITIES = ("guest.propose_reject", "issue.propose_comment", "issue.propose_data_repair",
                         "lessons.propose", "delegate.propose_research", "access.propose_invite")
# Consequential effects: one exact owner approval each. delegate.start_research queues a capped request;
# spend admission stays with the delegation runner, which enforces the job's cap or refuses it.
EXACT_CAPABILITIES = ("lessons.adopt", "guest.reject_request", "issue.comment", "issue.create_data_repair", "delegate.start_research",
                      "access.invite_user")
# Tier 2 (spec §8.2): ordinary private actions with read-back and undo, done at once under the owner-approved
# installation policy for the current owner request; no exact approval, zero spend.
STANDING_CAPABILITIES = ("host.career.synthetic", "agent.contact.existing", "agent.watch", "host.artifacts.create", "host.conversation.export", "lessons.keep_preference", "lessons.revoke_preference", "work.record_gap", "work.withdraw_gap",
                         "guild.board.note.create", "guild.board.note.update", "guild.board.operation.undo")
CATALOGUE = {**{c: CapabilityRule(1, False, True) for c in PROPOSAL_CAPABILITIES + STANDING_CAPABILITIES},
             **{c: CapabilityRule(1, True, True) for c in EXACT_CAPABILITIES}}
GRANTS = GrantStore(CATALOGUE)
RECEIPTS = ReceiptStore()


def request(capability: str, arguments: dict) -> ToolRequest:
    """The canonical request for one effect: its digest is what a grant names."""
    return ToolRequest(capability, 1, json.dumps(arguments, sort_keys=True, separators=(",", ":"),
                                                 ensure_ascii=False).encode("utf-8"))


def record_evidence(conn, context: ActionContext, operation_id: str, kind: str, outcome: dict) -> str:
    """A content-free outcome row; the receipt references only its id."""
    evidence_id = str(uuid4())
    with _cursor(conn) as cur:
        cur.execute("""INSERT INTO guild.mc_tool_evidence (evidence_id, principal_id, environment, operation_id, kind, outcome)
            VALUES (%s,%s,%s,%s,%s,%s::jsonb)""",
            (evidence_id, context.principal_id, context.environment, operation_id, kind, json.dumps(outcome)))
    return evidence_id


@dataclass(frozen=True, slots=True, repr=False)
class Effect:
    context: ActionContext
    intent: EffectIntent
    authority: GrantExecution
    lease: EffectLease

    @property
    def operation_id(self) -> str:
        return self.intent.operation_id

    def finish(self, conn, kind: str, outcome: dict, *, verified: bool) -> dict:
        """Record evidence and finish verified or failed; returns the receipt's public fields."""
        evidence_id = record_evidence(conn, self.context, self.operation_id, kind, outcome)
        RECEIPTS.finish(conn, self.lease, "verified" if verified else "failed", evidence_refs=(evidence_id,),
                        authority=self.authority)
        return RECEIPTS.read(conn, self.context.principal_id, self.context.environment, self.operation_id)

    def unknown(self, conn) -> None:
        RECEIPTS.finish(conn, self.lease, "unknown")


def _dispatch(conn, context, intent, worker) -> Effect:
    authority = GrantExecution(GRANTS, context, intent, context.policy_version)
    lease = RECEIPTS.claim(conn, context.principal_id, context.environment, intent.operation_id, worker,
                           authority=authority)
    RECEIPTS.dispatch(conn, lease, authority=authority)
    return Effect(context, intent, authority, lease)


def begin_turn_effect(conn, binding: TurnBinding, req: ToolRequest, *, worker: str) -> Effect:
    """Issue the turn grant for exactly ``req``, prepare its receipt and dispatch it, in ``conn``'s
    transaction (the caller holds the binding's ledger lock in the same transaction)."""
    if req.operation not in PROPOSAL_CAPABILITIES:
        raise ActionContractError("tool_capability_not_turn_grantable")
    grant_id, operation_id = str(uuid4()), str(uuid4())
    GRANTS.issue(conn, GrantDefinition(grant_id, binding.principal, binding.environment, binding.conversation_id,
                                       (binding.turn_id,), ("mc",), binding.policy_version, req.operation,
                                       req.operation_version, (req.arguments_sha256,), 1, binding.lease_until))
    context = binding.context((grant_id,))
    intent = EffectIntent(operation_id, req, grant_id=grant_id)
    GRANTS.prepare_effect(conn, context, intent, current_policy_version=binding.policy_version)
    return _dispatch(conn, context, intent, worker)


def begin_standing_effect(conn, binding: TurnBinding, req: ToolRequest, *, operation_id: str, worker: str) -> Effect:
    """Tier 2: a single-operation grant for exactly ``req``, owned by the live owner turn and its policy version, then
    the receipt prepared and dispatched under the deterministic ``operation_id`` (a retry reuses it), in ``conn``'s
    transaction. The caller commits before the file-backed effect and finishes after read-back (spec §10.1)."""
    if req.operation not in STANDING_CAPABILITIES:
        raise ActionContractError("tool_capability_not_standing")
    grant_id = str(uuid4())
    GRANTS.issue(conn, GrantDefinition(grant_id, binding.principal, binding.environment, binding.conversation_id,
                                       (binding.turn_id,), ("mc",), binding.policy_version, req.operation,
                                       req.operation_version, (req.arguments_sha256,), 1, binding.lease_until))
    context = binding.context((grant_id,))
    intent = EffectIntent(operation_id, req, grant_id=grant_id)
    GRANTS.prepare_effect(conn, context, intent, current_policy_version=binding.policy_version)
    return _dispatch(conn, context, intent, worker)


def receipt_exists(conn, principal, environment, operation_id) -> bool:
    """Whether this operation already has a receipt (a retry of the same request)."""
    try:
        RECEIPTS.read(conn, principal, environment, operation_id)
        return True
    except ActionContractError:
        return False


def reconcile_effect(conn, *, principal, environment, operation_id, worker) -> EffectLease | None:
    """A dispatched or unknown receipt whose domain write may have happened: a reconcile lease to finish it from
    read-back (never a renewed grant, never a second write), or None when it is not waiting for that."""
    try:
        return RECEIPTS.claim(conn, principal, environment, operation_id, worker, mode="reconcile")
    except ActionContractError:
        return None


def issue_exact(conn, *, grant_id, principal, environment, conversation_id, owner_turn_ids, req, proposal_id,
                operation_id, execution_deadline, policy_version, expected_target_version=None) -> None:
    """Owner-command path only, after the owner's Confirm: the one-time exact approval. Its approval and its lifetime
    both end at the persisted execution deadline (spec §9.2), never at the offer's expiry plus a margin."""
    approval_expires_at = execution_deadline
    GRANTS.issue(conn, GrantDefinition(grant_id, principal, environment, conversation_id, tuple(owner_turn_ids),
                                       ("mc",), policy_version, req.operation, req.operation_version,
                                       (req.arguments_sha256,), 1, execution_deadline, proposal_id=proposal_id,
                                       approved_operation_id=operation_id, approval_expires_at=approval_expires_at,
                                       expected_target_version=expected_target_version))


def prepare_exact(conn, context: ActionContext, intent: EffectIntent) -> None:
    GRANTS.prepare_effect(conn, context, intent, current_policy_version=context.policy_version)


def dispatch_exact(conn, context: ActionContext, intent: EffectIntent, *, worker: str) -> Effect:
    return _dispatch(conn, context, intent, worker)
