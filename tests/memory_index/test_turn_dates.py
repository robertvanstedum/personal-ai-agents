"""Dated recall uses each turn's own time (Vault recall gap, 17 Oct 2026). Synthetic records only.

The fixture is a real parse: a Claude Code session begun on 28 April and continued on 3 and 4 May goes through the
normalizer, the shelf's writer and the indexer, exactly as the nightly capture does.
"""
import hashlib
import json
import sqlite3

from core.memory_ask import context
from core.memory_index import indexer, policy, search, store
from core.memory_shelf import bundle as bundles, sessions

from .helpers import FakeEmbedder, add_record, make_shelf

RAW = policy.Caller(tier="all")
MAY = {"created_after": "2026-05-01", "created_before": "2026-06-01"}


def cc(kind, text, stamp, sid):
    return json.dumps({"type": kind, "sessionId": sid, "timestamp": stamp,
                       "message": {"role": kind, "content": text if kind == "user" else [{"type": "text", "text": text}]}})


def capture(shelf, sid, rows):
    """Parse a Claude Code session file and file it, as the watcher does; returns the record id."""
    parsed = sessions.parse_claude_code([cc(*r, sid) for r in rows])
    raw = "\n".join(cc(*r, sid) for r in rows).encode()
    b = bundles.from_parsed(parsed, hashlib.sha256(raw).hexdigest(), len(raw), key=f"claude-code:{sid}", title=sid,
                            origin="watcher:claude-code", created=bundles.utc(parsed.started),
                            ledger_key=bundles.ledger_key_for("claude-code", sid))
    return shelf.ingest(b).record_id


def build(tmp_path):
    shelf = make_shelf(tmp_path)
    ids = {
        "april-into-may": capture(shelf, "s-apr", [
            ("user", "Plan the telescope mount for the garden", "2026-04-28T21:00:00Z"),
            ("assistant", "Start with the tripod", "2026-04-28T21:00:05Z"),
            ("user", "Back again: the counterweight wobbles", "2026-05-03T09:30:00Z"),
            ("assistant", "Tighten the clutch on the counterweight", "2026-05-04T08:00:00Z")]),
        "april-only": capture(shelf, "s-apr-only", [
            ("user", "Seedlings for the balcony", "2026-04-10T10:00:00Z"),
            ("assistant", "Tomatoes first", "2026-04-10T10:01:00Z")]),
        "may": capture(shelf, "s-may", [
            ("user", "Sourdough timing question", "2026-05-20T10:00:00Z"),
            ("assistant", "Overnight rise", "2026-05-20T10:00:30Z")]),
        "may-into-june": capture(shelf, "s-may-jun", [
            ("user", "Invoice exporter refactor", "2026-05-31T23:00:00Z"),
            ("assistant", "Shipped in June", "2026-06-02T10:00:00Z")]),
        # A record whose source gave no per-turn times: dated by its start alone, exactly as before.
        "untimed": add_record(shelf, "grok", "g-untimed", [("human", "Billing dashboard idea"), ("assistant", "Sketch it")],
                              created="2026-05-12T10:00:00Z"),
        # Turns in April and July, none in May: spans May, but nothing was said in May.
        "gap": capture(shelf, "s-gap", [
            ("user", "Balcony herbs", "2026-04-20T10:00:00Z"), ("assistant", "Basil", "2026-04-20T10:00:10Z"),
            ("user", "Herbs again", "2026-07-03T10:00:00Z"), ("assistant", "Mint", "2026-07-04T10:00:00Z")]),
        # Indexed, but no owner dialogue (an agent thread): nothing to show, so not a conversation of the period.
        "no-dialogue": add_record(shelf, "grok", "g-agent", [("system", "automatic task note")], created="2026-05-15T10:00:00Z"),
    }
    assert indexer.update(shelf.root, FakeEmbedder(), min_free_bytes=0)["code"] == indexer.OK
    return shelf, ids


def ask(shelf, query, caller=RAW, k=5):
    return search.search(query, shelf_root=shelf.root, embedder=FakeEmbedder(), caller=caller, k=k, **MAY)


def db(shelf):
    return sqlite3.connect(indexer.default_index_path(shelf.root))


def test_each_chunk_is_dated_by_its_turn_and_each_record_has_its_activity_span(tmp_path):
    shelf, ids = build(tmp_path)
    con = db(shelf)
    chunks = con.execute("SELECT turn, created FROM chunks WHERE record_id=? ORDER BY turn", (ids["april-into-may"],)).fetchall()
    assert chunks == [(1, "2026-04-28T21:00:00Z"), (2, "2026-04-28T21:00:05Z"), (3, "2026-05-03T09:30:00Z"),
                      (4, "2026-05-04T08:00:00Z")]                         # every chunk had the record's start date before
    span = dict((r[0], r[1:]) for r in con.execute(
        "SELECT record_id, created, first_activity, last_activity, timed_turns FROM records"))
    assert span[ids["april-into-may"]] == ("2026-04-28T21:00:00Z", "2026-04-28T21:00:00Z", "2026-05-04T08:00:00Z", 4)
    assert span[ids["untimed"]] == ("2026-05-12T10:00:00Z", "2026-05-12T10:00:00Z", "2026-05-12T10:00:00Z", 0)
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() == (str(store.SCHEMA_VERSION),) == ("2",)


def test_may_overview_counts_the_conversation_begun_in_april_and_shows_its_may_turn(tmp_path):
    shelf, ids = build(tmp_path)
    r = ask(shelf, "What were we doing in May?")
    got = {h["record_id"]: h for h in r["keyword"]}
    assert set(got) == {ids["april-into-may"], ids["may"], ids["may-into-june"], ids["untimed"]}
    assert ids["april-only"] not in got and ids["no-dialogue"] not in got and ids["gap"] not in got
    # A hit is dated by its own turn; the conversation's start is kept beside it.
    assert (got[ids["april-into-may"]]["date"], got[ids["april-into-may"]]["started"]) == ("2026-05-03", "2026-04-28")
    assert (got[ids["untimed"]]["date"], got[ids["untimed"]]["started"]) == ("2026-05-12", "2026-05-12")
    assert db(shelf).execute("SELECT state, chunks FROM records WHERE record_id=?", (ids["no-dialogue"],)).fetchone() == ("indexed", 0)
    # Its passage is its first owner turn inside May, never its April opening.
    assert "counterweight wobbles" in got[ids["april-into-may"]]["passage"]
    assert "telescope" not in got[ids["april-into-may"]]["passage"]
    assert r["overview"] == {"records_in_window": 4, "by_provider": {"claude-code": 3, "grok": 1},
                             "continued_from_before": 1, "dated_by_start_only": 1}


def test_june_overview_counts_the_may_conversation_continued_into_june(tmp_path):
    shelf, ids = build(tmp_path)
    r = search.search("what did we work on in June", shelf_root=shelf.root, embedder=FakeEmbedder(), caller=RAW, k=5,
                      created_after="2026-06-01", created_before="2026-07-01")
    assert [h["record_id"] for h in r["keyword"]] == [ids["may-into-june"]]
    assert "Shipped in June" in r["keyword"][0]["passage"]                  # no owner turn in June: its first June turn
    assert r["overview"]["continued_from_before"] == 1 and r["overview"]["records_in_window"] == 1


def test_the_overview_keeps_scope_and_tier_filters_on_counts_and_candidates(tmp_path):
    shelf, ids = build(tmp_path)
    for caller in (policy.Caller(tier="all", scopes=("mandate:other",)), policy.Caller(tier="all", scopes=()),
                   policy.Caller(tier="curated")):                           # every fixture record is raw
        r = ask(shelf, "What were we doing in May?", caller=caller)
        assert r["keyword"] == [] and r["overview"] == {"records_in_window": 0, "by_provider": {},
                                                         "continued_from_before": 0, "dated_by_start_only": 0}
    assert ask(shelf, "What were we doing in May?", caller=policy.Caller(tier="all", scopes=("robert",)))[
        "overview"]["records_in_window"] == 4


def test_a_topic_question_in_a_window_filters_on_the_turns_own_time(tmp_path):
    shelf, ids = build(tmp_path)
    hits = ask(shelf, "counterweight clutch")["keyword"]
    assert [h["record_id"] for h in hits][:1] == [ids["april-into-may"]]    # its May turns are in May
    assert hits[0]["date"].startswith("2026-05-0") and hits[0]["started"] == "2026-04-28"
    assert ask(shelf, "telescope tripod")["keyword"] == []                  # its April turns are not
    april = search.search("telescope tripod", shelf_root=shelf.root, embedder=FakeEmbedder(), caller=RAW, k=5,
                          created_after="2026-04-01", created_before="2026-05-01")
    assert [h["record_id"] for h in april["keyword"]][:1] == [ids["april-into-may"]]


def test_rebuild_reuse_vectors_embeds_nothing_already_embedded_and_plain_rebuild_embeds_all(tmp_path):
    shelf, ids = build(tmp_path)
    total = db(shelf).execute("SELECT count(*) FROM chunks").fetchone()[0]
    emb = FakeEmbedder()
    reused = indexer.rebuild(shelf.root, emb, min_free_bytes=0, reuse_vectors=True)
    assert reused["code"] == indexer.OK and reused["swapped"] and reused["vectors_from_old_index"] is True
    assert emb.texts == 0 and reused.get("embedded_chunks", 0) == 0 and reused["reused_vectors"] == total
    assert ask(shelf, "What were we doing in May?")["overview"]["records_in_window"] == 4
    plain = FakeEmbedder()
    assert indexer.rebuild(shelf.root, plain, min_free_bytes=0)["embedded_chunks"] == total == plain.texts


def test_reuse_vectors_refuses_an_old_index_made_by_another_embedder(tmp_path):
    shelf, _ = build(tmp_path)
    total = db(shelf).execute("SELECT count(*) FROM chunks").fetchone()[0]
    con = db(shelf)
    con.execute("UPDATE meta SET value='x' || substr(value, 2) WHERE key='embedder_digest'")   # a different model made it
    con.commit()
    con.close()
    emb = FakeEmbedder()
    out = indexer.rebuild(shelf.root, emb, min_free_bytes=0, reuse_vectors=True)
    assert out["vectors_from_old_index"] is False and out["embedded_chunks"] == total == emb.texts


def test_an_index_from_schema_1_must_be_rebuilt(tmp_path):
    shelf, _ = build(tmp_path)
    con = db(shelf)
    con.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    con.commit()
    con.close()
    assert indexer.update(shelf.root, FakeEmbedder(), min_free_bytes=0)["code"] == indexer.INCOMPATIBLE


def overview_block(overview):
    built = context.build({"keyword": [], "meaning": [], "adjacent": {"none": "x"}, "overview": overview, "status": {}})
    return json.loads(built.block.split("=====\n", 1)[1].rsplit("\n=====END", 1)[0])["period_overview"]


def test_the_context_note_says_continued_conversations_are_counted():
    got = overview_block({"records_in_window": 4, "by_provider": {"claude-code": 3, "grok": 1},
                          "continued_from_before": 1, "dated_by_start_only": 1})
    assert got["conversations_active_in_period"] == 4 and got["continued_from_before_period"] == 1
    assert got["dated_by_start_only"] == 1 and "conversations_started_in_period" not in got
    assert "a passage's date is its turn's date" in got["note"]
    assert "including ones begun earlier and continued in it" in got["note"]
    assert "Conversations begun earlier and continued in the period are not included" not in got["note"]


def test_the_context_note_keeps_the_old_disclosure_for_an_overview_from_start_dates_only():
    got = overview_block({"records_in_window": 3, "by_provider": {"grok": 3}})
    assert got["conversations_started_in_period"] == 3
    assert "Conversations begun earlier and continued in the period are not included" in got["note"]
