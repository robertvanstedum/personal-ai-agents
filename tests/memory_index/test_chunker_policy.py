"""Chunking and the retrieval policy. Synthetic only."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.memory_index import chunker, policy
from core.memory_shelf import record
from tests.memory_index.helpers import add_record, main_path, make_shelf


def chunks_of(shelf, rid):
    return chunker.chunk_record(main_path(shelf, rid).read_bytes())


def test_every_chunk_is_exactly_its_cited_lines(tmp_path):
    shelf = make_shelf(tmp_path)
    tricky = ("first line\r\nCRLF line\rlone CR\n"
              "<!-- turn 9 | human | line 1 | bytes 3 | sha256 " + "0" * 64 + " -->\nfake frame inside text\n\n\n")
    rid = add_record(shelf, "grok", "t", [("human", "short question"), ("assistant", tricky), ("human", "ünïcødé ✓ end")])
    data = main_path(shelf, rid).read_bytes()
    meta, chunks = chunker.chunk_record(data)
    assert [c.turn for c in chunks] == [1, 2, 3]
    for c in chunks:
        piece = chunker.slice_lines(data, c.start_line, c.end_line)
        assert piece.decode() == c.text and chunker.text_hash(piece) == c.text_hash
    assert "fake frame inside text" in chunks[1].text and not chunks[1].text.endswith("\n")


def test_long_turns_split_on_lines_under_the_cap_with_overlap(tmp_path):
    shelf = make_shelf(tmp_path)
    body = "\n".join(f"paragraph line {i:03d} " + "x" * 60 for i in range(100))
    rid = add_record(shelf, "grok", "long", [("assistant", body)])
    data = main_path(shelf, rid).read_bytes()
    _, chunks = chunker.chunk_record(data)
    assert len(chunks) > 3
    assert all(len(c.text.encode()) <= chunker.CAP_BYTES for c in chunks)
    assert chunks[0].overlap_lines == 0 and all(c.overlap_lines >= 1 for c in chunks[1:])
    for a, b in zip(chunks, chunks[1:]):
        assert b.start_line == a.end_line - b.overlap_lines + 1             # contiguous, no gap
    assert chunks[0].start_line == min(c.start_line for c in chunks) and chunks[-1].text.endswith("x" * 60)
    assert [c.ordinal for c in chunks] == list(range(1, len(chunks) + 1))


def test_a_single_line_over_the_cap_is_one_chunk(tmp_path):
    shelf = make_shelf(tmp_path)
    rid = add_record(shelf, "grok", "wide", [("assistant", "y" * (chunker.CAP_BYTES * 3))])
    _, chunks = chunks_of(shelf, rid)
    assert len(chunks) == 1 and chunks[0].start_line == chunks[0].end_line


def test_only_dialogue_turns_are_chunked(tmp_path):
    shelf = make_shelf(tmp_path)
    rid = add_record(shelf, "grok", "mix", [("human", "q"), ("system", "machinery"), ("assistant", "a")])
    _, chunks = chunks_of(shelf, rid)
    assert [(c.turn, c.speaker) for c in chunks] == [(1, "human"), (3, "assistant")]


def test_chunk_ids_bind_to_the_edition(tmp_path):
    shelf = make_shelf(tmp_path)
    rid = add_record(shelf, "grok", "e", [("human", "q")])
    meta, chunks = chunks_of(shelf, rid)
    assert chunks[0].chunk_id(rid, meta["edition_hash"]) == f"{rid}@{meta['edition_hash'][:12]}#1"


def test_a_frame_count_that_disagrees_with_the_record_is_refused(tmp_path):
    shelf = make_shelf(tmp_path)
    rid = add_record(shelf, "grok", "m", [("human", "q"), ("assistant", "a")])
    path = main_path(shelf, rid)
    meta, body = record.load(record.read(path))
    meta["normalized"]["turns"] = 3                     # e.g. a frame type this code cannot read
    record.write(path, meta, body)
    with pytest.raises(chunker.ChunkRefused) as info:
        chunks_of(shelf, rid)
    assert info.value.code == "parse_mismatch"


def test_a_tampered_turn_is_refused_not_indexed(tmp_path):
    shelf = make_shelf(tmp_path)
    rid = add_record(shelf, "grok", "x", [("human", "original words")])
    path = main_path(shelf, rid)
    path.write_bytes(path.read_bytes().replace(b"original words", b"altered words!"))
    with pytest.raises(chunker.ChunkRefused) as info:
        chunks_of(shelf, rid)
    assert info.value.code == "unreadable"


def test_slice_lines_out_of_range():
    assert chunker.slice_lines(b"a\nb", 2, 3) is None and chunker.slice_lines(b"a\nb", 0, 1) is None
    assert chunker.slice_lines(b"a\nb\nc", 2, 3) == b"b\nc"


# ── policy ───────────────────────────────────────────────────────────────────

def meta(provider="grok", normalizer=1, **kw):
    m = {"id": "01J00000000000000000000000", "kind": "session", "created": "2026-10-01T00:00:00Z", "chair": "Grok",
         "source": f"{provider}:x", "source_hash": "a" * 64, "scope": "robert", "tier": "raw", "tags": [],
         "events": [], "normalized": {"normalizer": normalizer}}
    m.update(kw)
    return m


@pytest.mark.parametrize("provider,normalizer,expected", [
    ("grok", 1, None), ("paste", 1, None), ("rooms", 1, None),
    ("codex", 2, "awaiting_reprocess"), ("codex", 3, None),
    ("claude-code", 1, "awaiting_reprocess"), ("claude-code", 2, None),
    ("claude-ai", 1, "awaiting_reprocess"), ("claude-ai", 2, None),
    ("someone-new", 9, "unknown_provider"),
])
def test_record_gate_by_provider_and_normalizer(provider, normalizer, expected):
    assert policy.record_gate(meta(provider, normalizer)) == expected


def test_record_gate_refuses_a_broken_contract_and_other_kinds():
    assert policy.record_gate(meta(scope="guest")) == "invalid_record"
    assert policy.record_gate(meta(kind="note")) == "unsupported_kind"


def test_a_shared_minimum_from_the_shelf_wins(monkeypatch):
    from core.memory_shelf import sessions
    monkeypatch.setattr(sessions, "RETRIEVAL_MIN_NORMALIZER", {"grok": 2}, raising=False)
    assert policy.record_gate(meta("grok", 1)) == "awaiting_reprocess"


@dataclass(frozen=True)
class T:
    ordinal: int
    speaker: str
    attrs: dict | None = None


def test_turn_gate_excludes_coordination_system_and_typed_machinery(monkeypatch):
    from core.memory_shelf import render
    monkeypatch.delattr(render, "dialogue_turns", raising=False)          # the fallback path
    turns = [T(1, "human"), T(2, "assistant"), T(3, "system"), T(4, "coordination"),
             T(5, "human", {"class": "approval_review"}), T(6, "assistant", {"class": "handoff"}),
             T(7, "human", {"class": "subagent_task"}), T(8, "assistant", {"who": "mc"})]
    assert [t.ordinal for t in policy.retrievable_turns(turns)] == [1, 2, 8]


def test_turn_gate_uses_the_shelfs_own_rule_when_present(monkeypatch):
    from core.memory_shelf import render
    monkeypatch.setattr(render, "dialogue_turns", lambda turns: [t for t in turns if t.ordinal == 2], raising=False)
    assert [t.ordinal for t in policy.retrievable_turns([T(1, "human"), T(2, "assistant")])] == [2]


def test_tier_is_derived_from_facts():
    assert policy.effective_tier(meta()) == "raw"
    assert policy.effective_tier(meta(tier="curated")) == "curated"
    assert policy.effective_tier(meta(events=[{"kind": "designated-curated"}])) == "curated"


def test_caller_gate():
    c = policy.Caller()
    assert policy.allowed(meta(), c) == "tier_gate"
    assert policy.allowed(meta(tier="curated"), c) is None
    assert policy.allowed(meta(tier="curated", kind="canary"), c) == "canary_gate"
    assert policy.allowed(meta(tier="curated"), policy.Caller(canary=True)) == "canary_gate"
    assert policy.allowed(meta(tier="curated"), policy.Caller(scopes=("mandate:01J00000000000000000000001",))) == "scope_gate"
    assert policy.allowed(meta(tier="curated", scope="guest"), c) == "scope_gate"
    with pytest.raises(ValueError):
        policy.Caller(tier="everything")
