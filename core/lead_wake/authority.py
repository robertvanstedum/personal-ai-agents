"""Resolve a wake from one locked task-store snapshot; never trust forwarded packets.

This local adapter is deliberately not a network endpoint. The host pins the principal,
lead and environment; the credential must belong to that lead in that environment.
A successful read is not permission to execute later: consumers must revalidate.
"""
from copy import deepcopy

from core.canonical_json import digest
from core.lead_wake.inbox import StaleWake
from services.partnership_tasks.service import path_of, StoreUnavailable, wake_problem
from services.partnership_tasks.journal import JournalDamaged


class Authority:
    def __init__(self, service, token, *, principal, lead, environment):
        if lead not in ('mc', 'cos'):
            raise ValueError('unsupported lead')
        self.service, self.token = service, token
        self.principal, self.lead, self.environment = principal, lead, environment

    def __call__(self, operation_id):
        svc = self.service
        binding = svc.bindings.resolve(self.token, svc._clock())
        if (binding is None or binding.seat != self.lead or binding.origin != 'agent'
                or binding.principal != self.principal
                or binding.environment != self.environment or svc.environment != self.environment):
            raise PermissionError('wake authority binding unavailable')
        if (not isinstance(operation_id, str) or not operation_id.startswith('OB-wake-')
                or len(operation_id) != 48 or not all(c in '0123456789abcdef' for c in operation_id[8:])):
            raise StaleWake('invalid operation')

        def read():
            def record(kind, key):
                value = svc.journal.read(path_of(kind, key))
                if (value is None or value['environment'] != self.environment
                        or value['data_scope'] not in binding.read_scopes):
                    raise StaleWake('record unavailable')
                return value

            op = record('outbox', operation_id)
            if (op['destination'] != 'lead_wake' or op['operation'] != 'wake_lead'
                    or op['state'] not in ('attempted', 'unknown', 'acknowledged')):
                raise StaleWake('wake is not dispatched')
            task = record('task', op['task_id'])
            parent = record('task', task['parent_ref']) if task['parent_ref'] else task
            found = svc.charters.chartered(task.get('charter_ref'))
            if (found is None or parent['lead'] != self.lead
                    or task['state'] not in ('working', 'waiting', 'completed')
                    or parent['state'] not in ('working', 'waiting', 'completed')
                    or any(t['waiting'] and t['waiting']['reason'] == 'lead_unavailable' for t in (task, parent))
                    or not task['artifact_sets']):
                raise StaleWake('task no longer eligible')
            charter, kind = found
            current = task['artifact_sets'][-1]
            if (self.lead not in charter.leads or task['data_scope'] not in charter.scopes
                    or task['holder'] != charter.seat or set(current['authored_by']) != {charter.seat}
                    or kind['effects'] not in ('none', 'internal')):
                raise StaleWake('charter no longer applicable')
            request = record('request', task['request_ref'])
            if wake_problem(svc, task, binding.read_scopes):
                raise StaleWake('task intent no longer actionable')
            source = record('source_event', request['source_event_id'])
            if any(r['data_scope'] != task['data_scope'] for r in (op, parent, request, source)):
                raise StaleWake('scope differs')
            packet = {'kind': 'helper_result_ready', 'task_id': task['task_id'], 'lead': self.lead,
                      'source_ref': source['source'], 'source_event_id': source['source_event_id'],
                      'result_digest': current['digest'], 'charter_ref': task['charter_ref'],
                      'fence_generation': task['fence_generation'], 'environment': self.environment,
                      'data_scope': task['data_scope']}
            if (op['packet'] != packet or op['bundle_digest'] != digest(packet)
                    or operation_id != 'OB-wake-' + digest(packet)[7:47]
                    or op['fence_generation'] != task['fence_generation']):
                raise StaleWake('wake superseded')
            return deepcopy(packet)
        try:
            return svc.journal.view(read)
        except (JournalDamaged, OSError) as exc:
            raise StoreUnavailable(type(exc).__name__) from exc
