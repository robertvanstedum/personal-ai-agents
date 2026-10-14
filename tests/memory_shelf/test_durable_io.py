"""Failure injection for the bytes that become durable Vault records; synthetic only."""
import errno
import json
import os
import stat
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from core.memory_ask import identity
from core.memory_shelf import canary, editions, fsio, record


def fake_os(monkeypatch, **changes):
    monkeypatch.setattr(fsio, "os", types.SimpleNamespace(
        **{**{k: getattr(os, k) for k in dir(os)}, **changes}))


def meta():
    return record.new_front_matter(kind="session", chair="Codex", source="test:durable",
                                  source_hash="a" * 64, created="2026-10-12T12:00:00Z")


def test_short_positive_writes_complete_utf8_and_no_staging_file_remains(tmp_path, monkeypatch):
    fake_os(monkeypatch, write=lambda fd, data: os.write(fd, data[:3]))
    data = ("Grüße à bientôt 😀\n" * 30).encode()
    target = tmp_path / "new" / "record"
    fsio.write_atomic(target, data)
    assert target.read_bytes() == data
    assert list(target.parent.iterdir()) == [target]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("fault", ["zero", "partial-error", "sync"])
@pytest.mark.parametrize("operation", ["atomic", "record", "registry"])
def test_failed_replacement_preserves_previous_complete_file(tmp_path, monkeypatch, fault, operation):
    target = tmp_path / "saved"
    original = b"previous complete bytes\n"
    target.write_bytes(original)
    calls = 0

    def write(fd, data):
        nonlocal calls
        calls += 1
        if fault == "zero":
            return 0
        if calls == 1:
            return os.write(fd, data[:7])
        raise OSError(errno.ENOSPC, "synthetic disk full")

    def sync(fd):
        raise OSError(errno.EIO, "synthetic sync failure")

    fake_os(monkeypatch, **({"fsync": sync} if fault == "sync" else {"write": write}))
    with pytest.raises(OSError):
        if operation == "atomic":
            fsio.write_atomic(target, b"replacement content" * 30)
        elif operation == "record":
            record.write(target, meta(), "replacement record body")
        else:
            identity._write_raw(target, {"schema": identity.SCHEMA, "agents": {}})
    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]


def test_interrupted_write_retries_without_duplicating_bytes(tmp_path, monkeypatch):
    calls = 0

    def write(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError()
        return os.write(fd, data[:2])

    fake_os(monkeypatch, write=write)
    path = tmp_path / "complete"
    fsio.write_atomic(path, b"exact bytes")
    assert path.read_bytes() == b"exact bytes"


@pytest.mark.parametrize("operation", ["edition", "canary"])
def test_failed_immutable_write_never_publishes_a_partial_final_file(tmp_path, monkeypatch, operation):
    calls = 0

    def write(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return os.write(fd, data[:9])
        raise OSError(errno.ENOSPC, "synthetic full disk")

    def run():
        if operation == "edition":
            return editions.add_edition(tmp_path, b"edition bytes" * 20).path
        return canary.emit(tmp_path, datetime(2026, 10, 12, tzinfo=timezone.utc))

    fake_os(monkeypatch, write=write)
    with pytest.raises(OSError):
        run()
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]
    monkeypatch.undo()
    target = run()
    saved = target.read_bytes()
    assert saved
    assert run() == target and target.read_bytes() == saved
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_exclusive_publication_never_replaces_existing_file_or_symlink(tmp_path):
    target = tmp_path / "kept"
    fsio.write_exclusive(target, b"original")
    with pytest.raises(FileExistsError):
        fsio.write_exclusive(target, b"other")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        fsio.write_exclusive(link, b"other")
    assert target.read_bytes() == b"original" and link.is_symlink()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["kept", "link"]


def test_staging_names_are_unique_under_concurrent_replacements(tmp_path):
    target = tmp_path / "same-record"
    values = [str(i).encode() * 10000 for i in range(8)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda value: fsio.write_atomic(target, value), values))
    assert target.read_bytes() in values
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("fault", ["zero", "partial-error", "sync"])
def test_failed_append_rolls_back_only_its_own_bytes(tmp_path, monkeypatch, fault):
    path = tmp_path / "events.jsonl"
    fsio.append_jsonl(path, {"event": "prior", "text": "kept"})
    before = path.read_bytes()
    calls = 0

    def write(fd, data):
        nonlocal calls
        calls += 1
        if fault == "zero":
            return 0
        if calls == 1:
            return os.write(fd, data[:5])
        raise OSError(errno.EIO, "synthetic write failure")

    def sync(fd):
        raise OSError(errno.EIO, "synthetic sync failure")

    fake_os(monkeypatch, **({"fsync": sync} if fault == "sync" else {"write": write}))
    with pytest.raises(OSError):
        fsio.append_jsonl(path, {"event": "failed", "text": "never saved"})
    assert path.read_bytes() == before
    monkeypatch.undo()
    fsio.append_jsonl(path, {"event": "next"})
    assert [r["event"] for r in fsio.read_jsonl(path)] == ["prior", "next"]


def test_concurrent_short_appends_do_not_interleave_rows(tmp_path, monkeypatch):
    fake_os(monkeypatch, write=lambda fd, data: os.write(fd, data[:3]))
    path = tmp_path / "events.jsonl"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: fsio.append_jsonl(path, {"id": i, "text": "é" * 50}), range(12)))
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    assert sorted(r["id"] for r in rows) == list(range(12))
    assert all(r["text"] == "é" * 50 for r in rows)


def test_directory_sync_failure_is_reported_not_silently_successful(tmp_path, monkeypatch):
    def sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "synthetic directory sync failure")
        return os.fsync(fd)

    fake_os(monkeypatch, fsync=sync)
    target = tmp_path / "whole-but-publication-uncertain"
    with pytest.raises(fsio.PublicationUncertain, match="publication_uncertain"):
        fsio.write_atomic(target, b"complete content")
    # The rename already happened; callers must treat this as uncertain, not as
    # proof of a rollback. Any visible bytes are whole; retry is idempotent.
    assert target.read_bytes() == b"complete content"
    assert list(tmp_path.iterdir()) == [target]


def test_failed_rollback_cannot_swallow_the_next_good_jsonl_row(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    fsio.append_jsonl(path, {"event": "prior"})
    calls = 0

    def write(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return os.write(fd, data[:5])
        raise OSError(errno.EIO, "synthetic failure")

    def truncate(fd, size):
        raise OSError(errno.EIO, "synthetic rollback failure")

    fake_os(monkeypatch, write=write, ftruncate=truncate)
    with pytest.raises(OSError):
        fsio.append_jsonl(path, {"event": "failed"})
    monkeypatch.undo()
    fsio.append_jsonl(path, {"event": "next"})
    rows = fsio.read_jsonl(path)
    assert rows == [{"event": "prior"}, {"record_type": "log_gap", "reason": "truncated_tail"}, {"event": "next"}]


def test_registry_reports_published_but_uncertain_without_printing_a_token(tmp_path, monkeypatch):
    from core.memory_ask import agents_cli
    from io import StringIO

    def sync(path):
        raise OSError(errno.EIO, "synthetic directory sync failure")

    monkeypatch.setattr(fsio, "fsync_dir", sync)
    target = tmp_path / "agents.json"
    out = StringIO()
    assert agents_cli.main(["--registry", str(target), "add", "mc"], out=out,
                           is_tty=lambda: True, confirm=lambda _: True) == agents_cli.PROBLEM
    text = out.getvalue()
    assert text.startswith("uncertain: registry_durability_unconfirmed")
    assert "token (shown once" not in text and "token_sha256" not in text
    assert "mc" in identity.load(target)  # published, not silently claimed to be rolled back


@pytest.mark.parametrize("action", ["revoke", "rotate"])
def test_registry_uncertainty_for_existing_grant_has_action_specific_recovery(tmp_path, monkeypatch, action):
    from core.memory_ask import agents_cli
    from io import StringIO

    target = tmp_path / "agents.json"
    old_token = identity.add_agent(target, "mc")
    old_hash = json.loads(target.read_text())["agents"]["mc"]["token_sha256"]

    def sync(path):
        raise OSError(errno.EIO, "synthetic directory sync failure")

    monkeypatch.setattr(fsio, "fsync_dir", sync)
    out = StringIO()
    args = ["--registry", str(target), action, "mc"]
    assert agents_cli.main(args, out=out, is_tty=lambda: True, confirm=lambda _: True) == agents_cli.PROBLEM
    assert out.getvalue().startswith("uncertain:") and "token (shown once" not in out.getvalue()
    assert old_token not in out.getvalue()
    if action == "revoke":
        assert "Re-run revoke" in out.getvalue() and "safe to repeat" in out.getvalue()
        assert json.loads(target.read_text())["agents"]["mc"]["revoked_at"] is not None
    else:
        assert "rotate a token" in out.getvalue()
        assert json.loads(target.read_text())["agents"]["mc"]["token_sha256"] != old_hash
    monkeypatch.undo()
    # Repeating revoke writes and syncs again, even when the timestamp is set.
    assert agents_cli.main(args, out=StringIO(), is_tty=lambda: True, confirm=lambda _: True) == agents_cli.OK


@pytest.mark.parametrize("at", ["dirty", "edition", "record", "index"])
def test_shelf_publication_uncertain_is_explicit_and_retry_reconciles(tmp_path, monkeypatch, at):
    from core.memory_shelf import codes
    from .helpers import cc_bundle, cc_lines, make_shelf

    shelf = make_shelf(tmp_path)
    bundle = cc_bundle(cc_lines("uncertain-session", ("human", "Keep the whole source")))
    original = fsio._sync_publication
    failed = False

    def sync(path):
        nonlocal failed
        matches = {"dirty": path == shelf._dirty,
                   "edition": path.parent.name == "editions",
                   "record": path.suffix == ".md" and path.parent.name != "editions",
                   "index": path == shelf.index_path}
        if not failed and matches[at]:
            failed = True
            raise fsio.PublicationUncertain(errno.EIO, "publication_uncertain")
        return original(path)

    monkeypatch.setattr(fsio, "_sync_publication", sync)
    result = shelf.ingest(bundle)
    assert failed and result.outcome == codes.FAILED and result.reason == "publication_uncertain"
    assert any(row.get("reason") == "publication_uncertain"
               for row in fsio.read_jsonl(shelf.ledger_dir / "ledger.jsonl"))
    monkeypatch.undo()
    assert shelf.ingest(bundle).outcome in {codes.CAPTURED, codes.UNCHANGED}
    records = shelf.list_records()
    assert len(records) == 1
    main = shelf.main_path(records[0])
    assert "Keep the whole source" in main.read_text()
    assert len(list((main.parent / "editions").iterdir())) == 1
