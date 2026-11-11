"""Retained lead-review reasons in the existing task service, never a phantom file ref."""
from core.canonical_json import digest
from services.partnership_tasks.client import command


def evidence(task_id, review_id, reasons, *, environment, scope, runtime):
    if not isinstance(reasons, str) or not reasons.strip() or len(reasons) > 8000:
        raise ValueError('invalid review reasons')
    key = digest([task_id, review_id, reasons])[7:39]
    source_id = 'SE-review-' + key
    ref = {'store': 'task_service', 'id': source_id, 'edition': '1', 'digest': digest(reasons)}
    cmd = command('source_event.record', 'source_event', source_id,
                  {'source': {'store': 'lead-review', 'id': task_id + ':' + review_id, 'position': 1},
                   'content': reasons, 'correction_of': None},
                  command_id='cmd-review-evidence-' + key, environment=environment, scope=scope,
                  causal_parent={'store': 'task_service', 'id': task_id, 'edition': None, 'digest': None},
                  session_id=None, runtime=runtime)
    return ref, cmd
