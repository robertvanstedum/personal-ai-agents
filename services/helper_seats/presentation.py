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
        parts.append(readable(outcome))
    answer = '\n\n'.join(parts)
    if not answer or len(answer) > 7000:
        raise ValueError('accepted display exceeds bounded return')
    return dict(verdict='approve', reasons=REASONS, answer=answer), reviews[-1]['review_id']


# Readable display (Dev, 11 Nov 2026: Robert saw the raw JSON block in CoS's answer). Every field of the accepted outcome
# is kept, in a fixed order, so the lead's verbatim copy and the task service's check still compute the same text.
_FIRST = ("summary", "answer", "result", "found", "records", "items", "evidence", "searched", "not_searched",
          "limitations", "stopped", "note")
_ITEM_FIRST = ("title", "name", "path", "item_id", "status", "when")


def _label(key):
    return str(key).replace("_", " ").strip().capitalize() or "Value"


def _plain(value):
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if value is None:
        return "none"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _ordered(mapping, first):
    return sorted(mapping, key=lambda k: (first.index(k) if k in first else len(first), str(k)))


def readable(outcome):
    """The accepted outcome as short labelled lines and bullets, deterministically."""
    if not isinstance(outcome, dict):
        return _plain(outcome)
    lines = []
    for key in _ordered(outcome, _FIRST):
        value = outcome[key]
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            lines.append(_label(key) + ":")
            lines += ["- " + "; ".join(f"{_label(k)}: {_plain(item[k])}" for k in _ordered(item, _ITEM_FIRST)) for item in value]
        elif isinstance(value, list):
            lines.append(f"{_label(key)}: {', '.join(_plain(v) for v in value) or 'none'}")
        elif isinstance(value, dict):
            lines.append(_label(key) + ":")
            lines += [f"- {_label(k)}: {_plain(value[k])}" for k in _ordered(value, _ITEM_FIRST)]
        else:
            lines.append(f"{_label(key)}: {_plain(value)}")
    return "\n".join(lines)
