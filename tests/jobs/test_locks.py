"""WS3 C1.3: lock ownership probes and the orphan predicate (Codex build acceptance item 5)."""
from __future__ import annotations

import fcntl
import importlib.util
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.jobs import locks

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 28, 7, 0, tzinfo=timezone.utc)
M = timedelta(minutes=1)


def _fd_lock(path):
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def test_probe_reports_absent_and_never_creates(tmp_path):
    target = tmp_path / ".refresh.lock"
    assert locks.probe(target) == "absent" and not target.exists()


def test_an_old_unlocked_file_is_free_whatever_its_age(tmp_path):
    target = tmp_path / ".refresh.lock"
    target.write_text("")
    os.utime(target, (0, 0))                                        # ancient mtime proves nothing
    assert locks.probe(target) == "free"


def test_probe_sees_a_live_holder_and_leaves_it_holding(tmp_path):
    target = tmp_path / ".refresh.lock"
    owner = _fd_lock(target)
    before = os.stat(target)
    try:
        assert locks.probe(target) == "held"
        third = os.open(str(target), os.O_RDWR)                     # the owner still holds it exclusively
        try:
            try:
                fcntl.flock(third, fcntl.LOCK_EX | fcntl.LOCK_NB)
                raise AssertionError("the probe must not have released the owner's lock")
            except BlockingIOError:
                pass
        finally:
            os.close(third)
        after = os.stat(target)
        assert (before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns)
    finally:
        os.close(owner)


def test_probe_sees_a_holder_in_another_process(tmp_path):
    target = tmp_path / ".refresh.lock"
    code = ("import fcntl,os,sys,time; fd=os.open(sys.argv[1], os.O_RDWR|os.O_CREAT, 0o600); "
            "fcntl.flock(fd, fcntl.LOCK_EX); print('locked', flush=True); time.sleep(30)")
    holder = subprocess.Popen([sys.executable, "-c", code, str(target)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        assert locks.probe(target) == "held"
    finally:
        holder.kill()
        holder.wait()
    assert locks.probe(target) == "free"


def test_a_symlinked_lock_is_unknown(tmp_path):
    real = tmp_path / "real.lock"
    real.write_text("")
    link = tmp_path / ".refresh.lock"
    link.symlink_to(real)
    assert locks.probe(link) == "unknown"


def c(name, age_min, mounts=()):
    return locks.Container(name, NOW - age_min * M, tuple(mounts))


def test_a_long_step_whose_owner_holds_and_runs_is_not_an_orphan():
    out = locks.classify([c("minimoi-vault-seed", 30)], "held", True, NOW)
    assert out["seed"] == {"count": 1, "orphans": 0, "undetermined": 0, "oldest_s": 1800}


def test_the_orphan_predicate_needs_lock_not_held_and_owner_not_running():
    """Codex locks review 1: orphan only when the lock is known not held AND the owner is known not running."""
    cases = {("free", False): (1, 0), ("absent", False): (1, 0),            # orphan
             ("held", False): (0, 0), ("free", True): (0, 0), ("held", True): (0, 0),   # a live owner: no claim
             ("unknown", False): (0, 1), ("free", None): (0, 1), ("absent", None): (0, 1),  # cannot tell
             ("unknown", True): (0, 0), ("held", None): (0, 0)}
    for (lock_state, running), (orphans, undetermined) in cases.items():
        out = locks.classify([c("minimoi-vault-seed", 30)], lock_state, running, NOW)
        assert (out["seed"]["orphans"], out["seed"]["undetermined"]) == (orphans, undetermined), (lock_state, running)


def test_a_young_container_is_never_an_orphan():
    out = locks.classify([c("minimoi-vault-seed", 10), c("minimoi-vault-index-ab12cd34ef56", 5)], "free", False, NOW)
    assert out["seed"]["orphans"] == 0 and out["index_step"]["orphans"] == 0 and out["index_step"]["count"] == 1


def test_an_unnamed_container_with_a_vault_work_mount_is_an_unknown_holder_not_an_orphan():
    out = locks.classify([c("epic_davinci", 300, ["/index:/opt/minimoi/vault-work/meaning-seed"]),
                          c("minimoi-portal", 900, ["/app/runtime:/var/lib/docker/volumes/x"])], "free", False, NOW)
    assert out["unknown_holder"] == {"count": 1, "orphans": 0, "undetermined": 0, "oldest_s": 18000}
    assert out["seed"]["count"] == 0


def test_docker_unreadable_is_none_never_zero():
    assert locks.classify(None, "free", False, NOW) is None


class Done:
    def __init__(self, rc, out=""):
        self.returncode, self.stdout = rc, out


def test_list_containers_parses_inspect_and_reports_failure_as_none():
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[1] == "ps":
            return Done(0, "abc\ndef\n")
        return Done(0, "/minimoi-vault-seed|2026-11-28T06:30:00.123Z|/index:/opt/minimoi/vault-work/meaning-seed;\n"
                       "/minimoi-portal|2026-11-28T00:00:00Z|\n")
    rows = locks.list_containers(run=run)
    assert [r.name for r in rows] == ["minimoi-vault-seed", "minimoi-portal"]
    assert rows[0].mounts == ("/index:/opt/minimoi/vault-work/meaning-seed",) and rows[0].started_at is not None
    assert all("rm" not in a and "kill" not in a for a in calls)        # read-only commands only
    assert locks.list_containers(run=lambda argv, **kw: Done(1)) is None


def test_a_partial_or_malformed_inspect_is_unknown_never_empty():
    """Codex locks review 3: ps listed a container, inspect described none (or garbage): unknown, not zero."""
    def run_with(inspect_out):
        return lambda argv, **kw: Done(0, "abc\n") if argv[1] == "ps" else Done(0, inspect_out)
    assert locks.list_containers(run=run_with("garbage\n")) is None
    assert locks.list_containers(run=run_with("")) is None
    assert locks.list_containers(run=run_with("/minimoi-vault-seed|not-a-time|\n")) is None
    two = lambda argv, **kw: Done(0, "abc\ndef\n") if argv[1] == "ps" else Done(0, "/a|2026-11-28T06:30:00Z|\n")
    assert locks.list_containers(run=two) is None
    assert locks.list_containers(run=lambda argv, **kw: Done(0, "")) == []        # nothing running is truly empty


def test_a_fifo_in_the_locks_place_never_blocks(tmp_path):
    """Codex locks review 2: os.open on a FIFO with no writer blocked the probe."""
    fifo = tmp_path / ".refresh.lock"
    os.mkfifo(fifo)
    done = subprocess.run([sys.executable, "-c",
                           "import sys; sys.path.insert(0, sys.argv[2]); from core.jobs import locks; print(locks.probe(sys.argv[1]))",
                           str(fifo), str(REPO)], capture_output=True, text=True, timeout=10)
    assert done.stdout.strip() == "unknown" and fifo.exists()


def test_a_directory_in_the_locks_place_is_unknown(tmp_path):
    (tmp_path / ".refresh.lock").mkdir()
    assert locks.probe(tmp_path / ".refresh.lock") == "unknown"


def test_the_exporter_publishes_lock_states_and_container_counts_without_names(tmp_path):
    spec = importlib.util.spec_from_file_location("exporter_l", REPO / "scripts/jobs/exporter.py")
    ex = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ex)
    from core.jobs import registry
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".refresh.lock").write_text("")
    reg = registry.parse({"schema_version": 1, "jobs": []})
    text = ex.build(reg, tmp_path / "h", {"jobs": str(tmp_path / "jobs")}, "ec2", ex.Aliases.load(), NOW,
                    vault_root=vault, containers=[c("minimoi-vault-seed", 30), c("epic_davinci", 60, ["/x:/v/vault-work/s"])])
    assert 'minimoi_lock_state{lock="vault_refresh",state="free"} 1' in text
    assert 'minimoi_lock_state{lock="seed_index",state="absent"} 1' in text
    assert 'minimoi_vault_orphan_containers{container="seed"} 0' in text          # owner statuses unreadable:
    assert 'minimoi_vault_undetermined_containers{container="seed"} 1' in text     # undetermined, not orphan
    assert 'minimoi_vault_containers{container="unknown_holder"} 1' in text
    assert "minimoi_docker_readable 1" in text and "epic_davinci" not in text and "vault-work" not in text
    off = ex.build(reg, tmp_path / "h", {"jobs": str(tmp_path / "jobs")}, "ec2", ex.Aliases.load(), NOW, vault_root=vault)
    assert "minimoi_docker_readable" not in off and "minimoi_lock_state" in off
    blind = ex.build(reg, tmp_path / "h", {"jobs": str(tmp_path / "jobs")}, "ec2", ex.Aliases.load(), NOW,
                     vault_root=vault, containers=None)
    assert "minimoi_docker_readable 0" in blind and "minimoi_vault_containers" not in blind
