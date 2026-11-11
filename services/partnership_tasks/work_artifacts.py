"""Authenticated private working-document API, explicitly enabled by the host.

Uses the existing seat authentication and canonical owner-source records. These
are working drafts, never task approvals or authority to dispatch other work.
"""
from core.work_artifacts import Binding, Store
from services.partnership_tasks.service import StoreUnavailable

READ = {'find': {'query'}, 'read': {'artifact_id', 'revision'}}
SAVE = {'artifact_id', 'expected_revision', 'title', 'content', 'unresolved', 'source_event_id'}


def execute(svc, token, root, payload):
    if not isinstance(payload, dict) or set(payload) != {'operation', 'scope', 'arguments'}:
        raise ValueError('invalid work request')
    op, scope, args = payload['operation'], payload['scope'], payload['arguments']
    if op not in (*READ, 'save') or not isinstance(args, dict) or not isinstance(scope, str):
        raise ValueError('invalid work operation')
    allowed = SAVE if op == 'save' else READ[op]
    required = SAVE - {'artifact_id'} if op == 'save' else ({'query'} if op == 'find' else {'artifact_id'})
    if set(args) - allowed or required - set(args):
        raise ValueError('invalid work arguments')

    def authorize():
        b = svc.bindings.resolve(token, svc._clock())
        if (b is None or b.origin != 'agent' or b.seat not in ('mc', 'cos')
                or b.environment != svc.environment or scope not in b.read_scopes
                or ('save' if op == 'save' else 'read') not in b.work_artifacts
                or (op == 'save' and scope not in b.scopes)):
            raise PermissionError('work scope unavailable')
        return b

    bound = authorize()
    source_id = args.get('source_event_id')
    source_turn = 'canonical-read:' + bound.seat
    source = None
    if op == 'save':
        if not isinstance(source_id, str):
            raise ValueError('invalid source event')
        source = svc.get('source_event', source_id, token)
        expected_store = {'mc': 'mc-floor', 'cos': 'cos-history'}[bound.seat]
        if (source is None or source['producer'] != 'platform' or source['data_scope'] != scope
                or source['source']['store'] != expected_store):
            raise PermissionError('kept owner source required')
        source_turn = expected_store + ':' + source['source']['id']

    def validate(binding):
        current = authorize()
        if current != bound:
            raise PermissionError('work binding changed')
        if source is not None and svc.get('source_event', source_id, token) != source:
            raise PermissionError('work source changed')

    store = Store(root, Binding(bound.principal, bound.environment, scope, bound.seat, source_turn),
                  validate_source=validate)
    if op == 'find':
        return store.find(args['query'])
    if op == 'read':
        return store.read(args['artifact_id'], args.get('revision'))
    from core.canonical_json import digest
    # No model-selected operation identity; source and exact intent determine it.
    operation_id = 'save:' + digest([bound.seat, source_id, args])
    return store.save(**{k: args[k] for k in SAVE - {'source_event_id', 'artifact_id'}},
                      artifact_id=args.get('artifact_id'), operation_id=operation_id)
