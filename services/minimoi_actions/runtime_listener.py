"""Dedicated, default-disabled trusted runtime listener, never a portal blueprint.

The binder, run initiator and executor have DISTINCT environment/instance-bound credentials.
Only the trusted dispatch adapter may register a run, before model/tool dispatch.
Tool calls cannot opportunistically bind themselves. Identity comes from the
server credential map; metadata headers do not select owner/actor/environment.

The supplied executor is a registered application dispatcher, NOT an arbitrary
model function. It must enforce current role/policy, capability/grant/resource
constraints and receipts inside the borrowed transaction. It must not commit,
close the transaction or perform external sends under this lock. This factory
is not wired into the app or enabled by its presence in the repository.

Deployment must protect the private listener with network isolation and TLS or
a trusted local channel, omit all public-proxy routes, redact credential/context
headers, configure bounded DB waits and rotate identities on runtime restart.
"""
from dataclasses import dataclass
import hashlib
import json
import re
import secrets

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from core.minimoi_actions.bindings import RuntimeCall, RuntimeIdentity
from core.minimoi_actions.context import ActionContractError
from core.minimoi_actions.receipts import _uuid
from core.minimoi_actions.requests import parse_tool_request, _object, _constant, _bounded_tree

START_PATH = '/internal/mc/v1/runtime/start'
BIND_PATH = '/internal/mc/v1/runtime/bind'
ACTION_PATH = '/internal/mc/v1/actions'
_BIND_FIELDS = {'schema', 'context_reference', 'session_key', 'run_id', 'dispatch_id', 'generation'}
_RESULT_FIELDS = {'version', 'operation_id', 'status', 'code', 'receipt_id', 'work_id',
                  'verified_state', 'links', 'confirmation'}
_STATUSES = {'done', 'accepted', 'needs_confirmation', 'needs_clarification',
             'refused', 'conflict', 'failed', 'unknown'}


@dataclass(frozen=True, slots=True, repr=False)
class RuntimePolicy:
    identity: RuntimeIdentity
    bind_token_sha256: str
    execute_token_sha256: str
    capabilities: frozenset[tuple[str, int]]
    start_token_sha256: str

    def __post_init__(self):
        if (type(self.identity) is not RuntimeIdentity
                or not all(type(x) is str and re.fullmatch('[0-9a-f]{64}', x)
                           for x in (self.bind_token_sha256, self.execute_token_sha256, self.start_token_sha256))
                or len({self.bind_token_sha256, self.execute_token_sha256, self.start_token_sha256}) != 3
                or type(self.capabilities) is not frozenset
                or len(self.capabilities) > 100):
            raise ValueError('runtime_listener_policy_invalid')
        for cap in self.capabilities:
            if (type(cap) is not tuple or len(cap) != 2 or type(cap[0]) is not str
                    or not re.fullmatch(r'[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+', cap[0])
                    or len(cap[0]) > 128 or type(cap[1]) is not int or not 1 <= cap[1] < 2**63):
                raise ValueError('runtime_listener_policy_invalid')


def _result_bytes(result):
    """Validate before commit so malformed/oversize local results roll back.

    Domain verification/privacy and link authorization belong to the dispatcher.
    Unknown evidence or generic exception is never converted to `done` here.
    """
    if (type(result) is not dict or set(result) != _RESULT_FIELDS or type(result['version']) is not int
            or result['version'] != 1 or not _uuid(result['operation_id'])
            or type(result['status']) is not str or result['status'] not in _STATUSES
            or type(result['code']) is not str or not re.fullmatch('[a-z][a-z0-9_]{0,79}', result['code'])
            or not _uuid(result['receipt_id'])
            or (result['work_id'] is not None and not _uuid(result['work_id']))
            or type(result['links']) is not list or len(result['links']) > 100
            or (result['verified_state'] is not None and type(result['verified_state']) is not dict)
            or (result['confirmation'] is not None and type(result['confirmation']) is not dict)):
        raise ValueError('runtime_result_invalid')
    _bounded_tree(result)
    encoded = json.dumps(result, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')
    if len(encoded) > 64*1024:
        raise ValueError('runtime_result_too_large')
    return encoded


def create_app(*, environment, policies, bindings, execute, enabled=False):
    if (environment not in ('dev', 'prod') or type(enabled) is not bool
            or type(policies) is not tuple or not policies or not callable(execute)
            or not all(type(p) is RuntimePolicy and p.identity.environment == environment for p in policies)):
        raise ValueError('runtime_listener_config_invalid')
    digests = [x for p in policies for x in (p.bind_token_sha256, p.execute_token_sha256, p.start_token_sha256)]
    if len(set(digests)) != len(digests):
        raise ValueError('runtime_listener_credential_collision')
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 64*1024

    def authenticate(role):
        if not enabled:
            return None, (jsonify(error='runtime_actions_disabled'), 503)
        header = request.headers.get('Authorization', '')
        if not header.startswith('Bearer ') or not 32 <= len(header[7:]) <= 256:
            return None, (jsonify(error='runtime_unauthorized'), 401)
        digest = hashlib.sha256(header[7:].encode('utf-8')).hexdigest()
        field = {'bind': 'bind_token_sha256', 'execute': 'execute_token_sha256',
                 'start': 'start_token_sha256'}[role]
        policy = next((p for p in policies if secrets.compare_digest(getattr(p, field), digest)), None)
        if policy is None:
            return None, (jsonify(error='runtime_unauthorized'), 401)
        return policy, None

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify(error='runtime_request_too_large'), 413

    @app.get('/healthz')
    def health():
        return jsonify(status='enabled' if enabled else 'disabled')

    @app.post(BIND_PATH)
    def bind():
        policy, failure = authenticate('bind')
        if failure:
            return failure
        if request.mimetype != 'application/json':
            return jsonify(error='json_required'), 415
        try:
            raw = request.get_data(cache=False)
            if len(raw) > 4096:
                return jsonify(error='binding_metadata_invalid'), 400
            body = json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_constant)
            if type(body) is not dict or set(body) != _BIND_FIELDS or body['schema'] != 'mc-runtime-bind/1':
                return jsonify(error='binding_metadata_invalid'), 400
            call = RuntimeCall(policy.identity, body['session_key'], body['run_id'], body['dispatch_id'], body['generation'])
            bindings.bind(body['context_reference'], call)
            return jsonify(schema='mc-runtime-bind-ack/1', status='bound'), 200
        except RequestEntityTooLarge:
            raise
        except (ActionContractError, ValueError, TypeError, UnicodeError, RecursionError):
            return jsonify(error='binding_refused'), 400
        except Exception:
            return jsonify(error='binding_unavailable'), 503

    @app.post(START_PATH)
    def start():
        # Only the trusted run initiator has this credential. In particular the
        # model-facing executor can neither bind nor consume a fresh dispatch.
        policy, failure = authenticate('start')
        if failure:
            return failure
        if request.mimetype != 'application/json':
            return jsonify(error='json_required'), 415
        try:
            raw = request.get_data(cache=False)
            if len(raw) > 4096:
                return jsonify(error='binding_metadata_invalid'), 400
            body = json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_constant)
            if type(body) is not dict or set(body) != _BIND_FIELDS or body['schema'] != 'mc-runtime-start/1':
                return jsonify(error='binding_metadata_invalid'), 400
            call = RuntimeCall(policy.identity, body['session_key'], body['run_id'], body['dispatch_id'], body['generation'])
            bindings.start(body['context_reference'], call)
            return jsonify(schema='mc-runtime-start-ack/1', status='started'), 200
        except RequestEntityTooLarge:
            raise
        except (ActionContractError, ValueError, TypeError, UnicodeError, RecursionError):
            return jsonify(error='runtime_start_refused'), 409
        except Exception:
            # The durable claim may have committed. Never convert this into
            # permission to start inference or automatically resend the request.
            return jsonify(error='runtime_start_unconfirmed'), 503

    @app.post(ACTION_PATH)
    def action():
        policy, failure = authenticate('execute')
        if failure:
            return failure
        if request.mimetype != 'application/json':
            return jsonify(error='json_required'), 415
        try:
            # These are written by the trusted plugin, never copied from its
            # arguments. Store lookup still verifies every field under lock.
            generation = request.headers.get('X-MC-Generation', '')
            if not re.fullmatch('[1-9][0-9]{0,18}', generation) or int(generation) >= 2**63:
                return jsonify(error='binding_metadata_invalid'), 400
            call = RuntimeCall(policy.identity, request.headers.get('X-MC-Session', ''),
                request.headers.get('X-MC-Run', ''), request.headers.get('X-MC-Dispatch', ''), int(generation))
            reference = request.headers.get('X-MC-Context', '')
            tool = parse_tool_request(request.get_data(cache=False))
            if (tool.operation, tool.operation_version) not in policy.capabilities:
                return jsonify(error='capability_unavailable'), 403
            with bindings.locked_context(reference, call) as (conn, context):
                encoded = _result_bytes(execute(conn, context, tool))
            return Response(encoded, status=200, mimetype='application/json')
        except RequestEntityTooLarge:
            raise
        except ActionContractError:
            return jsonify(error='action_refused'), 400
        except Exception:
            # A connection loss may hide a committed effect. Caller reconciles
            # the application-assigned operation ID; never auto-retries a write.
            return jsonify(error='action_outcome_unconfirmed'), 503

    return app
