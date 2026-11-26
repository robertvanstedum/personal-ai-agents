"""Two small indexer additions for #660: a time budget that stops between records (the meaning seed's slices) and a read-only
donor index that lends vectors (MC's index borrows the full index's). Synthetic records and the deterministic FakeEmbedder."""
import sqlite3


from core.memory_index import indexer
from .helpers import FakeEmbedder, add_record, world


def chunk_count(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0], {r[0] for r in con.execute("SELECT record_id FROM records")}
    finally:
        con.close()


def test_budget_stops_between_records_and_the_next_run_resumes(tmp_path, monkeypatch):
    shelf, ids = world(tmp_path)
    index = tmp_path / "seed.sqlite"
    ticks = iter(range(0, 10_000, 10))                     # each monotonic() call moves 10 s on
    real = indexer.time.monotonic
    monkeypatch.setattr(indexer.time, "monotonic", lambda: next(ticks))
    first = indexer.update(shelf.root, FakeEmbedder(), index_path=index, min_free_bytes=None, budget_s=15)
    assert first["code"] == indexer.BUDGET_SPENT
    _, done_first = chunk_count(index)
    assert 0 < len(done_first) < len(ids)                   # some records indexed, committed, not all
    monkeypatch.setattr(indexer.time, "monotonic", real)
    emb = FakeEmbedder()
    second = indexer.update(shelf.root, emb, index_path=index, min_free_bytes=None, budget_s=3600)
    assert second["code"] == indexer.OK
    assert second.get("unchanged", 0) == len(done_first)    # what the first slice did is not done again
    _, done = chunk_count(index)
    assert done == set(ids.values())


def test_no_budget_means_unchanged_behaviour(tmp_path):
    shelf, _ = world(tmp_path)
    assert indexer.update(shelf.root, FakeEmbedder(), index_path=tmp_path / "i.sqlite", min_free_bytes=None)["code"] == "ok"


def test_donor_lends_vectors_so_nothing_is_embedded_twice(tmp_path):
    shelf, ids = world(tmp_path)
    full = tmp_path / "full.sqlite"
    assert indexer.update(shelf.root, FakeEmbedder(), index_path=full, min_free_bytes=None)["code"] == "ok"
    emb = FakeEmbedder()
    out = indexer.update(shelf.root, emb, index_path=tmp_path / "borrower.sqlite", min_free_bytes=None, donor_path=full)
    assert out["code"] == "ok" and out.get("embedded_chunks", 0) == 0 and out["reused_vectors"] > 0
    assert emb.texts == 0                                     # FakeEmbedder.identity embeds nothing: no text was sent at all


def test_donor_and_own_vectors_merge(tmp_path):
    """A partly built index keeps its own vectors and borrows the rest (Codex review: ``or`` ignored the donor)."""
    shelf, ids = world(tmp_path)
    full = tmp_path / "full.sqlite"
    assert indexer.update(shelf.root, FakeEmbedder(), index_path=full, min_free_bytes=None)["code"] == "ok"
    borrower = tmp_path / "borrower.sqlite"
    assert indexer.update(shelf.root, FakeEmbedder(), index_path=borrower, min_free_bytes=None, only=[ids["bread"]])["code"] == "ok"
    rid = ids["sky"]
    add_record(shelf, "grok", "g-sky", [("human", "Which telescope should I buy for looking at planets?"),
                                        ("assistant", "A 6 inch Dobsonian shows Jupiter's moons and Saturn's rings."),
                                        ("human", "And a filter for the moon?")])
    emb = FakeEmbedder()
    out = indexer.update(shelf.root, emb, index_path=borrower, min_free_bytes=None, donor_path=full)
    assert out["code"] == "ok"
    assert out["embedded_chunks"] == 1                        # only the new turn's text; the rest came from the donor


def test_donor_with_another_embedder_lends_nothing(tmp_path):
    shelf, _ = world(tmp_path)
    full = tmp_path / "full.sqlite"
    other = "0a109f422b47" + "e" * 52
    assert indexer.update(shelf.root, FakeEmbedder(digest=other), index_path=full, min_free_bytes=None)["code"] == "ok"
    out = indexer.update(shelf.root, FakeEmbedder(), index_path=tmp_path / "b.sqlite", min_free_bytes=None, donor_path=full)
    assert out["code"] == "ok" and out["reused_vectors"] == 0 and out["embedded_chunks"] > 0


def test_a_keyword_donor_lends_nothing_and_is_harmless(tmp_path):
    shelf, _ = world(tmp_path)
    kw = tmp_path / "kw.sqlite"
    assert indexer.update(shelf.root, None, index_path=kw, min_free_bytes=None, keyword_only=True)["code"] == "ok"
    out = indexer.update(shelf.root, FakeEmbedder(), index_path=tmp_path / "b.sqlite", min_free_bytes=None, donor_path=kw)
    assert out["code"] == "ok" and out["reused_vectors"] == 0
