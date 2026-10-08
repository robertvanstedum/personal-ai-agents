"""Torn tails, failed writes and damaged journals (v0.6 section 6 as amended by v0.7 Unit 1; acceptance rows J03, J04, J05)."""
from __future__ import annotations

import errno
import hashlib
import os
import shutil
import sys
from pathlib import Path

import pytest

from conftest import REPO, WORKSHOP, envelope, new_id, progress, run_child
from core.workshop_journal import fsutil, schema, strictjson
from core.workshop_journal.errors import RecoveryBlocked, WriteFailed
from core.workshop_journal.journal import Journal


def jpath(root) -> Path:
    return Path(root) / WORKSHOP / "events.jsonl"


def base(root) -> Path:
    return Path(root) / WORKSHOP


def seeded(root, n=3) -> Journal:
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    for i in range(n):
        assert j.append(progress(f"seed {i}")).committed
    return j


def clone(root, tmp_path, name) -> str:
    dest = tmp_path / name
    dest.mkdir(mode=0o700)
    shutil.copytree(root, dest, dirs_exist_ok=True)
    return str(dest)


def line_for(root, tmp_path, resubmit: dict) -> bytes:
    """The exact bytes the journal would write for a prepared envelope, produced in a throwaway copy of the folder."""
    copy = clone(root, tmp_path, "oracle")
    result = Journal(copy, WORKSHOP).append(resubmit)
    assert result.committed, result.to_json()
    return jpath(copy).read_bytes().splitlines(keepends=True)[-1]


def only_journal(root, fault, real):
    """Wrap fsutil._write so ``fault`` applies to the journal file and everything else (receipts, state) writes normally."""
    ino = jpath(root).stat().st_ino

    def wrapped(fd, view):
        return fault(fd, view) if os.fstat(fd).st_ino == ino else real(fd, view)
    return wrapped


def quarantine_files(root) -> dict[str, bytes]:
    folder = base(root) / "quarantine"
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())} if folder.exists() else {}


def manifests(root) -> list[dict]:
    return [strictjson.loads(v) for k, v in quarantine_files(root).items() if k.startswith("manifest-")]


def torn(root, tmp_path, *, cut: int | None, receipt: bool = True, text: str = "an interrupted handoff 😀 é"):
    """A journal whose last write was interrupted: returns (journal, prefix_bytes, tail_bytes, resubmit_envelope)."""
    j = seeded(root)
    prepared = j.prepare(envelope(text=text))
    resubmit = prepared.evidence["envelope"]
    line = line_for(root, tmp_path, resubmit)
    prefix = jpath(root).read_bytes()
    tail = line if cut is None else line[:cut]
    jpath(root).write_bytes(prefix + tail)
    if not receipt:
        (base(root) / "prepared" / f"{prepared.event_id}.json").unlink()
    return j, prefix, tail, resubmit


# ── J03: a crash at every kind of write boundary ───────────────────────────────────────────────────────────────────
def cut_points(line_len: int, emoji_at: int) -> list[int]:
    spread = set(range(1, 40)) | set(range(40, line_len - 1, 23)) | set(range(line_len - 8, line_len))
    return sorted(c for c in spread | set(range(emoji_at - 1, emoji_at + 5)) if 0 < c < line_len)


def test_J03_a_cut_at_any_byte_keeps_prior_records_preserves_the_evidence_and_never_glues_the_next_append(root, tmp_path):
    probe_root = clone(root, tmp_path, "probe-root") if False else None                    # (kept simple: one line length per run)
    j = seeded(root)
    prepared = j.prepare(envelope(text="an interrupted handoff 😀 é"))
    full = line_for(root, tmp_path, prepared.evidence["envelope"])
    emoji_at = full.index("😀".encode())
    shutil.rmtree(tmp_path / "oracle")
    cases = cut_points(len(full), emoji_at)
    assert len(cases) > 60
    for cut in cases:
        work = tmp_path / f"case-{cut}"
        work.mkdir(mode=0o700)
        r = str(work / "ws")
        shutil.copytree(root, r)
        data = jpath(r).read_bytes()
        jpath(r).write_bytes(data + full[:cut])
        after = Journal(r, WORKSHOP, lock_timeout=0.3)
        assert after.read().status == "torn_tail"
        result = after.append(progress("after the crash"))
        assert result.committed, (cut, result.to_json())
        new = jpath(r).read_bytes()
        assert new.startswith(data), cut                                                    # prior valid records intact
        events = after.read().events
        assert after.read().status == "ok" and [e["seq"] for e in events] == list(range(1, len(events) + 1)), cut
        kinds = [e["kind"] for e in events]
        if cut == len(full) - 1:                                                           # whole JSON, only the newline missing
            assert kinds.count("recovery") == 1 and prepared.event_id in {e["event_id"] for e in events}, cut
        else:
            assert kinds.count("recovery") == 1 and prepared.event_id not in {e["event_id"] for e in events}, cut
        evidence = quarantine_files(r)
        tail_sha = hashlib.sha256(full[:cut]).hexdigest()
        assert evidence[f"tail-{tail_sha}.bin"] == full[:cut], cut                          # the exact bytes are kept
        assert after.verify()["ok"] is True, cut


def test_J03_a_whole_json_tail_that_matches_its_prepare_receipt_is_completed(root, tmp_path):
    j, prefix, tail, resubmit = torn(root, tmp_path, cut=None)[0:4]
    tail_no_lf = tail.rstrip(b"\n")
    jpath(root).write_bytes(prefix + tail_no_lf)
    got = j.get(resubmit["event_id"])
    assert got.status == "not_found"                                                       # a read does not repair
    again = j.append(resubmit)                                                              # the retry finds it committed by recovery
    assert again.status == "duplicate" and again.committed and again.seq == 4
    events = j.read().events
    assert [e["kind"] for e in events] == ["progress"] * 3 + ["request", "recovery"] and events[3]["event_id"] == resubmit["event_id"]
    [m] = manifests(root)
    assert m["action"] == "completed_lf" and m["reason"] == "matched_prepare_receipt" and m["event_id"] == resubmit["event_id"]
    assert events[4]["payload"]["manifest_hash"] == hashlib.sha256(strictjson.canonical_bytes(m)).hexdigest()


def test_J03_valid_json_without_a_prepare_receipt_is_quarantined_even_though_it_parses(root, tmp_path):
    j, prefix, tail, resubmit = torn(root, tmp_path, cut=None, receipt=False)
    jpath(root).write_bytes(prefix + tail.rstrip(b"\n"))
    result = j.append(progress("next"))
    assert result.committed
    assert resubmit["event_id"] not in {e["event_id"] for e in j.read().events}
    [m] = manifests(root)
    assert (m["action"], m["reason"]) == ("quarantined_truncated", "no_prepare_receipt")
    retry = j.append(resubmit)                                                              # the stable ID survives; the retry commits
    assert retry.status == "committed" and retry.event_id == resubmit["event_id"]


def test_J03_valid_json_with_the_wrong_intent_is_quarantined(root, tmp_path):
    j = seeded(root)
    eid = new_id()
    prepared = j.prepare(envelope(event_id=eid, text="what was prepared"))
    intent = schema.normalize_intent({**envelope(text="what was actually written"), "event_id": eid,
                                      "at": prepared.evidence["envelope"]["at"]}, workshop=WORKSHOP, stream=j.stream,
                                     actors=schema.DEFAULT_ACTORS, resolved_at=None, event_id=None)
    row = schema.build_event(intent, seq=4, recorded_at="2026-10-08T20:00:00Z", origin=schema.make_origin("local-helper"))
    prefix = jpath(root).read_bytes()
    jpath(root).write_bytes(prefix + strictjson.canonical_bytes(row))                       # valid, canonical, next seq, matching ID
    assert j.append(progress("next")).committed
    [m] = manifests(root)
    assert (m["action"], m["reason"]) == ("quarantined_truncated", "intent_mismatch")
    assert eid not in {e["event_id"] for e in j.read().events}


def test_J03_a_tail_with_the_wrong_sequence_or_a_repeated_id_is_quarantined(root, tmp_path):
    j, prefix, tail, resubmit = torn(root, tmp_path, cut=None)
    row = strictjson.loads(tail)
    row["seq"] = 9                                                                          # forged sequence (not even canonical now)
    jpath(root).write_bytes(prefix + strictjson.canonical_bytes(row))
    assert j.append(progress("next")).committed
    assert manifests(root)[0]["reason"] in ("seq_mismatch", "invalid_event")


def test_J03_a_writer_killed_halfway_leaves_a_tail_the_next_append_preserves_and_cuts(root):
    crash = """
import os, sys
from core.workshop_journal import fsutil
from core.workshop_journal.journal import Journal
real = fsutil._write
journal_ino = os.stat(os.path.join(sys.argv[1], 'workshop-neubau', 'events.jsonl')).st_ino
def half(fd, view):
    if os.fstat(fd).st_ino != journal_ino:       # let the prepare receipt through; kill only inside the journal write
        return real(fd, view)
    real(fd, view[: max(1, len(view) // 2)])
    os._exit(9)                                  # a hard kill in the middle of the write
fsutil._write = half
Journal(sys.argv[1], 'workshop-neubau').append({'actor': 'codex', 'kind': 'progress', 'item': 'queue:146', 'text': 'killed',
                                              'payload': {'action': 'x'}})
"""
    j = seeded(root)
    before = jpath(root).read_bytes()
    child = run_child(crash, root)
    assert child.returncode == 9
    data = jpath(root).read_bytes()
    assert data.startswith(before) and len(data) > len(before) and not data.endswith(b"\n")
    assert j.read().status == "torn_tail" and j.verify()["tail_bytes"] == len(data) - len(before)
    assert j.append(progress("after the kill")).committed
    assert jpath(root).read_bytes().startswith(before) and j.read().status == "ok"
    assert hashlib.sha256(data[len(before):]).hexdigest() in "".join(quarantine_files(root))


def test_J03_recovery_is_idempotent_after_a_crash_between_preserving_and_cutting(root, tmp_path, monkeypatch):
    j, prefix, tail, resubmit = torn(root, tmp_path, cut=50)
    real = fsutil.truncate

    def explode(fd, size):
        raise OSError(errno.EIO, "crash before the cut")
    monkeypatch.setattr(fsutil, "truncate", explode)
    blocked = j.append(progress("first try"))
    assert (blocked.status, blocked.exit_code) == ("recovery_blocked", 5)
    assert jpath(root).read_bytes() == prefix + tail                                         # nothing destroyed
    monkeypatch.setattr(fsutil, "truncate", real)
    assert j.append(progress("second try")).committed
    assert len([k for k in quarantine_files(root) if k.startswith("manifest-")]) == 1        # one manifest, not two
    assert [e["kind"] for e in j.read().events].count("recovery") == 1


def test_J03_a_recovery_event_missed_by_a_crash_is_emitted_by_the_next_append(root, tmp_path, monkeypatch):
    j, prefix, tail, resubmit = torn(root, tmp_path, cut=60)

    def lost(self, s, doc, mhash):
        raise WriteFailed("crash after the cut")
    real = Journal._emit_recovery
    monkeypatch.setattr(Journal, "_emit_recovery", lost)
    first = j.append(progress("first"))
    assert first.status == "write_failed"
    assert jpath(root).read_bytes() == prefix                                                # the cut happened; no recovery event yet
    monkeypatch.setattr(Journal, "_emit_recovery", real)
    assert j.append(progress("second")).committed
    assert [e["kind"] for e in j.read().events].count("recovery") == 1
    assert j.verify()["recoveries_missing"] == 0


def test_J03_repair_dry_run_reports_without_changing_anything(root, tmp_path):
    j, prefix, tail, resubmit = torn(root, tmp_path, cut=70)
    snapshot = (jpath(root).read_bytes(), quarantine_files(root))
    plan = j.repair()
    assert plan["status"] == "would_repair" and plan["action"] == "quarantined_truncated" and plan["tail_bytes"] == len(tail)
    assert (jpath(root).read_bytes(), quarantine_files(root)) == snapshot
    done = j.repair(apply=True)
    assert done["status"] == "repaired" and done["manifest_hash"] == plan["manifest_hash"]
    assert j.read().status == "ok" and j.repair()["status"] == "nothing_to_repair"


# ── J04: failures while writing ────────────────────────────────────────────────────────────────────────────────────
def test_J04_short_writes_are_completed(root, monkeypatch):
    j = seeded(root)
    real = fsutil._write
    monkeypatch.setattr(fsutil, "_write", lambda fd, view: real(fd, view[:7]))              # never more than seven bytes at a time
    assert j.append(envelope()).committed
    monkeypatch.undo()
    assert j.read().status == "ok" and len(j.read().events) == 4 and j.verify()["ok"]


def test_J04_an_interrupted_call_is_retried(root, monkeypatch):
    j = seeded(root)
    real, calls = fsutil._write, []

    def flaky(fd, view):
        calls.append(1)
        if len(calls) == 1:
            raise InterruptedError()
        if len(calls) == 2:
            raise OSError(errno.EINTR, "interrupted")
        return real(fd, view)
    monkeypatch.setattr(fsutil, "_write", only_journal(root, flaky, real))
    assert j.append(envelope()).committed and len(calls) == 3


def test_J04_a_zero_byte_write_is_a_failure_that_leaves_nothing_and_a_retry_keeps_the_id(root, monkeypatch):
    j = seeded(root)
    before = jpath(root).read_bytes()
    real = fsutil._write
    monkeypatch.setattr(fsutil, "_write", only_journal(root, lambda fd, view: 0, real))
    eid = new_id()
    failed = j.append(envelope(event_id=eid))
    assert (failed.status, failed.committed, failed.retryable, failed.exit_code) == ("write_failed", False, True, 6)
    assert jpath(root).read_bytes() == before
    monkeypatch.setattr(fsutil, "_write", real)
    retried = j.append(envelope(event_id=eid))
    assert retried.status == "committed" and retried.event_id == eid and retried.seq == 4


def test_J04_a_partial_write_then_a_full_disk_is_cut_back(root, monkeypatch):
    j = seeded(root)
    before = jpath(root).read_bytes()
    real, count = fsutil._write, []

    def partial_then_full(fd, view):
        count.append(1)
        if len(count) == 1:
            return real(fd, view[:10])
        raise OSError(errno.ENOSPC, "no space")
    monkeypatch.setattr(fsutil, "_write", only_journal(root, partial_then_full, real))
    failed = j.append(envelope())
    assert failed.status == "write_failed" and failed.evidence == {"errno": errno.ENOSPC}
    assert jpath(root).read_bytes() == before                                               # the ten bytes were cut back off


def test_J04_if_the_cut_back_also_fails_the_next_append_quarantines_the_partial_line(root, monkeypatch):
    j = seeded(root)
    before = jpath(root).read_bytes()
    real_w, real_t, count = fsutil._write, fsutil.truncate, []

    def partial_then_full(fd, view):
        count.append(1)
        if len(count) == 1:
            return real_w(fd, view[:25])
        raise OSError(errno.ENOSPC, "no space")
    monkeypatch.setattr(fsutil, "_write", only_journal(root, partial_then_full, real_w))
    monkeypatch.setattr(fsutil, "truncate", lambda fd, size: (_ for _ in ()).throw(OSError(errno.EIO, "no cut")))
    assert j.append(envelope()).status == "write_failed"
    assert len(jpath(root).read_bytes()) == len(before) + 25
    monkeypatch.setattr(fsutil, "_write", real_w)
    monkeypatch.setattr(fsutil, "truncate", real_t)
    assert j.append(progress("after")).committed
    assert jpath(root).read_bytes().startswith(before) and j.read().status == "ok"


def test_J04_a_sync_failure_is_commit_unknown_resolved_by_id_without_a_second_event(root, monkeypatch):
    j = seeded(root)
    monkeypatch.setattr(fsutil, "full_sync", lambda fd, degraded_ok=False: (_ for _ in ()).throw(OSError(errno.EIO, "sync")))
    eid = new_id()
    unknown = j.append(envelope(event_id=eid))
    assert (unknown.status, unknown.committed, unknown.retryable, unknown.exit_code, unknown.event_id) == ("commit_unknown", None, True, 6, eid)
    monkeypatch.undo()
    assert j.get(eid).status == "found"                                                      # ask by ID before retrying
    again = j.append(envelope(event_id=eid))
    assert again.status == "duplicate" and len([e for e in j.read().events if e["event_id"] == eid]) == 1


@pytest.mark.skipif(sys.platform != "darwin", reason="F_FULLFSYNC exists on macOS")
def test_J04_strict_refuses_a_volume_without_full_sync_and_degraded_says_so(root, monkeypatch):
    def no_full(fd, cmd, *a):
        if cmd == fsutil.fcntl.F_FULLFSYNC:
            raise OSError(errno.ENOTSUP, "not supported")
        return 0
    monkeypatch.setattr(fsutil.fcntl, "fcntl", no_full)
    strict = Journal(root, WORKSHOP, lock_timeout=0.3).append(envelope())
    assert strict.status == "commit_unknown" and strict.committed is None                   # never an ordinary durable success
    degraded = Journal(root, WORKSHOP, durability="degraded", lock_timeout=0.3).append(envelope(event_id=strict.event_id))
    assert degraded.status == "duplicate"
    fresh = Journal(root, WORKSHOP, durability="degraded", lock_timeout=0.3).append(progress("on a weaker volume"))
    assert fresh.status == "committed" and fresh.evidence["durability"] == "degraded"


def test_J04_quarantine_that_cannot_be_written_blocks_recovery_and_changes_nothing(root, tmp_path, monkeypatch):
    j, prefix, tail, resubmit = torn(root, tmp_path, cut=80)
    real = fsutil.publish_new

    def refuse(dir_fd, name, data, **kw):
        if name.startswith("tail-"):
            raise OSError(errno.ENOSPC, "no space for evidence")
        return real(dir_fd, name, data, **kw)
    monkeypatch.setattr(fsutil, "publish_new", refuse)
    blocked = j.append(progress("next"))
    assert (blocked.status, blocked.exit_code, blocked.committed) == ("recovery_blocked", 5, False)
    assert jpath(root).read_bytes() == prefix + tail                                         # no truncation without evidence


# ── J05: interior damage, forged sequences, unknown versions ───────────────────────────────────────────────────────
def damaged(root, how: str):
    j = seeded(root, 5)
    lines = jpath(root).read_bytes().splitlines(keepends=True)
    third = strictjson.loads(lines[2])
    if how == "garbage":
        lines[2] = b"{ this is not json }\n"
    elif how == "wrong_intent":
        third["text"] = "edited after the fact"
        lines[2] = strictjson.canonical_bytes(third) + b"\n"
    elif how == "gap":
        del lines[2]
    elif how == "repeated_seq":
        lines[3] = lines[2]
    elif how == "unknown_version":
        third["v"] = 3
        lines[2] = strictjson.canonical_bytes(third) + b"\n"
    elif how == "blank_line":
        lines.insert(2, b"\n")
    elif how == "duplicate_id":
        fourth = strictjson.loads(lines[3])
        fourth["event_id"] = third["event_id"]
        lines[3] = strictjson.canonical_bytes(fourth) + b"\n"
    elif how == "non_utf8":
        lines[2] = b'{"v":2,"x":"\xff"}\n'
    elif how == "duplicate_key":
        lines[2] = lines[2].replace(b'{"actor"', b'{"actor":"codex","actor"', 1)
    jpath(root).write_bytes(b"".join(lines))
    return j, lines


@pytest.mark.parametrize("how", ["garbage", "wrong_intent", "gap", "repeated_seq", "unknown_version", "blank_line",
                                 "duplicate_id", "non_utf8", "duplicate_key"])
def test_J05_interior_damage_is_reported_as_a_gap_and_holds_writes(root, how):
    j, lines = damaged(root, how)
    bytes_before = jpath(root).read_bytes()
    good = 3 if how in ("repeated_seq", "duplicate_id") else 2                                # the row index where the damage sits
    read = j.read(deep=True)
    assert read.status == "corrupt" and read.complete is False, how
    assert [e["seq"] for e in read.events] == list(range(1, good + 1)), how                  # exactly the valid prefix, nothing reordered
    assert read.scan.problems and read.scan.problems[0]["offset"] == sum(len(l) for l in lines[:good]), how
    report = j.verify()
    assert report["ok"] is False and report["problems"] and report["events"] == good, how
    held = j.append(envelope())
    assert (held.status, held.exit_code, held.committed) == ("corrupt", 5, False), how
    assert jpath(root).read_bytes() == bytes_before, how                                     # held, not repaired, not appended to
    assert j.repair()["status"] == "blocked" and j.repair(apply=True)["status"] == "corrupt"
    assert j.state() == (None, "refused")


def test_J05_a_forged_sequence_is_never_reordered_or_skipped(root):
    j = seeded(root, 4)
    lines = jpath(root).read_bytes().splitlines(keepends=True)
    lines[1], lines[2] = lines[2], lines[1]
    jpath(root).write_bytes(b"".join(lines))
    read = j.read()
    assert read.status == "corrupt" and [e["seq"] for e in read.events] == [1]


def test_J05_tampering_beyond_the_newest_rows_is_caught_by_verify_not_by_an_append(root):
    """Decision recorded in WORKSHOP_BUILD_DECISIONS: an append deep-checks the newest 25 rows (a full deep scan costs about
    14 times a shallow one); verify and deep reads check them all."""
    j = seeded(root, 40)
    lines = jpath(root).read_bytes().splitlines(keepends=True)
    row = strictjson.loads(lines[4])
    row["text"] = "edited long after"
    lines[4] = strictjson.canonical_bytes(row) + b"\n"
    jpath(root).write_bytes(b"".join(lines))
    assert j.verify()["ok"] is False and j.read(deep=True).status == "corrupt"
    assert j.append(progress("an append only checks the newest rows")).committed


def test_J04_evidence_whose_folder_sync_fails_blocks_the_cut(root, tmp_path, monkeypatch):
    j, prefix, tail, resubmit = torn(root, tmp_path, cut=90)

    def fail_sync(dir_fd, *, durable=True):
        if durable:
            raise OSError(errno.EIO, "directory sync failed")
    monkeypatch.setattr(fsutil, "fsync_dir", fail_sync)
    blocked = j.append(progress("next"))
    assert (blocked.status, blocked.committed) == ("recovery_blocked", False)
    assert jpath(root).read_bytes() == prefix + tail                                         # evidence not durable: nothing cut
