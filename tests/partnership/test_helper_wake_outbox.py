"""Durable helper result notification to the lead, not an owner notice or inference.
The destination here is synthetic; production continuation is deliberately unwired.
"""
from unittest.mock import Mock
from services.partnership_tasks.outbox import drain_once, DestinationUnreachable
from tests.partnership.test_helper_ask import rig, ask, review  # noqa: F401


def wakes(rig):
    return [rig.svc.get('outbox', p.stem, 'tok-principal')
            for p in (rig.svc.journal.root / 'outbox').glob('OB-wake-*.json')]


class Destination:
    def __init__(self, lose=False):
        self.receipt = None
        self.calls = 0
        self.lose = lose

    def deliver(self, operation_id, payload_digest, **kwargs):
        self.calls += 1
        self.receipt = {'store': 'lead_wake', 'id': operation_id, 'digest': payload_digest}
        if self.lose:
            raise DestinationUnreachable()
        return {'receipt': self.receipt}

    def lookup(self, operation_id, **kwargs):
        return {'state': 'applied', 'receipt': self.receipt}


def test_submission_records_one_reference_only_wake_and_no_channel_means_pending(rig):
    answer = ask(rig)
    [wake] = wakes(rig)
    assert wake['task_id'] == answer['data']['task_id']
    assert wake['packet']['result_digest'] == answer['data']['set_digest']
    assert wake['packet']['source_ref']['store'] == 'mc-floor'
    assert 'content' not in wake['packet'] and 'prompt' not in wake['packet']
    never = Mock(side_effect=AssertionError('not a harness or owner notice'))
    assert drain_once(rig.svc, 'tok-drainer', never, wake['operation_id'], never) == 'pending'
    assert rig.worker.pass_once() == 0
    assert len(wakes(rig)) == 1


def test_lost_destination_receipt_is_reconciled_without_second_dispatch(rig):
    ask(rig)
    [wake] = wakes(rig)
    dest = Destination(lose=True)
    run = lambda: drain_once(rig.svc, 'tok-drainer', None, wake['operation_id'], lead_wake=dest)
    assert run() == 'unknown'
    assert run() == 'acknowledged'
    assert run() == 'acknowledged'
    assert dest.calls == 1


def test_revised_helper_result_fences_older_unsent_wake(rig):
    answer = ask(rig)
    [old] = wakes(rig)
    review(rig, answer, verdict='changes_requested')
    rig.worker.pass_once()
    assert len(wakes(rig)) == 2
    dest = Destination()
    assert drain_once(rig.svc, 'tok-drainer', None, old['operation_id'], lead_wake=dest) == 'fenced'
    assert dest.calls == 0


def test_wrong_destination_receipt_cannot_mark_lead_wake_delivered(rig):
    ask(rig)
    [wake] = wakes(rig)
    dest = Destination()
    def wrong(op, digest, **kwargs):
        return {'receipt': {'store': 'notices', 'id': op, 'digest': digest}}
    dest.deliver = wrong
    # The authoritative record refuses the forged/misdirected receipt; no success claim.
    assert drain_once(rig.svc, 'tok-drainer', None, wake['operation_id'], lead_wake=dest) == 'attempted'


def test_cancellation_fences_pending_wake_without_dispatch(rig):
    from tests.partnership.synth import Flow, ref
    answer = ask(rig)
    [wake] = wakes(rig)
    changed = Flow(rig.svc).transition(answer['data']['task_id'], 'canceled', token='tok-principal', reason='stop', evidence=[ref('records', 'owner-stop')])
    assert changed['outcome'] == 'accepted', str(changed)
    dest = Destination()
    assert drain_once(rig.svc, 'tok-drainer', None, wake['operation_id'], lead_wake=dest) == 'fenced'
    assert dest.calls == 0


def test_cancel_after_uncertain_send_and_definite_absence_never_resends(rig):
    from tests.partnership.synth import Flow, ref
    answer = ask(rig)
    [wake] = wakes(rig)
    dest = Destination(lose=True)
    run = lambda: drain_once(rig.svc, 'tok-drainer', None, wake['operation_id'], lead_wake=dest)
    assert run() == 'unknown'
    changed = Flow(rig.svc).transition(answer['data']['task_id'], 'canceled', token='tok-principal', reason='stop', evidence=[ref('records', 'owner-stop')])
    assert changed['outcome'] == 'accepted', str(changed)
    dest.lookup = lambda *args, **kwargs: {'state': 'absent'}
    assert run() == 'fenced'
    assert run() == 'fenced'
    assert dest.calls == 1
