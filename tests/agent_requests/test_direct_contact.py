"""Synthetic transport only. No native session, queue command or provider calls."""
from dataclasses import replace
import json
import os
import threading
import time

import pytest

from core.agent_requests import ledger as L
from core.agent_requests.direct_contact import Delivery, Outbox, OwnerSource, Refused, Target


TARGET = Target('codex-owner', 'codex', '11111111-1111-1111-1111-111111111111', 'local', '/synthetic/repo', 'fixture', 'enrolled-v1')
SOURCE = OwnerSource('account:owner', 'c-123456789abc', 'note-1', 'kept-note:note-1')


class Transport:
    name = 'fixture'
    def __init__(self, behavior=None):
        self.calls = []
        self.behavior = behavior
    def deliver(self, target, envelope, *, timeout):
        self.calls.append((target, envelope, timeout))
        return self.behavior(target, envelope) if self.behavior else Delivery('accepted', target.session_id, 'native-message-1')


@pytest.fixture
def setup(tmp_path):
    tmp_path.chmod(0o700)
    def make(transport=None, **kwargs):
        return Outbox(tmp_path, {TARGET.alias: TARGET}, transport, **kwargs)
    return tmp_path, make


def enqueue(box, source=SOURCE):
    return box.enqueue(TARGET.alias, source, 'Please review the exact current source. This is a request, not approval.')


def acknowledge(box, rid, transport, result=None):
    target, envelope, timeout = transport.calls[0]
    return box.native_receipt(rid, transport=target.transport, session_id=target.session_id,
                              correlation=envelope['correlation'], result=result)


def test_unavailable_is_durable_queued_and_no_fake_rooms_delivery(setup):
    path, make = setup
    box = make()
    rid = enqueue(box)
    assert box.dispatch(rid)['reason'] == 'transport_unavailable'
    assert make().status(rid) == box.status(rid)
    assert box.status(rid)['fallback'] == 'rooms_available_not_sent'
    assert not (box.root / 'requests' / rid / 'intent.json').exists()
    assert not (path / 'pending').exists()  # Old generic watcher root is untouched.
    assert L.load(str(box.root), rid)['stage'] == 'queued'


def test_alias_enrollment_and_owner_note_idempotency(setup):
    path, make = setup
    box = make()
    with pytest.raises(Refused, match='target_not_enrolled'):
        box.enqueue(TARGET.session_id, SOURCE, 'x')
    rid = enqueue(box)
    assert enqueue(box) == rid
    assert enqueue(box, replace(SOURCE, conversation_id='c-aaaaaaaaaaaa')) != rid
    with pytest.raises(Refused, match='request_reused'):
        box.enqueue(TARGET.alias, SOURCE, 'different owner text under same kept note')
    assert len(L.load(str(box.root), rid)['events']) == 1


def test_enrollment_change_cannot_retarget_existing_request(setup):
    path, make = setup
    box = make()
    rid = enqueue(box)
    changed = replace(TARGET, session_id='22222222-2222-2222-2222-222222222222')
    other = Outbox(path, {TARGET.alias: changed}, Transport())
    with pytest.raises(Refused, match='enrollment_changed'):
        other.dispatch(rid)


def test_transport_accepted_ack_and_result_are_three_different_states(setup):
    path, make = setup
    transport = Transport()
    box = make(transport)
    rid = enqueue(box)
    assert box.dispatch(rid)['state'] == 'dispatched'
    assert L.load(str(box.root), rid)['stage'] == 'queued'
    assert acknowledge(box, rid, transport)['state'] == 'acknowledged'
    assert acknowledge(box, rid, transport, result='Native worker says reviewed.')['state'] == 'result'
    assert box.status(rid)['reason'] == 'agent_report_not_verification'
    box.dispatch(rid)
    assert len(transport.calls) == 1
    assert L.load(str(box.root), rid)['stage'] == 'returned'


@pytest.mark.parametrize('field,value', [('session_id', '22222222-2222-2222-2222-222222222222'), ('transport', 'other'), ('correlation', 'wrong')])
def test_native_receipt_wrong_binding_never_changes_ledger(setup, field, value):
    path, make = setup
    transport = Transport()
    box = make(transport)
    rid = enqueue(box)
    box.dispatch(rid)
    kwargs = {'transport': TARGET.transport, 'session_id': TARGET.session_id, 'correlation': transport.calls[0][1]['correlation']}
    kwargs[field] = value
    before = L.load(str(box.root), rid)
    with pytest.raises(Refused, match='receipt_binding_mismatch'):
        box.native_receipt(rid, **kwargs)
    assert L.load(str(box.root), rid) == before


def test_uncertain_timeout_survives_reopen_and_late_return_cannot_rewrite(setup):
    path, make = setup
    done, release = threading.Event(), threading.Event()
    def wait(target, envelope):
        release.wait(0.5)
        done.set()
        return Delivery('accepted', target.session_id, 'late')
    transport = Transport(wait)
    box = make(transport)
    rid = enqueue(box)
    started = time.monotonic()
    assert box.dispatch(rid, timeout=0.01)['state'] == 'unknown'
    assert time.monotonic() - started < 0.4
    assert make(transport).dispatch(rid)['state'] == 'unknown'
    release.set()
    assert done.wait(1)
    assert box.status(rid)['state'] == 'unknown'
    assert len(transport.calls) == 1
    assert acknowledge(box, rid, transport)['state'] == 'acknowledged'  # A real later callback reconciles.


def test_crash_after_intent_never_resends(setup, monkeypatch):
    path, make = setup
    transport = Transport()
    box = make(transport)
    rid = enqueue(box)
    def crash(*args, **kwargs):
        raise RuntimeError('simulated durable-outcome failure')
    monkeypatch.setattr(L, 'record_delivery', crash)
    with pytest.raises(RuntimeError):
        box.dispatch(rid)
    assert make(transport).dispatch(rid)['state'] == 'unknown'
    assert len(transport.calls) == 1


def test_wrong_session_transport_response_is_uncertain_not_dispatched(setup):
    path, make = setup
    box = make(Transport(lambda t, e: Delivery('accepted', '22222222-2222-2222-2222-222222222222')))
    rid = enqueue(box)
    assert box.dispatch(rid)['state'] == 'unknown'


def test_native_deadline_is_durable_and_later_result_reconciles(setup):
    path, make = setup
    now = [100.0]
    transport = Transport()
    box = make(transport, clock=lambda: now[0])
    rid = enqueue(box)
    box.dispatch(rid)
    acknowledge(box, rid, transport)
    now[0] = 500
    assert box.expire(rid, after=60)['state'] == 'unknown'
    assert make(transport).status(rid)['state'] == 'unknown'
    assert box.dispatch(rid)['state'] == 'unknown'
    assert len(transport.calls) == 1
    assert acknowledge(box, rid, transport, result='Late actual native reply.')['state'] == 'result'


def test_concurrent_dispatches_call_transport_exactly_once(setup):
    path, make = setup
    transport = Transport()
    box = make(transport)
    rid = enqueue(box)
    results = []
    threads = [threading.Thread(target=lambda: results.append(make(transport).dispatch(rid))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)
        assert not thread.is_alive()
    assert len(transport.calls) == 1
    assert [r['state'] for r in results] == ['dispatched', 'dispatched']


def test_torn_events_refuse_before_dispatch_and_no_raw_exception_written(setup):
    path, make = setup
    transport = Transport()
    box = make(transport)
    rid = enqueue(box)
    with (box.root / 'requests' / rid / 'events.jsonl').open('ab') as out:
        out.write(b'{"kind":')
    with pytest.raises(Refused, match='incomplete_events'):
        box.dispatch(rid)
    assert transport.calls == []


def test_provider_failure_and_cancel_do_not_retry(setup):
    path, make = setup
    transport = Transport(lambda t, e: Delivery('failed', t.session_id))
    box = make(transport)
    rid = enqueue(box)
    assert box.dispatch(rid)['state'] == 'failed'
    box.dispatch(rid)
    assert len(transport.calls) == 1
    rid2 = enqueue(box, replace(SOURCE, note_id='note-2'))
    L.cancel(str(box.root), rid2, 'robert')
    assert box.dispatch(rid2)['reason'] == 'cancelled'
    assert len(transport.calls) == 1


def test_symlink_namespace_and_request_folder_refuse(setup, tmp_path):
    path, make = setup
    box = make()
    rid = enqueue(box)
    folder = box.root / 'requests' / rid
    moved = folder.with_name('renamed')
    folder.rename(moved)
    folder.symlink_to(moved, target_is_directory=True)
    with pytest.raises(Refused, match='unsafe_directory'):
        box.dispatch(rid)


@pytest.mark.parametrize('timeout', [True, float('nan'), float('inf'), 0, 31])
def test_timeout_validation_before_intent(setup, timeout):
    path, make = setup
    box = make(Transport())
    rid = enqueue(box)
    with pytest.raises(Refused, match='invalid_timeout'):
        box.dispatch(rid, timeout=timeout)
    assert not (box.root / 'requests' / rid / 'intent.json').exists()


def test_native_ack_requires_prior_intent_and_exact_immutable_intent_binding(setup):
    path, make = setup
    transport = Transport()
    box = make(transport)
    rid = enqueue(box)
    binding = json.loads((box.root / 'requests' / rid / 'direct.json').read_bytes())
    with pytest.raises(Refused, match='receipt_binding_mismatch'):
        box.native_receipt(rid, transport=TARGET.transport, session_id=TARGET.session_id, correlation=binding['correlation'])
    box.dispatch(rid)
    intent = box.root / 'requests' / rid / 'intent.json'
    changed = json.loads(intent.read_bytes())
    changed['target_sha256'] = '0' * 64
    intent.write_text(json.dumps(changed))
    with pytest.raises(Refused, match='intent_binding_mismatch'):
        acknowledge(box, rid, transport)
    assert L.load(str(box.root), rid)['stage'] == 'queued'


def test_invalid_action_refuses_before_any_request_binding(setup):
    path, make = setup
    box = make()
    with pytest.raises(L.Refused):
        box.enqueue(TARGET.alias, SOURCE, 'x' * (L.ACTION_MAX + 1))
    assert not (box.root / 'requests').exists()
