from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from core.lead_wake.lane import Lane, serialize_cos, configured_cos_lane


def test_normal_turn_and_review_use_the_same_lock(tmp_path,monkeypatch):
    monkeypatch.setenv('COS_LEAD_WAKE','1')
    monkeypatch.setenv('COS_EXECUTION_LANE_DIR',str(tmp_path))
    monkeypatch.setenv('COS_STORE_ENVIRONMENT','synthetic')
    entered,finish=Event(),Event()
    @serialize_cos
    def owner():
        entered.set()
        assert finish.wait(3)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(owner)
        assert entered.wait(3)
        with Lane(tmp_path,environment='synthetic',lead='cos').hold(blocking=False) as held:
            assert held is False
        finish.set();pending.result()
    with configured_cos_lane().hold(blocking=False) as held:
        assert held is True


def test_review_blocks_normal_turn_until_release(tmp_path,monkeypatch):
    monkeypatch.setenv('COS_LEAD_WAKE','1')
    monkeypatch.setenv('COS_EXECUTION_LANE_DIR',str(tmp_path))
    monkeypatch.setenv('COS_STORE_ENVIRONMENT','synthetic')
    entered,trying=Event(),Event()
    @serialize_cos
    def owner():entered.set()
    def attempt():trying.set();owner()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with configured_cos_lane().hold(blocking=False) as held:
            assert held
            pending=pool.submit(attempt)
            assert trying.wait(3)
            assert not entered.is_set()
        pending.result(timeout=3)
    assert entered.is_set()


def test_enabled_without_persistent_config_fails_closed():
    with pytest.raises(RuntimeError):configured_cos_lane({'COS_LEAD_WAKE':'1'})
    assert configured_cos_lane({}) is None
