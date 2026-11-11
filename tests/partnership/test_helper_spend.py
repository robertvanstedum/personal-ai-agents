from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from services.helper_seats.spend import Ledger, AlreadyCalled, CapReached


def reserve(ledger,key,task='TK-example',amount=0.05):
    return ledger.reserve(key,bound_usd=amount,task_spent=0,task_cap=0.05,purpose='bounded test',task_id=task)


def test_parallel_reservations_share_one_daily_cap(tmp_path):
    def attempt(n):
        try:
            reserve(Ledger(tmp_path,daily_cap=0.05),str(n),task='TK-'+str(n))
            return 'reserved'
        except CapReached:
            return 'capped'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,range(2)))==['capped','reserved']


def test_task_cap_and_call_identity_survive_midnight_and_restart(tmp_path):
    first=Ledger(tmp_path,daily_cap=1,clock=lambda:86400)
    reserve(first,'original')
    later=Ledger(tmp_path,daily_cap=1,clock=lambda:172800)
    with pytest.raises(AlreadyCalled):reserve(later,'original')
    with pytest.raises(CapReached):reserve(later,'revision')
    assert later.spent_today()==0


def test_corrupted_unknown_reservation_does_not_become_zero(tmp_path):
    ledger=Ledger(tmp_path,daily_cap=1)
    p=reserve(ledger,'first')
    p.write_text('{partial')
    with pytest.raises(json.JSONDecodeError):reserve(ledger,'next',task='TK-other')


def test_shared_task_allowance_counts_helper_and_review(tmp_path):
    ledger=Ledger(tmp_path,daily_cap=1)
    p=reserve(ledger,'helper',amount=0.03)
    reserve(ledger,'lead-review',amount=0.02)
    with pytest.raises(CapReached):reserve(ledger,'retry',amount=0.01)
    ledger.settle(p,0.01)
    ledger.settle(p,0.01)
    with pytest.raises(ValueError):ledger.settle(p,0)
    reserve(ledger,'remaining',amount=0.02)


def test_new_ledger_ancestors_are_durable_before_reservation(tmp_path, monkeypatch):
    import os
    real=os.fsync
    synced=[]
    def sync(fd):
        synced.append(os.fstat(fd).st_ino)
        real(fd)
    monkeypatch.setattr(os,'fsync',sync)
    root=tmp_path/'new-state'/'spend'
    ledger=Ledger(root,daily_cap=1)
    reserve(ledger,'first')
    assert tmp_path.stat().st_ino in synced
    assert root.parent.stat().st_ino in synced
    assert root.stat().st_ino in synced


def shared(ledger, key, part, amount, task='TK-shared'):
    return ledger.reserve(key, bound_usd=amount, task_spent=0, task_cap=0.05,
                          purpose=part, task_id=task, part=part)


def test_partition_is_immutable_and_legacy_calls_cannot_consume_review(tmp_path):
    from services.helper_seats.spend import AllocationConflict
    ledger = Ledger(tmp_path, daily_cap=0.1)
    allocation = ledger.allocate_task('TK-shared', task_cap=0.05, review_reserve=0.02)
    assert allocation['helper'] == pytest.approx(0.03)
    assert Ledger(tmp_path, daily_cap=0.1).allocate_task('TK-shared', task_cap=0.05, review_reserve=0.02) == allocation
    shared(ledger, 'helper-first', 'helper', 0.03)
    with pytest.raises(CapReached, match='helper'):
        shared(ledger, 'helper-retry', 'helper', 0.001)
    with pytest.raises(AllocationConflict):
        reserve(ledger, 'legacy-bypass', task='TK-shared', amount=0.02)
    with pytest.raises(AllocationConflict):
        ledger.allocate_task('TK-shared', task_cap=0.05, review_reserve=0.01)
    with pytest.raises(AllocationConflict):
        shared(Ledger(tmp_path, daily_cap=1), 'higher-cap', 'review', 0.02)
    shared(ledger, 'review-first', 'review', 0.02)
    assert ledger.spent_today() == pytest.approx(0.05)


def test_partition_counts_prior_unknown_charges_and_never_fabricates_headroom(tmp_path):
    ledger = Ledger(tmp_path, daily_cap=1)
    reserve(ledger, 'legacy-helper', task='TK-shared', amount=0.04)
    with pytest.raises(CapReached, match='helper'):
        ledger.allocate_task('TK-shared', task_cap=0.05, review_reserve=0.02)
    assert not (tmp_path/'allocations').exists()


def test_helper_review_processes_share_daily_and_task_caps_after_restart(tmp_path):
    ledger = Ledger(tmp_path, daily_cap=0.05, clock=lambda: 86400)
    ledger.allocate_task('TK-shared', task_cap=0.05, review_reserve=0.02)
    marker = shared(ledger, 'helper', 'helper', 0.03)
    later = Ledger(tmp_path, daily_cap=0.05, clock=lambda: 172800)
    shared(later, 'review', 'review', 0.02)
    with pytest.raises(AlreadyCalled):
        shared(later, 'review', 'review', 0.02)
    with pytest.raises(CapReached):
        shared(later, 'next-review', 'review', 0.001)
    # An actual joined receipt can tighten cost; unknown reservation cannot.
    later.settle(marker, 0.02)
    shared(later, 'helper-revision', 'helper', 0.01)
    assert later.spent_today() == pytest.approx(0.03)


def test_concurrent_review_claims_cannot_each_take_partition(tmp_path):
    Ledger(tmp_path, daily_cap=1).allocate_task('TK-shared', task_cap=0.05, review_reserve=0.02)
    def attempt(n):
        try:
            shared(Ledger(tmp_path, daily_cap=1), 'review-'+str(n), 'review', 0.02)
            return 'reserved'
        except CapReached:
            return 'capped'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ['capped', 'reserved']


def test_worker_does_not_invent_lower_cost_bound_to_make_room_for_review(tmp_path):
    from services.helper_seats.worker import reserve_model_call
    ledger = Ledger(tmp_path, daily_cap=1)
    args = dict(task_id='TK-shared', key='helper', task_cap=0.05, purpose='test')
    with pytest.raises(ValueError, match='proved helper call bound'):
        reserve_model_call(ledger, **args, environ={'HELPER_LEAD_REVIEW_RESERVE_USD': '0.02'})
    assert ledger.spent_today() == 0
    with pytest.raises(CapReached, match='helper'):
        reserve_model_call(ledger, **args, environ={'HELPER_LEAD_REVIEW_RESERVE_USD': '0.02', 'HELPER_MODEL_CALL_BOUND_USD': '0.05'})
    assert ledger.spent_today() == 0
    marker, bound = reserve_model_call(ledger, **args, environ={
        'HELPER_LEAD_REVIEW_RESERVE_USD': '0.02', 'HELPER_MODEL_CALL_BOUND_USD': '0.03'})
    assert bound == 0.03 and json.loads(marker.read_text())['part'] == 'helper'
    shared(ledger, 'review', 'review', 0.02)
    assert ledger.spent_today() == pytest.approx(0.05)


def test_worker_shared_partition_is_off_by_default(tmp_path):
    from services.helper_seats.worker import reserve_model_call
    ledger = Ledger(tmp_path, daily_cap=1)
    marker, bound = reserve_model_call(ledger, task_id='TK-old', key='old', task_cap=0.05, purpose='old', environ={})
    assert bound == 0.05 and 'part' not in json.loads(marker.read_text())
    assert not (tmp_path/'allocations').exists()


@pytest.mark.parametrize('prompt',['x'*200_000,'😀'*20_000,'"'*100_000])
def test_total_serialized_model_request_rejected_before_reserve_or_dispatch(tmp_path,prompt):
    from services.helper_seats.worker import call_model,RequestTooLarge
    ledger=Ledger(tmp_path/'spend',daily_cap=1)
    calls=[]
    with pytest.raises(RequestTooLarge):
        call_model(ledger,prompt=prompt,purpose='large request',task_id='TK-large',key='large',task_cap=.05,
                   environ={'HELPER_MODEL':'minimoi-test'},post=lambda *a,**kw:calls.append(kw))
    assert calls==[] and ledger.spent_today()==0
    assert not list((tmp_path/'spend').rglob('*.json'))


def test_request_bytes_sent_are_exactly_the_bounded_bytes_and_output_is_capped(tmp_path):
    from types import SimpleNamespace
    from services.helper_seats.worker import call_model,MAX_REQUEST_BYTES
    key=tmp_path/'key';key.write_text('synthetic')
    env={'HELPER_MODEL':'minimoi-test','HELPER_GATEWAY_KEY_FILE':str(key),'HELPER_GATEWAY_URL':'http://test/v1',
         'HELPER_LEAD_REVIEW_RESERVE_USD':'.005','HELPER_MODEL_CALL_BOUND_USD':'.025'}
    ledger=Ledger(tmp_path/'spend',daily_cap=1)
    calls=[]
    def post(url,**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'choices':[{'message':{'content':'answer'}}]})
    assert call_model(ledger,prompt='A German question: Grüße 😀',purpose='bounded',task_id='TK-small',key='small',
                      task_cap=.05,environ=env,post=post)=='answer'
    raw=calls[0]['data']
    assert isinstance(raw,bytes) and len(raw)<=MAX_REQUEST_BYTES and b'\\ud83d' in raw
    assert json.loads(raw)['max_tokens']==400
    assert calls[0]['allow_redirects'] is False
    assert ledger.spent_today()==pytest.approx(.025)
