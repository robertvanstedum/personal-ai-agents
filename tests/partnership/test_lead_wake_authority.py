import pytest

from core.lead_wake.authority import Authority
from core.lead_wake.inbox import Inbox, StaleWake
from services.partnership_tasks.outbox import drain_once
from tests.partnership.test_helper_ask import rig, ask, review  # noqa: F401
from tests.partnership.test_helper_wake_outbox import wakes


def authority(rig, **kwargs):
    options = dict(principal='principal', lead='mc', environment='synthetic')
    options.update(kwargs)
    return Authority(rig.svc, 'tok-mc', **options)


def delivered(rig, tmp_path):
    answer = ask(rig)
    [op] = wakes(rig)
    inbox = Inbox(tmp_path/'wake.sqlite', authority(rig))
    assert drain_once(rig.svc, 'tok-drainer', None, op['operation_id'], lead_wake=inbox) == 'acknowledged'
    return answer, op, inbox


def test_real_task_outbox_inbox_restart_and_revalidation(rig, tmp_path):
    _, op, inbox = delivered(rig, tmp_path)
    assert Inbox(inbox.path, authority(rig)).queued() == [(op['operation_id'], op['packet'])]
    assert drain_once(rig.svc, 'tok-drainer', None, op['operation_id'], lead_wake=inbox) == 'acknowledged'
    assert len(inbox.queued()) == 1


@pytest.mark.parametrize('options', [dict(principal='other'), dict(lead='cos'), dict(environment='prod')])
def test_host_binding_cannot_be_changed_by_payload(rig, tmp_path, options):
    _, op, _ = delivered(rig, tmp_path)
    with pytest.raises(PermissionError):
        authority(rig, **options)(op['operation_id'])


def test_pending_wake_is_not_admitted_by_read_credential(rig):
    ask(rig)
    [op] = wakes(rig)
    with pytest.raises(StaleWake):
        authority(rig)(op['operation_id'])


def test_cancel_after_receipt_suppresses_queued_work(rig, tmp_path):
    from tests.partnership.synth import Flow, ref
    answer, _, inbox = delivered(rig, tmp_path)
    result = Flow(rig.svc).transition(answer['data']['task_id'], 'canceled', token='tok-principal',
                                     reason='stop', evidence=[ref('records', 'owner-stop')])
    assert result['outcome'] == 'accepted'
    assert inbox.queued() == []


def test_new_result_and_revoked_charter_suppress_old_work(rig, tmp_path):
    from services.partnership_tasks.charters import Charters
    answer, _, inbox = delivered(rig, tmp_path)
    review(rig, answer, verdict='changes_requested')
    rig.worker.pass_once()
    assert inbox.queued() == []
    # Select by current state, not filename order.
    newest = next(op for op in wakes(rig) if op['state'] == 'pending')
    assert drain_once(rig.svc, 'tok-drainer', None, newest['operation_id'], lead_wake=inbox) == 'acknowledged'
    rig.svc.charters = Charters()
    assert inbox.queued() == []


def test_correction_after_receipt_blocks_continuation(rig, tmp_path):
    from tests.partnership.synth import Flow
    _, op, inbox = delivered(rig, tmp_path)
    Flow(rig.svc).source('SE-owner-correction', 'Do not use the original premise.',
                         correction_of=op['packet']['source_event_id'])
    assert inbox.queued() == []


def test_superseded_intent_blocks_late_result_even_with_current_fence(rig, tmp_path):
    from tests.partnership.synth import Flow, cmd, ref
    answer = ask(rig)
    task = rig.svc.get('task', answer['data']['task_id'], 'tok-mc')
    interp = rig.svc.get('interpretation', task['interpretation_ref'], 'tok-mc')
    Flow(rig.svc).interpret('IN-owner-new-intent', interp['source_event_ids'], 'Changed direction',
                            token='tok-mc', supersedes=interp['interpretation_id'])
    task = rig.svc.get('task', task['task_id'], 'tok-mc')
    result = rig.svc.submit(cmd('task.submit_artifacts', 'task', task['task_id'],
                               {'artifacts': [ref('artifacts', 'late-helper-result', digest_='sha256:'+'b'*64)],
                                'fence_generation': task['fence_generation']},
                               expected=task['revision']), 'tok-apprentice')
    assert result['outcome'] == 'accepted', result
    latest = rig.svc.get('task', task['task_id'], 'tok-mc')['artifact_sets'][-1]['digest']
    op = next(op for op in wakes(rig) if op['packet']['result_digest'] == latest)
    inbox = Inbox(tmp_path/'wake.sqlite', authority(rig))
    for _ in range(3):
        assert drain_once(rig.svc, 'tok-drainer', None, op['operation_id'], lead_wake=inbox) == 'fenced'
    assert inbox.queued() == []


def test_http_resolution_uses_seat_and_client_checks_host_identity(rig, tmp_path):
    from services.partnership_tasks.http_api import create_app
    from services.partnership_tasks.client import TaskClient, TaskServiceUnavailable
    _, op, _ = delivered(rig, tmp_path)
    http = create_app(rig.svc).test_client()
    class Session:
        def request(self, method, url, **kwargs):
            from urllib.parse import urlsplit
            response = http.open(urlsplit(url).path, method=method,
                                 headers=kwargs['headers'], data=kwargs['data'])
            class Reply:
                status_code = response.status_code
                def json(self): return response.get_json()
            return Reply()
    client = TaskClient('http://synthetic.invalid', 'tok-mc', session=Session())
    assert client.resolve_wake(op['operation_id'], principal='principal', lead='mc',
                               environment='synthetic') == op['packet']
    with pytest.raises(TaskServiceUnavailable):
        client.resolve_wake(op['operation_id'], principal='other', lead='mc', environment='synthetic')
    for token, status in [('bad', 401), ('tok-drainer', 404), ('tok-cos', 409)]:
        assert http.get('/v1/wakes/'+op['operation_id'],
                        headers={'Authorization': 'Bearer '+token}).status_code == status
    wrong = TaskClient('http://synthetic.invalid', 'tok-cos', session=Session())
    with pytest.raises(TaskServiceUnavailable):
        wrong.resolve_wake(op['operation_id'], principal='principal', lead='mc', environment='synthetic')
    from services.partnership_tasks.charters import Charters
    rig.svc.charters = Charters()
    with pytest.raises(StaleWake):
        client.resolve_wake(op['operation_id'], principal='principal', lead='mc', environment='synthetic')


@pytest.mark.parametrize('change', ['correction', 'charter', 'superseded'])
def test_change_between_dispatch_record_and_delivery_eventually_fences(rig, tmp_path, change):
    from services.partnership_tasks.charters import Charters
    from tests.partnership.synth import Flow
    ask(rig)
    [op] = wakes(rig)
    inbox = Inbox(tmp_path/'wake.sqlite', authority(rig))
    calls = []
    class Destination:
        def deliver(self, *args, **kwargs):
            calls.append(args[0])
            if change == 'charter':
                rig.svc.charters = Charters()
            elif change == 'correction':
                Flow(rig.svc).source('SE-late-correction', 'Changed direction',
                                     correction_of=op['packet']['source_event_id'])
            else:
                task = rig.svc.get('task', op['task_id'], 'tok-mc')
                Flow(rig.svc).interpret('IN-late-new-intent', [op['packet']['source_event_id']],
                                        'Changed direction', token='tok-mc', supersedes=task['interpretation_ref'])
            return inbox.deliver(*args, **kwargs)
        def lookup(self, *args, **kwargs):
            return inbox.lookup(*args, **kwargs)
    destination = Destination()
    def run(): return drain_once(rig.svc, 'tok-drainer', None, op['operation_id'], lead_wake=destination)
    with pytest.raises(StaleWake): run()
    assert run() == 'fenced'
    assert run() == 'fenced'
    assert len(calls) == 1


def prepared_publication(rig, tmp_path):
    answer, op, inbox = delivered(rig, tmp_path)
    claim = 'publication-claim-original'
    assert inbox.claim(op['operation_id'], claim)
    result = {'verdict': 'approve', 'reasons': 'Checked the exact helper output.', 'answer': 'The helper found the source; MC checked it.'}
    inbox.save_review(op['operation_id'], claim, result)
    intent = inbox.prepare_publication(op['operation_id'], conversation_id='original-chat', created_at='2026-11-11T10:00:00+00:00')
    return answer, op, inbox, intent


def test_publication_intent_survives_restart_and_cannot_rerender_or_retarget(rig, tmp_path):
    from core.lead_wake.inbox import Conflict
    _, op, inbox, intent = prepared_publication(rig, tmp_path)
    restarted = Inbox(inbox.path, authority(rig))
    assert restarted.publication(op['operation_id']) == intent
    assert restarted.prepare_publication(op['operation_id'], conversation_id='original-chat', created_at='2026-11-12T10:00:00+00:00') == intent
    with pytest.raises(Conflict):
        restarted.prepare_publication(op['operation_id'], conversation_id='another-chat', created_at=intent['created_at'])


def test_stop_wins_before_publication_and_callback_never_runs(rig, tmp_path):
    from tests.partnership.synth import Flow, ref
    answer, op, inbox, _ = prepared_publication(rig, tmp_path)
    assert Flow(rig.svc).transition(answer['data']['task_id'], 'canceled', token='tok-principal', reason='stop', evidence=[ref('records', 'stop')])['outcome'] == 'accepted'
    calls = []
    with pytest.raises(StaleWake):
        authority(rig).commit_current(op['operation_id'], op['packet'], inbox, lambda intent: calls.append(intent))
    assert calls == [] and inbox.status(op['operation_id']) != 'presented'


def test_canonical_write_wins_task_lock_before_stop(rig, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from tests.partnership.synth import Flow, ref
    answer, op, inbox, intent = prepared_publication(rig, tmp_path)
    entered, stopping, release = Event(), Event(), Event()
    saved = {}
    def canonical(value):
        assert value == intent
        entered.set()
        assert release.wait(3)
        saved[value['id']] = value
        return {k: value[k] for k in ('store', 'id', 'result_digest')}
    def stop():
        stopping.set()
        return Flow(rig.svc).transition(answer['data']['task_id'], 'canceled', token='tok-principal', reason='stop', evidence=[ref('records', 'stop')])
    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(authority(rig).commit_current, op['operation_id'], op['packet'], inbox, canonical)
        assert entered.wait(3)
        cancellation = pool.submit(stop)
        assert stopping.wait(3)
        assert not cancellation.done()
        release.set()
        assert publication.result(timeout=3)['id'] == intent['id']
        assert cancellation.result(timeout=3)['outcome'] == 'accepted'
    assert list(saved) == [intent['id']] and inbox.status(op['operation_id']) == 'presented'


def test_lost_canonical_ack_keeps_exact_intent_for_readback_not_new_inference(rig, tmp_path):
    from services.partnership_tasks.service import StoreUnavailable
    _, op, inbox, intent = prepared_publication(rig, tmp_path)
    saved = {}
    def lost_ack(value):
        saved[value['id']] = value
        raise OSError('acknowledgement lost after durable canonical save')
    with pytest.raises(StoreUnavailable):
        authority(rig).commit_current(op['operation_id'], op['packet'], inbox, lost_ack)
    assert inbox.publication(op['operation_id']) == intent
    assert inbox.review(op['operation_id']) is not None
    assert inbox.status(op['operation_id']) == 'reviewed'
    receipt = {k: saved[intent['id']][k] for k in ('store', 'id', 'result_digest')}
    inbox.mark_presented(op['operation_id'], receipt)  # host readback, no regenerated text or model call
    assert inbox.status(op['operation_id']) == 'presented'


def test_superseded_result_and_wrong_receipt_never_mark_presented(rig, tmp_path):
    answer, op, inbox, _ = prepared_publication(rig, tmp_path)
    with pytest.raises(ValueError, match='unconfirmed'):
        authority(rig).commit_current(op['operation_id'], op['packet'], inbox, lambda intent: {'status': 'accepted'})
    assert inbox.status(op['operation_id']) == 'reviewed'
    review(rig, answer, verdict='changes_requested')
    rig.worker.pass_once()
    calls = []
    with pytest.raises(StaleWake):
        authority(rig).commit_current(op['operation_id'], op['packet'], inbox, lambda intent: calls.append(intent))
    assert not calls


def test_read_only_lead_can_resolve_but_cannot_publish(rig, tmp_path, monkeypatch):
    from dataclasses import replace
    _, op, inbox, _ = prepared_publication(rig, tmp_path)
    original = rig.svc.bindings.resolve
    def read_only(token, now):
        binding = original(token, now)
        return replace(binding, scopes=frozenset()) if token == 'tok-mc' else binding
    monkeypatch.setattr(rig.svc.bindings, 'resolve', read_only)
    assert authority(rig)(op['operation_id']) == op['packet']
    calls = []
    with pytest.raises(PermissionError, match='publication scope'):
        authority(rig).commit_current(op['operation_id'], op['packet'], inbox, lambda intent: calls.append(intent))
    assert not calls and inbox.status(op['operation_id']) == 'reviewed'
