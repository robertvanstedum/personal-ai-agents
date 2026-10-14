"""Adjacent slot rules (Codex review of 974fb204, P2): ``older_thread`` means older, and the distance cutoff must be a
real number in range. Synthetic data only."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from core.memory_index import adjacent, indexer, policy
from tests.memory_index.helpers import FakeEmbedder, add_record, make_shelf

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def facts(created, provider="grok", kind="session"):
    return {"provider": provider, "kind": kind, "created": created}


# ── older_thread is older, by at least 30 days, than every hit ───────────────

def test_a_candidate_exactly_thirty_days_older_than_the_hit_is_an_older_thread():
    assert adjacent._basis(facts(NOW - timedelta(days=30)), [facts(NOW)]) == "older_thread"


@pytest.mark.parametrize("candidate", [
    NOW - timedelta(days=29, hours=23),              # just under 30 days older
    NOW,                                             # the same moment
    NOW + timedelta(days=30),                        # a newer record, a month later (Codex's reproduction, reversed)
    NOW + timedelta(days=270),                       # January hit, October candidate
])
def test_a_candidate_that_is_not_at_least_thirty_days_older_is_not_an_older_thread(candidate):
    assert adjacent._basis(facts(candidate), [facts(NOW)]) is None


def test_the_candidate_must_be_older_than_every_hit_not_just_one():
    old, newer_hit = NOW - timedelta(days=100), NOW - timedelta(days=10)
    candidate = facts(NOW - timedelta(days=50))
    assert adjacent._basis(candidate, [facts(newer_hit), facts(NOW)]) == "older_thread"
    assert adjacent._basis(candidate, [facts(old), facts(NOW)]) is None          # one hit is older than the candidate


def test_an_unknown_date_is_never_an_older_thread():
    assert adjacent._basis(facts(None), [facts(NOW)]) is None
    assert adjacent._basis(facts(NOW - timedelta(days=90)), [facts(None)]) is None


def test_other_source_and_other_kind_do_not_depend_on_age():
    assert adjacent._basis(facts(NOW + timedelta(days=5), provider="claude-ai"), [facts(NOW)]) == "other_source"
    assert adjacent._basis(facts(NOW + timedelta(days=5), kind="canary"), [facts(NOW)]) == "other_kind"


def test_end_to_end_a_newer_record_of_the_same_source_is_not_adjacent(tmp_path, monkeypatch):
    shelf = make_shelf(tmp_path)
    add_record(shelf, "grok", "a", [("human", "sourdough starter question"), ("assistant", "feed it daily")],
               created="2026-03-01T10:00:00Z")
    newer = add_record(shelf, "grok", "b", [("human", "yeast rise timing"), ("assistant", "dough proof warm")],
                       created="2026-09-25T10:00:00Z")
    emb = FakeEmbedder()
    indexer.update(shelf.root, emb, min_free_bytes=0)
    monkeypatch.setenv("MOI_ADJACENT_MAX_DISTANCE", "0.99")
    from core.memory_ask import ask as ask_mod
    r = ask_mod.ask("sourdough starter", shelf_root=shelf.root, embedder=emb, caller=policy.Caller(tier="all"), k=1)
    assert r["adjacent"].get("record_id") != newer
    assert r["adjacent"] == {"none": "nothing_close_enough_elsewhere"}


# ── the distance cutoff must be a real number in range ───────────────────────

@pytest.mark.parametrize("value,ok", [(0.0, True), (0.3, True), (0.6, True), (2.0, True), (1, True),
                                      (-0.1, False), (2.0001, False), (math.nan, False), (math.inf, False), (-math.inf, False),
                                      (True, False), ("0.3", False), (None, False)])
def test_valid_distance(value, ok):
    assert adjacent.valid_distance(value) is ok


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "-0.5", "3", "abc", "", "1e999"])
def test_a_bad_environment_cutoff_falls_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("MOI_ADJACENT_MAX_DISTANCE", raw)
    assert adjacent.max_distance() == adjacent.DEFAULT_MAX_DISTANCE


@pytest.mark.parametrize("raw,expected", [("0.3", 0.3), ("0", 0.0), ("2", 2.0)])
def test_a_good_environment_cutoff_is_used(monkeypatch, raw, expected):
    monkeypatch.setenv("MOI_ADJACENT_MAX_DISTANCE", raw)
    assert adjacent.max_distance() == expected


def test_a_nan_distance_is_never_close_enough_and_a_nan_limit_is_replaced(monkeypatch):
    """NaN compares false with everything, so ``score > limit`` let it through. It must not."""
    monkeypatch.setattr(adjacent, "_facts", lambda con, ids: {"top": facts(NOW), "x": facts(NOW, provider="claude-ai")})
    hits = [{"record_id": "top"}]
    row = {"record_id": "x", "chunk_id": "x#1", "scope": "robert", "score": math.nan}
    unresolved: list = []
    out = adjacent.pick(None, "/nowhere", [row], hits, policy.Caller(), unresolved, limit=0.6)
    assert out == {"none": "nothing_close_enough_elsewhere"} and unresolved == []
    ok_row = dict(row, score=0.9)                      # above the cutoff either way
    assert adjacent.pick(None, "/nowhere", [ok_row], hits, policy.Caller(), unresolved, limit=math.nan) == \
        {"none": "nothing_close_enough_elsewhere"}      # a NaN limit became the default 0.6, so 0.9 is too far
    assert adjacent.pick(None, "/nowhere", [dict(row, score=None)], hits, policy.Caller(), unresolved) == \
        {"none": "nothing_close_enough_elsewhere"}
