"""Real journal commit: exact review and publication linearize together with Stop."""
from dataclasses import replace

import pytest

from core.canonical_json import digest
from services.partnership_tasks.auth import token_sha256
from services.partnership_tasks.outbox import drain_once, DestinationUnreachable
from services.partnership_tasks.service import TaskService
from tests.partnership.synth import cmd, Flow, ref
from tests.partnership.test_helper_ask import rig  # noqa: F401
from tests.partnership.test_lead_wake_authority import delivered


def setup(rig, tmp_path, verdict='approve'):
    _, wake, _ = delivered(rig, tmp_path)
    svc = rig.svc
    svc.helper_returns = True
    base = svc.bindings.resolve('tok-mc', svc._clock())
    svc.bindings._by_hash[token_sha256('tok-return')] = replace(
        base, binding_id='bind-return', operations=frozenset({'task.commit_helper_return'}),
        helper_return_commit=True)
    task = svc.get('task', wake['task_id'], 'tok-mc')
    payload = {'wake_operation_id': wake['operation_id'], 'result_digest': wake['packet']['result_digest'],
               'verdict': verdict, 'reasons': 'Checked the exact helper result.', 'answer': 'The template is build_log.html.'}
    command = cmd('task.commit_helper_return', 'task', task['task_id'], payload,
                  expected=task['revision'], cid='cmd-return-first')
    return svc, wake, command


def publication(svc, wake):
    return svc.get('outbox', 'OB-publish-' + wake['operation_id'][8:], 'tok-mc')


def test_atomic_accept_and_exact_immutable_intent_restart_replay(rig, tmp_path):
    svc, wake, command = setup(rig, tmp_path)
    result = svc.submit(command, 'tok-return')
    assert result['outcome'] == 'accepted', str(result)
    task = svc.get('task', wake['task_id'], 'tok-mc')
    ob = publication(svc, wake)
    assert task['state'] == 'completed' and len(task['acceptances']) == 1
    assert task['reviews'][-1]['review_ref']['id'] == ob['operation_id']
    assert ob['packet']['source_ref'] == wake['packet']['source_ref']
    assert ob['packet']['text'] == command['payload']['answer']
    assert ob['bundle_digest'] == digest(ob['packet'])
    assert ob['state'] == 'pending' and ob['destination_receipt'] is None
    restarted = TaskService(svc.journal.root, svc.environment, svc.bindings,
                            clock=svc._clock, charters=svc.charters, helper_returns=True)
    assert restarted.submit(command, 'tok-return')['replayed']
    changed = dict(command, command_id='cmd-return-second')
    assert restarted.submit(changed, 'tok-return')['code'] == 'id_taken'
    assert publication(restarted, wake) == ob
    assert drain_once(svc, 'tok-drainer', None, ob['operation_id']) == 'pending'


@pytest.mark.parametrize('token,enabled', [('tok-mc', True), ('tok-principal', True), ('tok-return', False)])
def test_dedicated_permission_and_feature_gate(rig, tmp_path, token, enabled):
    svc, wake, command = setup(rig, tmp_path)
    svc.helper_returns = enabled
    assert svc.submit(command, token)['code'] == 'not_authorized'
    assert publication(svc, wake) is None
    assert svc.get('task', wake['task_id'], 'tok-mc')['reviews'] == []


@pytest.mark.parametrize('change', ['stop', 'correction', 'new_result', 'revoked', 'scope', 'destination'])
def test_precommit_fences_and_no_partial_review(rig, tmp_path, change):
    svc, wake, command = setup(rig, tmp_path)
    if change == 'stop':
        assert Flow(svc).transition(wake['task_id'], 'canceled', token='tok-principal',
                                   reason='stop', evidence=[ref('records', 'stop')])['outcome'] == 'accepted'
    elif change == 'correction':
        Flow(svc).source('SE-correction-return', 'Changed direction.', correction_of=wake['packet']['source_event_id'])
    elif change == 'new_result':
        task = svc.get('task', wake['task_id'], 'tok-mc')
        assert svc.submit(cmd('task.submit_artifacts', 'task', task['task_id'],
            {'artifacts': [ref('artifacts', 'new-result', digest_='sha256:'+'b'*64)],
             'fence_generation': task['fence_generation']}, expected=task['revision']), 'tok-apprentice')['outcome'] == 'accepted'
    elif change in ('revoked', 'scope'):
        key = token_sha256('tok-return')
        b = svc.bindings._by_hash[key]
        svc.bindings._by_hash[key] = replace(b, **({'revoked_at': svc._clock()} if change == 'revoked' else {'scopes': frozenset()}))
    else:
        command['payload']['conversation_id'] = 'other'
        command['payload_digest'] = digest(command['payload'])
    command['expected_revision'] = svc.get('task', wake['task_id'], 'tok-mc')['revision']
    assert svc.submit(command, 'tok-return')['outcome'] != 'accepted'
    assert publication(svc, wake) is None
    assert svc.get('task', wake['task_id'], 'tok-mc')['reviews'] == []


class Destination:
    """Test-only canonical persistence stand-in, never enabled by production config."""
    def __init__(self, svc, lose=False):
        self.svc, self.lose, self.saved, self.calls = svc, lose, {}, 0
    def deliver(self, opid, payload_digest, **kwargs):
        self.calls += 1
        ob = self.svc.get('outbox', opid, 'tok-drainer')
        assert ob['bundle_digest'] == payload_digest
        self.saved[opid] = {'store': 'lead_publication', 'id': opid, 'edition': None, 'digest': payload_digest}
        if self.lose:
            raise DestinationUnreachable()
        return {'receipt': self.saved[opid]}
    def lookup(self, opid, **kwargs):
        return {'state': 'applied', 'receipt': self.saved[opid]}


def test_committed_return_survives_stop_and_lost_ack_without_second_write(rig, tmp_path):
    svc, wake, command = setup(rig, tmp_path, verdict='changes_requested')
    assert svc.submit(command, 'tok-return')['outcome'] == 'accepted'
    ob = publication(svc, wake)
    stopped = Flow(svc).transition(wake['task_id'], 'canceled', token='tok-principal',
                                  reason='stop', evidence=[ref('records', 'stop')])
    assert stopped['outcome'] == 'accepted'
    assert ob['operation_id'] in stopped['open_effects']
    dest = Destination(svc, lose=True)
    assert drain_once(svc, 'tok-drainer', None, ob['operation_id'], publication=dest) == 'unknown'
    assert drain_once(svc, 'tok-drainer', None, ob['operation_id'], publication=dest) == 'acknowledged'
    assert dest.calls == 1
    assert publication(svc, wake)['packet'] == ob['packet']


def test_pending_unknown_absent_after_stop_retries_same_committed_intent(rig, tmp_path):
    svc, wake, command = setup(rig, tmp_path, verdict='changes_requested')
    assert svc.submit(command, 'tok-return')['outcome'] == 'accepted'
    ob = publication(svc, wake)
    class Absent(Destination):
        def deliver(self, *args, **kwargs):
            if not self.calls:
                self.calls += 1
                raise DestinationUnreachable()
            return super().deliver(*args, **kwargs)
        def lookup(self, *args, **kwargs):
            # Destination contract: returning absent permanently fences attempt 1.
            return {'state': 'absent'}
    dest = Absent(svc)
    assert drain_once(svc, 'tok-drainer', None, ob['operation_id'], publication=dest) == 'unknown'
    Flow(svc).transition(wake['task_id'], 'canceled', token='tok-principal', reason='stop', evidence=[ref('records', 'stop')])
    assert drain_once(svc, 'tok-drainer', None, ob['operation_id'], publication=dest) == 'pending'
    assert drain_once(svc, 'tok-drainer', None, ob['operation_id'], publication=dest) == 'acknowledged'
    assert len(dest.saved) == 1


def test_commit_wins_stop_race_under_same_writer_lock(rig, tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from services.partnership_tasks.service import _Planner
    svc, wake, command = setup(rig, tmp_path, verdict='changes_requested')
    task = svc.get('task', wake['task_id'], 'tok-mc')
    reached, release = threading.Event(), threading.Event()
    original = _Planner._record_task_review
    def paused(self, task):
        reached.set()
        assert release.wait(5)
        return original(self, task)
    monkeypatch.setattr(_Planner, '_record_task_review', paused)
    # A second service process has its own Journal object and the same file lock.
    peer = TaskService(svc.journal.root, svc.environment, svc.bindings, clock=svc._clock,
                       charters=svc.charters, helper_returns=True)
    # Use the established Flow to build the exact transition shape before race.
    from unittest.mock import patch
    captured = []
    with patch.object(peer, 'submit', side_effect=lambda c, t: captured.append(c) or {'outcome': 'accepted'}):
        Flow(peer).transition(task['task_id'], 'canceled', token='tok-principal', reason='stop', evidence=[ref('records', 'stop')])
    stop = captured[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        committing = pool.submit(svc.submit, command, 'tok-return')
        assert reached.wait(5)
        stopping = pool.submit(peer.submit, stop, 'tok-principal')
        assert not stopping.done()
        release.set()
        assert committing.result(timeout=5)['outcome'] == 'accepted'
        assert stopping.result(timeout=5)['outcome'] == 'conflict'
    stopped = Flow(peer).transition(task['task_id'], 'canceled', token='tok-principal', reason='stop', evidence=[ref('records', 'stop')])
    assert stopped['outcome'] == 'accepted'
    assert publication(svc, wake)['operation_id'] in stopped['open_effects']


def test_binding_rechecked_after_waiting_for_journal(rig, tmp_path, monkeypatch):
    svc, wake, command = setup(rig, tmp_path)
    original = svc.journal.run
    def revoke_before_plan(cid, dgst, plan):
        key = token_sha256('tok-return')
        svc.bindings._by_hash[key] = replace(svc.bindings._by_hash[key], revoked_at=svc._clock())
        return original(cid, dgst, plan)
    monkeypatch.setattr(svc.journal, 'run', revoke_before_plan)
    assert svc.submit(command, 'tok-return')['code'] == 'not_authenticated'
    assert publication(svc, wake) is None


def test_return_binding_cannot_be_general_owner_or_tool_credential():
    from services.partnership_tasks.auth import Bindings
    record = {'binding_id': 'bind-service', 'token_sha256': token_sha256('service'), 'seat': 'mc',
              'principal': 'principal', 'environment': 'synthetic', 'origin': 'agent',
              'scopes': ['personal'], 'read_scopes': ['personal'],
              'operations': ['task.commit_helper_return'], 'helper_return_commit': True}
    assert Bindings([record]).resolve('service', '2026-11-10T00:00:00Z').helper_return_commit
    for changes in ({'seat': 'apprentice'}, {'origin': 'owner_session'},
                    {'operations': ['task.commit_helper_return', 'task.accept']}):
        with pytest.raises(ValueError, match='dedicated'):
            Bindings([dict(record, **changes)])


@pytest.mark.parametrize('fail_on_write', [0, 1])
def test_crash_finishes_exact_review_acceptance_and_publication_together(rig, tmp_path, monkeypatch, fail_on_write):
    from core import durable_files
    svc, wake, command = setup(rig, tmp_path)
    real, calls = durable_files.write_doc, []
    def crash(path, record):
        if len(calls) == fail_on_write:
            raise OSError('simulated journal interruption')
        calls.append(str(path))
        return real(path, record)
    monkeypatch.setattr(durable_files, 'write_doc', crash)
    assert svc.submit(command, 'tok-return')['code'] == 'store_unavailable'
    monkeypatch.setattr(durable_files, 'write_doc', real)
    recovered = TaskService(svc.journal.root, svc.environment, svc.bindings,
                            clock=svc._clock, charters=svc.charters, helper_returns=True)
    result = recovered.submit(command, 'tok-return')
    assert result['outcome'] == 'accepted' and result['replayed']
    task = recovered.get('task', wake['task_id'], 'tok-mc')
    ob = publication(recovered, wake)
    assert task['state'] == 'completed' and len(task['reviews']) == len(task['acceptances']) == 1
    assert task['reviews'][0]['review_ref']['digest'] == ob['bundle_digest']
    assert ob['packet']['text'] == command['payload']['answer']


def test_unresolved_other_review_dissent_cannot_be_silently_accepted(rig, tmp_path):
    svc, wake, command = setup(rig, tmp_path)
    task = svc.get('task', wake['task_id'], 'tok-mc')
    other = cmd('task.record_review', 'task', task['task_id'],
                {'review_id': 'RV-other-dissent', 'reviewed_set_digest': command['payload']['result_digest'],
                 'input_digest': command['payload']['result_digest'], 'verdict': 'changes_requested',
                 'review_ref': ref('records', 'other-dissent', digest_='sha256:'+'c'*64), 'findings_ref': None},
                expected=task['revision'])
    assert svc.submit(other, 'tok-cos')['outcome'] == 'accepted'
    command['expected_revision'] = svc.get('task', wake['task_id'], 'tok-mc')['revision']
    result = svc.submit(command, 'tok-return')
    assert result['outcome'] == 'refused' and result['detail'] == 'disagreement_not_delivered'
    assert publication(svc, wake) is None
    assert len(svc.get('task', wake['task_id'], 'tok-mc')['reviews']) == 1


def test_dedicated_wrong_lead_cannot_commit(rig, tmp_path):
    svc, wake, command = setup(rig, tmp_path)
    key = token_sha256('tok-return')
    svc.bindings._by_hash[key] = replace(svc.bindings._by_hash[key], seat='cos')
    assert svc.submit(command, 'tok-return')['code'] == 'fenced'
    assert publication(svc, wake) is None


def test_existing_authenticated_http_command_transport_has_same_gate(rig, tmp_path):
    from services.partnership_tasks.http_api import create_app
    svc, wake, command = setup(rig, tmp_path)
    http = create_app(svc).test_client()
    response = http.post('/v1/commands', json=command, headers={'Authorization': 'Bearer tok-return'})
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()['outcome'] == 'accepted'
    assert publication(svc, wake)['state'] == 'pending'
