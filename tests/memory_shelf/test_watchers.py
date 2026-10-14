"""Watchers (B6): approval gate, dry run, never_copy, change detection, streaming, ledger. Synthetic trees only."""
import json
import os
import shutil
import tracemalloc
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.memory_shelf import approvals, codes, events as ev, fsio, ledger, record, watchers
from core.memory_shelf.config import SourceCfg

from .helpers import FAKE_KEY, cc_lines, codex_lines, make_shelf, write_tree

OWNER = ev.OwnerAuthority("moi-approve")
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def tree(tmp_path):
    root = tmp_path / "claude-projects"
    write_tree(root, {
        "projA/s-1.jsonl": cc_lines("s-1", ("human", "CONTENT-ONE hello"), ("assistant", "answer one")),
        "projB/s-2.jsonl": cc_lines("s-2", ("human", f"key {FAKE_KEY}"), ("assistant", "answer two")),
        "projA/s-1/subagents/agent.jsonl": cc_lines("agent", ("human", "sidechain")),
        "secret-proj/s-3.jsonl": cc_lines("s-3", ("human", "NEVER-COPY-CONTENT"), ("assistant", "x")),
    })
    return root


def cfg_for(root, never=("secret-proj",), name="claude-code", kind="claude-code"):
    return SourceCfg(name, kind, root, tuple(never))


def approve(shelf, cfg):
    approvals.approve_source(shelf, cfg.name, cfg.fingerprint(), OWNER)


def test_first_run_is_a_dry_run_listing_only(tmp_path):
    shelf, cfg = make_shelf(tmp_path), cfg_for(tree(tmp_path))
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out["status"] == "dry_run_only"
    assert shelf.list_records() == [] and ledger.latest(shelf) == {} and shelf.pending() == []
    listing = json.loads(approvals.dry_run_path(shelf, "claude-code").read_text())
    assert [s["id"] for s in listing["sessions"]] == ["s-1", "s-2"]            # sidechain file is not a session
    assert listing["counts"] == {"would_copy": 2, "excluded": {"never_copy": 1}, "bytes": listing["counts"]["bytes"]}
    blob = json.dumps(listing)
    for secret in ("CONTENT-ONE", "NEVER-COPY-CONTENT", FAKE_KEY, "secret-proj", "projA", "answer"):
        assert secret not in blob                                              # never content, never excluded names
    assert all(set(s) == {"id", "bytes", "date", "decision"} for s in listing["sessions"])
    again = watchers.run_source(shelf, cfg, now=NOW)
    assert again["status"] == codes.NOT_APPROVED and shelf.list_records() == []


def test_dry_run_never_opens_a_file(tmp_path, monkeypatch):
    shelf, cfg = make_shelf(tmp_path), cfg_for(tree(tmp_path))
    real_open = open
    opened = []

    def spy(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", spy)
    watchers.dry_run(shelf, cfg, NOW)
    assert not [p for p in opened if str(cfg.root) in p]


def test_approved_source_captures_and_the_ledger_balances(tmp_path):
    root = tree(tmp_path)
    shelf, cfg = make_shelf(tmp_path), cfg_for(root)
    watchers.run_source(shelf, cfg, now=NOW)
    approve(shelf, cfg)
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out == {"status": "ok", "counts": {codes.CAPTURED: 2, codes.EXCLUDED: 1}}
    assert len(shelf.list_records()) == 2
    rep = ledger.report(shelf)["sources"]["claude-code"]
    assert rep["captured"] == 2 and rep["excluded"] == {"never_copy": 1} and rep["missing"] == {}
    assert ledger.report(shelf)["expected_exclusions"]["scrubbed_turns"] == 1
    for p in Path(shelf.root).rglob("*"):
        if p.is_file():
            assert FAKE_KEY not in p.read_text(errors="ignore") and "NEVER-COPY-CONTENT" not in p.read_text(errors="ignore")
    meta, _ = record.load(shelf.main_path(shelf.index()["claude-code:s-2"]).read_text())
    original = (root / "projB/s-2.jsonl").read_bytes()
    import hashlib
    assert meta["source_hash"] == hashlib.sha256(original).hexdigest() and FAKE_KEY.encode() in original   # source untouched


def test_unchanged_files_are_skipped_and_touch_alone_is_not_an_edition(tmp_path):
    root = tree(tmp_path)
    shelf, cfg = make_shelf(tmp_path), cfg_for(root)
    watchers.dry_run(shelf, cfg, NOW); approve(shelf, cfg)
    watchers.run_source(shelf, cfg, now=NOW)
    assert watchers.run_source(shelf, cfg, now=NOW)["counts"]["skipped_unchanged"] == 2
    target = root / "projA/s-1.jsonl"
    os.utime(target, (NOW.timestamp(), NOW.timestamp() + 5))
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out["counts"][codes.UNCHANGED] == 1 and len(shelf.list_records()) == 2
    meta, _ = record.load(shelf.main_path(shelf.index()["claude-code:s-1"]).read_text())
    assert meta["edition"] == 1


def test_a_grown_session_adds_an_edition_to_the_same_record(tmp_path):
    root = tree(tmp_path)
    shelf, cfg = make_shelf(tmp_path), cfg_for(root)
    watchers.dry_run(shelf, cfg, NOW); approve(shelf, cfg)
    watchers.run_source(shelf, cfg, now=NOW)
    (root / "projA/s-1.jsonl").write_text(cc_lines("s-1", ("human", "CONTENT-ONE hello"), ("assistant", "answer one"),
                                                    ("human", "a later question")))
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out["counts"] == {codes.EDITION_ADDED: 1, "skipped_unchanged": 1, codes.EXCLUDED: 1}
    meta, body = record.load(shelf.main_path(shelf.index()["claude-code:s-1"]).read_text())
    assert meta["edition"] == 2 and "a later question" in body and len(shelf.list_records()) == 2


def test_never_copy_is_decided_before_the_file_is_opened(tmp_path):
    root = tree(tmp_path)
    locked = root / "secret-proj/s-3.jsonl"
    locked.chmod(0)
    try:
        shelf, cfg = make_shelf(tmp_path), cfg_for(root)
        watchers.dry_run(shelf, cfg, NOW); approve(shelf, cfg)
        out = watchers.run_source(shelf, cfg, now=NOW)
        assert out["counts"].get(codes.FAILED) is None and out["counts"][codes.EXCLUDED] == 1   # an open would have failed
    finally:
        locked.chmod(0o600)


def test_changing_never_copy_makes_the_approval_stale(tmp_path):
    root = tree(tmp_path)
    shelf, cfg = make_shelf(tmp_path), cfg_for(root)
    watchers.dry_run(shelf, cfg, NOW); approve(shelf, cfg)
    loosened = cfg_for(root, never=())                                  # someone removes the exclusion
    assert approvals.source_status(shelf, "claude-code", loosened.fingerprint()) == approvals.STALE
    out = watchers.run_source(shelf, loosened, now=NOW)
    assert out["status"] == "dry_run_only" and shelf.list_records() == []
    assert watchers.run_source(shelf, loosened, now=NOW)["status"] == codes.NOT_APPROVED


def test_a_session_still_being_written_is_unstable_and_retried(tmp_path):
    root = tree(tmp_path)
    shelf, cfg = make_shelf(tmp_path), cfg_for(root)
    watchers.dry_run(shelf, cfg, NOW); approve(shelf, cfg)
    fresh = datetime.fromtimestamp(os.stat(root / "projA/s-1.jsonl").st_mtime, tz=timezone.utc)
    out = watchers.run_source(shelf, cfg, now=fresh, settle_seconds=60)
    assert out["counts"][codes.UNSTABLE] == 2 and shelf.list_records() == []
    assert ledger.report(shelf)["sources"]["claude-code"]["missing"] == {codes.UNSTABLE: 2}     # visible, not lost
    out = watchers.run_source(shelf, cfg, now=datetime.now(timezone.utc), settle_seconds=0)
    assert out["counts"][codes.CAPTURED] == 2 and ledger.report(shelf)["ok"]


def test_disk_low_stops_the_run_and_deletes_nothing(tmp_path, monkeypatch):
    root = tree(tmp_path)
    shelf, cfg = make_shelf(tmp_path), cfg_for(root)
    watchers.dry_run(shelf, cfg, NOW); approve(shelf, cfg)
    watchers.run_source(shelf, cfg, now=NOW)
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(100 * 1024 ** 3, 99 * 1024 ** 3, 1024 ** 3))
    shelf.min_free_bytes = 5 * 1024 ** 3
    (root / "projA/s-1.jsonl").write_text(cc_lines("s-1", ("human", "grown"), ("assistant", "x")))
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out == {"status": codes.DISK_LOW, "counts": {}} and len(shelf.list_records()) == 2


def test_bad_and_empty_sessions_are_visible_in_the_ledger_not_silent(tmp_path):
    root = tmp_path / "p"
    write_tree(root, {"a/only-tools.jsonl": json.dumps({"type": "queue-operation", "sessionId": "q"}) + "\n",
                      "a/empty.jsonl": "", "a/junk.jsonl": "{nope\n{nope2\n" + cc_lines("j", ("human", "real"))})
    shelf, cfg = make_shelf(tmp_path), cfg_for(root, never=())
    watchers.dry_run(shelf, cfg, NOW); approve(shelf, cfg)
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out["counts"] == {codes.EXCLUDED: 2, codes.CAPTURED: 1}
    rep = ledger.report(shelf)["sources"]["claude-code"]
    assert rep["excluded"] == {"empty": 1, "no_turns": 1} and rep["missing"] == {}
    meta, _ = record.load(shelf.main_path(shelf.index()["claude-code:j"]).read_text())
    assert meta["normalized"]["malformed_lines"] == 2


def test_codex_source(tmp_path):
    root = tmp_path / "codex"
    write_tree(root, {"2026/10/03/rollout-2026-10-03T11-00-00-0199aaaa-bbbb-cccc-dddd-eeeeffff0000.jsonl":
                      codex_lines("0199aaaa-bbbb-cccc-dddd-eeeeffff0000", ("human", "q"), ("assistant", "a"))})
    cfg = SourceCfg("codex", "codex", root)
    shelf = make_shelf(tmp_path)
    watchers.dry_run(shelf, cfg, NOW)
    assert json.loads(approvals.dry_run_path(shelf, "codex").read_text())["sessions"][0]["id"] == "0199aaaa-bbbb-cccc-dddd-eeeeffff0000"
    approve(shelf, cfg)
    assert watchers.run_source(shelf, cfg, now=NOW)["counts"] == {codes.CAPTURED: 1}
    assert "codex:0199aaaa-bbbb-cccc-dddd-eeeeffff0000" in shelf.index()


def test_symlinks_are_not_followed(tmp_path):
    root = tmp_path / "p"
    outside = tmp_path / "outside"
    write_tree(outside, {"x.jsonl": cc_lines("x", ("human", "OUTSIDE"))})
    write_tree(root, {"a/real.jsonl": cc_lines("r", ("human", "real"))})
    os.symlink(outside / "x.jsonl", root / "a/link.jsonl")
    os.symlink(outside, root / "linkdir")
    assert [c.rel for c in watchers.discover(SourceCfg("claude-code", "claude-code", root))] == ["a/real.jsonl"]


def test_big_session_is_streamed_with_bounded_memory(tmp_path):
    root = tmp_path / "p"
    (root / "a").mkdir(parents=True)
    filler = "x" * 8000
    with open(root / "a/big.jsonl", "w") as handle:
        for i in range(6000):                                     # ~48 MB of tool results, which are only counted
            handle.write(json.dumps({"type": "user", "sessionId": "big", "message": {"content": [
                {"type": "tool_result", "content": filler}]}}) + "\n")
        handle.write(cc_lines("big", ("human", "the one real question"), ("assistant", "the answer")))
    shelf, cfg = make_shelf(tmp_path), cfg_for(root, never=())
    watchers.dry_run(shelf, cfg, NOW); approve(shelf, cfg)
    tracemalloc.start()
    out = watchers.run_source(shelf, cfg, now=NOW)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert out["counts"] == {codes.CAPTURED: 1} and peak < 10 * 1024 * 1024
    meta, _ = record.load(shelf.main_path(shelf.index()["claude-code:big"]).read_text())
    assert meta["normalized"]["omitted"]["tool_result"] == 6000 and meta["normalized"]["turns"] == 2


def test_nothing_is_ever_written_to_the_source_root(tmp_path):
    root = tree(tmp_path)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    shelf, cfg = make_shelf(tmp_path), cfg_for(root)
    watchers.dry_run(shelf, cfg, NOW); approve(shelf, cfg)
    watchers.run_source(shelf, cfg, now=NOW)
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    assert before == after
