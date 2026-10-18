"""Turn dates through the incremental sync (composition of the two 17 Oct 2026 packages). Synthetic and local, like
``test_vault_apply``: a Mac shelf, r3's seed, one batch, ``vault-apply`` with production's keyword-only index.

A Claude Code conversation begun on 28 April and continued on 3 May is first on the shelf as a version-2 edition (no turn
times: dated by its start). The Mac re-reads the unchanged source with the current parser, which adds an edition with
turn times; that edition is synced and applied. Production's period overview for May must then count the conversation as
active (continued from April) and show its May turn, dated May.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace

from core.memory_index import indexer, policy, search, store
from core.memory_shelf import bundle as bundles, sessions
from tests.vault.test_vault_apply import NeverCalled, apply, current, send, w  # noqa: F401 - w is the fixture

RAW = policy.Caller(tier="all")
SID = "s-april-may"


def rows():
    def cc(kind, text, stamp):
        return json.dumps({"type": kind, "sessionId": SID, "timestamp": stamp,
                           "message": {"role": kind, "content": text if kind == "user" else [{"type": "text", "text": text}]}})
    return [cc("user", "Plan the telescope mount", "2026-04-28T21:00:00Z"),
            cc("assistant", "Start with the tripod", "2026-04-28T21:00:05Z"),
            cc("user", "Back again: the counterweight wobbles", "2026-05-03T09:30:00Z"),
            cc("assistant", "Tighten the clutch on the counterweight", "2026-05-04T08:00:00Z")]


def ingest(shelf, parsed):
    raw = "\n".join(rows()).encode()
    b = bundles.from_parsed(parsed, hashlib.sha256(raw).hexdigest(), len(raw), key=f"claude-code:{SID}", title=SID,
                            origin="watcher:claude-code", created=bundles.utc("2026-04-28T21:00:00Z"),
                            ledger_key=bundles.ledger_key_for("claude-code", SID))
    return shelf.ingest(b)


def untimed_v2():
    """What the shelf held before this change: the same turns, no times, parser version 2."""
    parsed = sessions.parse_claude_code(rows())
    parsed.turns = [replace(t, attrs=None) for t in parsed.turns]
    parsed.normalizer = 2
    return parsed


def may(w, monkeypatch, query="What were we doing in May?"):
    """vault-ask's read of the serving release, keyword-only (no sqlite-vec on the host)."""
    with monkeypatch.context() as m:
        for mod in (store, indexer, search):
            m.setattr(mod, "sqlite_vec", None)
        return search.search(query, shelf_root=w.vault / "current" / "shelf", embedder=NeverCalled(),
                             index_path=w.vault / "current" / "index" / "memory.sqlite", caller=RAW, k=5,
                             created_after="2026-05-01", created_before="2026-06-01")


def served_chunks(w, rid):
    con = sqlite3.connect(f"file:{w.vault / 'current' / 'index' / 'memory.sqlite'}?mode=ro&immutable=1", uri=True)
    try:
        return con.execute("SELECT turn, created FROM chunks WHERE record_id=? ORDER BY turn", (rid,)).fetchall()
    finally:
        con.close()


def test_a_reprocessed_edition_synced_to_production_is_active_in_may(w, monkeypatch):
    first = ingest(w.mac, untimed_v2())
    assert first.outcome == "captured"
    rid = first.record_id
    send(w, [rid])
    assert apply(w)["code"] == "ok"
    before = may(w, monkeypatch)
    assert rid not in {h["record_id"] for h in before["keyword"]}               # dated by its April start only
    assert {c for _, c in served_chunks(w, rid)} == {"2026-04-28T21:00:00Z"}

    # The Mac's new parser re-reads the unchanged source: a new edition with turn times (normalizer:2->3).
    again = ingest(w.mac, sessions.parse_claude_code(rows()))
    assert again.outcome == "edition-added" and again.record_id == rid
    bid = send(w, [rid])
    out = apply(w)
    assert out["code"] == "ok" and out["applied"] == 1 and out["stored"] == 1 and out["release"] == current(w)

    # Production's own keyword-only index re-dated the chunks from the applied edition.
    assert served_chunks(w, rid) == [(1, "2026-04-28T21:00:00Z"), (2, "2026-04-28T21:00:05Z"),
                                     (3, "2026-05-03T09:30:00Z"), (4, "2026-05-04T08:00:00Z")]
    after = may(w, monkeypatch)
    got = {h["record_id"]: h for h in after["keyword"]}
    assert rid in got and "counterweight wobbles" in got[rid]["passage"] and "telescope" not in got[rid]["passage"]
    assert (got[rid]["date"], got[rid]["started"]) == ("2026-05-03", "2026-04-28")
    assert after["overview"]["records_in_window"] == 1 and after["overview"]["continued_from_before"] == 1
    assert after["overview"]["dated_by_start_only"] == 0
    # A topic question in May finds the May turn, and the April turn stays in April.
    assert rid in {h["record_id"] for h in may(w, monkeypatch, "counterweight clutch")["keyword"]}
    assert rid not in {h["record_id"] for h in may(w, monkeypatch, "telescope tripod")["keyword"]}
    assert bid


def test_a_schema_1_production_index_is_rebuilt_by_the_first_apply_after_the_upgrade(w, monkeypatch):
    # The seed was published by code before this change: its index says schema 1. The image's indexer answers
    # rebuild_needed and vault-apply rebuilds production's keyword-only index in the same build (sync R4 path).
    rid = ingest(w.mac, sessions.parse_claude_code(rows())).record_id
    send(w, [rid])
    assert apply(w)["code"] == "ok"
    index = w.vault / "releases" / current(w) / "index" / "memory.sqlite"
    con = sqlite3.connect(index)
    con.execute("UPDATE meta SET value='1' WHERE key='schema_version'")         # as an index from before the change
    con.commit()
    con.close()
    from tests.memory_index.helpers import add_record
    other = add_record(w.mac, "grok", "g-new", [("human", "zeppelin question"), ("assistant", "zeppelin answer")])
    send(w, [other])
    out = apply(w)
    assert out["code"] == "ok" and out["built"]
    con = sqlite3.connect(f"file:{w.vault / 'current' / 'index' / 'memory.sqlite'}?mode=ro&immutable=1", uri=True)
    try:
        assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() == (str(store.SCHEMA_VERSION),)
    finally:
        con.close()
    assert may(w, monkeypatch)["overview"]["continued_from_before"] == 1


def test_with_no_batch_a_schema_1_index_waits_for_rebuild_index_and_search_refuses_it_until_then(w, monkeypatch):
    # Deployment order (composition finding): the image with schema 2 refuses a schema-1 serving index, and vault-apply rebuilds
    # it only when a batch arrives or on --rebuild-index. The operator runs --rebuild-index right after the deploy.
    import pytest
    rid = ingest(w.mac, sessions.parse_claude_code(rows())).record_id
    send(w, [rid])
    assert apply(w)["code"] == "ok"
    index = w.vault / "releases" / current(w) / "index" / "memory.sqlite"
    con = sqlite3.connect(index)
    con.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    con.commit()
    con.close()
    with pytest.raises(store.IndexIncompatible):
        may(w, monkeypatch)
    assert apply(w)["code"] == "noop"                                          # nothing to apply: the index is left as it is
    with pytest.raises(store.IndexIncompatible):
        may(w, monkeypatch)
    out = apply(w, rebuild_index=True)
    assert out["code"] == "ok" and out["built"]
    assert may(w, monkeypatch)["overview"]["continued_from_before"] == 1
