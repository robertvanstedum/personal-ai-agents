"""A big record resumes instead of restarting (#660, the production seed of 26-28 Nov): its new text is embedded batch by batch, each batch
is saved before the next, and the time budget is checked between batches. A run stopped inside the record keeps its batches; the next run
embeds only what is left. Synthetic records and the deterministic FakeEmbedder; the clock is driven by the embedder."""
import sqlite3

from core.memory_index import embedder as E
from core.memory_index import indexer, store
from .helpers import FakeEmbedder, add_record, make_shelf

WORDS = ("amber basil cedar dune ember fjord garnet harbor iris juniper kelp lagoon meadow nectar orchid pebble quartz "
         "raven saffron thistle umber violet willow yarrow zephyr").split()


def big_record(shelf, turns: int = 40) -> str:
    """One conversation whose every turn has distinct text, so every chunk needs its own vector."""
    return add_record(shelf, "grok", "g-big", [("human" if i % 2 == 0 else "assistant",
                                                f"Turn {i}: {WORDS[i % len(WORDS)]} and {WORDS[(i * 7) % len(WORDS)]}, note {i * 13}.")
                                               for i in range(turns)])


class SlowEmbedder(FakeEmbedder):
    """Each request moves the shared clock on by ``cost`` seconds (a CPU embedder on long texts)."""

    def __init__(self, clock: list, cost: float, batch: int = 4):
        super().__init__()
        self.clock, self.cost, self.batch = clock, cost, batch

    def _embed(self, texts):
        self.clock[0] += self.cost
        return super()._embed(texts)


def rows(index, sql):
    con = store.connect(index, readonly=True)               # with the vector extension, so ``vec`` can be read
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def vectors_by_text(index):
    return dict(rows(index, "SELECT c.text_hash, v.embedding FROM chunks c JOIN vec v ON v.id = c.id"))


def test_a_big_record_stopped_by_the_budget_resumes_from_its_saved_batches(tmp_path, monkeypatch):
    shelf = make_shelf(tmp_path)
    rid = big_record(shelf)
    index = tmp_path / "seed.sqlite"
    clock = [0.0]
    monkeypatch.setattr(indexer.time, "monotonic", lambda: clock[0])

    first_emb = SlowEmbedder(clock, cost=100.0, batch=4)
    first = indexer.update(shelf.root, first_emb, index_path=index, min_free_bytes=None, budget_s=250)
    # Batches start at t=0, 100 and 200 (each under the 250 s budget); the fourth would start at 300: the run stops there, inside the
    # record, having overrun the budget by less than one batch.
    assert first["code"] == indexer.BUDGET_SPENT
    assert first_emb.calls == 3 and first["embedded_chunks"] == 12
    assert rows(index, "SELECT COUNT(*) FROM chunks")[0][0] == 0                     # the record itself is not written yet...
    assert rows(index, "SELECT COUNT(*), COUNT(DISTINCT record_id) FROM pending_vectors")[0] == (12, 1)   # ...its 3 batches are kept
    assert rows(index, "SELECT record_id FROM pending_vectors LIMIT 1")[0][0] == rid

    second_emb = SlowEmbedder(clock, cost=1.0, batch=4)
    second = indexer.update(shelf.root, second_emb, index_path=index, min_free_bytes=None, budget_s=3600)
    total = rows(index, "SELECT COUNT(*) FROM chunks")[0][0]
    assert second["code"] == indexer.OK and second["indexed"] == 1 and total > 12
    assert second_emb.texts == total - 12                    # nothing embedded twice: only what the first run had not reached
    assert second["embedded_chunks"] == total - 12 and second["reused_vectors"] == 12
    assert rows(index, "SELECT COUNT(*) FROM pending_vectors")[0][0] == 0           # written: its pending rows are gone

    # The resumed index holds exactly the vectors a single uninterrupted run makes.
    monkeypatch.undo()
    whole = tmp_path / "whole.sqlite"
    assert indexer.update(shelf.root, FakeEmbedder(), index_path=whole, min_free_bytes=None)["code"] == indexer.OK
    assert vectors_by_text(index) == vectors_by_text(whole)


def test_a_big_record_finishes_over_several_slices(tmp_path, monkeypatch):
    """A record far bigger than one slice of embedding still completes, a few batches per slice, each slice ending near its budget."""
    shelf = make_shelf(tmp_path)
    big_record(shelf, turns=60)
    index = tmp_path / "seed.sqlite"
    clock = [0.0]
    monkeypatch.setattr(indexer.time, "monotonic", lambda: clock[0])
    sent, codes = 0, []
    for _ in range(20):
        emb = SlowEmbedder(clock, cost=100.0, batch=4)
        out = indexer.update(shelf.root, emb, index_path=index, min_free_bytes=None, budget_s=150)
        sent += emb.texts
        codes.append(out["code"])
        assert emb.calls <= 2                                # each slice: at most the batches that start inside its budget
        if out["code"] == indexer.OK:
            break
    total = rows(index, "SELECT COUNT(*) FROM chunks")[0][0]
    assert codes[-1] == indexer.OK and set(codes[:-1]) == {indexer.BUDGET_SPENT} and len(codes) > 2
    assert sent == total                                     # every chunk embedded exactly once across all the slices


def test_an_embedder_failure_inside_a_record_keeps_the_batches_already_made(tmp_path):
    shelf = make_shelf(tmp_path)
    big_record(shelf, turns=20)
    index = tmp_path / "i.sqlite"

    class FailsLater(FakeEmbedder):
        batch = 4

        def _embed(self, texts):
            if self.calls == 2:
                raise E.EmbedderUnavailable("down")
            return super()._embed(texts)
    out = indexer.update(shelf.root, FailsLater(), index_path=index, min_free_bytes=None)
    assert out["code"] == indexer.EMBEDDER_UNAVAILABLE
    assert rows(index, "SELECT COUNT(*) FROM pending_vectors")[0][0] == 8
    emb = FakeEmbedder()
    emb.batch = 4
    assert indexer.update(shelf.root, emb, index_path=index, min_free_bytes=None)["code"] == indexer.OK
    assert emb.texts == rows(index, "SELECT COUNT(*) FROM chunks")[0][0] - 8


def test_an_index_made_before_the_resume_table_gains_it_on_its_next_write(tmp_path):
    shelf = make_shelf(tmp_path)
    big_record(shelf, turns=6)
    index = tmp_path / "old.sqlite"
    assert indexer.update(shelf.root, FakeEmbedder(), index_path=index, min_free_bytes=None)["code"] == indexer.OK
    con = sqlite3.connect(index)
    con.execute("DROP TABLE pending_vectors")
    con.commit()
    con.close()
    add_record(shelf, "grok", "g-new", [("human", "A brand new question about tide pools."), ("assistant", "Look at low tide.")])
    out = indexer.update(shelf.root, FakeEmbedder(), index_path=index, min_free_bytes=None)
    assert out["code"] == indexer.OK and out["indexed"] == 1
    assert rows(index, "SELECT COUNT(*) FROM pending_vectors")[0][0] == 0


def test_the_request_timeout_scales_with_the_batch():
    assert E.request_timeout(1) == 120 and E.request_timeout(6) == 120
    assert E.request_timeout(E.SERVER_BATCH) == 160 and E.request_timeout(E.BATCH) == 320
    server = E.OllamaEmbedder("http://vault-embedder:11434", batch=E.SERVER_BATCH)
    assert server.batch == 8 and server.timeout == 160
    assert E.OllamaEmbedder().batch == 16 and E.OllamaEmbedder(timeout=5).timeout == 5


def test_documents_are_sent_in_the_embedders_own_batch_size():
    emb = FakeEmbedder()
    emb.batch = E.SERVER_BATCH
    emb.embed_documents([f"text {i}" for i in range(20)], 4000)
    assert emb.calls == 3                                    # 8 + 8 + 4
