"""C1, the store (spec v0.2 §3.2–3.3): T1, and T21's in-process serialization."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from minimoi_portal.action_waiting import store as st
from aw_fakes import clock

PYTEST_FIXTURES = (clock,)                  # used by name in the tests below


@pytest.fixture
def store(tmp_path, clock):
    return st.Store(tmp_path / "notices", clock=clock)


def _observe(store, key, versions, now=1.0, **kw):
    with store.edit() as tx:
        return st.observe(tx, key=key, kind="guest_access", versions=versions, now=now, **kw)


def test_create_join_resolve_and_never_reopen_on_seen_versions(store):
    outcome, item = _observe(store, "guest:a", ["pending:#1"])
    assert (outcome, item["code"], item["generation"]) == ("created", "W-1", 1)
    outcome, item = _observe(store, "guest:a", ["pending:#1", "guild:4@t"])
    assert outcome == "joined" and item["refs"] == ["pending:#1", "guild:4@t"]
    assert _observe(store, "guest:a", ["pending:#1", "guild:4@t"])[0] == "open"
    with store.edit() as tx:
        assert st.resolve(tx, tx.data["items"]["W-1"], "source_closed", 2.0)
    data = store.read()
    assert data["items"]["W-1"]["state"] == "resolved" and st.open_items(data) == []
    # A lingering answered ref (or a flicker) shows only seen versions: nothing new, nothing re-told.
    assert _observe(store, "guest:a", ["guild:4@t"]) == ("seen", None)
    # A genuinely new version after the answer opens the next generation.
    outcome, item = _observe(store, "guest:a", ["guild:4@t2"])
    assert (outcome, item["code"], item["generation"]) == ("created", "W-2", 2)
    with store.edit() as tx:
        assert not st.resolve(tx, tx.data["items"]["W-1"], "source_closed", 3.0)   # terminal


def test_leftover_tmp_is_ignored_and_removed_on_the_next_commit(store):
    _observe(store, "guest:a", ["pending:#1"])
    stray = store.root / ".store.json.999.1.tmp"
    stray.write_text("{not json")
    assert store.read()["next_code"] == 2
    _observe(store, "guest:b", ["pending:#2"])
    assert not stray.exists()


def test_malformed_store_is_refused_and_never_overwritten(store):
    _observe(store, "guest:a", ["pending:#1"])
    (store.root / st.STORE).write_text("{broken")
    with pytest.raises(st.StoreUnavailable, match="store_malformed"):
        _observe(store, "guest:b", ["pending:#2"])
    assert (store.root / st.STORE).read_text() == "{broken"
    assert not store.readable()


def test_a_crash_before_the_rename_keeps_the_old_record(store, monkeypatch):
    _observe(store, "guest:a", ["pending:#1"])
    real = os.replace

    def crash(src, dst):
        raise OSError("disk gone")
    monkeypatch.setattr(st.os, "replace", crash)
    with pytest.raises(st.StoreUnavailable, match="store_write_failed"):
        _observe(store, "guest:b", ["pending:#2"])
    assert store.last_commit_ok is False
    monkeypatch.setattr(st.os, "replace", real)
    data = store.read()
    assert list(data["items"]) == ["W-1"] and data["next_code"] == 2


def test_an_exception_inside_a_transaction_commits_nothing(store):
    with pytest.raises(RuntimeError):
        with store.edit() as tx:
            st.observe(tx, key="guest:a", kind="guest_access", versions=["x"], now=1.0)
            raise RuntimeError("boom")
    assert store.read()["items"] == {}


def test_events_carry_codes_and_fixed_reasons_only(store):
    _observe(store, "guest:a", ["pending:#1"])
    with store.edit() as tx:
        tx.event("send_failed", "W-1", reason="free text with spaces https://api.telegram.org/bot123:abc")
        st.resolve(tx, tx.data["items"]["W-1"], "source_closed", 2.0)
    lines = [json.loads(x) for x in (store.root / st.EVENTS).read_text().splitlines()]
    assert {e["kind"] for e in lines} == {"created", "send_failed", "resolved"}
    for e in lines:
        assert set(e) <= {"kind", "code", "reason", "message_id", "at"}
    assert [e["reason"] for e in lines if e["kind"] == "send_failed"] == ["other"]


def test_hmac_key_is_private_stable_and_kind_separated(store):
    a = store.digest("guest", "ann@example.test")
    assert a == store.digest("guest", "ann@example.test") != store.digest("reset", "ann@example.test")
    assert len(a) == 20 and "ann" not in a
    key = store.root / st.KEY
    assert key.stat().st_mode & 0o777 == 0o600 and len(key.read_bytes()) == 32
    other = st.Store(store.root.parent / "other")
    assert other.digest("guest", "ann@example.test") != a       # a per-environment key


def test_a_store_without_a_folder_refuses_cleanly():
    s = st.Store(None)
    with pytest.raises(st.StoreUnavailable, match="store_unset"):
        s.read()
    assert s.read_state() == {} and not s.readable()


def test_two_threads_serialize_on_the_lock(store):
    """T21: request threads and the sender thread exclude each other inside one process."""
    def worker(n):
        for i in range(25):
            _observe(store, f"guest:{n}-{i}", [f"v{n}-{i}"])
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    data = store.read()
    assert data["next_code"] == 101 and len(data["items"]) == 100
    assert len({i["key"] for i in data["items"].values()}) == 100


def test_another_process_holding_the_lock_blocks_a_write(store):
    store.root.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen([sys.executable, "-c", (
        "import fcntl, os, sys, time\n"
        f"fd = os.open({str(store.root / '.lock')!r}, os.O_RDWR | os.O_CREAT, 0o600)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "print('held', flush=True)\n"
        "time.sleep(0.8)\n")], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        _observe(store, "guest:a", ["pending:#1"])
        assert time.monotonic() - started > 0.4
    finally:
        holder.wait(5)
