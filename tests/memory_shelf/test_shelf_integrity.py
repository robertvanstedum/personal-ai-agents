"""Shelf integrity (Codex review 2026-10-04): interrupted publication, the cross-process lock, doctor and repair.

Synthetic text only. Interruptions are injected at each boundary of one ingest: edition saved, main file written,
index written, ledger row written."""
import io
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from core.memory_shelf import cli, codes, editions, fsio, ledger, record, render, repair, shelf as shelf_mod
from core.memory_shelf.config import Config

from .helpers import cc_bundle, cc_lines, make_shelf

REPO = Path(__file__).resolve().parents[2]
V1 = cc_lines("s-1", ("human", "first question"), ("assistant", "first answer"))
V2 = cc_lines("s-1", ("human", "first question"), ("assistant", "first answer"), ("human", "second question"),
              ("assistant", "second answer"))


def main_of(shelf):
    entry = shelf.list_records()[0]
    return shelf.main_path(entry)


def body_turns(path):
    return [t.text for t in render.parse_body(record.load(record.read(path))[1])]


class Boom(OSError):
    pass


def fail_once(monkeypatch, owner, name):
    """Make ``owner.name`` raise on its first call and work afterwards."""
    real, state = getattr(owner, name), {"n": 0}

    def wrapper(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise Boom("injected")
        return real(*a, **k)
    monkeypatch.setattr(owner, name, wrapper)
    return state


# ── interrupted edition update ──────────────────────────────────────────────

def test_an_edition_saved_before_the_main_file_moved_is_finished_on_retry_never_called_unchanged(tmp_path, monkeypatch):
    shelf = make_shelf(tmp_path)
    assert shelf.ingest(cc_bundle(V1)).outcome == codes.CAPTURED
    path = main_of(shelf)
    second = cc_bundle(V2)
    fail_once(monkeypatch, record, "write")
    assert shelf.ingest(second).outcome == codes.FAILED
    assert len(editions.list_editions(path.parent)) == 2 and body_turns(path) == ["first question", "first answer"]
    retry = shelf.ingest(second)
    assert retry.outcome == codes.EDITION_ADDED, retry                      # the orphan edition is not proof of publication
    meta, _ = record.load(record.read(path))
    assert meta["edition"] == 2 and body_turns(path)[-1] == "second answer"
    assert [e["edition"] for e in meta["events"] if e["kind"] == "edition-added"] == [2]
    assert len(editions.list_editions(path.parent)) == 2
    assert shelf.ingest(second).outcome == codes.UNCHANGED                   # and now it really is done


def test_a_publication_stopped_before_the_index_write_is_unchanged_on_retry_and_the_index_heals(tmp_path, monkeypatch):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(V1))
    second = cc_bundle(V2)
    state = fail_once(monkeypatch, fsio, "write_json")                       # the index write
    with pytest.raises(Boom):
        shelf.ingest(second)
    assert shelf._dirty.exists() and body_turns(main_of(shelf))[-1] == "second answer"
    assert shelf.ingest(second).outcome == codes.UNCHANGED
    assert not shelf._dirty.exists() and shelf.index() == shelf.scan_index() and len(shelf.list_records()) == 1


def test_a_stop_before_the_ledger_row_leaves_nothing_to_redo_and_the_retry_writes_the_row(tmp_path, monkeypatch):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(V1))
    second = cc_bundle(V2)
    fail_once(monkeypatch, ledger, "record")
    with pytest.raises(Boom):
        shelf.ingest(second)
    assert not shelf._dirty.exists() and body_turns(main_of(shelf))[-1] == "second answer"
    assert shelf.ingest(second).outcome == codes.UNCHANGED
    assert max(r["outcome"] for r in ledger.latest(shelf).values()) in codes.OK_OUTCOMES


def test_a_new_record_stopped_after_its_edition_but_before_its_main_file_is_created_once_on_retry(tmp_path, monkeypatch):
    shelf = make_shelf(tmp_path)
    first = cc_bundle(V1)
    fail_once(monkeypatch, record, "write")
    assert shelf.ingest(first).outcome == codes.FAILED
    assert shelf.list_records() == []
    result = shelf.ingest(first)
    assert result.outcome == codes.CAPTURED, result
    assert len(shelf.list_records()) == 1 and len(editions.list_editions(main_of(shelf).parent)) == 1
    assert shelf.ingest(first).outcome == codes.UNCHANGED


def test_the_same_bundle_applied_twice_after_success_is_unchanged_not_a_second_record(tmp_path):
    shelf = make_shelf(tmp_path)
    first = cc_bundle(V1)
    assert shelf.ingest(first).outcome == codes.CAPTURED
    assert shelf.ingest(first).outcome == codes.UNCHANGED and len(shelf.list_records()) == 1


# ── the cross-process lock ──────────────────────────────────────────────────

CHILD = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, {repo!r})
    from core.memory_shelf.shelf import Shelf
    from tests.memory_shelf.helpers import cc_bundle, cc_lines
    shelf = Shelf({root!r}, min_free_bytes=0)
    shelf.init_layout()
    tag = sys.argv[1]
    for i in range(int(sys.argv[2])):
        sid = f"{{tag}}-{{i}}"
        r = shelf.ingest(cc_bundle(cc_lines(sid, ("human", "q " + sid), ("assistant", "a"))))
        assert r.outcome == "captured", r
""")


def run_children(root, tags, n):
    code = CHILD.format(repo=str(REPO), root=str(root))
    procs = [subprocess.Popen([sys.executable, "-c", code, tag, str(n)], cwd=REPO, stderr=subprocess.PIPE) for tag in tags]
    return [(p.wait(timeout=120), p.stderr.read().decode()[-300:]) for p in procs]


def test_two_processes_ingesting_at_once_lose_no_record_and_the_index_matches_the_records(tmp_path):
    shelf = make_shelf(tmp_path)
    results = run_children(shelf.root, ["a", "b", "c"], 12)
    assert [code for code, _ in results] == [0, 0, 0], results
    assert len(shelf.scan_index()) == 36 and shelf.index() == shelf.scan_index() and not shelf._dirty.exists()


def test_a_writer_waits_while_another_process_holds_the_shelf_lock(tmp_path):
    shelf = make_shelf(tmp_path)
    code = CHILD.format(repo=str(REPO), root=str(shelf.root))
    with shelf.lock():
        child = subprocess.Popen([sys.executable, "-c", code, "w", "1"], cwd=REPO, stderr=subprocess.PIPE)
        time.sleep(1.5)
        assert child.poll() is None                                          # blocked, not failed and not finished
        assert shelf.list_records() == []
    assert child.wait(timeout=60) == 0 and len(shelf.list_records()) == 1


def test_a_writer_that_cannot_get_the_lock_reports_shelf_busy_and_writes_nothing(tmp_path, monkeypatch):
    shelf = make_shelf(tmp_path)
    monkeypatch.setattr(shelf_mod, "LOCK_WAIT_S", 0.2)
    code = "import fcntl,sys,time,os; fd=os.open(sys.argv[1], os.O_RDWR|os.O_CREAT); fcntl.flock(fd, fcntl.LOCK_EX); print('held', flush=True); time.sleep(5)"
    holder = subprocess.Popen([sys.executable, "-c", code, str(shelf.lock_path)], stdout=subprocess.PIPE)
    try:
        assert holder.stdout.readline().strip() == b"held"
        result = shelf.ingest(cc_bundle(V1))
        assert result.outcome == codes.FAILED and result.reason == "shelf_busy" and shelf.list_records() == []
    finally:
        holder.kill()


def test_the_lock_is_reentrant_within_a_thread(tmp_path):
    shelf = make_shelf(tmp_path)
    with shelf.lock():
        with shelf.lock():
            assert shelf.ingest(cc_bundle(V1)).outcome == codes.CAPTURED


def test_an_approval_event_and_an_ingest_do_not_overwrite_each_other(tmp_path):
    """Both are read-modify-write of the same main file; each runs inside the lock."""
    from core.memory_shelf import approvals, events as ev
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(V1))
    rid = shelf.list_records()[0]["id"]
    approvals.approve_record(shelf, rid, ev.OwnerAuthority("moi-approve"))
    shelf.ingest(cc_bundle(V2))
    meta, _ = record.load(record.read(main_of(shelf)))
    assert [e["kind"] for e in meta["events"]] == ["approved-direct", "edition-added"]


# ── doctor and repair ───────────────────────────────────────────────────────

def damage_like_the_old_writer(path):
    """What the old ``dump`` did: trim the last turn's trailing blanks."""
    meta, body = record.load(record.read(path))
    path.write_bytes(record.dump(meta, body).rstrip().encode("utf-8"))


TRAILING = cc_lines("s-9", ("human", "a question"), ("assistant", "an answer with trailing blanks   \n\n"))


def test_doctor_finds_a_record_whose_body_the_old_writer_trimmed_and_counts_only(tmp_path):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(TRAILING))
    shelf.ingest(cc_bundle(V1))
    damage_like_the_old_writer(shelf.main_path(shelf.index()["claude-code:s-9"]))
    res = repair.inspect(shelf)
    assert res["records"] == 2 and res["states"] == {"body_unreadable": 1, "ok": 1}
    assert "trailing" not in json.dumps(res) and "answer" not in json.dumps(res)


def test_repair_rebuilds_the_body_from_the_edition_keeps_the_old_file_and_never_touches_an_edition(tmp_path):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(TRAILING))
    path = main_of(shelf)
    editions_before = {e.path.name: e.path.read_bytes() for e in editions.list_editions(path.parent)}
    damage_like_the_old_writer(path)
    assert repair.classify(path) == repair.BODY_UNREADABLE
    rid = shelf.list_records()[0]["id"]
    assert repair.recover(shelf, rid) == "repaired"
    assert repair.classify(path) == repair.OK
    assert body_turns(path) == ["a question", "an answer with trailing blanks   \n\n"]
    assert {e.path.name: e.path.read_bytes() for e in editions.list_editions(path.parent)} == editions_before
    assert len(list((path.parent / "repairs").glob("*-main.md"))) == 1
    assert repair.recover(shelf, rid) == "ok"                                 # nothing left to do


def test_repair_refuses_when_there_is_no_intact_edition_to_rebuild_from(tmp_path):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(TRAILING))
    path = main_of(shelf)
    damage_like_the_old_writer(path)
    for ed in editions.list_editions(path.parent):
        ed.path.write_bytes(ed.path.read_bytes().replace(b"an answer", b"AN ANSWER"))      # a corrupt edition
    rid = shelf.list_records()[0]["id"]
    before = path.read_bytes()
    assert repair.recover(shelf, rid) == repair.EDITION_CORRUPT and path.read_bytes() == before


def test_a_main_file_behind_its_newest_edition_is_reported_not_rewritten(tmp_path, monkeypatch):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(V1))
    fail_once(monkeypatch, record, "write")
    shelf.ingest(cc_bundle(V2))
    path = main_of(shelf)
    assert repair.classify(path) == repair.MAIN_BEHIND
    assert repair.recover(shelf, shelf.list_records()[0]["id"]) == repair.MAIN_BEHIND


def test_the_doctor_and_repair_commands(tmp_path):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(TRAILING))
    damage_like_the_old_writer(main_of(shelf))
    cfgfile = tmp_path / "c.json"
    cfgfile.write_text(json.dumps({"schema_version": 1, "shelf_root": str(shelf.root), "inbox_root": str(tmp_path / "in"),
                                   "repo_root": str(tmp_path), "headroom": {"min_free_bytes": 0}, "sources": {}}))

    def run(*argv, yes):
        out = io.StringIO()
        code = cli.main(["--config", str(cfgfile), *argv], out=out, confirm=lambda _p: yes, is_tty=lambda: True)
        return code, out.getvalue()
    code, text = run("doctor", yes=False)
    assert code == cli.REFUSED and "body_unreadable" in text and "trailing" not in text
    code, text = run("repair", yes=False)
    assert code == cli.REFUSED and repair.classify(main_of(shelf)) == repair.BODY_UNREADABLE     # no yes, no change
    code, text = run("repair", yes=True)
    assert code == cli.OK and '"repaired": 1' in text and repair.classify(main_of(shelf)) == repair.OK
    assert run("doctor", yes=False)[0] == cli.OK


def test_publishing_the_same_edition_twice_records_one_event(tmp_path):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(V1))
    second = cc_bundle(V2)
    path = main_of(shelf)
    meta, _ = record.load(record.read(path))
    editions.add_edition(path.parent, second.edition, "jsonl")
    shelf._publish(second, meta, path, 2, None)
    meta, _ = record.load(record.read(path))
    shelf._publish(second, meta, path, 2, None)
    meta, _ = record.load(record.read(path))
    assert [e["edition"] for e in meta["events"] if e["kind"] == "edition-added"] == [2]


def test_an_approval_waits_for_the_shelf_lock_instead_of_racing_an_ingest(tmp_path):
    import threading
    from core.memory_shelf import approvals, events as ev
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(V1))
    rid = shelf.list_records()[0]["id"]
    done = threading.Event()

    def approve():
        approvals.approve_record(shelf, rid, ev.OwnerAuthority("moi-approve"))
        done.set()
    with shelf.lock():
        worker = threading.Thread(target=approve)
        worker.start()
        assert not done.wait(0.5)                                            # blocked while another writer holds the lock
    assert done.wait(10)
    worker.join()


def test_repair_writes_nothing_when_the_rebuilt_body_would_not_match_the_edition(tmp_path, monkeypatch):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(TRAILING))
    path = main_of(shelf)
    damage_like_the_old_writer(path)
    before = path.read_bytes()
    monkeypatch.setattr(render, "render_body", lambda parsed: "")
    assert repair.recover(shelf, shelf.list_records()[0]["id"]) == "rebuild_mismatch"
    assert path.read_bytes() == before and not (path.parent / "repairs").exists()


# ── draining the same outbox from two processes ─────────────────────────────

DRAINER = textwrap.dedent("""
    import sys
    sys.path.insert(0, {repo!r})
    from core.memory_shelf.shelf import Shelf
    out = Shelf({root!r}, min_free_bytes=0).drain()
    print(len(out))
""")


def test_two_processes_draining_one_outbox_apply_each_bundle_once_and_neither_fails(tmp_path):
    shelf = make_shelf(tmp_path)
    for i in range(24):
        sid = f"d-{i}"
        shelf.stage(cc_bundle(cc_lines(sid, ("human", "q " + sid), ("assistant", "a"))))
    code = DRAINER.format(repo=str(REPO), root=str(shelf.root))
    procs = [subprocess.Popen([sys.executable, "-c", code], cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
    results = [(p.wait(timeout=120), p.stderr.read().decode()[-300:]) for p in procs]
    assert [r[0] for r in results] == [0, 0, 0], results
    assert len(shelf.scan_index()) == 24 and shelf.pending() == [] and len(list((shelf.outbox / "_done").glob("*.json"))) == 24
    assert shelf.index() == shelf.scan_index()
