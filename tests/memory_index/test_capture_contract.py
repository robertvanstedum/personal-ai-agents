"""Forward contract with the capture revision (typed coordination turns, classes). Runs only on a shelf that has it;
skipped on a shelf without ``render.dialogue_turns``. Synthetic only."""
from __future__ import annotations

import pytest

from core.memory_index import indexer, policy, search, store
from core.memory_shelf import render
from tests.memory_index.helpers import FakeEmbedder, add_record, make_shelf

pytestmark = pytest.mark.skipif(not hasattr(render, "dialogue_turns"), reason="needs the capture revision's typed turns")


def test_typed_machinery_never_reaches_the_index(tmp_path):
    shelf = make_shelf(tmp_path)
    rid = add_record(shelf, "codex", "c-new", [
        ("human", "owner question about yeast"),
        ("coordination", "handoff envelope telescope", {"class": "handoff", "to": "codex"}),
        ("human", "approval review prompt telescope", {"class": "approval_review"}),
        ("assistant", "structured decision telescope", {"class": "approval_review"}),
        ("assistant", "the answer about yeast")], normalizer=3)
    emb = FakeEmbedder()
    assert indexer.update(shelf.root, emb, min_free_bytes=0)["code"] == "ok"
    con = store.connect(indexer.default_index_path(shelf.root), readonly=True)
    turns = [r[0] for r in con.execute("SELECT turn FROM chunks WHERE record_id=? ORDER BY turn", (rid,))]
    con.close()
    assert turns == [1, 5]
    r = search.search("telescope", shelf_root=shelf.root, embedder=emb, caller=policy.Caller(tier="raw"))
    assert r["keyword"] == [] and all(h["turn"] in (1, 5) for h in r["meaning"])
    hit = search.search("yeast", shelf_root=shelf.root, embedder=emb, caller=policy.Caller(tier="raw"))["keyword"][0]
    assert hit["record_id"] == rid and hit["passage"] in ("owner question about yeast", "the answer about yeast")


def test_rooms_turns_with_speaker_ids_are_located_and_cited(tmp_path):
    shelf = make_shelf(tmp_path)
    rid = add_record(shelf, "rooms", "inst:s1", [
        ("human", "robert opens the meeting about tomatoes", {"who": "robert", "seq": "1"}),
        ("assistant", "mc replies about seedlings", {"who": "mc", "seq": "2"})], chair="Rooms")
    emb = FakeEmbedder()
    indexer.update(shelf.root, emb, min_free_bytes=0)
    r = search.search("tomatoes seedlings", shelf_root=shelf.root, embedder=emb, caller=policy.Caller(tier="raw"))
    assert {h["turn"] for h in r["keyword"]} == {1, 2} and r["unresolved"] == []
    assert all(h["record_id"] == rid for h in r["keyword"])
