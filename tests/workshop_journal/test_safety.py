"""Folder, link and permission safety, and the lock (v0.6 section 6 as amended by v0.7 Unit 1; acceptance row A01)."""
from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

from conftest import REPO, WORKSHOP, envelope, new_id, progress, run_child
from core.workshop_journal import fsutil
from core.workshop_journal.journal import Journal


def base(root) -> Path:
    return Path(root) / WORKSHOP


def seeded(root) -> Journal:
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    assert j.append(progress("seed")).committed
    return j


def refused(result, reason_part: str | None = None):
    assert (result.status, result.committed, result.exit_code) == ("unsafe_root", False, 2), result.to_json()
    if reason_part:
        assert reason_part in result.reason


# ── created with private modes ─────────────────────────────────────────────────────────────────────────────────────
def test_A01_everything_the_journal_creates_is_private(root):
    seeded(root)
    assert stat.S_IMODE(base(root).stat().st_mode) == 0o700
    for name in ("events.jsonl", ".lock", "state.json"):
        assert stat.S_IMODE((base(root) / name).stat().st_mode) == 0o600, name
    for folder in ("prepared",):
        assert stat.S_IMODE((base(root) / folder).stat().st_mode) == 0o700


# ── links and look-alikes are refused, not followed ────────────────────────────────────────────────────────────────
def test_A01_a_symlinked_root_is_refused_and_nothing_is_written_through_it(root, tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir(mode=0o700)
    link = tmp_path / "linked"
    link.symlink_to(real)
    refused(Journal(str(link), WORKSHOP).append(envelope()), "root")
    assert list(real.iterdir()) == []


def test_A01_a_symlinked_workshop_folder_is_refused(root, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (Path(root) / WORKSHOP).symlink_to(elsewhere)
    refused(Journal(root, WORKSHOP).append(envelope()), "folder")
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("name", ["events.jsonl", ".lock", "state.json"])
def test_A01_a_symlinked_managed_file_is_refused(root, tmp_path, name):
    seeded(root)
    target = tmp_path / "secret.txt"
    target.write_text("do not touch\n")
    os.chmod(target, 0o600)
    (base(root) / name).unlink()
    (base(root) / name).symlink_to(target)
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    if name == "state.json":                                    # state is derived: a bad state file never blocks the journal
        assert j.append(progress("still fine")).committed
        assert not (base(root) / "state.json").is_symlink()     # publishing replaced the link itself, never wrote through it
        assert j.state()[1] == "file"
    else:
        refused(j.append(progress("x")))
    assert target.read_text() == "do not touch\n"


def test_A01_a_hard_linked_journal_is_refused(root, tmp_path):
    seeded(root)
    os.link(base(root) / "events.jsonl", tmp_path / "other-name")
    refused(Journal(root, WORKSHOP, lock_timeout=0.3).append(progress("x")), "single")
    assert Journal(root, WORKSHOP).read().status == "unsafe_root"


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o604])
def test_A01_a_journal_others_can_read_or_write_is_refused(root, mode):
    seeded(root)
    os.chmod(base(root) / "events.jsonl", mode)
    refused(Journal(root, WORKSHOP, lock_timeout=0.3).append(progress("x")))


@pytest.mark.parametrize("mode", [0o755, 0o770, 0o707])
def test_A01_a_workshop_folder_open_to_others_is_refused(root, mode):
    seeded(root)
    os.chmod(base(root), mode)
    refused(Journal(root, WORKSHOP, lock_timeout=0.3).append(progress("x")))


def test_A01_a_root_others_can_write_is_refused(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o777)
    refused(Journal(str(shared), WORKSHOP).append(envelope()), "root")


def test_A01_a_directory_where_a_file_belongs_is_refused(root):
    seeded(root)
    (base(root) / "events.jsonl").unlink()
    (base(root) / "events.jsonl").mkdir(mode=0o700)
    refused(Journal(root, WORKSHOP, lock_timeout=0.3).append(progress("x")))


def test_A01_a_workshop_id_is_a_name_not_a_path(root):
    for bad in ("../x", "a/b", "", ".", "..", "x" * 200, "has space", "Upper"):
        with pytest.raises(Exception):
            Journal(root, bad)


# ── a swap between opening and using is caught ─────────────────────────────────────────────────────────────────────
def test_A01_a_journal_swapped_after_it_was_opened_is_refused_and_neither_file_is_written(root, monkeypatch):
    j = seeded(root)
    path = base(root) / "events.jsonl"
    real = fsutil.read_all
    state = {"done": False}

    def swap_after_read(fd):
        data = real(fd)
        if not state["done"]:
            state["done"] = True
            os.rename(path, base(root) / "moved-away")
            path.write_bytes(data)
            os.chmod(path, 0o600)
        return data
    monkeypatch.setattr(fsutil, "read_all", swap_after_read)
    size_old, size_new = (base(root) / "events.jsonl").stat().st_size, None
    refused(j.append(progress("racing")), "diverge")
    monkeypatch.undo()
    assert (base(root) / "moved-away").stat().st_size == size_old
    assert path.stat().st_size == size_old                      # the replacement was not appended to either


def test_A01_a_lock_file_replaced_while_waiting_is_refused(root, monkeypatch):
    j = seeded(root)
    lock = base(root) / ".lock"
    real = fcntl.flock

    def flock_then_swap(fd, op):
        real(fd, op)
        if op & fcntl.LOCK_EX:
            lock.unlink()
            lock.write_bytes(b"")
            os.chmod(lock, 0o600)
    monkeypatch.setattr(fcntl, "flock", flock_then_swap)
    refused(j.append(progress("racing")), "diverge")


# ── the lock ───────────────────────────────────────────────────────────────────────────────────────────────────────
HOLD = """
import fcntl, os, sys, time
fd = os.open(os.path.join(sys.argv[1], 'workshop-neubau', '.lock'), os.O_RDWR)
fcntl.flock(fd, fcntl.LOCK_EX)
print('locked', flush=True)
time.sleep(float(sys.argv[2]))
"""


def hold_lock(root, seconds):
    proc = subprocess.Popen([os.sys.executable, "-c", HOLD, root, str(seconds)], stdout=subprocess.PIPE, text=True,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert proc.stdout.readline().strip() == "locked"
    return proc


def test_A01_a_busy_lock_is_a_bounded_retryable_failure_that_never_breaks_the_lock(root):
    j = seeded(root)
    before = (base(root) / "events.jsonl").read_bytes()
    holder = hold_lock(root, 3)
    try:
        started = time.monotonic()
        busy = j.append(progress("waits"))
        waited = time.monotonic() - started
        assert (busy.status, busy.exit_code, busy.retryable, busy.committed) == ("lock_busy", 4, True, False)
        assert 0.25 <= waited < 2.0
        assert (base(root) / "events.jsonl").read_bytes() == before
    finally:
        holder.kill()
        holder.wait()
    assert j.append(progress("after it is free")).committed          # a dead holder frees the lock; nothing was broken by force


def test_A01_a_read_while_a_writer_holds_the_lock_reports_in_progress_for_a_partial_tail_and_stays_readable(root):
    j = seeded(root)
    path = base(root) / "events.jsonl"
    with open(path, "ab") as handle:
        handle.write(b'{"v":2,"partial')
    holder = hold_lock(root, 3)
    try:
        read = j.read()
        assert read.status == "tail_in_progress" and len(read.events) == 1
    finally:
        holder.kill()
        holder.wait()
    assert j.read().status == "torn_tail"                             # nobody holds it now: this is a real torn tail


def test_A01_a_read_with_a_clean_journal_while_locked_is_still_ok(root):
    j = seeded(root)
    holder = hold_lock(root, 3)
    try:
        assert j.read().status == "ok"
    finally:
        holder.kill()
        holder.wait()


def test_A01_legacy_and_v2_callers_share_the_same_lock(root):
    j = seeded(root)
    holder = hold_lock(root, 3)
    try:
        legacy = j.append_legacy({"v": 1, "at": "2026-10-08T12:00:00Z", "actor": "robert", "kind": "note", "text": "hi"})
        assert legacy.status == "lock_busy"
    finally:
        holder.kill()
        holder.wait()


def test_A01_reads_never_create_anything(root):
    j = Journal(root, WORKSHOP)
    assert j.read().status == "missing" and j.state() == (None, "missing")
    assert list(Path(root).iterdir()) == []
    assert j.get(new_id()).status == "missing" and j.verify()["status"] == "missing"
    assert list(Path(root).iterdir()) == []


def test_A01_an_enoent_answer_to_a_racing_create_is_retried(root, monkeypatch):
    """Seen on APFS under two writers starting at once: open(O_CREAT) answered ENOENT although the name was being created."""
    real, hits = os.open, []

    def flaky(path, flags, *a, **k):
        if path == ".lock" and flags & os.O_CREAT and len(hits) < 3:
            hits.append(1)
            raise FileNotFoundError(2, "racing create")
        return real(path, flags, *a, **k)
    monkeypatch.setattr(os, "open", flaky)
    assert Journal(root, WORKSHOP, lock_timeout=0.3).append(progress("first writer")).committed and len(hits) == 3


def test_A01_a_missing_file_without_create_is_still_missing_at_once(root, monkeypatch):
    seeded(root)
    calls = []
    real = os.open
    monkeypatch.setattr(os, "open", lambda p, f, *a, **k: (calls.append(p) if p == "events.jsonl" else None) or real(p, f, *a, **k))
    (base(root) / "events.jsonl").unlink()
    assert Journal(root, WORKSHOP).read().status == "missing" and calls.count("events.jsonl") == 1


# ── a synced copy (events and state only, no lock file) can be read but never written ────────────────────────────
def test_a_replica_without_a_lock_file_is_readable_and_never_writable(root, tmp_path):
    j = seeded(root)
    assert j.append(progress("second")).committed
    copy = tmp_path / "replica"
    (copy / WORKSHOP).mkdir(parents=True, mode=0o700)
    os.chmod(copy, 0o700)
    for name in ("events.jsonl",):
        data = (base(root) / name).read_bytes()
        (copy / WORKSHOP / name).write_bytes(data)
        os.chmod(copy / WORKSHOP / name, 0o600)
    plain = Journal(str(copy), WORKSHOP)
    assert plain.read().status == "unsupported_writer"                                        # a normal reader still refuses a lockless folder
    replica = Journal(str(copy), WORKSHOP, replica=True)
    read = replica.read()
    assert read.status == "ok" and len(read.events) == 2 and replica.brief()["as_of"]["seq"] == 2
    refused = replica.append(progress("must not be written"))
    assert (refused.status, refused.reason, refused.committed) == ("invalid_input", "replica_is_read_only", False)
    assert replica.write_state is not None
    with pytest.raises(Exception):
        replica.write_state()
    assert sorted(p.name for p in (copy / WORKSHOP).iterdir()) == ["events.jsonl"]            # nothing was created beside the copy
    (copy / WORKSHOP / "events.jsonl").write_bytes(b"garbage\n")
    assert replica.read().status == "corrupt"                                                 # and damage is still damage
