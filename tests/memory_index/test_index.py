"""M3 index: update/rebuild (T-D), pointing rule (T-E), model pin, orphans, freshness, gates. Synthetic only."""
from __future__ import annotations

import os
import shutil
import sqlite3

import pytest

from core.memory_index import health, indexer, policy, search, store
from core.memory_index.embedder import PIN_DIGEST
from tests.memory_index.helpers import (FakeEmbedder, add_record, designate, main_path, make_shelf, tree_digest,
                                        world)

RAW = policy.Caller(tier="raw")


def ids_of(hits):
    """Record ids in hit order, each once (a record can have a hit per turn)."""
    return list(dict.fromkeys(h["record_id"] for h in hits))


@pytest.fixture
def built(tmp_path):
    shelf, ids = world(tmp_path)
    emb = FakeEmbedder()
    result = indexer.update(shelf.root, emb, min_free_bytes=0)
    assert result["code"] == "ok"
    return shelf, ids, emb, result


def q(shelf, emb, text, caller=RAW, **kw):
    return search.search(text, shelf_root=shelf.root, embedder=emb, caller=caller, **kw)


# ── what gets indexed ────────────────────────────────────────────────────────

def test_update_indexes_retrievable_records_and_holds_the_rest(built):
    shelf, ids, emb, result = built
    assert result["indexed"] == 4                                  # three grok + canary
    assert result["held"] == {"awaiting_reprocess": 1}             # codex normalizer 2 < 3
    con = store.connect(indexer.default_index_path(shelf.root), readonly=True)
    speakers = {r[0] for r in con.execute("SELECT DISTINCT speaker FROM chunks")}
    held_chunks = con.execute("SELECT COUNT(*) FROM chunks WHERE record_id=?", (ids["held"],)).fetchone()[0]
    con.close()
    assert speakers == {"human", "assistant"}                      # the system turn is not indexed
    assert held_chunks == 0


def test_legacy_misattributed_text_never_reaches_retrieval(built):
    shelf, ids, emb, _ = built
    for tier in ("raw", "all"):
        r = q(shelf, emb, "APPROVAL REVIEW prompt telescope invoice", policy.Caller(tier=tier))
        assert ids["held"] not in ids_of(r["keyword"]) + ids_of(r["meaning"])
    r = q(shelf, emb, "SYSTEM NOTE internal")
    assert all("SYSTEM NOTE" not in h["passage"] for h in r["keyword"] + r["meaning"])


def test_index_keeps_no_record_text(built):
    shelf, *_ = built
    con = sqlite3.connect(indexer.default_index_path(shelf.root))
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    # contentless FTS: there is no content table, and reading the column back yields nothing
    assert "fts_content" not in tables
    con.enable_load_extension(True)
    import sqlite_vec
    sqlite_vec.load(con)
    assert {r[0] for r in con.execute("SELECT text FROM fts")} == {None}
    blob = indexer.default_index_path(shelf.root).read_bytes()
    assert b"Dobsonian shows Jupiter" not in blob
    con.close()


def test_indexing_never_writes_the_shelf(tmp_path):
    shelf, _ = world(tmp_path)
    before = tree_digest(shelf.root)
    emb = FakeEmbedder()
    indexer.update(shelf.root, emb, min_free_bytes=0)
    indexer.rebuild(shelf.root, emb, min_free_bytes=0)
    q(shelf, emb, "telescope")
    health.status(shelf.root, embedder=emb)
    assert tree_digest(shelf.root) == before


def test_index_file_is_private(built):
    shelf, *_ = built
    path = indexer.default_index_path(shelf.root)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"


# ── search shape and honesty (R5) ────────────────────────────────────────────

def test_two_labelled_lists_keyword_and_meaning(built):
    shelf, ids, emb, _ = built
    r = q(shelf, emb, "looking at planets through my scope")
    assert ids_of(r["meaning"])[0] == ids["sky"]
    r2 = q(shelf, emb, "yeast rising tall")             # no shared word with the bread record
    assert r2["keyword"] == [] or ids["bread"] not in ids_of(r2["keyword"])
    assert ids_of(r2["meaning"])[0] == ids["bread"]
    r3 = q(shelf, emb, "Dobsonian")
    assert ids_of(r3["keyword"]) == [ids["sky"]]
    assert set(r) >= {"query_id", "keyword", "meaning", "adjacent", "unresolved", "status"}
    assert r["adjacent"] == {"none": "not_requested"}


def test_hits_carry_citation_and_provenance_read_from_disk(built):
    shelf, ids, emb, _ = built
    hit = q(shelf, emb, "Dobsonian")["keyword"][0]
    lines = main_path(shelf, ids["sky"]).read_bytes().split(b"\n")
    s, e = hit["lines"]
    assert hit["passage"].encode() == b"\n".join(lines[s - 1:e])
    assert hit["file"].startswith("sessions-raw/") and hit["date"] == "2026-10-01"
    assert hit["provenance"].startswith("written by Grok")
    assert hit["chunk_id"].startswith(ids["sky"] + "@")


def test_default_tier_is_curated_and_raw_only_on_request(built):
    shelf, ids, emb, _ = built
    default = q(shelf, emb, "telescope planets", policy.Caller())
    assert default["keyword"] == [] and default["meaning"] == [] and default["unresolved"] == []
    assert ids["sky"] in ids_of(q(shelf, emb, "telescope planets")["keyword"])
    designate(shelf, ids["sky"])
    indexer.update(shelf.root, emb, min_free_bytes=0)
    curated = q(shelf, emb, "telescope planets", policy.Caller())
    assert ids_of(curated["keyword"]) == [ids["sky"]] and ids_of(curated["meaning"]) == [ids["sky"]]
    assert ids["sky"] not in ids_of(q(shelf, emb, "telescope planets")["keyword"])   # raw now excludes it


def test_canary_only_when_asked_by_name(built):
    shelf, ids, emb, _ = built
    for tier in ("raw", "all", "curated"):
        r = q(shelf, emb, "zebra quartz", policy.Caller(tier=tier))
        assert ids["canary"] not in ids_of(r["keyword"]) + ids_of(r["meaning"])
    r = q(shelf, emb, "zebra quartz", policy.Caller(tier="raw", canary=True))
    assert ids_of(r["keyword"]) == [ids["canary"]]


def test_empty_and_tiny_corpora_are_honestly_empty(tmp_path):
    shelf = make_shelf(tmp_path)
    emb = FakeEmbedder()
    assert indexer.update(shelf.root, emb, min_free_bytes=0)["code"] == "ok"
    r = q(shelf, emb, "anything at all", policy.Caller(tier="all"))
    assert (r["keyword"], r["meaning"], r["unresolved"]) == ([], [], [])
    add_record(shelf, "grok", "only", [("human", "one lonely question about tomatoes")])
    indexer.update(shelf.root, emb, min_free_bytes=0)
    r = q(shelf, emb, "telescope", policy.Caller(tier="all"), k=5)
    assert r["keyword"] == [] and len(r["meaning"]) == 1         # never padded beyond what exists


def test_scope_allow_list_filters(built):
    shelf, ids, emb, _ = built
    r = q(shelf, emb, "telescope", policy.Caller(tier="raw", scopes=("mandate:01J00000000000000000000000",)))
    assert r["keyword"] == [] and r["meaning"] == []


def test_one_hit_per_turn_per_list(tmp_path):
    shelf = make_shelf(tmp_path)
    long = "\n".join(f"line {i} about the telescope eyepiece and focal length" for i in range(120))
    rid = add_record(shelf, "grok", "long", [("human", "telescope?"), ("assistant", long)])
    emb = FakeEmbedder()
    indexer.update(shelf.root, emb, min_free_bytes=0)
    r = q(shelf, emb, "telescope eyepiece")
    keys = [(h["record_id"], h["turn"]) for h in r["keyword"]]
    assert len(keys) == len(set(keys)) and (rid, 2) in keys


# ── T-D rebuild ──────────────────────────────────────────────────────────────

QUERIES = ["looking at planets through my scope", "yeast rising tall", "October invoice", "Dobsonian"]


def snapshot(shelf, emb):
    out = []
    for text in QUERIES:
        r = q(shelf, emb, text)
        out.append(([(h["chunk_id"], h["lines"]) for h in r["keyword"]], [(h["chunk_id"], h["lines"]) for h in r["meaning"]]))
    return out


def test_T_D_delete_index_then_rebuild_gives_equivalent_results(built):
    shelf, ids, emb, _ = built
    before = snapshot(shelf, emb)
    path = indexer.default_index_path(shelf.root)
    path.unlink()
    with pytest.raises(store.IndexMissing):
        q(shelf, emb, "telescope")
    result = indexer.rebuild(shelf.root, emb, min_free_bytes=0)
    assert result["code"] == "ok" and result["swapped"] and result["indexed"] == 4
    assert snapshot(shelf, emb) == before


def test_rebuild_reembeds_everything_and_carries_no_vector_over(built):
    shelf, ids, emb, _ = built
    fresh = FakeEmbedder()
    result = indexer.rebuild(shelf.root, fresh, min_free_bytes=0)
    con = store.connect(indexer.default_index_path(shelf.root), readonly=True)
    total = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    con.close()
    assert result.get("reused_vectors", 0) == 0
    assert fresh.texts == total == result["embedded_chunks"]


def test_failed_rebuild_leaves_the_old_index_untouched(built, monkeypatch):
    shelf, ids, emb, _ = built
    path = indexer.default_index_path(shelf.root)
    old = path.read_bytes()
    broken = FakeEmbedder()
    calls = {"n": 0}
    real = broken._embed

    def flaky(texts):
        calls["n"] += 1
        if calls["n"] > 1:
            # Raise the exception bound by the loaded unit, even if another test reimports its dependency.
            raise indexer.EmbedderUnavailable("down")
        return real(texts)
    broken._embed = flaky
    result = indexer.rebuild(shelf.root, broken, min_free_bytes=0)
    assert result["code"] == "embedder_unavailable" and result["swapped"] is False
    assert path.read_bytes() == old
    assert not [p for p in path.parent.iterdir() if ".rebuild-" in p.name]


def test_rebuild_and_update_need_headroom_and_never_delete(built, monkeypatch):
    shelf, ids, emb, _ = built
    path = indexer.default_index_path(shelf.root)
    old = path.read_bytes()
    from core.memory_shelf import fsio
    monkeypatch.setattr(fsio, "check_headroom", lambda *a, **k: "disk_low")
    assert indexer.rebuild(shelf.root, emb)["code"] == "disk_low"
    assert indexer.update(shelf.root, emb)["code"] == "disk_low"
    assert path.read_bytes() == old


def test_rebuild_needs_room_for_a_second_copy(built, monkeypatch):
    shelf, ids, emb, _ = built
    size = indexer.default_index_path(shelf.root).stat().st_size
    seen = []
    from core.memory_shelf import fsio
    monkeypatch.setattr(fsio, "check_headroom", lambda root, floor, frac: seen.append(floor) or None)
    indexer.rebuild(shelf.root, emb, min_free_bytes=1000)
    assert seen == [1000 + size]


def test_writers_are_serialized_by_the_index_lock(built, monkeypatch):
    shelf, ids, emb, _ = built
    monkeypatch.setattr(indexer, "LOCK_WAIT_S", 0.1)
    with indexer.index_lock(indexer.default_index_path(shelf.root)):
        assert indexer.update(shelf.root, emb, min_free_bytes=0)["code"] == "index_busy"
        assert indexer.rebuild(shelf.root, emb, min_free_bytes=0)["code"] == "index_busy"


# ── incremental update (embed-on-file) ───────────────────────────────────────

def test_update_is_idempotent_and_cheap_when_nothing_changed(built):
    shelf, ids, emb, _ = built
    calls = emb.calls
    result = indexer.update(shelf.root, emb, min_free_bytes=0)
    assert result["unchanged"] == 5 and result.get("indexed", 0) == 0 and emb.calls == calls


def test_new_record_is_picked_up_and_shows_as_pending_until_then(built):
    shelf, ids, emb, _ = built
    rid = add_record(shelf, "grok", "g-garden", [("human", "When do I plant tomatoes seedlings outside?")])
    st = health.status(shelf.root, embedder=emb)
    assert st["pending"] == {"unindexed": 1} and st["light"] == "warn"
    indexer.update(shelf.root, emb, min_free_bytes=0)
    st = health.status(shelf.root, embedder=emb)
    assert st["pending"] == {} and st["light"] == "ok"
    assert ids_of(q(shelf, emb, "tomatoes")["keyword"]) == [rid]


def test_event_append_reuses_vectors_and_rebinds_lines(built):
    shelf, ids, emb, _ = built
    texts = emb.texts
    designate(shelf, ids["bread"])                                # front matter grows: every line moves
    result = indexer.update(shelf.root, emb, min_free_bytes=0)
    assert result["reindexed"] == 1 and result["embedded_chunks"] == 0 and emb.texts == texts
    hit = q(shelf, emb, "starter twice", policy.Caller(tier="curated"))["keyword"][0]
    lines = main_path(shelf, ids["bread"]).read_bytes().split(b"\n")
    assert hit["passage"].encode() == b"\n".join(lines[hit["lines"][0] - 1:hit["lines"][1]])


def test_new_edition_rebinds_chunk_ids_to_the_new_edition(built):
    shelf, ids, emb, _ = built
    con = store.connect(indexer.default_index_path(shelf.root), readonly=True)
    before = {r[0] for r in con.execute("SELECT chunk_id FROM chunks WHERE record_id=?", (ids["sky"],))}
    con.close()
    add_record(shelf, "grok", "g-sky", [
        ("human", "Which telescope should I buy for looking at planets?"),
        ("assistant", "A 6 inch Dobsonian shows Jupiter's moons and Saturn's rings."),
        ("human", "And for photographing nebulae?")])
    indexer.update(shelf.root, emb, min_free_bytes=0)
    con = store.connect(indexer.default_index_path(shelf.root), readonly=True)
    after = {r[0] for r in con.execute("SELECT chunk_id FROM chunks WHERE record_id=?", (ids["sky"],))}
    edition = con.execute("SELECT edition FROM records WHERE record_id=?", (ids["sky"],)).fetchone()[0]
    con.close()
    assert edition == 2 and before.isdisjoint(after) and len(after) == 3


def test_reprocessed_record_leaves_hold_when_its_new_edition_arrives(built):
    shelf, ids, emb, _ = built
    add_record(shelf, "codex", "c-old", [("human", "a real owner question about yeast"), ("assistant", "an answer")],
               normalizer=3)
    result = indexer.update(shelf.root, emb, min_free_bytes=0)
    assert result["reindexed"] == 1 and result["held"] == {}
    assert ids_of(q(shelf, emb, "yeast")["keyword"]) == [ids["held"]]


def test_embedder_down_during_update_never_breaks_and_shows_pending(built):
    shelf, ids, emb, _ = built
    add_record(shelf, "grok", "g-new", [("human", "seedlings in the garden")])
    assert indexer.update(shelf.root, FakeEmbedder(down=True), min_free_bytes=0)["code"] == "embedder_unavailable"
    st = health.status(shelf.root, embedder=FakeEmbedder(down=True))
    assert st["light"] == "warn" and st["model_pin"] == "unavailable" and st["pending"] == {"unindexed": 1}


def test_embedder_down_at_query_time_still_returns_keyword_honestly(built):
    shelf, ids, emb, _ = built
    r = q(shelf, FakeEmbedder(down=True), "Dobsonian")
    assert ids_of(r["keyword"]) == [ids["sky"]] and r["meaning"] == []
    assert r["status"]["meaning"] == "embedder_unavailable"


# ── T-E pointing rule ────────────────────────────────────────────────────────

def poison(shelf, sql, args=()):
    con = store.connect(indexer.default_index_path(shelf.root))
    con.execute(sql, args)
    con.commit()
    con.close()


def test_T_E_reference_poisoned_to_another_file_fails_visibly(built):
    shelf, ids, emb, _ = built
    other = main_path(shelf, ids["bread"]).relative_to(shelf.root)
    poison(shelf, "UPDATE chunks SET rel=? WHERE record_id=?", (str(other), ids["sky"]))
    r = q(shelf, emb, "Dobsonian")
    assert r["keyword"] == []
    assert {u["reason"] for u in r["unresolved"]} == {"record_mismatch"}
    assert all(set(u) == {"chunk_id", "list", "reason"} for u in r["unresolved"])
    assert all("Dobsonian" not in str(u) and "sourdough" not in str(u) for u in r["unresolved"])


def test_T_E_reference_poisoned_to_same_record_wrong_lines_fails_visibly(built):
    shelf, ids, emb, _ = built
    poison(shelf, "UPDATE chunks SET start_line=start_line-1, end_line=end_line-1 WHERE record_id=?", (ids["sky"],))
    r = q(shelf, emb, "Dobsonian")
    assert r["keyword"] == [] and {u["reason"] for u in r["unresolved"]} == {"text_changed"}


def test_T_E_reference_outside_the_shelf_is_refused(built, tmp_path):
    shelf, ids, emb, _ = built
    outside = tmp_path / "elsewhere.md"
    shutil.copy(main_path(shelf, ids["sky"]), outside)
    poison(shelf, "UPDATE chunks SET rel=? WHERE record_id=?", ("../elsewhere.md", ids["sky"]))
    r = q(shelf, emb, "Dobsonian")
    assert r["keyword"] == [] and {u["reason"] for u in r["unresolved"]} == {"path_not_permitted"}


def test_T_E_poisoned_edition_or_hash_fails_visibly(built):
    shelf, ids, emb, _ = built
    poison(shelf, "UPDATE chunks SET text_hash=? WHERE record_id=?", ("0" * 64, ids["sky"]))
    assert {u["reason"] for u in q(shelf, emb, "Dobsonian")["unresolved"]} == {"text_changed"}
    poison(shelf, "UPDATE chunks SET edition_hash=? WHERE record_id=?", ("f" * 64, ids["bread"]))
    r = q(shelf, emb, "starter twice")
    assert ids["bread"] not in ids_of(r["keyword"]) and "edition_changed" in {u["reason"] for u in r["unresolved"]}


def test_T_E_tier_gate_is_read_from_the_record_not_the_index(built):
    shelf, ids, emb, _ = built
    poison(shelf, "UPDATE chunks SET tier='curated' WHERE record_id=?", (ids["sky"],))
    poison(shelf, "UPDATE vec SET tier='curated' WHERE id IN (SELECT id FROM chunks WHERE record_id=?)", (ids["sky"],))
    r = q(shelf, emb, "Dobsonian", policy.Caller())
    assert r["keyword"] == [] and {u["reason"] for u in r["unresolved"]} == {"tier_gate"}


# ── model pin ────────────────────────────────────────────────────────────────

def test_model_pin_query_against_a_different_embedder_version_refuses(built):
    shelf, ids, emb, _ = built
    other = FakeEmbedder(digest=PIN_DIGEST + "0" * 52)               # same pin prefix, different full digest
    with pytest.raises(search.QueryRefused) as info:
        q(shelf, other, "Dobsonian")
    assert info.value.code == "pin_mismatch" and "rebuild" in str(info.value)
    assert indexer.update(shelf.root, other, min_free_bytes=0)["code"] == "pin_mismatch"
    assert health.status(shelf.root, embedder=other)["light"] == "failed"


def test_model_pin_an_unpinned_model_is_refused_everywhere(built):
    shelf, ids, emb, _ = built
    wrong = FakeEmbedder(digest="deadbeef" * 8)
    with pytest.raises(search.QueryRefused):
        q(shelf, wrong, "Dobsonian")
    assert indexer.rebuild(shelf.root, wrong, min_free_bytes=0)["code"] == "pin_mismatch"
    wrong_name = FakeEmbedder(name="bge-m3:latest")
    assert indexer.update(shelf.root, wrong_name, min_free_bytes=0)["code"] == "pin_mismatch"


def test_rebuild_with_the_new_pinned_model_clears_the_mismatch(built, monkeypatch):
    shelf, ids, emb, _ = built
    other = FakeEmbedder(digest=PIN_DIGEST + "0" * 52)
    assert indexer.rebuild(shelf.root, other, min_free_bytes=0)["code"] == "ok"
    assert ids_of(q(shelf, other, "Dobsonian")["keyword"]) == [ids["sky"]]
    with pytest.raises(search.QueryRefused):
        q(shelf, emb, "Dobsonian")


def test_chunks_record_their_embedder_and_version(built):
    shelf, *_ = built
    con = store.connect(indexer.default_index_path(shelf.root), readonly=True)
    rows = set(con.execute("SELECT embedder, embedder_version FROM chunks").fetchall())
    con.close()
    assert rows == {("nomic-embed-text:latest", PIN_DIGEST)}


# ── orphans ──────────────────────────────────────────────────────────────────

def test_orphan_deleted_source_fails_health_until_update_or_rebuild(built):
    shelf, ids, emb, _ = built
    folder = main_path(shelf, ids["money"]).parent
    shutil.rmtree(folder)
    shelf.index_path.unlink()                                     # the shelf's own index rebuilds from the records
    st = health.status(shelf.root, embedder=emb)
    assert st["light"] == "failed" and st["orphans"]["record_not_on_shelf"] == 1
    assert st["orphans"]["chunks_of_missing_records"] > 0
    r = q(shelf, emb, "October invoice")
    assert ids["money"] not in ids_of(r["keyword"]) + ids_of(r["meaning"])
    assert "file_missing" in {u["reason"] for u in r["unresolved"]}
    indexer.rebuild(shelf.root, emb, min_free_bytes=0)
    st = health.status(shelf.root, embedder=emb)
    assert st["orphans"] == {} and st["light"] == "ok"
    r = q(shelf, emb, "October invoice")
    assert ids["money"] not in ids_of(r["keyword"]) + ids_of(r["meaning"]) and r["unresolved"] == []


def test_orphan_internal_inconsistency_is_a_failure(built):
    shelf, ids, emb, _ = built
    poison(shelf, "DELETE FROM vec WHERE id = (SELECT MIN(id) FROM chunks)")
    st = health.status(shelf.root, embedder=emb)
    assert st["light"] == "failed" and st["orphans"] == {"chunks_without_vector": 1}


def test_update_drops_a_deleted_records_chunks(built):
    shelf, ids, emb, _ = built
    shutil.rmtree(main_path(shelf, ids["money"]).parent)
    shelf.index_path.unlink()
    result = indexer.update(shelf.root, emb, min_free_bytes=0)
    assert result["removed"] == 1
    assert health.status(shelf.root, embedder=emb)["orphans"] == {}


# ── freshness light and holds ────────────────────────────────────────────────

def test_status_without_index_is_unknown_never_healthy(tmp_path):
    shelf, _ = world(tmp_path)
    st = health.status(shelf.root, embedder=FakeEmbedder())
    assert st["light"] == "unknown" and st["reason"] == "no_index"


def test_expected_holds_do_not_turn_the_light_yellow_but_problems_do(built):
    shelf, ids, emb, _ = built
    st = health.status(shelf.root, embedder=emb)
    assert st["light"] == "ok" and st["held"] == {"awaiting_reprocess": 1} and st["indexed_current"] == 4
    path = main_path(shelf, ids["bread"])
    data = path.read_bytes().replace(b"overnight cold", b"overnight COLD")    # a frame that no longer matches
    path.write_bytes(data)
    indexer.update(shelf.root, emb, min_free_bytes=0)
    st = health.status(shelf.root, embedder=emb)
    assert st["held"].get("unreadable") == 1 and st["light"] == "warn"


def test_incompatible_index_refuses_until_rebuild(built):
    shelf, ids, emb, _ = built
    poison(shelf, "UPDATE meta SET value='999' WHERE key='policy_version'")
    with pytest.raises(store.IndexIncompatible):
        q(shelf, emb, "Dobsonian")
    assert indexer.update(shelf.root, emb, min_free_bytes=0)["code"] == "rebuild_needed"
    assert health.status(shelf.root, embedder=emb)["light"] == "failed"
    assert indexer.rebuild(shelf.root, emb, min_free_bytes=0)["code"] == "ok"
    assert ids_of(q(shelf, emb, "Dobsonian")["keyword"]) == [ids["sky"]]


def test_status_and_errors_carry_no_text(built):
    shelf, ids, emb, _ = built
    st = health.status(shelf.root, embedder=emb)
    blob = repr(st)
    for word in ("sourdough", "telescope", "invoice", "g-sky", "grok g"):
        assert word not in blob


# ── agent-neutral caller and resource use ────────────────────────────────────

def test_the_same_procedure_serves_any_agent_and_names_the_caller(built):
    shelf, ids, emb, _ = built
    results = {}
    for agent, entry in (("robert", "cli"), ("cos", "chat"), ("mc", "chat")):
        r = q(shelf, emb, "Dobsonian", policy.Caller(tier="raw", agent=agent, entry_point=entry))
        assert r["caller"] == {"agent": agent, "entry_point": entry}
        results[agent] = [(h["chunk_id"], h["passage"]) for h in r["keyword"] + r["meaning"]]
    assert results["robert"] == results["cos"] == results["mc"]           # identity never changes what is returned
    with pytest.raises(ValueError):
        policy.Caller(agent="Master Craftsman")


def test_the_embedder_is_released_after_every_run_and_query(built):
    shelf, ids, emb, _ = built
    before = emb.releases
    indexer.update(shelf.root, emb, min_free_bytes=0)
    q(shelf, emb, "Dobsonian")
    indexer.rebuild(shelf.root, emb, min_free_bytes=0)
    assert emb.releases == before + 3
