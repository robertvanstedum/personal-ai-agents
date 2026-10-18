"""A dated question with no topic ("What were we doing in May?") gets that period's conversations, not a keyword search
for the word "may" (Codex review, 17 Oct 2026). Synthetic records only."""
from core.memory_index import indexer, policy, search
from .helpers import FakeEmbedder, add_record, make_shelf

RAW = policy.Caller(tier="all")


def build(tmp_path):
    shelf = make_shelf(tmp_path)
    ids = {
        "grok-may": add_record(shelf, "grok", "g1", [("human", "Help me plan the telescope mount"), ("assistant", "Start with the tripod")],
                               created="2026-05-03T10:00:00Z"),
        "codex-may": add_record(shelf, "codex", "c1", [("human", "Refactor the invoice exporter"), ("assistant", "Done")],
                                created="2026-05-20T10:00:00Z", normalizer=3),
        "claude-may": add_record(shelf, "claude-ai", "a1", [("human", "Sourdough timing question"), ("assistant", "Overnight rise")],
                                 created="2026-05-31T23:00:00Z", normalizer=2),                          # last day of the month is inside
        "april": add_record(shelf, "grok", "g0", [("human", "Garden seedlings"), ("assistant", "Water them")],
                            created="2026-04-28T10:00:00Z"),
        "june": add_record(shelf, "codex", "c2", [("human", "Billing dashboard"), ("assistant", "Shipped")],
                           created="2026-06-01T00:00:00Z", normalizer=3),
    }
    indexer.update(shelf.root, FakeEmbedder(), min_free_bytes=0)
    return shelf, ids


def ask(shelf, query, k=5):
    return search.search(query, shelf_root=shelf.root, embedder=FakeEmbedder(), caller=RAW, k=k,
                         created_after="2026-05-01", created_before="2026-06-01")


def test_what_were_we_doing_in_may_returns_mays_conversations_though_none_says_may(tmp_path):
    shelf, ids = build(tmp_path)
    r = ask(shelf, "What were we doing in May?")
    got = {h["record_id"] for h in r["keyword"]}
    assert got == {ids["grok-may"], ids["codex-may"], ids["claude-may"]}
    assert r["overview"] == {"records_in_window": 3, "by_provider": {"claude-ai": 1, "codex": 1, "grok": 1},
                             "continued_from_before": 0, "dated_by_start_only": 3}
    assert r["status"]["meaning"] in (search.OVERVIEW, search.VECTOR_UNAVAILABLE, "embedder_unavailable")


def test_the_overview_spreads_across_sources_before_repeating_one(tmp_path):
    shelf, ids = build(tmp_path)
    for i in range(4):
        add_record(shelf, "grok", f"gx{i}", [("human", f"Another grok thread {i}")], created="2026-05-10T10:00:00Z")
    indexer.update(shelf.root, FakeEmbedder(), min_free_bytes=0)
    providers = [h["record_id"] for h in ask(shelf, "what did we work on in May", k=3)["keyword"]]
    assert ids["codex-may"] in providers and ids["claude-may"] in providers     # one per source first, not three groks


def test_a_topic_question_in_a_window_is_still_a_keyword_search(tmp_path):
    shelf, ids = build(tmp_path)
    r = ask(shelf, "what about the invoice exporter in May")
    assert "overview" not in r and [h["record_id"] for h in r["keyword"]][:1] == [ids["codex-may"]]


def test_a_record_without_turn_times_is_dated_by_its_start_alone(tmp_path):
    # These synthetic records carry no per-turn times, so April's stays in April (turn-dated: test_turn_dates.py).
    shelf, ids = build(tmp_path)
    assert ids["april"] not in {h["record_id"] for h in ask(shelf, "What were we doing in May?")["keyword"]}


def test_topic_terms_drop_time_words_and_years():
    assert search.topic_terms("What were we doing in May 2026?") == []
    assert "mai" not in search.topic_terms("Was haben wir im Mai gemacht")
    assert search.topic_terms("the invoice exporter in May") == ["invoice", "exporter"]


def test_the_overview_counts_respect_the_callers_scopes(tmp_path):
    # Codex r2 review: a restricted caller got 0 passages but the counts of 3 records it may not read.
    shelf, ids = build(tmp_path)
    for scopes, expect in ((("mandate:other",), 0), ((), 0)):
        r = search.search("What were we doing in May?", shelf_root=shelf.root, embedder=FakeEmbedder(),
                          caller=policy.Caller(tier="all", scopes=scopes), k=5,
                          created_after="2026-05-01", created_before="2026-06-01")
        assert r["keyword"] == [] and r["overview"] == {"records_in_window": expect, "by_provider": {},
                                                         "continued_from_before": 0, "dated_by_start_only": 0}
    allowed = search.search("What were we doing in May?", shelf_root=shelf.root, embedder=FakeEmbedder(),
                            caller=policy.Caller(tier="all", scopes=("robert",)), k=5,
                            created_after="2026-05-01", created_before="2026-06-01")
    assert allowed["overview"]["records_in_window"] == 3 and len(allowed["keyword"]) == 3
