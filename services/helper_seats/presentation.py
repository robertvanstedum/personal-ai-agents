"""Deterministic display of an already accepted, exact helper result. No inference."""
import json
from core.canonical_json import digest

REASONS = 'The lead already approved and accepted this exact helper result.'


def accepted_result(task, lead, get, review_id=None):
    current = task['artifact_sets'][-1]
    reviews = [r for r in task['reviews'] if r['reviewer'] == lead
               and r['reviewed_set_digest'] == current['digest'] and r['verdict'] == 'approve'
               and (review_id is None or r['review_id'] == review_id)]
    if (not reviews or task['lead'] != lead or task['state'] != 'completed'
            or not any(a['artifact_set_digest'] == current['digest'] and a['status'] == 'current'
                       for a in task['acceptances'])):
        raise ValueError('exact accepted review required')
    parts = []
    for ref in current['artifacts']:
        row = get('source_event', ref['id']) if ref['store'] == 'task_service' else None
        if (row is None or row['environment'] != task['environment'] or row['data_scope'] != task['data_scope']
                or row['source']['store'] != 'helper-result' or row['provenance']['actor_seat'] != task['holder']
                or digest(row['content']) != ref['digest']):
            raise ValueError('verified helper result required')
        value = json.loads(row['content'])
        if value['drafted_by'] != task['holder']:
            raise ValueError('helper attribution differs')
        outcome = value['outcome']
        # Preserve every evidence/limitation field. Never silently shorten accepted evidence.
        parts.append(json.dumps(outcome, ensure_ascii=False, sort_keys=True, indent=2))
    answer = '\n\n'.join(parts)
    if not answer or len(answer) > 7000:
        raise ValueError('accepted display exceeds bounded return')
    return dict(verdict='approve', reasons=REASONS, answer=answer), reviews[-1]['review_id']
