"""Shelf writer, dedup/editions, headroom, permissions, ledger, review queue (B1, B2, R3, §4)."""
import os
import shutil
import stat
from collections import namedtuple

import pytest

from core.memory_shelf import codes, editions, fsio, ledger, record, review
from core.memory_shelf.shelf import Shelf

from .helpers import FAKE_KEY, cc_bundle, cc_lines, make_shelf


def main_of(shelf, key):
    return shelf.main_path(shelf.index()[key])


def test_layout_has_every_kind_and_private_modes(tmp_path):
    shelf = make_shelf(tmp_path)
    for name in ("sessions-raw", "sessions", "notes", "turns", "snapshots", "briefs", "outbox", "_ledger", "_status"):
        assert stat.S_IMODE((shelf.root / name).stat().st_mode) == 0o700
    r = shelf.ingest(cc_bundle(cc_lines("s-1", ("human", "hello"), ("assistant", "hi"))))
    assert r.outcome == codes.CAPTURED
    main = main_of(shelf, "claude-code:s-1")
    assert stat.S_IMODE(main.stat().st_mode) == 0o600
    assert stat.S_IMODE(main.parent.stat().st_mode) == 0o700
    ed = next((main.parent / "editions").iterdir())
    assert stat.S_IMODE(ed.stat().st_mode) == 0o600 and ed.name.startswith("1--")
    assert main.name == f"{main.parent.name}.md" and "--" in main.name


def test_record_loads_and_is_the_scrubbed_edition(tmp_path):
    shelf = make_shelf(tmp_path)
    text = cc_lines("s-1", ("human", f"my key is {FAKE_KEY} ok"), ("assistant", "noted"))
    shelf.ingest(cc_bundle(text))
    main = main_of(shelf, "claude-code:s-1")
    meta, body = record.load(main.read_text())
    assert FAKE_KEY not in main.read_text() and FAKE_KEY not in (main.parent / "editions").glob("*").__next__().read_text()
    assert meta["normalized"]["redacted_turns"] == 1 and meta["edition"] == 1
    assert meta["edition_hash"][:12] in next((main.parent / "editions").iterdir()).name


def test_same_session_twice_is_a_noop(tmp_path):
    shelf = make_shelf(tmp_path)
    b = cc_bundle(cc_lines("s-1", ("human", "hello")))
    assert shelf.ingest(b).outcome == codes.CAPTURED
    before = main_of(shelf, b.key).read_bytes()
    assert shelf.ingest(cc_bundle(cc_lines("s-1", ("human", "hello")))).outcome == codes.UNCHANGED
    assert main_of(shelf, b.key).read_bytes() == before
    assert len(list((main_of(shelf, b.key).parent / "editions").iterdir())) == 1
    assert len(shelf.list_records()) == 1


def test_changed_bytes_with_the_same_session_id_add_an_edition(tmp_path):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(cc_lines("s-1", ("human", "hello"))))
    main = main_of(shelf, "claude-code:s-1")
    first_edition = next((main.parent / "editions").iterdir())
    first_bytes = first_edition.read_bytes()
    r = shelf.ingest(cc_bundle(cc_lines("s-1", ("human", "hello"), ("assistant", "more"))))
    assert r.outcome == codes.EDITION_ADDED
    meta, body = record.load(main.read_text())
    assert meta["edition"] == 2 and "more" in body
    assert [e["kind"] for e in meta["events"]] == ["edition-added"] and meta["events"][0]["edition"] == 2
    assert first_edition.read_bytes() == first_bytes                        # edition 1 is untouched
    assert sorted(p.name[:2] for p in (main.parent / "editions").iterdir()) == ["1-", "2-"]
    assert len(shelf.list_records()) == 1                                    # one conversation, one record
    # going back to the old bytes is not a new edition either
    assert shelf.ingest(cc_bundle(cc_lines("s-1", ("human", "hello")))).outcome == codes.UNCHANGED


def test_events_survive_a_new_edition(tmp_path):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(cc_lines("s-1", ("human", "file this: first"), ("assistant", "ok"))))
    shelf.ingest(cc_bundle(cc_lines("s-1", ("human", "file this: first"), ("assistant", "ok"), ("human", "more"))))
    meta, _ = record.load(main_of(shelf, "claude-code:s-1").read_text())
    kinds = [e["kind"] for e in meta["events"]]
    assert kinds.count("designated-curated") == 1 and "edition-added" in kinds       # designated once, not per edition


def test_edition_files_are_never_overwritten(tmp_path, monkeypatch):
    folder = tmp_path / "rec"
    folder.mkdir()
    a = editions.add_edition(folder, b"one", "jsonl")
    b = editions.add_edition(folder, b"two", "jsonl")
    assert (a.number, b.number) == (1, 2) and a.path.read_bytes() == b"one"
    # a file already sitting at the next edition's name is never replaced
    blocker = folder / "editions" / f"3--{editions.digest(b'three')[:12]}.jsonl"
    blocker.write_bytes(b"not yours")
    real = editions.list_editions                       # simulate a racing writer the listing has not seen
    monkeypatch.setattr(editions, "list_editions", lambda d: [e for e in real(d) if e.number < 3])
    with pytest.raises(FileExistsError):
        editions.add_edition(folder, b"three", "jsonl")
    assert blocker.read_bytes() == b"not yours" and a.path.read_bytes() == b"one"


def test_different_source_id_is_never_auto_merged(tmp_path):
    shelf = make_shelf(tmp_path)
    same_words = ("human", "identical first message")
    shelf.ingest(cc_bundle(cc_lines("s-1", same_words)))
    shelf.ingest(cc_bundle(cc_lines("s-2", same_words)))
    assert len(shelf.list_records()) == 2
    # two CLI sessions starting alike are not a manual-submission overlap: no flag
    assert review.items(shelf) == []


def test_disk_low_skips_writes_and_deletes_nothing(tmp_path, monkeypatch):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(cc_lines("s-1", ("human", "hello"))))
    keep = main_of(shelf, "claude-code:s-1")
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(100 * 1024 ** 3, 98 * 1024 ** 3, 2 * 1024 ** 3))
    low = Shelf(shelf.root, min_free_bytes=5 * 1024 ** 3)
    assert low.headroom() == codes.DISK_LOW
    path, code = low.stage(cc_bundle(cc_lines("s-2", ("human", "x"))))
    assert path is None and code == codes.DISK_LOW and low.pending() == []
    assert low.ingest(cc_bundle(cc_lines("s-3", ("human", "x")))).outcome == codes.DISK_LOW
    assert keep.is_file() and len(low.list_records()) == 1                    # nothing written, nothing removed


def test_unmeasurable_free_space_counts_as_disk_low(tmp_path, monkeypatch):
    def boom(_):
        raise OSError("no")
    monkeypatch.setattr(shutil, "disk_usage", boom)
    assert fsio.check_headroom(tmp_path, 1) == codes.DISK_LOW


def test_fraction_threshold_for_ec2(tmp_path, monkeypatch):
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(100, 95, 5))
    assert fsio.check_headroom(tmp_path, None, 0.10) == codes.DISK_LOW
    assert fsio.check_headroom(tmp_path, None, 0.04) is None


def test_outbox_stage_then_drain_keeps_the_bundle_and_is_idempotent(tmp_path):
    shelf = make_shelf(tmp_path)
    b = cc_bundle(cc_lines("s-1", ("human", "hello")))
    path, _ = shelf.stage(b)
    assert path.is_file() and shelf.pending() == [path]
    assert [r.outcome for r in shelf.drain()] == [codes.CAPTURED]
    assert shelf.pending() == [] and (shelf.outbox / "_done" / path.name).is_file()      # moved, never deleted
    path2, _ = shelf.stage(cc_bundle(cc_lines("s-1", ("human", "hello"))))
    assert [r.outcome for r in shelf.drain()] == [codes.UNCHANGED]


def test_a_crash_between_edition_and_index_never_makes_a_second_record(tmp_path, monkeypatch):
    shelf = make_shelf(tmp_path)
    b = cc_bundle(cc_lines("s-1", ("human", "hello")))
    real = fsio.write_json
    calls = {"n": 0}

    def flaky(path, doc):
        if path == shelf.index_path and calls["n"] == 0:
            calls["n"] += 1
            raise OSError("crash")
        return real(path, doc)
    monkeypatch.setattr(fsio, "write_json", flaky)
    with pytest.raises(OSError):
        shelf.ingest(b)                                                        # record written, index not
    assert shelf._dirty.exists()
    assert shelf.ingest(cc_bundle(cc_lines("s-1", ("human", "hello")))).outcome == codes.UNCHANGED
    assert len(list((shelf.root / "sessions-raw").iterdir())) == 1


def test_ledger_counts_captured_exclusions_and_missing(tmp_path):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(cc_lines("s-1", ("human", f"{FAKE_KEY}"), ("assistant", "x"))))
    ledger.record(shelf, "claude-code", "k-never", codes.EXCLUDED, reason=codes.NEVER_COPY)
    ledger.record(shelf, "claude-code", "k-crash", codes.DISCOVERED)
    ledger.record(shelf, "claude-code", "k-low", codes.DISK_LOW)
    rep = ledger.report(shelf)
    src = rep["sources"]["claude-code"]
    assert src["captured"] == 1 and src["excluded"] == {"never_copy": 1} and src["expected"] == 4
    assert src["missing"] == {"discovered": 1, "disk_low": 1} and rep["missing"] == 2 and not rep["ok"]
    assert rep["expected_exclusions"]["scrubbed_turns"] == 1
    assert "never_copy" in str(rep) and "k-never" not in str(rep)


def test_ledger_notices_a_captured_record_that_is_gone(tmp_path):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(cc_lines("s-1", ("human", "hello"))))
    shutil.rmtree(shelf.root / "sessions-raw")
    (shelf.root / "sessions-raw").mkdir()
    shelf.index_path.unlink()
    rep = ledger.report(shelf)
    assert rep["sources"]["claude-code"]["missing"] == {"record_absent": 1}


def test_review_items_are_idempotent_and_resolve(tmp_path):
    shelf = make_shelf(tmp_path)
    assert review.add(shelf, "designation-candidate", "abc", {"record": "r"}) is True
    assert review.add(shelf, "designation-candidate", "abc", {"record": "r"}) is False
    assert len(review.items(shelf)) == 1
    assert review.resolve(shelf, "designation-candidate", "abc", "confirmed")
    assert review.items(shelf) == [] and len(review.items(shelf, state=None)) == 1
    with pytest.raises(ValueError):
        review.add(shelf, "approved", "x", {})
