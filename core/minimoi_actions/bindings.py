"""Pure trusted-run binding rules for harness §3.2/§15.

This is the decision model, NOT a registry or an authentication mechanism.
The service must load these values from its durable store under the same lock
as the admission/receipt transition. Never build them from tool arguments.
An authenticated wrapper supplies run/session identity; a model cannot bind a
run by making its first tool call. Durable uniqueness, tombstone retention and
the pinned runtime adapter are separate integration requirements.
"""

from dataclasses import dataclass, replace
from datetime import datetime

from .context import ActionContext, ActionContractError, _identifier


@dataclass(frozen=True, slots=True, repr=False)
class RuntimeIdentity:
    """Verified service identity plus a trusted boot/connection epoch.

    A restart uses a new instance_id; old bindings must not be resumed by a
    service merely possessing the same long-lived credential. The auth layer
    determines these fields, never headers copied without authentication.
    """

    runtime_id: str
    instance_id: str
    actor_id: str
    environment: str

    def __post_init__(self) -> None:
        if not all(_identifier(v) for v in (self.runtime_id, self.instance_id, self.actor_id)):
            raise ActionContractError('runtime_identity_invalid')
        if type(self.environment) is not str or self.environment not in {'dev', 'prod'}:
            raise ActionContractError('runtime_environment_invalid')


@dataclass(frozen=True, slots=True, repr=False)
class RuntimeCall:
    """Metadata attached by the authenticated wrapper outside model arguments."""

    identity: RuntimeIdentity
    session_key: str
    run_id: str
    dispatch_id: str
    generation: int

    def __post_init__(self) -> None:
        if type(self.identity) is not RuntimeIdentity:
            raise ActionContractError('runtime_identity_invalid')
        if not all(_identifier(v) for v in (self.session_key, self.run_id, self.dispatch_id)):
            raise ActionContractError('runtime_call_invalid')
        if type(self.generation) is not int or self.generation < 1:
            raise ActionContractError('runtime_generation_invalid')


@dataclass(frozen=True, slots=True, repr=False)
class RunBinding:
    context: ActionContext
    identity: RuntimeIdentity
    session_key: str
    state: str = 'awaiting_run'
    run_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.context) is not ActionContext or type(self.identity) is not RuntimeIdentity:
            raise ActionContractError('binding_context_invalid')
        if (self.context.actor_id != self.identity.actor_id
                or self.context.environment != self.identity.environment):
            raise ActionContractError('binding_identity_mismatch')
        if not _identifier(self.session_key):
            raise ActionContractError('binding_session_invalid')
        if type(self.state) is not str or self.state not in {'awaiting_run', 'bound', 'revoked', 'closed'}:
            raise ActionContractError('binding_state_invalid')
        if self.run_id is not None and not _identifier(self.run_id):
            raise ActionContractError('binding_run_invalid')
        if ((self.state == 'bound' and self.run_id is None)
                or (self.state == 'awaiting_run' and self.run_id is not None)):
            raise ActionContractError('binding_state_invalid')


def _live(binding: RunBinding, *, now: datetime, current_dispatch_id: str,
          current_generation: int) -> None:
    if type(binding) is not RunBinding:
        raise ActionContractError('binding_context_invalid')
    if not _identifier(current_dispatch_id) or type(current_generation) is not int or current_generation < 1:
        raise ActionContractError('binding_current_invalid')
    if (binding.context.dispatch_id != current_dispatch_id
            or binding.context.generation != current_generation):
        raise ActionContractError('binding_superseded')
    if binding.state in {'revoked', 'closed'}:
        raise ActionContractError('binding_inactive')
    if binding.context.has_expired(now):
        raise ActionContractError('binding_expired')


def bind_run(binding: RunBinding, call: RuntimeCall, *, now: datetime,
             current_dispatch_id: str, current_generation: int) -> RunBinding:
    """Trusted dispatch adapter only: register its authenticated runtime ID.

    The durable store must enforce one runtime/run pair per dispatch and CAS
    the returned state. Retrying the *identical* registration is idempotent;
    rebinding to a different run is never allowed. A tool endpoint must not
    call this function as a fallback for an absent binding.
    """
    _live(binding, now=now, current_dispatch_id=current_dispatch_id,
          current_generation=current_generation)
    _same_dispatch(binding, call)
    if binding.run_id is not None and binding.run_id != call.run_id:
        raise ActionContractError('binding_run_conflict')
    return replace(binding, state='bound', run_id=call.run_id)


def _same_dispatch(binding: RunBinding, call: RuntimeCall) -> None:
    if type(call) is not RuntimeCall:
        raise ActionContractError('runtime_call_invalid')
    if (binding.identity != call.identity or binding.session_key != call.session_key
            or binding.context.dispatch_id != call.dispatch_id
            or binding.context.generation != call.generation):
        raise ActionContractError('binding_call_mismatch')


def check_bound_call(binding: RunBinding, call: RuntimeCall, *, now: datetime,
                     current_dispatch_id: str, current_generation: int) -> ActionContext:
    """Return the stored context only for an exact, still-live bound call.

    This does not approve the operation. The executor must also check current
    grants, policy, role, scope, budget and cancellation in its transaction.
    Checking a cached snapshot before a later effect is not sufficient.
    """
    _live(binding, now=now, current_dispatch_id=current_dispatch_id,
          current_generation=current_generation)
    _same_dispatch(binding, call)
    if binding.state != 'bound' or binding.run_id != call.run_id:
        raise ActionContractError('binding_not_registered')
    return binding.context


def end_binding(binding: RunBinding, *, cancelled: bool) -> RunBinding:
    """Keep identity/run tombstones; neither close nor cancel undoes effects."""
    if type(binding) is not RunBinding or type(cancelled) is not bool:
        raise ActionContractError('binding_terminal_invalid')
    if binding.state in {'revoked', 'closed'}:
        return binding
    return replace(binding, state='revoked' if cancelled else 'closed')
