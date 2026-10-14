"""The keyword query ignores words that carry no topic (real-data finding, 4 Oct 2026: Robert's conversational questions
were ORed word by word, so short chunks of "I don't know ..." outranked the topic). Synthetic records only."""
import pytest

from core.memory_index import indexer, policy, search, store
from .helpers import FakeEmbedder, add_record, make_shelf

RAW = policy.Caller(tier="all")


@pytest.mark.parametrize("text,expected", [
    ("I'd like to know the found and fixed issues during the build", ['"found"', '"fixed"', '"issues"', '"build"']),
    ("Grok review adopted", ['"grok"', '"review"', '"adopted"']),
    ("what do I know about it", ['"what"', '"do"', '"i"', '"know"', '"about"', '"it"']),   # nothing but filler: searched as written
    ("wie ist das mit dem Gespräch", ['"gespräch"']),
    ("AI and CI", ['"ai"', '"ci"']),                                                         # short words that mean something stay
    ("", None),
])
def test_filler_words_are_dropped_unless_nothing_else_is_left(text, expected):
    got = search.fts_query(text)
    assert (got.split(" OR ") if got else None) == expected


def test_a_word_in_most_chunks_is_dropped_when_other_words_remain(tmp_path):
    shelf = make_shelf(tmp_path)
    for i in range(12):                                                  # "widget" is in every chunk: it names no topic here
        add_record(shelf, "grok", f"w{i}", [("human", f"widget question number {i}"), ("assistant", f"widget answer {i}")])
    add_record(shelf, "grok", "z", [("human", "widget zebra stripes"), ("assistant", "stripes are camouflage")])
    indexer.update(shelf.root, FakeEmbedder(), min_free_bytes=0)
    con = store.connect(indexer.default_index_path(shelf.root), readonly=True)
    assert search.fts_query("widget zebra", con) == '"zebra"'
    assert search.fts_query("widget", con) == '"widget"'                 # the only term: kept
    assert search.fts_query("widget unknownterm", con) == '"unknownterm"'
    con.close()


def test_a_conversational_question_finds_the_topic_not_the_filler(tmp_path):
    shelf = make_shelf(tmp_path)
    filler = add_record(shelf, "grok", "filler", [("human", "I don't know why we don't do both, I'd like to see it")])
    topic = add_record(shelf, "grok", "topic", [("assistant", "The integration test failed because the gateway restarted "
                                                              "while its key database was down.")])
    indexer.update(shelf.root, FakeEmbedder(), min_free_bytes=0)
    r = search.search("I don't know what I'd like to see failed in the integration test", shelf_root=shelf.root,
                      embedder=FakeEmbedder(), caller=RAW, k=2)
    assert [h["record_id"] for h in r["keyword"]][:1] == [topic] and filler not in [h["record_id"] for h in r["keyword"]][:1]
