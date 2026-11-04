"""B1B: the task service journal (spec v1.1 A01, D05, E01; acceptance F01, F07; Q06 integrity).

SYNTHETIC. Every test works in its own temporary folder. Two ``Journal`` objects on one folder stand for two writer
processes; the multi-process tests start real processes."""
from __future__ import annotations

import json
import multiprocessing as mp
import os
from pathlib import Path

import pytest

from core import durable_files as fsio
from core.canonical_json import digest
from services.partnership_tasks import contracts
from services.partnership_tasks.journal import GENESIS, JOURNAL, Conflict, Journal, Plan, Refused, Write

SCHEMA = "minimoi.partnership.result/1"


def clock():
    return "2026-11-04T16:00:00+00:00"


def journal(root):
    return Journal(root, result_schema=SCHEMA, clock=clock, doc_dirs=("requests", "tasks"))


def evidence(targets=("requests/RQ-syn-0001.json",)):
    return {"actor_seat": "mc", "origin": "agent", "session_id": "syn", "runtime": {"kind": "deterministic"},
            "causal_parent": None, "basis_ref": None, "environment": "synthetic", "data_scope": "personal",
            "event_at": None, "targets": list(targets), "binding_id": "bind-mc-syn",
            "revisions": {t: 1 for t in targets},
            "links": {"source_event_id": None, "interpretation_id": None, "request_id": "RQ-syn-0001",
                      "task_id": None, "authority_ref": None}}


def put(j, cid, docs, *, revision=1, payload=None, kind="request.record"):
    """Write ``docs`` ({path: record}) as one command; refuses if a target already exists (a create)."""
    payload = payload if payload is not None else docs
    def plan():
        for path in docs:
            if j.read(path) is not None:
                raise Conflict(1)
        return Plan([Write(p, r) for p, r in docs.items()], revision, kind, evidence(tuple(docs)))
    return j.run(cid, digest(payload), plan)


REQ = {"requests/RQ-syn-0001.json": {"id": "RQ-syn-0001", "v": 1}}
PROMO = {"requests/RQ-syn-0002.json": {"id": "RQ-syn-0002", "task_ref": "TK-syn-0002"},
         "tasks/TK-syn-0002.json": {"id": "TK-syn-0002", "request_ref": "RQ-syn-0002"}}


def test_accepted_with_receipt_and_every_line_matches_the_evidence_contract(tmp_path):
    j = journal(tmp_path)
    res = put(j, "cmd-a-1", REQ)
    assert res["outcome"] == "accepted" and res["revision"] == 1 and not res["replayed"]
    assert contracts.errors("command", "result", res) == []
    rows = j.entries()
    assert [r["t"] for r in rows] == ["intent", "completed"]
    for row in rows:
        assert contracts.errors("evidence", "journal_entry", row) == []
    assert rows[0]["prev_hash"] == GENESIS and rows[1]["prev_hash"] == rows[0]["hash"]


def test_b0_1_a_second_writer_process_replays_and_refuses_exactly_like_the_first(tmp_path):
    a, b = journal(tmp_path), journal(tmp_path)
    b.recover()                                             # B's view is taken before A writes
    first = put(a, "cmd-a-1", REQ)
    again = put(b, "cmd-a-1", REQ)
    assert again == dict(first, replayed=True)
    other = {"requests/RQ-syn-0001.json": {"id": "RQ-syn-0001", "v": 2}}
    assert put(b, "cmd-a-1", other)["code"] == "command_id_reused"
    assert put(a, "cmd-a-1", other)["code"] == "command_id_reused"
    assert j_doc(tmp_path, "requests/RQ-syn-0001.json") == REQ["requests/RQ-syn-0001.json"]


def j_doc(root, rel):
    return fsio.read_doc(Path(root) / rel)


def test_receipt_lookup_answers_from_the_journal_in_any_process(tmp_path):
    a, b = journal(tmp_path), journal(tmp_path)
    first = put(a, "cmd-a-1", REQ)
    assert b.receipt("cmd-a-1", digest(REQ)) == {"schema": "minimoi.partnership.receipt_lookup/1",
                                                 "command_id": "cmd-a-1", "answer": "recorded", "result": first}
    assert b.receipt("cmd-a-1", digest({"x": 1}))["answer"] == "different"
    assert b.receipt("cmd-zz-9")["answer"] == "absent"
    for answer in (b.receipt("cmd-a-1"), b.receipt("cmd-zz-9")):
        assert contracts.errors("command", "receipt_lookup", answer) == []


def test_refusal_and_conflict_write_nothing(tmp_path):
    j = journal(tmp_path)
    put(j, "cmd-a-1", REQ)
    assert put(j, "cmd-a-2", REQ, payload={"other": 1})["outcome"] == "conflict"
    def refuse():
        raise Refused("not_authorized", "test")
    assert j.run("cmd-a-3", digest({}), refuse) == {"schema": SCHEMA, "command_id": "cmd-a-3", "outcome": "refused",
                                                    "code": "not_authorized", "detail": "test"}
    assert len(j.entries()) == 2


@pytest.mark.parametrize("fail_on_write", [0, 1])
def test_f01_crash_between_the_two_documents_of_a_promotion_finishes_forward_once(tmp_path, monkeypatch, fail_on_write):
    """The intent is durable; the process dies before the first or second document lands. The next writer finishes
    the same intent: one request, one task, linked both ways, and the client's retry gets that receipt."""
    real, calls = fsio.write_doc, {"n": 0}
    def crashing(path, record):
        if calls["n"] == fail_on_write:
            raise OSError("simulated crash")
        calls["n"] += 1
        real(path, record)
    monkeypatch.setattr(fsio, "write_doc", crashing)
    dead = journal(tmp_path)
    assert put(dead, "cmd-pr-1", PROMO, kind="request.promote")["code"] == "store_unavailable"
    monkeypatch.setattr(fsio, "write_doc", real)
    survivor = journal(tmp_path)
    counts = survivor.recover()
    assert counts["recovered"] == 1
    for path, record in PROMO.items():
        assert j_doc(tmp_path, path) == record
    retry = put(survivor, "cmd-pr-1", PROMO, kind="request.promote")
    assert retry["outcome"] == "accepted" and retry["replayed"] is True
    rows = survivor.entries()
    assert [r["t"] for r in rows] == ["intent", "completed"] and rows[1]["recovered"] is True
    assert rows[1]["actor_seat"] == "mc" and rows[1]["links"]["request_id"] == "RQ-syn-0001"   # evidence survives
    assert contracts.errors("evidence", "journal_entry", rows[1]) == []


def test_crash_after_the_writes_before_the_completed_line_is_recovered_not_rewritten(tmp_path, monkeypatch):
    j = journal(tmp_path)
    real_append = fsio.append_line
    def no_completed(path, row):
        if row.get("t") == "completed":
            raise OSError("simulated crash")
        real_append(path, row)
    monkeypatch.setattr(fsio, "append_line", no_completed)
    assert put(j, "cmd-pr-1", PROMO)["outcome"] == "failed"
    monkeypatch.setattr(fsio, "append_line", real_append)
    before = {p: os.stat(tmp_path / p).st_mtime_ns for p in PROMO}
    other = journal(tmp_path)
    assert other.receipt("cmd-pr-1", digest(PROMO))["answer"] == "recorded"
    assert {p: os.stat(tmp_path / p).st_mtime_ns for p in PROMO} == before


def test_a_torn_journal_tail_is_quarantined_and_never_read_as_a_record(tmp_path):
    j = journal(tmp_path)
    put(j, "cmd-a-1", REQ)
    with open(tmp_path / JOURNAL, "ab") as fh:
        fh.write(b'{"t":"intent","seq":3')
    other = journal(tmp_path)
    assert other.recover()["quarantined"] == 1
    assert len(other.entries()) == 2
    assert list((tmp_path / "journal").glob("quarantine-*"))
    assert put(other, "cmd-a-2", {"requests/RQ-syn-0003.json": {"id": "RQ-syn-0003"}})["outcome"] == "accepted"


def test_q06_an_edited_journal_line_holds_the_store(tmp_path):
    j = journal(tmp_path)
    put(j, "cmd-a-1", REQ)
    lines = (tmp_path / JOURNAL).read_bytes().splitlines(keepends=True)
    row = json.loads(lines[1]); row["actor_seat"] = "principal"                 # a rewrite of who did it
    lines[1] = (json.dumps(row, sort_keys=True) + "\n").encode()
    (tmp_path / JOURNAL).write_bytes(b"".join(lines))
    other = journal(tmp_path)
    assert other.receipt("cmd-a-1")["answer"] == "unknown"
    assert put(other, "cmd-a-2", {"requests/RQ-syn-0003.json": {"id": "RQ-syn-0003"}})["code"] == "store_unavailable"


def test_q06_a_deleted_line_and_a_recomputed_rewrite_are_caught_by_an_exported_head(tmp_path):
    j = journal(tmp_path)
    put(j, "cmd-a-1", REQ)
    head = j.head()
    assert head["seq"] == 2 and j.verify_head(head)
    # A privileged rewrite: drop everything and write a different, internally consistent history.
    (tmp_path / JOURNAL).unlink()
    (tmp_path / "requests/RQ-syn-0001.json").unlink()
    rewritten = journal(tmp_path)
    put(rewritten, "cmd-other-1", {"requests/RQ-syn-0001.json": {"id": "RQ-syn-0001", "v": 99}})
    assert rewritten.verify_head(head) is False                   # detected only because the head was exported


def test_a_document_changed_outside_the_journal_during_an_intent_holds_the_store(tmp_path, monkeypatch):
    real, calls = fsio.write_doc, {"n": 0}
    def crash_first(path, record):
        if calls["n"] == 0:
            calls["n"] += 1
            raise OSError("simulated crash")
        real(path, record)
    monkeypatch.setattr(fsio, "write_doc", crash_first)
    put(journal(tmp_path), "cmd-pr-1", PROMO)
    monkeypatch.setattr(fsio, "write_doc", real)
    real(tmp_path / "requests/RQ-syn-0002.json", {"id": "RQ-syn-0002", "edited": "by hand"})
    other = journal(tmp_path)
    assert other.receipt("cmd-pr-1")["answer"] == "unknown"
    assert j_doc(tmp_path, "tasks/TK-syn-0002.json") is None      # nothing more was written on a broken premise


# ---- real processes (F07) ---------------------------------------------------------------------------------------

def _worker(root, worker_no, n, out, barrier):
    j = journal(root)
    results, own = [], []
    barrier.wait(timeout=30)                                      # both processes start the race together
    for i in range(n):
        cid = f"cmd-shared-{i:03d}"
        doc = {f"requests/RQ-syn-{i:04d}.json": {"id": f"RQ-syn-{i:04d}"}}
        results.append(put(j, cid, doc))
        mine = {f"requests/RQ-w{worker_no}-{i:04d}.json": {"id": f"RQ-w{worker_no}-{i:04d}"}}
        own.append(put(j, f"cmd-w{worker_no}-{i:03d}", mine))  # appends from a possibly stale view
    out.put((worker_no, results, own))


def test_f07_two_writer_processes_racing_on_the_same_commands_apply_each_exactly_once(tmp_path):
    ctx = mp.get_context("spawn")
    out, barrier = ctx.Queue(), ctx.Barrier(2)
    procs = [ctx.Process(target=_worker, args=(str(tmp_path), k, 25, out, barrier)) for k in range(2)]
    for p in procs:
        p.start()
    got, own = {}, {}
    for _ in procs:
        k, shared, mine = out.get(timeout=60)
        got[k], own[k] = shared, mine
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0
    for i in range(25):
        a, b = got[0][i], got[1][i]
        assert {a["outcome"], b["outcome"]} == {"accepted"}
        assert a["receipt_id"] == b["receipt_id"]                  # one write, one receipt
        assert sorted([a["replayed"], b["replayed"]]) == [False, True]
    assert all(r["outcome"] == "accepted" and not r["replayed"] for k in own for r in own[k])
    rows = journal(tmp_path).entries()                            # the whole chain still verifies
    assert sum(r["t"] == "completed" for r in rows) == 75
    assert journal(tmp_path).recover()["recovered"] == 0          # and every document matches the journal


def _die_mid_promotion(root):
    real = fsio.write_doc
    calls = {"n": 0}
    def die(path, record):
        if calls["n"] == 1:
            os._exit(9)                                            # killed between the two documents
        calls["n"] += 1
        real(path, record)
    fsio.write_doc = die
    put(journal(root), "cmd-pr-1", PROMO)


def test_f07_kill_mid_write_then_another_process_finishes_the_same_intent(tmp_path):
    ctx = mp.get_context("spawn")
    p = ctx.Process(target=_die_mid_promotion, args=(str(tmp_path),))
    p.start()
    p.join(timeout=60)
    assert p.exitcode == 9
    assert j_doc(tmp_path, "requests/RQ-syn-0002.json") == PROMO["requests/RQ-syn-0002.json"]   # first landed
    assert j_doc(tmp_path, "tasks/TK-syn-0002.json") is None                                       # second did not
    j = journal(tmp_path)
    assert j.receipt("cmd-pr-1", digest(PROMO))["answer"] == "recorded"
    for path, record in PROMO.items():
        assert j_doc(tmp_path, path) == record


def test_a_stale_view_appends_onto_the_other_process_chain(tmp_path):
    a, b = journal(tmp_path), journal(tmp_path)
    a.recover()
    put(b, "cmd-b-1", REQ)
    put(a, "cmd-a-2", {"requests/RQ-syn-0003.json": {"id": "RQ-syn-0003"}})
    rows = journal(tmp_path).entries()
    assert [r["seq"] for r in rows] == [1, 2, 3, 4]


def test_q06_a_document_edited_after_its_command_completed_holds_the_store(tmp_path):
    j = journal(tmp_path)
    put(j, "cmd-a-1", REQ)
    fsio.write_doc(tmp_path / "requests/RQ-syn-0001.json", {"id": "RQ-syn-0001", "v": 1, "edited": True})
    fresh = journal(tmp_path)
    assert fresh.receipt("cmd-a-1")["answer"] == "unknown"
    # The process that already verified also refuses to build on the edited document.
    def update():
        return Plan([Write("requests/RQ-syn-0001.json", {"id": "RQ-syn-0001", "v": 2})], 2, "request.refine",
                    evidence())
    assert j.run("cmd-a-2", digest({"v": 2}), update)["code"] == "store_unavailable"


def test_q06_cutting_off_the_last_command_leaves_a_document_the_journal_cannot_explain(tmp_path):
    j = journal(tmp_path)
    put(j, "cmd-a-1", REQ)
    put(j, "cmd-a-2", {"requests/RQ-syn-0003.json": {"id": "RQ-syn-0003"}})
    lines = (tmp_path / JOURNAL).read_bytes().splitlines(keepends=True)
    (tmp_path / JOURNAL).write_bytes(b"".join(lines[:-2]))          # a rollback: chain still valid up to seq 2
    fresh = journal(tmp_path)
    assert fresh.receipt("cmd-a-2")["answer"] == "unknown"           # not "absent": a retry must not re-apply


def test_q06_documents_without_any_journal_are_held(tmp_path):
    j = journal(tmp_path)
    put(j, "cmd-a-1", REQ)
    (tmp_path / JOURNAL).unlink()
    assert journal(tmp_path).receipt("cmd-a-1")["answer"] == "unknown"
    assert j.receipt("cmd-a-1")["answer"] == "unknown"               # the running process notices too


def test_a_held_store_comes_back_after_repair_and_recover(tmp_path):
    j = journal(tmp_path)
    put(j, "cmd-a-1", REQ)
    path = tmp_path / "requests/RQ-syn-0001.json"
    good = path.read_bytes()
    fsio.write_doc(path, {"id": "tampered"})
    with pytest.raises(Exception):
        j.recover()
    assert j.receipt("cmd-a-1")["answer"] == "unknown"
    path.write_bytes(good)                                           # the operator restores the document
    j.recover()
    assert j.receipt("cmd-a-1")["answer"] == "recorded"


def test_plan_paths_must_be_unique_store_documents(tmp_path):
    j = journal(tmp_path)
    for bad in (["requests/RQ-1.json", "requests/RQ-1.json"], ["../escape.json"], ["journal/journal.jsonl"]):
        plan = lambda bad=bad: Plan([Write(p, {"x": 1}) for p in bad], 1, "request.record", evidence())
        with pytest.raises(ValueError):
            j.run("cmd-bad-" + str(len(bad)), digest(bad), plan)
