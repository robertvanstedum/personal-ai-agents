"""Synthetic dispatch only; the actual Codex binary is never invoked."""
from dataclasses import replace
import subprocess
from types import SimpleNamespace

import pytest

from core.agent_requests import codex_desktop_queue as Q
from core.agent_requests import ledger as L
from core.agent_requests.direct_contact import Outbox, OwnerSource, Refused, Target

TARGET = Target('codex-owner', 'codex', '11111111-1111-1111-1111-111111111111',
                'local', '/synthetic/repo', Q.TRANSPORT, 'fixture-v1')
SOURCE = OwnerSource('owner', 'c-123456789abc', 'note-1', 'kept-note:note-1')
IDENTITY = (1, 2, 501, 20, 0o100755, 100, 1, 1)


def binding(**changes):
    return replace(Q.QueueEnrollment(TARGET, IDENTITY, 'a' * 64, 'b' * 64, True, True), **changes)


def setup(tmp_path, monkeypatch, behavior=None, enrollment=None):
    tmp_path.chmod(0o700)
    calls = []
    monkeypatch.setattr(Q, '_binary', lambda path: IDENTITY)
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return behavior() if behavior else SimpleNamespace(returncode=0)
    transport = Q.CodexDesktopQueue(enrollment or binding(), run=run)
    box = Outbox(tmp_path, {TARGET.alias: TARGET}, transport)
    rid = box.enqueue(TARGET.alias, SOURCE, 'Synthetic exact-session review request.')
    return box, rid, calls


def test_plan_defaults_hold_without_any_probe_or_send():
    e = Q.QueueEnrollment(TARGET, IDENTITY, 'a' * 64, 'b' * 64)
    plan = Q.admission(e)
    assert plan['holds'] == ['same_app_routing_unproven', 'native_return_channel_unproven']
    assert not plan['ready'] and not plan['automatic_retry'] and not plan['queue_acceptance_is_pickup']


def test_exact_queue_argv_and_acceptance_not_ack(tmp_path, monkeypatch):
    box, rid, calls = setup(tmp_path, monkeypatch)
    state = box.dispatch(rid)
    argv, options = calls[0]
    assert argv[:5] == [Q.BUNDLED_BINARY, 'queue', '--thread', TARGET.session_id, '--message']
    assert len(argv) == 6 and 'request_not_new_owner_approval' in argv[5]
    assert options == {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL,
                       'stderr': subprocess.DEVNULL, 'timeout': 10, 'check': False, 'cwd': TARGET.cwd}
    assert state['state'] == 'dispatched' and state['reason'] == 'transport_accepted_not_ack'
    assert L.load(str(box.root), rid)['stage'] == 'queued'
    assert box.dispatch(rid) == state and len(calls) == 1
    restarted = Outbox(tmp_path, {TARGET.alias: TARGET}, box.dispatcher)
    assert restarted.dispatch(rid) == state and len(calls) == 1


@pytest.mark.parametrize('changes', [
    {'same_app_routing_verified': False}, {'authenticated_native_return_ready': False},
    {'target': replace(TARGET, session_id='22222222-2222-2222-2222-222222222222')},
    {'binary_identity': (9, *IDENTITY[1:])},
])
def test_admission_or_identity_mismatch_sends_nothing(tmp_path, monkeypatch, changes):
    box, rid, calls = setup(tmp_path, monkeypatch, enrollment=binding(**changes))
    assert box.dispatch(rid)['state'] == 'unknown'
    assert not calls
    box.dispatch(rid)
    assert not calls  # durable intent prevents implicit retry even after preflight refusal


@pytest.mark.parametrize('behavior', [lambda: SimpleNamespace(returncode=1),
    lambda: (_ for _ in ()).throw(subprocess.TimeoutExpired('fixture', 1)),
    lambda: (_ for _ in ()).throw(OSError('synthetic-no-private-details')),
    lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
])
def test_any_ambiguous_command_outcome_is_unknown_and_never_retried(tmp_path, monkeypatch, behavior):
    box, rid, calls = setup(tmp_path, monkeypatch, behavior)
    assert box.dispatch(rid)['state'] == 'unknown'
    assert len(calls) == 1
    assert Outbox(tmp_path, {TARGET.alias: TARGET}, box.dispatcher).dispatch(rid)['state'] == 'unknown'
    assert len(calls) == 1


def test_exact_trusted_receipt_and_result_can_reconcile_unknown(tmp_path, monkeypatch):
    box, rid, calls = setup(tmp_path, monkeypatch, lambda: SimpleNamespace(returncode=1))
    assert box.dispatch(rid)['state'] == 'unknown'
    _, binding_doc, _, _, _ = box._load(rid)
    with pytest.raises(Refused, match='receipt_binding_mismatch'):
        box.native_receipt(rid, transport=Q.TRANSPORT, session_id=TARGET.session_id,
                           correlation='0' * 48)
    assert box.native_receipt(rid, transport=Q.TRANSPORT, session_id=TARGET.session_id,
                              correlation=binding_doc['correlation'])['state'] == 'acknowledged'
    assert box.native_receipt(rid, transport=Q.TRANSPORT, session_id=TARGET.session_id,
                              correlation=binding_doc['correlation'], result='Synthetic native answer.')['state'] == 'result'
    assert len(calls) == 1


def test_interrupted_intent_recovery_never_resends(tmp_path, monkeypatch):
    box, rid, calls = setup(tmp_path, monkeypatch)
    import core.agent_requests.direct_contact as D
    folder, data, target, _, _ = box._load(rid)
    D._publish(folder / 'intent.json', {'v': 1, 'at': 1, 'transport': target.transport,
                'target_sha256': D._hash(data['target']), 'correlation': data['correlation']})
    assert Outbox(tmp_path, {TARGET.alias: TARGET}, box.dispatcher).dispatch(rid)['state'] == 'unknown'
    assert not calls
