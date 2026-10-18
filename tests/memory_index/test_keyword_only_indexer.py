"""Production's own keyword index: the keyword-only schema variant and indexer mode (17 Oct 2026).

Production has no embedder and no sqlite-vec. It keeps its own index: the same chunker and policy, FTS only, built once from the
shelf and then updated for the records each sync batch changes. Synthetic records only.
"""
import sqlite3

import pytest

from core.memory_index import health, indexer, policy, search, store
from .helpers import FakeEmbedder, add_record, designate, make_shelf, world

RAW = policy.Caller(tier="all")


@pytest.fixture
def no_vec(monkeypatch):
    """A production host: the vector extension is not installed."""
    for mod in (store, indexer, search):
        monkeypatch.setattr(mod, "sqlite_vec", None)


class NeverCalled(FakeEmbedder):
    def identity(self):
        raise AssertionError("keyword-only mode must never ask an embedder")


def tables(path):
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def chunk_rows(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute("SELECT chunk_id, record_id, edition_hash, ordinal, turn, speaker, rel, start_line, end_line, text_hash,"
                           " tier, scope, canary, created, truncated FROM chunks ORDER BY chunk_id").fetchall()
    finally:
        con.close()


def record_rows(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute("SELECT record_id, edition_hash, state, reason, chunks FROM records ORDER BY record_id").fetchall()
    finally:
        con.close()


def test_keyword_only_build_needs_no_vector_extension_and_no_embedder(tmp_path, no_vec):
    shelf, ids = world(tmp_path)
    path = tmp_path / "kw.sqlite"
    out = indexer.update(shelf.root, None, index_path=path, min_free_bytes=0, keyword_only=True)
    assert out["code"] == indexer.OK and out["indexed"] >= 3
    assert "vec" not in {t.split("_")[0] for t in tables(path)}
    con = store.connect(path, readonly=True)
    try:
        assert store.is_keyword_only(con) and store.get_meta(con)["embedder"] == "none"
        assert "embedder_digest" not in store.get_meta(con)
        fts_rows = con.execute("SELECT count(*) FROM fts_docsize").fetchone()[0]
        assert fts_rows == con.execute("SELECT count(*) FROM chunks").fetchone()[0] > 0
    finally:
        con.close()
    r = search.search("sourdough", shelf_root=shelf.root, embedder=NeverCalled(), index_path=path, caller=RAW, k=3)
    assert [h["record_id"] for h in r["keyword"]][:1] == [ids["bread"]]
    assert r["meaning"] == [] and r["status"]["meaning"] == search.VECTOR_UNAVAILABLE


def test_the_same_chunker_gives_the_same_chunks_and_holds_as_the_vector_index(tmp_path, no_vec, monkeypatch):
    shelf, _ = world(tmp_path)
    kw = tmp_path / "kw.sqlite"
    indexer.update(shelf.root, None, index_path=kw, min_free_bytes=0, keyword_only=True)
    monkeypatch.undo()                                        # the Mac: sqlite-vec present, an embedder
    pytest.importorskip("sqlite_vec")
    vec = tmp_path / "vec.sqlite"
    assert indexer.update(shelf.root, FakeEmbedder(), index_path=vec, min_free_bytes=0)["code"] == indexer.OK
    assert chunk_rows(kw) == chunk_rows(vec)
    assert record_rows(kw) == record_rows(vec)


def test_an_update_for_changed_records_replaces_their_keyword_rows(tmp_path, no_vec):
    shelf, ids = world(tmp_path)
    path = tmp_path / "kw.sqlite"
    indexer.update(shelf.root, None, index_path=path, min_free_bytes=0, keyword_only=True)
    add_record(shelf, "grok", "g-bread", [("human", "How do I make my sourdough rise better?"),
                                          ("assistant", "Use a banneton and a dutch oven for the bake.")])
    out = indexer.update(shelf.root, None, index_path=path, min_free_bytes=0, keyword_only=True, only=[ids["bread"]])
    assert out["code"] == indexer.OK and out.get("reindexed") == 1
    hit = lambda q: [h["record_id"] for h in search.search(q, shelf_root=shelf.root, embedder=NeverCalled(), index_path=path,
                                                           caller=RAW, k=3)["keyword"]]
    assert ids["bread"] in hit("banneton")
    assert ids["bread"] not in hit("starter")                 # the old edition's text is gone from the keyword index
    con = sqlite3.connect(path)
    try:
        assert con.execute("SELECT count(*) FROM fts_docsize").fetchone()[0] == con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    finally:
        con.close()
    designate(shelf, ids["sky"])                               # a metadata-only change (no new edition) is re-read too
    assert indexer.update(shelf.root, None, index_path=path, min_free_bytes=0, keyword_only=True, only=[ids["sky"]])["reindexed"] == 1


def test_keyword_only_and_vector_indexes_are_never_written_as_each_other(tmp_path, no_vec, monkeypatch):
    shelf, _ = world(tmp_path)
    kw = tmp_path / "kw.sqlite"
    indexer.update(shelf.root, None, index_path=kw, min_free_bytes=0, keyword_only=True)
    monkeypatch.undo()
    pytest.importorskip("sqlite_vec")
    assert indexer.update(shelf.root, FakeEmbedder(), index_path=kw, min_free_bytes=0)["code"] == indexer.INCOMPATIBLE
    vec = tmp_path / "vec.sqlite"
    indexer.update(shelf.root, FakeEmbedder(), index_path=vec, min_free_bytes=0)
    assert indexer.update(shelf.root, None, index_path=vec, min_free_bytes=0, keyword_only=True)["code"] == indexer.INCOMPATIBLE


def test_keyword_only_rebuild_swaps_in_a_complete_index_and_health_reads_it(tmp_path, no_vec):
    shelf, ids = world(tmp_path)
    path = tmp_path / "kw.sqlite"
    out = indexer.rebuild(shelf.root, None, index_path=path, min_free_bytes=0, keyword_only=True)
    assert out["code"] == indexer.OK and out["swapped"] is True
    con = store.connect(path, readonly=True)
    try:
        assert store.is_keyword_only(con)
        assert "chunks_without_vector" not in health.orphans(con, shelf.root, {rid: rel for rid, rel, _ in indexer.shelf_entries(shelf.root)})
    finally:
        con.close()
    assert {r[0]: r[2] for r in record_rows(path)}[ids["held"]] == "held"
