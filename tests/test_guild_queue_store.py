"""B1 (a): the Build Queue's only writer — lock, unchanged-check, atomic
replace, read-back, receipt, journal and reconciliation.

These tests use the real sidecar flock and real processes where concurrency or
a crash matters.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from domains.guild import queue_store as qs

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable


def _items():
    return [
        {"id": 1, "spec_title": "One", "status": "in_build", "summary": "0"},
        {"id": 2, "spec_title": "Two", "status": "spec_ready", "summary": "0"},
    ]


@pytest.fixture
def queue(tmp_path):
    folder = tmp_path / "state" / "guild"
    folder.mkdir(parents=True)
    path = folder / "build_queue.json"
    path.write_bytes(qs.serialize(_items()))
    return path


def store(path, **kw):
    kw.setdefault("in_container", False)
    return qs.QueueStore(path, **kw)


def digest_of(path, item_id):
    item = next(i for i in json.loads(path.read_text()) if i["id"] == item_id)
    return qs.item_digest(item)


def journal(path):
    raw = (path.parent / qs.JOURNAL_NAME).read_text()
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


# ── The verified Save ─────────────────────────────────────────────────────────

def test_status_save_is_verified_with_receipt_and_journal(queue):
    result = store(queue).save_status(1, "blocked", expect_item_digest=digest_of(queue, 1),
                                      note="waiting", principal="robert")
    assert result.result == qs.SAVED and result.verified
    assert (result.old, result.new) == ("in_build", "blocked")
    assert re.fullmatch(r"q-\d{8}T\d{6}Z-[0-9a-f]{6}", result.receipt_id)
    assert result.receipt_id.endswith(result.after_digest[:6])
    saved = json.loads(queue.read_text())
    assert saved[0]["status"] == "blocked" and saved[0]["blocked_reason"] == "waiting"
    assert qs.sha256_bytes(queue.read_bytes()) == result.after_digest
    kinds = [(r["kind"], r["op_id"]) for r in journal(queue)]
    assert [k for k, _ in kinds] == ["intent", "completed"]
    assert kinds[0][1] == kinds[1][1]
    assert journal(queue)[1]["receipt_id"] == result.receipt_id


def test_blocked_reason_is_cleared_for_other_statuses(queue):
    s = store(queue)
    s.save_status(1, "blocked", expect_item_digest=digest_of(queue, 1), note="x", principal="r")
    s.save_status(1, "done", expect_item_digest=digest_of(queue, 1), note="ignored", principal="r")
    assert json.loads(queue.read_text())[0]["blocked_reason"] is None


def test_edit_metadata_goes_through_the_same_journal(queue):
    result = store(queue).edit_metadata(2, {"summary": "new words"},
                                        expect_item_digest=digest_of(queue, 2), principal="r")
    assert result.ok
    assert json.loads(queue.read_text())[1]["summary"] == "new words"
    intent = journal(queue)[0]
    assert intent["op"] == "edit" and intent["item_id"] == 2


def test_invalid_status_and_field_write_nothing(queue):
    before = queue.read_bytes()
    s = store(queue)
    assert s.save_status(1, "shipped", expect_item_digest=digest_of(queue, 1),
                         principal="r").result == qs.INVALID
    assert s.edit_metadata(1, {"status": "done"}, expect_item_digest=digest_of(queue, 1),
                           principal="r").result == qs.INVALID
    assert queue.read_bytes() == before


def test_unchanged_check_conflict_returns_current_state(queue):
    stale = digest_of(queue, 1)
    s = store(queue)
    assert s.edit_metadata(1, {"summary": "other tab"}, expect_item_digest=stale,
                           principal="r").ok
    after_other_tab = queue.read_bytes()
    result = s.save_status(1, "done", expect_item_digest=stale, principal="r")
    assert result.result == qs.CONFLICT
    assert result.current_item["summary"] == "other tab"
    assert result.current_item_digest == digest_of(queue, 1)
    assert queue.read_bytes() == after_other_tab
    assert [r["kind"] for r in journal(queue)] == ["intent", "completed"]


def test_change_to_another_item_is_not_a_conflict_and_is_kept(queue):
    d1 = digest_of(queue, 1)
    s = store(queue)
    assert s.edit_metadata(2, {"summary": "kept"}, expect_item_digest=digest_of(queue, 2),
                           principal="r").ok
    assert s.save_status(1, "done", expect_item_digest=d1, principal="r").ok
    saved = json.loads(queue.read_text())
    assert saved[0]["status"] == "done" and saved[1]["summary"] == "kept"


def test_missing_digest_is_refused(queue):
    result = store(queue).save_status(1, "done", expect_item_digest=None, principal="r")
    assert result.result == qs.CONFLICT
    assert not (queue.parent / qs.JOURNAL_NAME).exists()


def test_store_never_creates_the_queue_file(tmp_path):
    path = tmp_path / "build_queue.json"
    result = store(path).save_status(1, "done", expect_item_digest="x", principal="r")
    assert result.result == qs.UNAVAILABLE
    assert not path.exists()


def test_corrupt_queue_is_unavailable(queue):
    queue.write_text("{not json")
    result = store(queue).save_status(1, "done", expect_item_digest="x", principal="r")
    assert result.result == qs.UNAVAILABLE
    assert queue.read_text() == "{not json"


def test_idempotent_repeat_returns_the_first_receipt(queue):
    s = store(queue)
    d = digest_of(queue, 1)
    first = s.save_status(1, "done", expect_item_digest=d, principal="r", idempotency_key="k1")
    second = s.save_status(1, "done", expect_item_digest=d, principal="r", idempotency_key="k1")
    assert second.ok and second.repeated and second.receipt_id == first.receipt_id
    assert [r["kind"] for r in journal(queue)] == ["intent", "completed"]


def test_busy_when_another_writer_holds_the_lock(queue):
    s = store(queue, lock_timeout_s=0.2)
    before = queue.read_bytes()
    fd = os.open(s.lock_path, os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        result = s.save_status(1, "done", expect_item_digest=digest_of(queue, 1), principal="r")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert result.result == qs.BUSY
    assert queue.read_bytes() == before
    assert not (queue.parent / qs.JOURNAL_NAME).exists()


def test_file_mode_and_owner_are_preserved(queue):
    os.chmod(queue, 0o664)
    uid = queue.stat().st_uid
    assert store(queue).save_status(1, "done", expect_item_digest=digest_of(queue, 1),
                                    principal="r").ok
    assert queue.stat().st_mode & 0o7777 == 0o664, "the replace left a mkstemp 0600 file"
    assert queue.stat().st_uid == uid


# ── Concurrency: real processes, real flock ───────────────────────────────────

_WORKER = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, {repo!r})
    from domains.guild import queue_store as qs
    path, rounds = sys.argv[1], int(sys.argv[2])
    s = qs.QueueStore(path, in_container=False, lock_timeout_s=10)
    done = 0
    while done < rounds:
        item = next(i for i in json.loads(open(path).read()) if i["id"] == 1)
        n = int(item["summary"])
        r = s.edit_metadata(1, {{"summary": str(n + 1)}},
                            expect_item_digest=qs.item_digest(item), principal=sys.argv[3])
        if r.ok:
            done += 1
        elif r.result not in ("conflict", "busy"):
            raise SystemExit("unexpected " + r.result)
""")


def test_two_concurrent_writer_processes_lose_no_update(queue, tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text(_WORKER.format(repo=str(REPO)))
    rounds = 25
    procs = [subprocess.Popen([PY, str(worker), str(queue), str(rounds), f"w{i}"])
             for i in range(2)]
    for proc in procs:
        assert proc.wait(timeout=120) == 0
    assert json.loads(queue.read_text())[0]["summary"] == str(2 * rounds)
    completed = [r for r in journal(queue) if r["kind"] == "completed"]
    assert len(completed) == 2 * rounds
    assert not list(queue.parent.glob(".build_queue.json.tmp-*"))


# ── Crashes and verification ──────────────────────────────────────────────────

_KILLED = textwrap.dedent("""
    import os, sys, json
    sys.path.insert(0, {repo!r})
    from domains.guild import queue_store as qs
    qs._replace = lambda src, dst: os._exit(9)   # killed after temp+fsync, before replace
    path = sys.argv[1]
    item = json.loads(open(path).read())[0]
    qs.QueueStore(path, in_container=False).save_status(
        1, "done", expect_item_digest=qs.item_digest(item), principal="r")
""")


def test_kill_between_temp_and_replace_leaves_old_file_intact(queue, tmp_path):
    before = queue.read_bytes()
    script = tmp_path / "killed.py"
    script.write_text(_KILLED.format(repo=str(REPO)))
    proc = subprocess.run([PY, str(script), str(queue)])
    assert proc.returncode == 9
    assert queue.read_bytes() == before, "a torn or partial queue file was left"
    assert json.loads(queue.read_text()) == _items()
    lines = journal(queue)
    assert [r["kind"] for r in lines] == ["intent"]
    assert list(queue.parent.glob(".build_queue.json.tmp-*")), "expected the dead writer's temp"
    # The next write reconciles it as not applied and cleans the stale temp.
    result = store(queue).save_status(2, "done", expect_item_digest=digest_of(queue, 2),
                                      principal="r")
    assert result.ok
    assert [r.get("outcome") for r in result.reconciled] == ["not_applied"]
    assert not list(queue.parent.glob(".build_queue.json.tmp-*"))


def test_read_back_mismatch_is_uncertain_and_becomes_a_check(queue, monkeypatch):
    def replace_then_corrupt(src, dst):
        os.replace(src, dst)
        with open(dst, "ab") as stream:
            stream.write(b" ")
    monkeypatch.setattr(qs, "_replace", replace_then_corrupt)
    s = store(queue)
    result = s.save_status(1, "done", expect_item_digest=digest_of(queue, 1), principal="r")
    assert result.result == qs.UNCERTAIN and not result.verified and result.receipt_id is None
    assert journal(queue)[-1]["outcome"] == qs.UNCERTAIN
    checks = s.unresolved_checks()
    assert [c["item_id"] for c in checks] == [1]
    assert s.mark_checked(checks[0]["op_id"], "robert")
    assert s.unresolved_checks() == []


def test_audit_failure_is_reported_never_a_condition(queue, caplog):
    def broken(_item, _old, _new):
        raise RuntimeError("db down")
    result = store(queue).save_status(1, "done", expect_item_digest=digest_of(queue, 1),
                                      principal="r", audit=broken)
    assert result.ok and result.audit == "failed"
    assert journal(queue)[-1]["audit"] == "failed"
    assert "audit insert failed" in caplog.text


# ── Reconciliation of ALL unfinished intents ──────────────────────────────────

def test_reconciliation_handles_every_unfinished_intent(queue):
    s = store(queue)
    live = qs.sha256_bytes(queue.read_bytes())
    jpath = queue.parent / qs.JOURNAL_NAME

    def intent(op_id, before, after, item_id=1):
        return {"kind": "intent", "op_id": op_id, "op": "status", "item_id": item_id,
                "before_digest": before, "after_digest": after}

    lines = [
        intent("d" * 32, "e" * 64, "f" * 64),              # nothing explains it -> uncertain
        intent("a" * 32, "0" * 64, "1" * 64),              # applied, then a publish replaced it
        {"kind": "replaced", "writer": "publish", "before_digest": "1" * 64, "after_digest": live},
        intent("b" * 32, live, "3" * 64),                  # crashed before replace
        intent("c" * 32, live, "4" * 64, item_id=2),       # crashed before replace
    ]
    text = "\n".join(json.dumps(r) for r in lines[:2]) + "\n{torn"
    text += "\n" + "\n".join(json.dumps(r) for r in lines[2:])  # no trailing newline
    jpath.write_text(text)

    outcomes = {o["op_id"][0]: o for o in s.reconcile()}
    assert outcomes["d"]["outcome"] == qs.UNCERTAIN
    assert outcomes["a"]["kind"] == "completed" and outcomes["a"]["recovered"] is True
    assert outcomes["b"]["outcome"] == "not_applied"
    assert outcomes["c"]["outcome"] == "not_applied"
    assert [c["op_id"] for c in s.unresolved_checks()] == ["d" * 32]
    # Idempotent: nothing left to reconcile, and the torn line did not corrupt appends.
    assert s.reconcile() == []
    for line in jpath.read_text().splitlines():
        if line.strip() and line != "{torn":
            json.loads(line)


# ── M2: where writes are allowed ──────────────────────────────────────────────

def test_unset_path_refuses_writes():
    result = qs.QueueStore(None).save_status(1, "done", expect_item_digest="x", principal="r")
    assert result.result == qs.REFUSED and "GUILD_QUEUE_PATH is not set" in result.reason


def test_folder_inside_the_code_tree_is_refused(tmp_path):
    code = tmp_path / "app"
    (code / "data" / "guild").mkdir(parents=True)
    path = code / "data" / "guild" / "build_queue.json"
    path.write_bytes(qs.serialize(_items()))
    before = path.read_bytes()
    s = qs.QueueStore(path, in_container=False, code_root=code)
    result = s.save_status(1, "done", expect_item_digest=digest_of(path, 1), principal="r")
    assert result.result == qs.REFUSED and "code tree" in result.reason
    assert path.read_bytes() == before


def test_real_repository_copy_is_refused():
    problem = qs.write_location_problem(REPO / "data" / "guild" / "build_queue.json",
                                        in_container=False)
    assert problem and "code tree" in problem


def test_in_a_container_the_folder_must_be_a_mount(queue, monkeypatch):
    s = qs.QueueStore(queue, in_container=True)
    assert s.write_problem() == "the queue folder is not a mounted host folder"
    before = queue.read_bytes()
    assert s.save_status(1, "done", expect_item_digest=digest_of(queue, 1),
                         principal="r").result == qs.REFUSED
    assert queue.read_bytes() == before
    monkeypatch.setattr(qs.os.path, "ismount", lambda p: str(p) == str(queue.parent))
    assert s.write_problem() is None
    assert s.save_status(1, "done", expect_item_digest=digest_of(queue, 1), principal="r").ok


def test_missing_folder_is_refused(tmp_path):
    assert qs.write_location_problem(tmp_path / "nope" / "build_queue.json",
                                     in_container=False) == "the queue folder does not exist"


def test_container_detection_includes_production_flask_env(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    assert qs._running_in_container() is True


def test_validating_read_marks_missing_and_corrupt(queue, tmp_path):
    assert store(queue).read_items() == _items()
    with pytest.raises(qs.QueueUnavailable):
        store(tmp_path / "missing.json").read_items()
    with pytest.raises(qs.QueueUnavailable):
        qs.QueueStore(None).read_items()
