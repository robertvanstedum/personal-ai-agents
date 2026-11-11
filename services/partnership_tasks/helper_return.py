"""Service-only exact-result commit; no inference or network call under the journal lock.

A committed publication is already authorized work, so later task cancellation does
not delete it. Delivery is separate: the destination must validate current disclosure
access and the canonical source/conversation, then idempotently persist this exact
packet under the owner conversation lane. No destination is enabled by this module.
"""
from core.canonical_json import digest
from services.partnership_tasks.journal import Refused
from services.partnership_tasks.service import path_of, wake_problem, SCHEMAS


def commit(p):
    svc, b, payload = p.svc, p.b, p.p
    if not svc.helper_returns:
        raise Refused('not_authorized', 'helper_returns_disabled')
    if (not b.helper_return_commit or b.origin != 'agent' or b.seat not in ('mc', 'cos')
            or b.operations != frozenset({'task.commit_helper_return'})):
        raise Refused('not_authorized', 'dedicated_return_binding_required')
    # Check task visibility before the deterministic publication-existence test.
    p.same_scope(p.load('task', p.cmd['target']['id']), 'task')
    opid = 'OB-publish-' + payload['wake_operation_id'][8:]
    # Only the same journal command may replay. A second command cannot replace
    # an immutable intent, even if the task subsequently changes or is reopened.
    previous = svc.journal.read(path_of('outbox', opid))
    if previous is not None:
        p.same_scope(previous, 'outbox')
        raise Refused('id_taken', 'publication_already_committed')
    task = p.task_for_change()
    wake = p.load('outbox', payload['wake_operation_id'])
    p.same_scope(wake, 'outbox')
    if (wake['operation'] != 'wake_lead' or wake['destination'] != 'lead_wake'
            or wake['state'] not in ('attempted', 'unknown', 'acknowledged')
            or wake['task_id'] != task['task_id'] or p.effective_lead(task) != b.seat
            or wake_problem(svc, task, b.read_scopes)):
        raise Refused('fenced', 'wake_no_longer_current')
    current = task['artifact_sets'][-1]
    request = p.load('request', task['request_ref'])
    source = p.load('source_event', request['source_event_id'])
    p.same_scope(request, 'request')
    p.same_scope(source, 'source_event')
    expected = {'kind': 'helper_result_ready', 'task_id': task['task_id'], 'lead': b.seat,
                'source_ref': source['source'], 'source_event_id': source['source_event_id'],
                'result_digest': current['digest'], 'charter_ref': task['charter_ref'],
                'fence_generation': task['fence_generation'], 'environment': svc.environment,
                'data_scope': task['data_scope']}
    if (wake['packet'] != expected or wake['bundle_digest'] != digest(expected)
            or wake['operation_id'] != 'OB-wake-' + digest(expected)[7:47]
            or wake['fence_generation'] != task['fence_generation']
            or payload['result_digest'] != current['digest']):
        raise Refused('fenced', 'result_changed')
    # The host must still verify actual canonical ownership/privacy at delivery.
    # Here only a platform-recorded source may select a destination; model text
    # cannot supply a conversation ID or another source reference.
    store = {'mc': 'mc-floor', 'cos': 'cos-history'}[b.seat]
    source_ref = source['source']
    pieces = source_ref['id'].split(':')
    if (source['provenance']['origin'] != 'platform' or source_ref['store'] != store
            or len(pieces) != 2 or not all(pieces)):
        raise Refused('not_authorized', 'canonical_platform_source_required')
    review = {k: payload[k] for k in ('verdict', 'reasons', 'answer')}
    if any(not v.strip() for v in review.values()):
        raise Refused('payload_invalid', 'empty_review')
    review_id = 'RV-return-' + payload['wake_operation_id'][8:]
    packet = {'kind': 'helper_return_committed', 'wake_operation_id': wake['operation_id'],
              'task_id': task['task_id'], 'lead': b.seat, 'principal': b.principal,
              'environment': svc.environment, 'data_scope': task['data_scope'],
              'source_event_id': source['source_event_id'], 'source_ref': source_ref,
              'conversation_id': pieces[0], 'source_turn_id': pieces[1],
              'store': store, 'id': 'consult:' + opid, 'created_at': p.now,
              'result_digest': current['digest'], 'fence_generation': task['fence_generation'],
              'review': review, 'review_digest': digest(review), 'review_id': review_id,
              'accepted': review['verdict'] == 'approve', 'drafted_by': task['holder'],
              'text': review['answer'], 'committed_task_revision': task['revision'] + (2 if review['verdict'] == 'approve' else 1)}
    review_ref = {'store': 'task_service', 'id': opid, 'edition': '1', 'digest': digest(packet)}
    original = p.p
    try:
        p.p = {'review_id': review_id, 'reviewed_set_digest': current['digest'],
               'input_digest': current['digest'], 'verdict': review['verdict'],
               'review_ref': review_ref, 'findings_ref': review_ref}
        reviewed = p._record_task_review(task)
        updated = next(w.record for w in reviewed.writes if w.path == path_of('task', task['task_id']))
        if review['verdict'] == 'approve':
            # Never silently acknowledge somebody else's disagreement. The normal
            # acceptance rule refuses this commit if any unresolved dissent exists.
            p.p = {'artifact_set_digest': current['digest'], 'fence_generation': task['fence_generation'],
                   'acknowledged_disagreements': []}
            accepted = p._accept_task(updated)
            updated = next(w.record for w in accepted.writes if w.path == path_of('task', task['task_id']))
    finally:
        p.p = original
    ob = {'schema': SCHEMAS['outbox'], 'operation_id': opid, 'task_id': task['task_id'],
          'destination': 'lead_publication', 'operation': 'publish_return', 'bundle_digest': digest(packet),
          'packet': packet, 'bundle_ref': wake['bundle_ref'], 'fence_generation': task['fence_generation'],
          'interpretation_ref': task['interpretation_ref'], 'state': 'pending', 'attempts': [],
          'destination_receipt': None, 'reason': 'helper_return_committed'}
    return p.finish([(path_of('task', task['task_id']), p.checked('task', updated)),
                     (path_of('outbox', opid), p.checked('outbox', p.stamp(ob, None)))], updated['revision'])
