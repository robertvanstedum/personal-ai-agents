"""Offline adversarial checks for read isolation and selection constraints."""
import copy
from datetime import datetime, timezone
import random
from pathlib import Path

import pytest

from domains.curator import archive_queries, briefing_queries, briefing_views
from domains.curator.selection_rules import select_articles


def select(pool, count, reserve, cap, diversity=0.3):
    return select_articles(
        pool, count, diversity, reserve,
        active_interests={}, active_priorities=[], source_trust={}, x_post_cap=cap,
        domain_from_url=lambda url: "example.invalid",
        interest_boost_fn=lambda entry, interests: 0,
        priorities_boost_fn=lambda entry, priorities: 0,
        report=lambda *args: None,
    )


@pytest.mark.parametrize("seed", range(100))
def test_selection_limits_identity_and_repeatability_across_sparse_pools(seed):
    rng = random.Random(seed)
    pool = [dict(id=i, source=rng.choice(["A", "B"]), category="other",
                 score=rng.choice([0, 5, 10]), age_multiplier=rng.choice([0, 0.5, 1]),
                 content_type=rng.choice(["rss", "x_bookmark"]))
            for i in range(rng.randrange(25))]
    original_order = [id(entry) for entry in pool]
    twin = copy.deepcopy(pool)
    count, reserve, cap = rng.randrange(30), rng.choice([0, 0.2, 0.5, 1]), rng.randrange(5)
    chosen = select(pool, count, reserve, cap)
    repeated = select(twin, count, reserve, cap)
    assert len(chosen) <= min(count, len(pool))
    assert len({id(entry) for entry in chosen}) == len(chosen)
    assert sum(entry["content_type"] == "x_bookmark" for entry in chosen) <= cap
    assert all(any(entry is candidate for candidate in pool) for entry in chosen)
    assert [id(entry) for entry in pool] == original_order
    assert [entry["id"] for entry in chosen] == [entry["id"] for entry in repeated]
    assert pool == twin


@pytest.mark.parametrize("reserve", [0, 0.5, 1])
def test_zero_x_allowance_never_selects_x_even_when_pool_is_all_x(reserve):
    pool = [dict(source="A", score=10, content_type="x_bookmark") for _ in range(8)]
    assert select(pool, 10, reserve, 0) == []


@pytest.mark.parametrize("reserve", [0, 0.5, 1])
def test_ties_retain_input_order_across_both_phases(reserve):
    pool = [dict(id=i, source="A", score=5, content_type="rss") for i in range(8)]
    assert [entry["id"] for entry in select(pool, 8, reserve, 2, diversity=0)] == list(range(8))


class UnreadablePath:
    def exists(self):
        return True

    def read_text(self):
        raise PermissionError("synthetic unreadable file")


def test_unreadable_inputs_do_not_escape_read_fallbacks():
    path = UnreadablePath()
    def never_called(*args):
        raise AssertionError("enrichment/clock must not run after read failure")
    with pytest.raises(briefing_views.InvalidBriefing):
        briefing_queries.load_briefing(path, clock=never_called,
                                      datetime_type=datetime, enrich=never_called)
    assert briefing_queries.load_radar(path) == []
    assert briefing_queries.latest_article_count(path) == 0
    assert briefing_queries.latest_briefing_date(path, datetime_type=datetime) == "—"
    assert archive_queries.bookmarked_sources(path) == []


@pytest.mark.parametrize("broken_side", ["scan", "dive"])
def test_one_failed_catalog_does_not_remove_the_other(tmp_path, broken_side):
    scans = tmp_path / "scans"
    dives = tmp_path / "dives"
    scans.mkdir()
    dives.mkdir()
    (scans / "abc-topic.md").write_text("# Scan\n**Date:** 2026-10-29")
    (dives / "topic-deeper-dive-001.md").write_text("# DEEPER DIVE: Topic\nGenerated: 2026-10-29")
    class BrokenRoot:
        def glob(self, pattern):
            raise PermissionError("synthetic catalog failure")
    result_dives, result_scans = archive_queries.scans_dives_data(
        BrokenRoot() if broken_side == "scan" else scans,
        BrokenRoot() if broken_side == "dive" else dives,
        scan_metadata=archive_queries.scan_metadata,
    )
    assert len(result_dives) == (0 if broken_side == "dive" else 1)
    assert len(result_scans) == (0 if broken_side == "scan" else 1)


@pytest.mark.parametrize("published", [None, "", "not-a-date", "9999-99-99", []])
def test_invalid_timestamps_have_explicit_unavailable_label(published):
    assert briefing_views.time_ago(published, datetime(2026, 10, 29, tzinfo=timezone.utc)) == "N/A"



@pytest.fixture(scope="module")
def parent_server_functions():
    """Load only pure helpers and one route from the reviewed Batch 4 parent."""
    import ast
    import html
    import json
    import re
    from flask import request, jsonify

    source = (Path(__file__).parent / "fixtures/curator_adversarial_parent.txt").read_text()
    names = {"_calc_time_ago", "_strip_summary", "_load_briefing_articles", "api_briefing"}
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    for node in nodes:
        node.decorator_list = []
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 10, 29, 12, tzinfo=timezone.utc)
            return value if tz else value.replace(tzinfo=None)
    namespace = dict(json=json, re=re, _html=html, datetime=Clock,
                     timezone=timezone, request=request, jsonify=jsonify)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<frozen-parent-helpers>", "exec"), namespace)
    return namespace


@pytest.mark.parametrize("payload", [
    {"unexpected": 1}, [None], [{"final_score": "9"}],
    [{"summary": ["text"]}], [{"final_score": float("nan")}],
    [{"final_score": float("inf")}], [],
])
def test_malformed_briefing_http_is_refused_without_mutating_snapshot(
    payload, tmp_path, parent_server_functions, record_property,
):
    """Old route crashes/returns invalid JSON; hardened route refuses safely."""
    import json
    from flask import Flask
    import ast

    path = tmp_path / "curator_latest.json"
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    parent_server_functions.update(_DATA_DIR=tmp_path, REPO_ROOT=tmp_path)
    parent = Flask("frozen_parent_edge_checks")
    parent.add_url_rule("/api/briefing", view_func=parent_server_functions["api_briefing"])
    source = (Path(__file__).resolve().parents[1] / "domains/curator/curator_server.py").read_text()
    names = {"_calc_time_ago", "_strip_summary", "_load_briefing_articles", "api_briefing"}
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    for node in nodes:
        node.decorator_list = []
    current = dict(parent_server_functions)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<current-route-body>", "exec"), current)
    app = Flask("current_edge_checks")
    app.add_url_rule("/api/briefing", view_func=current["api_briefing"])
    old = parent.test_client().get("/api/briefing")
    new = app.test_client().get("/api/briefing")
    if payload == []:
        assert new.status_code == old.status_code == 200
        assert new.data == old.data
    else:
        assert new.status_code == 503
        assert new.get_json() == {"error": "Briefing temporarily unavailable"}
        assert b"NaN" not in new.data and b"Infinity" not in new.data
    assert path.read_bytes() == before
    record_property("http_status", new.status_code)
    record_property("nonstandard_numeric_json", b"NaN" in new.data or b"Infinity" in new.data)


@pytest.mark.parametrize("reserve", [-1, 2, "0.2", None, float("nan")])
def test_invalid_selection_settings_use_safe_default(reserve, record_property):
    import ast
    source = (Path(__file__).parent / "fixtures/curator_adversarial_parent.txt").read_text()
    node = next(node for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == "parent")
    code = ast.Module(body=[node], type_ignores=[])
    namespace = dict(print=lambda *args: None, _domain_from_url=lambda url: "example.invalid",
                     apply_interest_boost=lambda *args: 0, apply_priorities_boost=lambda *args: 0)
    exec(compile(code, "<frozen-parent-selection>", "exec"), namespace)
    pool = [dict(id=i, source="A", score=5, content_type="rss") for i in range(20)]
    def outcome(call):
        try:
            return {"selected": [entry["id"] for entry in call()]}
        except Exception as error:
            return {"error": type(error).__name__}
    old = outcome(lambda: namespace["parent"](copy.deepcopy(pool), 4, 0.3, reserve, {}, [], {}, 2))
    new = outcome(lambda: select(copy.deepcopy(pool), 4, reserve, 2))
    assert "error" in old or len(old["selected"]) > 4
    assert new == outcome(lambda: select(copy.deepcopy(pool), 4, 0.20, 2))
    assert len(new["selected"]) == 4
    record_property("outcome", repr(new))
    record_property("exceeds_requested_count", len(new.get("selected", [])) > 4)


def test_read_then_disappear_race_is_controlled(tmp_path, parent_server_functions, record_property):
    """Missing metadata is refused as unavailable, without inventing a date."""
    class DisappearingPath:
        def exists(self):
            return True
        def read_text(self):
            return '[{}]'
        def stat(self):
            raise FileNotFoundError("synthetic removal after read")
    path = DisappearingPath()
    class Root:
        def __truediv__(self, name):
            return path
    parent_server_functions["_DATA_DIR"] = Root()
    with pytest.raises(FileNotFoundError):
        parent_server_functions["_load_briefing_articles"]()
    with pytest.raises(briefing_views.InvalidBriefing):
        briefing_queries.load_briefing(path, clock=lambda: datetime.now(timezone.utc),
                                      datetime_type=datetime, enrich=lambda raw, now: raw)
    record_property("controlled_error", "InvalidBriefing")


@pytest.mark.parametrize("metadata", [None, [], {"themes": None}, {"themes": "policy"}])
def test_feedback_metadata_shape_errors_and_durable_outcomes(
    metadata, tmp_path, monkeypatch, record_property,
):
    """Malformed provider metadata learns only article facts, once per action."""
    import json
    import importlib
    monkeypatch.setenv("CURATOR_DATA_DIR", str(tmp_path))
    feedback = importlib.import_module("domains.curator.curator_feedback")
    path = tmp_path / "curator_preferences.json"
    monkeypatch.setattr(feedback, "PREFERENCES_FILE", path)
    monkeypatch.setattr(feedback.preference_store, "writer_domain",
                        lambda: "Linux:11111111-1111-1111-1111-111111111111")
    monkeypatch.setattr(feedback, "extract_metadata", lambda *args: copy.deepcopy(metadata))
    events = []
    monkeypatch.setattr(feedback, "log_feedback", lambda **kwargs: events.append(kwargs))
    with feedback.preference_store.locked(path):
        feedback.save_preferences(feedback.load_preferences())
    article = dict(title="Synthetic", source="Fixture", category="other",
                   url="https://example.invalid/article", hash_id="synthetic-shape")
    feedback.record_feedback(1, "liked", "synthetic words", article)
    saved = json.loads(path.read_text())
    assert saved["learned_patterns"]["preferred_themes"] == {}
    assert saved["learned_patterns"]["preferred_content_types"] == {}
    assert saved["learned_patterns"]["preferred_sources"] == {"Fixture": 2}
    assert saved["learned_patterns"]["sample_size"] == 1
    assert len(events) == 1
    first_save = path.read_bytes()
    duplicate = feedback.record_feedback(1, "liked", "synthetic words", article)
    assert duplicate["duplicate"] is True
    assert path.read_bytes() == first_save and len(events) == 1
    record_property("outcome", "article facts preserved; malformed inferred themes discarded")


@pytest.mark.parametrize("entry", [
    {"final_score": True}, {"final_score": 10 ** 400},
    {"extra": {"score": float("nan")}}, {"published": []},
])
def test_snapshot_validation_covers_overflow_booleans_and_nested_nonfinite_values(entry):
    with pytest.raises(briefing_views.InvalidBriefing):
        briefing_views.validate_articles([{"title": "Valid"}, entry])


@pytest.mark.parametrize("value", [True, False, 10 ** 400, float("inf"), -float("inf"), {}, []])
def test_invalid_reserve_types_and_extremes_use_existing_default(value):
    from domains.curator.selection_rules import validated_reserve
    assert validated_reserve(value) == 0.20


@pytest.mark.parametrize("metadata", [None, [], {"themes": "policy"},
                                     {"themes": [None]}, {"content_type": [3]}])
def test_direct_learning_rejects_invalid_metadata_before_mutating_preferences(metadata):
    from domains.curator.feedback_learning import update_patterns
    prefs = {"learned_patterns": {"preferred_themes": {"existing": 2}}}
    original = copy.deepcopy(prefs)
    with pytest.raises(ValueError, match="Invalid feedback metadata"):
        update_patterns(prefs, metadata, "liked", now=datetime.now, classify_url=lambda url: ("", ""))
    assert prefs == original
