"""Postgres registry for the trusted binding rules; not exposed as a model tool.

Apply sql/001_runtime_bindings.sql and sql/007_runtime_execution_start.sql explicitly. The admission adapter supplies a
monotonically increasing generation per runtime session (NOT the old turn
ledger's per-claim counter). The unique constraints and session row lock enforce
this across workers. Instances/references come from authenticated server code;
neither knowledge of a run ID nor a global runtime token is owner authority.

No listener or runtime adapter imports this yet. Execution also needs current
grant/policy checks and an operation receipt in the yielded transaction. Keep
terminal rows until a separately reviewed replay-retention policy permits GC.
"""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import re
import secrets

from .bindings import RunBinding, RuntimeCall, RuntimeIdentity, bind_run, check_bound_call
from .context import ActionContext, ActionContractError, _identifier

_REFERENCE = re.compile(r'[A-Za-z0-9_-]{43}')
_MAX_GENERATION = 2**63 - 1


class RegistryUnavailable(RuntimeError):
    """Fixed safe error, never the DSN or an upstream database body."""


@dataclass(frozen=True, slots=True, repr=False)
class RegisteredDispatch:
    dispatch_id: str
    context_reference: str


def _digest(reference: str) -> str:
    if type(reference) is not str or not _REFERENCE.fullmatch(reference):
        raise ActionContractError('binding_reference_invalid')
    return hashlib.sha256(reference.encode('ascii')).hexdigest()


def _scope(identity: RuntimeIdentity, session_key: str) -> tuple:
    if type(identity) is not RuntimeIdentity or not _identifier(session_key):
        raise ActionContractError('binding_scope_invalid')
    return identity.environment, identity.runtime_id, session_key


def _encode(context: ActionContext) -> str:
    value = asdict(context)
    value['expires_at'] = context.expires_at.isoformat()
    return json.dumps(value, separators=(',', ':'), ensure_ascii=True)


def _decode(row) -> RunBinding:
    value, runtime, instance, session, state, run_id = row
    try:
        value = dict(value)
        value['expires_at'] = datetime.fromisoformat(value['expires_at'])
        value['grant_ids'] = tuple(value['grant_ids'])
        context = ActionContext(**value)
        identity = RuntimeIdentity(runtime, instance, context.actor_id, context.environment)
        return RunBinding(context, identity, session, state, run_id)
    except (ValueError, TypeError, KeyError):
        raise RegistryUnavailable('binding_record_invalid') from None


class BindingStore:
    def __init__(self, connect):
        """Factory must return a fresh psycopg2 connection with autocommit off.

        Configure bounded connection/statement/lock timeouts in the factory.
        No default DATABASE_URL: deployment must choose the environment.
        """
        self._connect = connect

    @contextmanager
    def _transaction(self):
        import psycopg2
        try:
            conn = self._connect()
        except Exception:
            raise RegistryUnavailable('binding_store_unavailable') from None
        try:
            if conn.autocommit:
                raise RegistryUnavailable('binding_transaction_required')
            with conn:
                yield conn
        except psycopg2.IntegrityError:
            raise ActionContractError('binding_storage_conflict') from None
        except psycopg2.Error:
            raise RegistryUnavailable('binding_store_unavailable') from None
        finally:
            conn.close()

    def admit(self, context: ActionContext, identity: RuntimeIdentity,
              session_key: str) -> RegisteredDispatch:
        binding = RunBinding(context, identity, session_key)
        if context.generation > _MAX_GENERATION:
            raise ActionContractError('runtime_generation_invalid')
        scope = _scope(identity, session_key)
        reference = secrets.token_urlsafe(32)
        with self._transaction() as conn, conn.cursor() as cur:
            cur.execute('SELECT clock_timestamp()')
            if context.has_expired(cur.fetchone()[0]):
                raise ActionContractError('binding_expired')
            cur.execute('''INSERT INTO guild.mc_runtime_sessions
                (environment,runtime_id,session_key,principal_id,actor_id)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                scope + (context.principal_id, context.actor_id))
            cur.execute('''SELECT principal_id,actor_id,generation FROM guild.mc_runtime_sessions
                WHERE environment=%s AND runtime_id=%s AND session_key=%s FOR UPDATE''', scope)
            principal, actor, generation = cur.fetchone()
            if (principal, actor) != (context.principal_id, context.actor_id):
                raise ActionContractError('binding_session_owner_conflict')
            if context.generation <= generation:
                raise ActionContractError('binding_generation_conflict')
            # Expiry must be checked again AFTER waiting for the session lock.
            cur.execute('SELECT clock_timestamp()')
            if context.has_expired(cur.fetchone()[0]):
                raise ActionContractError('binding_expired')
            cur.execute('''UPDATE guild.mc_runtime_bindings SET state='revoked',
                updated_at=clock_timestamp() WHERE environment=%s AND runtime_id=%s
                AND session_key=%s AND state IN ('awaiting_run','bound')''', scope)
            cur.execute('''INSERT INTO guild.mc_runtime_bindings
                (dispatch_id,reference_hash,environment,runtime_id,instance_id,session_key,
                 generation,context,state,expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)''',
                (context.dispatch_id, _digest(reference), identity.environment,
                 identity.runtime_id, identity.instance_id, session_key, context.generation,
                 _encode(context), binding.state, context.expires_at))
            cur.execute('''UPDATE guild.mc_runtime_sessions SET generation=%s,dispatch_id=%s
                WHERE environment=%s AND runtime_id=%s AND session_key=%s''',
                (context.generation, context.dispatch_id) + scope)
        return RegisteredDispatch(context.dispatch_id, reference)

    @staticmethod
    def _locked(cur, reference: str, call: RuntimeCall):
        if type(call) is not RuntimeCall:
            raise ActionContractError('runtime_call_invalid')
        scope = _scope(call.identity, call.session_key)
        digest = _digest(reference)
        # All paths take session then binding locks, including cancellation.
        cur.execute('''SELECT dispatch_id,generation FROM guild.mc_runtime_sessions
            WHERE environment=%s AND runtime_id=%s AND session_key=%s FOR UPDATE''', scope)
        current = cur.fetchone()
        if current is None:
            raise ActionContractError('binding_not_registered')
        cur.execute('''SELECT context,runtime_id,instance_id,session_key,state,run_id
            FROM guild.mc_runtime_bindings WHERE reference_hash=%s
            AND environment=%s AND runtime_id=%s AND session_key=%s FOR UPDATE''',
            (digest,) + scope)
        row = cur.fetchone()
        if row is None:
            raise ActionContractError('binding_not_registered')
        cur.execute('SELECT clock_timestamp()')
        live = dict(now=cur.fetchone()[0], current_dispatch_id=current[0], current_generation=current[1])
        return _decode(row), live

    def bind(self, reference: str, call: RuntimeCall) -> None:
        """Dispatch adapter only, after authenticating the runtime response."""
        with self._transaction() as conn, conn.cursor() as cur:
            binding, live = self._locked(cur, reference, call)
            bound = bind_run(binding, call, **live)
            cur.execute('''UPDATE guild.mc_runtime_bindings SET state='bound',run_id=%s,
                updated_at=clock_timestamp() WHERE dispatch_id=%s''',
                (bound.run_id, bound.context.dispatch_id))

    def start(self, reference: str, call: RuntimeCall) -> None:
        """Consume a registered dispatch once, before starting inference.

        A lost acknowledgement is uncertain, not permission to retry. This
        durable claim is deliberately non-idempotent: reusing a run ID must not
        launch another agent loop, even after a process restart. A new owner
        turn requires a newly admitted generation. No tool endpoint calls this.
        """
        with self._transaction() as conn, conn.cursor() as cur:
            binding, live = self._locked(cur, reference, call)
            context = check_bound_call(binding, call, **live)
            cur.execute('''UPDATE guild.mc_runtime_bindings
                SET started_at=clock_timestamp(),updated_at=clock_timestamp()
                WHERE dispatch_id=%s AND started_at IS NULL RETURNING dispatch_id''',
                (context.dispatch_id,))
            if cur.fetchone() is None:
                raise ActionContractError('binding_already_started')

    @contextmanager
    def locked_context(self, reference: str, call: RuntimeCall):
        """Hold binding and session locks while preparing an authorized effect.

        Yield (connection, context) so grant checks and receipt preparation can
        share this transaction. Do not commit/rollback/close the connection in
        the caller. External effects require their separate prepared-intent
        protocol; this context manager does not make external writes atomic.
        """
        with self._transaction() as conn, conn.cursor() as cur:
            binding, live = self._locked(cur, reference, call)
            context = check_bound_call(binding, call, **live)
            cur.execute('SELECT started_at FROM guild.mc_runtime_bindings WHERE dispatch_id=%s',
                        (context.dispatch_id,))
            if cur.fetchone()[0] is None:
                raise ActionContractError('binding_not_started')
            yield conn, context

    def end(self, identity: RuntimeIdentity, session_key: str, dispatch_id: str,
            generation: int, *, cancelled: bool) -> bool:
        """Trusted admission/closeout only. Never undo or erase an effect."""
        scope = _scope(identity, session_key)
        if (not _identifier(dispatch_id) or type(generation) is not int
                or not 0 < generation <= _MAX_GENERATION or type(cancelled) is not bool):
            raise ActionContractError('binding_terminal_invalid')
        with self._transaction() as conn, conn.cursor() as cur:
            cur.execute('''SELECT actor_id FROM guild.mc_runtime_sessions
                WHERE environment=%s AND runtime_id=%s AND session_key=%s FOR UPDATE''', scope)
            session = cur.fetchone()
            if session is None:
                return False
            if session[0] != identity.actor_id:
                raise ActionContractError('binding_identity_mismatch')
            cur.execute('''UPDATE guild.mc_runtime_bindings SET state=%s,updated_at=clock_timestamp()
                WHERE environment=%s AND runtime_id=%s AND session_key=%s
                AND instance_id=%s AND dispatch_id=%s AND generation=%s
                AND state IN ('awaiting_run','bound') RETURNING dispatch_id''',
                ('revoked' if cancelled else 'closed',) + scope +
                (identity.instance_id, dispatch_id, generation))
            return cur.fetchone() is not None
