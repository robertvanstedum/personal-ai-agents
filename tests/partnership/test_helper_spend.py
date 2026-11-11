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
