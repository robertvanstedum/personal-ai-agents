from concurrent.futures import ThreadPoolExecutor
import pytest
from core.canonical_json import digest
from core.lead_wake.inbox import Inbox, StaleWake, Conflict

OP='OB-wake-fixture'
PACKET={'task_id':'TK-test','kind':'helper_result_ready','lead':'mc','data_scope':'personal'}

@pytest.fixture
def inbox(tmp_path):
    return Inbox(tmp_path/'inbox.sqlite', lambda op: dict(PACKET))


def send(inbox, attempt=1):
    return inbox.deliver(OP,digest(PACKET),task_id='TK-test',kind='helper_result_ready',attempt=attempt)


def test_duplicate_concurrent_delivery_and_restart_keep_one_receipt(inbox):
    with ThreadPoolExecutor(max_workers=2) as pool:
        values=list(pool.map(lambda _:send(inbox), range(2)))
    assert values[0]==values[1]
    restarted=Inbox(inbox.path,inbox.resolve)
    assert restarted.lookup(OP,attempt=1)['state']=='applied'
    assert restarted.queued()==[(OP,PACKET)]


def test_absence_fences_old_send_but_allows_revalidated_new_attempt(inbox):
    assert inbox.lookup(OP,attempt=1)=={'state':'absent'}
    with pytest.raises(StaleWake):send(inbox)
    send(inbox,attempt=2)
    with pytest.raises(StaleWake):send(inbox)
    assert len(inbox.queued())==1


def test_changed_task_cannot_use_the_same_operation(inbox):
    with pytest.raises(Conflict):
        inbox.deliver(OP,digest(PACKET),task_id='TK-other',kind='helper_result_ready',attempt=1)
    assert inbox.queued()==[]


def test_cancel_after_receipt_keeps_receipt_but_suppresses_execution(inbox):
    send(inbox)
    def canceled(op):raise StaleWake('canceled')
    inbox.resolve=canceled
    assert inbox.queued()==[]
    assert inbox.lookup(OP,attempt=1)['state']=='applied'


def test_unavailable_authority_is_not_proof_of_cancellation(inbox):
    send(inbox)
    def down(op):raise OSError('unavailable')
    inbox.resolve=down
    with pytest.raises(OSError):inbox.queued()
    inbox.resolve=lambda op:dict(PACKET)
    assert len(inbox.queued())==1


def test_lookup_races_delivery_without_false_absence(inbox):
    from threading import Barrier
    barrier = Barrier(2)
    def delivery():
        barrier.wait()
        try:
            send(inbox)
            return 'delivered'
        except StaleWake:
            return 'fenced'
    def lookup():
        barrier.wait()
        return inbox.lookup(OP, attempt=1)['state']
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(delivery), pool.submit(lookup)
        result = a.result(), b.result()
    assert result in [('delivered', 'applied'), ('fenced', 'absent')]
    assert len(inbox.queued()) == (1 if result[0] == 'delivered' else 0)


def test_two_consumers_only_one_claim_and_restart_never_reinvokes(inbox):
    send(inbox)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda n: inbox.claim(OP, 'claim-identity-'+str(n)*16), range(2)))
    assert sorted(claims) == [False, True]
    restarted = Inbox(inbox.path, inbox.resolve)
    assert restarted.claim(OP, 'replacement-claim-identity') is False
    assert restarted.queued() == []


def test_uncertain_attempt_only_accepts_its_late_exact_review(inbox):
    send(inbox)
    owner = 'claim-identity-original'
    assert inbox.claim(OP, owner)
    inbox.uncertain(OP, owner)
    assert inbox.claim(OP, 'claim-identity-retry') is False
    result = dict(verdict='approve', reasons='Evidence checked.', answer='The source identifies the file.')
    with pytest.raises(Conflict): inbox.save_review(OP, 'claim-identity-retry', result)
    inbox.save_review(OP, owner, result)
    restarted = Inbox(inbox.path, inbox.resolve)
    assert restarted.review(OP) == result
    restarted.save_review(OP, owner, result)
    with pytest.raises(Conflict): restarted.save_review(OP, owner, dict(result, answer='Changed answer'))


def test_cancel_after_claim_refuses_late_review_but_keeps_receipt(inbox):
    send(inbox)
    assert inbox.claim(OP, 'claim-identity-original')
    def canceled(op): raise StaleWake('canceled')
    inbox.resolve = canceled
    with pytest.raises(StaleWake):
        inbox.save_review(OP, 'claim-identity-original', dict(verdict='approve', reasons='Checked', answer='Late'))
    assert inbox.review(OP) is None
    assert inbox.lookup(OP, attempt=1)['state'] == 'applied'
