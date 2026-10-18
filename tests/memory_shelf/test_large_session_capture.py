"""Streaming capture of large session files (17 Oct 2026). Synthetic files only, generated on the fly in tmp_path.

The large fixtures are ~72 MiB (over the retired 64 MiB hold) and made mostly of a few huge tool-output lines, which is
what makes real sessions large. Peak Python allocation during a whole watcher pass is measured with tracemalloc and must
stay a small fraction of the file size.
"""
import io
import json
import os
import shutil
import tracemalloc
from pathlib import Path

import pytest

from core.memory_shelf import approvals, events, fidelity, fsio, ledger, record, render, watchers
from core.memory_shelf.config import Config, SourceCfg
from tests.memory_shelf.helpers import make_shelf

zstd = pytest.importorskip("compression.zstd")

SID = "33333333-4444-5555-6666-777777777777"
BIG_LINE = 2 << 20                    # 2 MiB of tool output per line
BIG_LINES = 36                        # ~72 MiB in all
OWNER = events.OwnerAuthority("moi-approve")


def row(doc) -> bytes:
    return (json.dumps(doc) + "\n").encode()


def write_cc(path: Path, sid: str = "cc-big", lines: int = BIG_LINES, size: int = BIG_LINE, tail: str = "final answer"):
    filler = "x" * size
    with open(path, "wb") as f:
        f.write(row({"type": "user", "sessionId": sid, "timestamp": "2026-10-16T10:00:00Z",
                     "message": {"role": "user", "content": "owner question"}}))
        for i in range(lines):
            f.write(row({"type": "assistant", "sessionId": sid, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash", "id": f"t{i}", "input": {}}]}}))
            f.write(row({"type": "user", "sessionId": sid, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": filler}]}}))
        f.write(row({"type": "assistant", "sessionId": sid, "message": {"role": "assistant",
                                                                        "content": [{"type": "text", "text": tail}]}}))


def write_codex(path: Path, sid: str = SID, lines: int = BIG_LINES, size: int = BIG_LINE):
    filler = "y" * size
    with open(path, "wb") as f:
        f.write(row({"type": "session_meta", "timestamp": "2026-10-16T11:00:00Z", "payload": {"id": sid}}))
        f.write(row({"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "UserMessage", "content": [{"type": "text", "text": "owner request"}]}}}))
        for i in range(lines):
            f.write(row({"type": "response_item", "payload": {"type": "function_call_output", "call_id": f"c{i}",
                                                              "output": filler}}))
        f.write(row({"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "AgentMessage", "content": [{"type": "text", "text": "agent answer"}]}}}))


def compress(src: Path, dst: Path):
    with open(src, "rb") as fin, zstd.open(dst, "wb") as fout:
        shutil.copyfileobj(fin, fout, 1 << 20)


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    base = tmp_path_factory.mktemp("big")
    write_cc(base / "cc.jsonl")
    write_codex(base / "codex.jsonl")
    assert (base / "cc.jsonl").stat().st_size > 64 << 20 and (base / "codex.jsonl").stat().st_size > 64 << 20
    return base


def approved(shelf, cfg):
    watchers.dry_run(shelf, cfg)
    approvals.approve_source(shelf, cfg.name, cfg.fingerprint(), OWNER)


def cc_source(tmp_path, big):
    root = tmp_path / "claude"
    (root / "proj").mkdir(parents=True)
    shutil.copyfile(big / "cc.jsonl", root / "proj" / "cc-big.jsonl")
    cfg = SourceCfg("claude-code", "claude-code", root)
    shelf = make_shelf(tmp_path)
    approved(shelf, cfg)
    return shelf, cfg, root / "proj" / "cc-big.jsonl"


def codex_source(tmp_path, big=None, never=()):
    root = tmp_path / "codex" / "2026" / "10" / "16"
    root.mkdir(parents=True)
    path = root / f"rollout-2026-10-16T11-00-00-{SID}.jsonl"
    if big is not None:
        shutil.copyfile(big / "codex.jsonl", path)
    else:
        write_codex(path, lines=2, size=64)
    cfg = SourceCfg("codex", "codex", tmp_path / "codex", tuple(never))
    shelf = make_shelf(tmp_path)
    approved(shelf, cfg)
    return shelf, cfg, path


def measured(fn):
    tracemalloc.start()
    try:
        result = fn()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, peak


def only_record(shelf):
    [entry] = shelf.list_records()
    meta, body = record.load(record.read(shelf.main_path(entry)))
    return entry, meta, body


def state_entry(shelf, name):
    files = fsio.read_json(watchers._state_path(shelf, name))["files"]
    [entry] = files.values()
    return entry


# ── the line reader ─────────────────────────────────────────────────────────

def test_stream_lines_keeps_line_numbers_and_drains_oversized_lines_in_pieces():
    data = b"ab\n" + b"z" * 50 + b"\ncd\n" + b"q" * 30
    got = list(watchers.stream_lines(io.BytesIO(data), 1 << 20, 10))
    assert got[0] == b"ab\n" and got[2] == b"cd\n" and len(got) == 4
    assert json.loads(got[1]) == {"type": watchers.OVERSIZED_LINE, "bytes": 51}
    assert json.loads(got[3]) == {"type": watchers.OVERSIZED_LINE, "bytes": 30}            # last line without newline
    assert list(watchers.stream_lines(io.BytesIO(b"0123456789"), 10, 10)) == [b"0123456789"]   # exact boundaries pass
    with pytest.raises(watchers.SourceTooLarge) as exc:
        list(watchers.stream_lines(io.BytesIO(data), len(data) - 1, 10))
    assert exc.value.code == watchers.SOURCE_OVER_CEILING


# ── large files are captured, with bounded memory ───────────────────────────

def test_large_claude_code_session_is_captured_with_bounded_memory(tmp_path, big):
    shelf, cfg, path = cc_source(tmp_path, big)
    result, peak = measured(lambda: watchers.run_source(shelf, cfg))
    assert result["status"] == "ok" and result["counts"] == {"captured": 1}
    _, meta, body = only_record(shelf)
    assert meta["normalized"]["turns"] == 2 and meta["normalized"]["turns_by_role"] == {"assistant": 1, "human": 1}
    assert meta["normalized"]["omitted"]["tool_result"] == BIG_LINES
    assert meta["source_hash"] == render.hash_file(path)[0]
    assert len(body) < 64 << 10                                     # the record keeps turns and pointers, not tool output
    assert peak < 24 << 20, peak                                    # a few line-sized buffers, never the 72 MiB file
    assert ledger.report(shelf)["missing"] == 0


@pytest.mark.parametrize("archived", [False, True])
def test_large_codex_session_plain_or_zst_is_captured_with_bounded_memory(tmp_path, big, archived):
    shelf, cfg, path = codex_source(tmp_path, big)
    plain_sha = render.hash_file(path)[0]
    if archived:
        compress(path, path.with_name(path.name + ".zst"))
        path.unlink()
    result, peak = measured(lambda: watchers.run_source(shelf, cfg))
    assert result["status"] == "ok" and result["counts"] == {"captured": 1}
    entry, meta, _ = only_record(shelf)
    assert meta["source"] == f"codex:{SID}" and meta["source_hash"] == plain_sha    # identity = the uncompressed session
    assert meta["normalized"]["turns"] == 2
    assert meta["normalized"]["omitted"]["response_item:function_call_output"] == BIG_LINES
    assert peak < 24 << 20, peak


def test_oversized_line_is_never_decoded_and_is_a_visible_omission(tmp_path, big, monkeypatch):
    shelf, cfg, path = cc_source(tmp_path, big)
    monkeypatch.setenv("MOI_CAPTURE_MAX_LINE_BYTES", str(1 << 20))      # every 2 MiB tool line is now "oversized"
    result, peak = measured(lambda: watchers.run_source(shelf, cfg))
    assert result["status"] == "ok" and result["counts"]["captured"] == 1 and result["counts"]["unknown_kind"] == 1
    _, meta, body = only_record(shelf)
    assert meta["normalized"]["turns"] == 2                            # the turns around them are all kept
    assert meta["normalized"]["omitted"]["line:moi:oversized_line"] == BIG_LINES
    assert "[omitted: line:moi:oversized_line, source line 3]" in body
    assert "unknown_kind" in meta["normalized"]["coverage"]["flags"]     # a Watch for the owner, never silent
    assert peak < 8 << 20, peak                                         # 1 MiB pieces: the 2 MiB lines are never held


# ── change detection ────────────────────────────────────────────────────────

def test_unchanged_large_file_is_not_recaptured_and_a_change_adds_an_edition(tmp_path, big):
    shelf, cfg, path = cc_source(tmp_path, big)
    assert watchers.run_source(shelf, cfg)["counts"] == {"captured": 1}
    assert watchers.run_source(shelf, cfg)["counts"] == {"skipped_unchanged": 1}
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))  # touched, not changed
    assert watchers.run_source(shelf, cfg)["counts"] == {"unchanged": 1}
    with open(path, "ab") as f:
        f.write(row({"type": "user", "sessionId": "cc-big", "message": {"role": "user", "content": "a later question"}}))
    assert watchers.run_source(shelf, cfg)["counts"] == {"edition-added": 1}
    entry, meta, _ = only_record(shelf)
    assert meta["edition"] == 2 and meta["normalized"]["turns"] == 3
    assert len(list((shelf.main_path(entry).parent / "editions").iterdir())) == 2


def test_compressing_a_captured_session_is_unchanged_and_keeps_its_key(tmp_path):
    shelf, cfg, path = codex_source(tmp_path)
    assert watchers.run_source(shelf, cfg)["counts"] == {"captured": 1}
    before, records = state_entry(shelf, "codex"), shelf.list_records()
    archive = path.with_name(path.name + ".zst")
    compress(path, archive)
    # in flight (both forms present): the live file is read, the archive waits
    assert [c.rel for c in watchers.discover(cfg)] == [path.relative_to(cfg.root).as_posix()]
    path.unlink()
    assert watchers.run_source(shelf, cfg)["counts"] == {"unchanged": 1}
    after = state_entry(shelf, "codex")                          # the same state/ledger key: one entry, not two
    assert after["sha256"] == before["sha256"] and after["rel"].endswith(".jsonl.zst")
    assert shelf.list_records() == records and ledger.report(shelf)["missing"] == 0
    # fidelity replay follows the session into its archive and sees the same bytes it captured
    _, meta, _ = only_record(shelf)
    got, reason = fidelity.load_source(Config(sources={"codex": cfg}), meta, expected_hash=meta["source_hash"])
    assert reason is None and got[1] == meta["source_hash"] and len(got[0].turns) == 2


def test_never_copy_on_the_session_name_also_excludes_its_archive(tmp_path):
    shelf, cfg, path = codex_source(tmp_path, never=(f"rollout-2026-10-16T11-00-00-{SID}.jsonl",))
    compress(path, path.with_name(path.name + ".zst"))
    path.unlink()
    assert watchers.build_listing(cfg)["counts"]["excluded"] == {"never_copy": 1}
    approved(shelf, cfg)
    assert watchers.run_source(shelf, cfg)["counts"] == {"excluded": 1} and shelf.list_records() == []


# ── ceilings and status ─────────────────────────────────────────────────────

def test_decompressed_stream_over_the_ceiling_is_held_with_its_own_reason(tmp_path):
    shelf, cfg, path = codex_source(tmp_path)
    archive = path.with_name(path.name + ".zst")
    with zstd.open(archive, "wb") as f:                     # tiny on disk, 8 MiB once decompressed
        f.write(path.read_bytes())
        for _ in range(8):
            f.write(row({"type": "compacted", "payload": {"text": "z" * (1 << 20)}}))
    path.unlink()
    assert archive.stat().st_size < 1 << 20
    result = watchers.run_source(shelf, cfg, max_source_bytes=4 << 20)
    assert result["status"] == "warn" and result["counts"] == {"failed": 1, "source_over_ceiling": 1}
    assert state_entry(shelf, "codex")["reason"] == "source_over_ceiling" and shelf.list_records() == []


def test_any_failure_makes_the_pass_warn_with_counts_and_success_does_not_move(tmp_path, big, monkeypatch):
    shelf, cfg, path = cc_source(tmp_path, big)
    status_file = Path(shelf.status_dir) / "watch-claude-code.json"
    monkeypatch.setenv("MOI_CAPTURE_MAX_NORMALIZED_BYTES", "8")       # the kept turn text alone is over this
    result = watchers.run_source(shelf, cfg)
    assert result["status"] == "warn" and result["counts"] == {"failed": 1, "normalized_too_large": 1}
    doc = fsio.read_json(status_file)
    assert doc["status"] == "warn" and doc["counts"]["failed"] == 1 and "last_success_at" not in doc
    assert shelf.list_records() == [] and ledger.report(shelf)["missing"] == 1
    monkeypatch.delenv("MOI_CAPTURE_MAX_NORMALIZED_BYTES")
    assert watchers.run_source(shelf, cfg)["status"] == "ok"           # retried, not stuck
    assert fsio.read_json(status_file)["last_success_at"] and ledger.report(shelf)["missing"] == 0


def test_a_corrupt_archive_is_a_failed_item_and_a_warn(tmp_path):
    shelf, cfg, path = codex_source(tmp_path)
    archive = path.with_name(path.name + ".zst")
    compress(path, archive)
    archive.write_bytes(archive.read_bytes()[:-4])              # truncated mid-frame
    path.unlink()
    result = watchers.run_source(shelf, cfg)
    assert result["status"] == "warn" and result["counts"] == {"failed": 1}
    assert next(iter(ledger.latest(shelf).values()))["reason"] == "parse_failed"


def test_invalid_line_limit_holds_fidelity_replay_with_a_fixed_reason(tmp_path, monkeypatch):
    shelf, cfg, path = codex_source(tmp_path)
    assert watchers.run_source(shelf, cfg)["counts"] == {"captured": 1}
    _, meta, _ = only_record(shelf)
    monkeypatch.setenv("MOI_CAPTURE_MAX_LINE_BYTES", "0")
    for legacy in (None, "1000000"):
        if legacy:
            monkeypatch.setenv("MOI_CAPTURE_MAX_SOURCE_BYTES", legacy)
        got, reason = fidelity.load_source(Config(sources={"codex": cfg}), meta)
        assert got is None and reason == fidelity.REPLAY_LIMIT_INVALID


def test_a_long_pass_checkpoints_its_state_between_files(tmp_path, monkeypatch):
    shelf, cfg, path = codex_source(tmp_path)
    other = path.with_name("rollout-2026-10-16T12-00-00-44444444-5555-6666-7777-888888888888.jsonl")
    write_codex(other, sid="44444444-5555-6666-7777-888888888888", lines=1, size=8)
    approved(shelf, cfg)
    monkeypatch.setattr(watchers, "STATE_CHECKPOINT_S", 0.0)
    real, seen = watchers._capture, []

    def spy(*args, **kw):
        doc = fsio.read_json(watchers._state_path(shelf, "codex"))
        seen.append(len((doc or {}).get("files", {})))
        return real(*args, **kw)
    monkeypatch.setattr(watchers, "_capture", spy)
    assert watchers.run_source(shelf, cfg)["counts"] == {"captured": 2}
    assert seen == [0, 1]                                   # the first file's outcome was on disk before the second began


# ── the retained-text budget is enforced while parsing (Codex review, 17 Oct) ──

def write_dialogue(kind: str, tmp_path: Path, turns: int, text_bytes: int = 96):
    """A session made only of small dialogue lines: every line is a kept turn. Returns (shelf, cfg, path)."""
    if kind == "claude-code":
        root = tmp_path / "claude"
        (root / "proj").mkdir(parents=True)
        path, cfg = root / "proj" / "cc-many.jsonl", SourceCfg("claude-code", "claude-code", root)
        with open(path, "wb") as f:
            for i in range(turns):
                role = "user" if i % 2 == 0 else "assistant"
                f.write(row({"type": role, "sessionId": "cc-many", "message": {
                    "role": role, "content": [{"type": "text", "text": f"turn {i:06d} " + "w" * text_bytes}]}}))
    else:
        root = tmp_path / "codex" / "2026" / "10" / "16"
        root.mkdir(parents=True)
        path, cfg = root / f"rollout-2026-10-16T11-00-00-{SID}.jsonl", SourceCfg("codex", "codex", tmp_path / "codex")
        with open(path, "wb") as f:
            f.write(row({"type": "session_meta", "timestamp": "2026-10-16T11:00:00Z", "payload": {"id": SID}}))
            for i in range(turns):
                item = "UserMessage" if i % 2 == 0 else "AgentMessage"
                f.write(row({"type": "event_msg", "payload": {"type": "item_completed", "item": {
                    "type": item, "content": [{"type": "text", "text": f"turn {i:06d} " + "w" * text_bytes}]}}}))
    shelf = make_shelf(tmp_path)
    approved(shelf, cfg)
    return shelf, cfg, path


@pytest.mark.parametrize("kind", ["claude-code", "codex"])
def test_retained_budget_stops_the_parse_as_soon_as_it_is_exceeded(tmp_path, monkeypatch, kind):
    shelf, cfg, _ = write_dialogue(kind, tmp_path, turns=5000)        # ~0.5 MiB of kept text; the budget is 4 KiB
    monkeypatch.setenv("MOI_CAPTURE_MAX_NORMALIZED_BYTES", str(4 << 10))
    real, consumed = watchers.stream_lines, []

    def counting(*args, **kw):                                        # counts the lines the parser actually pulled
        for raw in real(*args, **kw):
            consumed.append(1)
            yield raw
    monkeypatch.setattr(watchers, "stream_lines", counting)
    result = watchers.run_source(shelf, cfg)
    assert result["status"] == "warn" and result["counts"] == {"failed": 1, "normalized_too_large": 1}
    assert len(consumed) < 100, len(consumed)                         # ~40 turns fit: the other ~4960 lines are never read
    assert shelf.list_records() == [] and state_entry(shelf, cfg.name)["reason"] == "normalized_too_large"
    [last] = ledger.latest(shelf).values()
    assert last["outcome"] == "failed" and last["reason"] == "normalized_too_large"


def test_retained_budget_charges_a_codex_copy_once_and_keeps_the_turns_identical(tmp_path):
    from core.memory_shelf import sessions
    lines = [row({"type": "session_meta", "payload": {"id": SID}})]
    for i in range(20):                                               # each message twice: item and its response_item copy
        text = f"message {i:03d} " + "m" * 90
        lines.append(row({"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "UserMessage", "content": [{"type": "text", "text": text}]}}}))
        lines.append(row({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": text}]}}))
    lines.append(row({"type": "response_item", "payload": {"type": "agent_message", "author": "a", "recipient": "b",
                                                           "content": [{"type": "input_text", "text": "hand over"},
                                                                       {"type": "encrypted_content", "data": "e" * 4096}]}}))
    free = sessions.parse_codex(lines)
    kept = sum(len(t.text.encode()) for t in free.turns)
    with sessions.retained_budget(kept + 64):                        # held text is ~2x kept: a copy must not count twice
        bounded = sessions.parse_codex(lines)
    assert [(t.speaker, t.text, t.line, t.basis, t.attrs) for t in bounded.turns] == \
        [(t.speaker, t.text, t.line, t.basis, t.attrs) for t in free.turns]
    assert bounded.omitted == free.omitted and bounded.manifest == free.manifest
    with pytest.raises(sessions.RetainedTooLarge):
        with sessions.retained_budget(kept - 1):
            sessions.parse_codex(lines)


# ── a session with no turns because of a resource limit is a Watch, not a quiet exclusion ──

def test_entirely_oversized_dialogue_is_a_flagged_failure_not_a_no_turns_exclusion(tmp_path, monkeypatch):
    shelf, cfg, _ = write_dialogue("claude-code", tmp_path, turns=6, text_bytes=4096)
    monkeypatch.setenv("MOI_CAPTURE_MAX_LINE_BYTES", "1024")          # every dialogue line is now an oversized placeholder
    result = watchers.run_source(shelf, cfg)
    assert result["status"] == "warn"
    assert result["counts"] == {"failed": 1, "no_turns_oversized": 1, "unknown_kind": 1}
    assert shelf.list_records() == []
    entry = state_entry(shelf, cfg.name)
    assert entry["outcome"] == "failed" and entry["reason"] == "no_turns_oversized" and "unknown_kind" in entry["flags"]
    [last] = ledger.latest(shelf).values()
    assert last["outcome"] == "failed" and last["reason"] == "no_turns_oversized" and "unknown_kind" in last["flags"]
    [item] = (Path(shelf.status_dir) / "review").glob("coverage-flag--*.json")
    doc = fsio.read_json(item)
    assert doc["state"] == "open" and "unknown_kind" in doc["detail"]["flags"]
    assert "excluded" not in ledger.report(shelf)["sources"]["claude-code"] or \
        not ledger.report(shelf)["sources"]["claude-code"]["excluded"]
    monkeypatch.delenv("MOI_CAPTURE_MAX_LINE_BYTES")                  # the limit lifted: captured on the next pass
    assert watchers.run_source(shelf, cfg)["counts"] == {"captured": 1}
    assert fsio.read_json(item)["state"] != "open"


def test_fidelity_replay_keeps_the_retained_text_budget_and_holds_with_a_fixed_reason(tmp_path, monkeypatch):
    # Codex r2 review: a 4096-byte cap still retained 10000 bytes on replay. The replay now parses inside the budget.
    shelf, cfg, path = codex_source(tmp_path)
    assert watchers.run_source(shelf, cfg)["counts"] == {"captured": 1}
    _, meta, _ = only_record(shelf)
    got, reason = fidelity.load_source(Config(sources={"codex": cfg}), meta)
    assert reason is None and len(got[0].turns) == 2                     # within the default budget: replays whole
    monkeypatch.setenv("MOI_CAPTURE_MAX_NORMALIZED_BYTES", "16")
    got, reason = fidelity.load_source(Config(sources={"codex": cfg}), meta)
    assert got is None and reason == fidelity.NORMALIZED_TOO_LARGE
