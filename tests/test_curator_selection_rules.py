"""Preserve category, boost and radar-selection policy independently of providers."""
import copy
import importlib
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture(params=['domains.curator.curator_rss_v2', 'domains.curator.selection_rules'])
def rules(request):
    return importlib.import_module(request.param)


@pytest.mark.parametrize('title,summary,category', [
    ('China AI development', '', 'geo_major'),
    ('Brazil gold reserves', '', 'geo_other'),
    ('Gold debt', '', 'monetary'),
    ('Debt and AI development', '', 'fiscal'),
    ('Semiconductor manufacturing', '', 'technology'),
    ('Garden flowers', 'Botany', 'other'),
    ('A policy report', 'Russia', 'geo_major'),
])
def test_category_priority_and_summary_matching(rules, title, summary, category):
    assert rules.assign_category({'title': title, 'summary': summary}) == category


@pytest.mark.parametrize('raw,maximum,expected', [
    (-10, 200, 0.0), (0, 200, 0.0), (100, 200, 5.0),
    (200, 200, 10.0), (250, 200, 10.0), (50, 100, 5.0),
])
def test_normalized_score_boundaries(rules, raw, maximum, expected):
    assert rules.normalize_score(raw, maximum) == expected


def test_zero_score_scale_still_raises(rules):
    with pytest.raises(ZeroDivisionError):
        rules.normalize_score(10, 0)


def test_interest_modifiers_sum_without_mutating_inputs(rules):
    entry = {'category': 'fiscal'}
    interests = {'fiscal': [{'modifier': 3}, {'modifier': -1}], 'other': [{'modifier': 99}]}
    before = copy.deepcopy((entry, interests))
    assert rules.apply_interest_boost(entry, interests) == 2
    assert rules.apply_interest_boost({}, {}) == 0
    assert (entry, interests) == before


def test_priority_matching_cap_and_annotations(rules):
    entry = {'title': 'Central banks', 'summary': 'Rates today'}
    priorities = [
        {'id': 'central', 'label': 'Central policy', 'keywords': ['central bank policy', 'banks'], 'boost': 2.5},
        {'id': 'rates', 'keywords': ['rates markets'], 'boost': 1.0},
        {'id': 'negative', 'label': 'Reduce', 'keywords': ['today'], 'boost': -0.25},
        {'id': 'short', 'keywords': ['US AI'], 'boost': 99.0},
    ]
    before = copy.deepcopy(priorities)
    assert rules.apply_priorities_boost(entry, priorities) == 3.0
    assert entry['matched_priorities'] == [
        {'priority_id': 'central', 'label': 'Central policy', 'boost': 2.5},
        {'priority_id': 'rates', 'label': 'Unknown', 'boost': 1.0},
        {'priority_id': 'negative', 'label': 'Reduce', 'boost': -0.25},
    ]
    assert priorities == before


def test_unmatched_priorities_preserve_existing_entry_annotations(rules):
    entry = {'title': 'Garden flowers', 'summary': '', 'matched_priorities': [{'existing': True}]}
    before = copy.deepcopy(entry)
    assert rules.apply_priorities_boost(entry, []) == 0.0
    assert rules.apply_priorities_boost(entry, [{'keywords': ['xyz'], 'boost': 5}]) == 0.0
    assert entry == before  # Callers, not this operation, own annotation reset.


def test_radar_exclusion_deduplication_copy_and_cap_before_sort(rules):
    pool = [
        {'hash_id': 'top', 'title': 'Topic', 'score': 50},
        {'hash_id': 'a', 'title': 'TOPIC first', 'score': 1},
        {'hash_id': 'a', 'title': 'Topic duplicate', 'score': 100},
        {'link': 'https://example.test/b', 'source': 'Topic News', 'final_score': 5},
        {'hash_id': 'later', 'title': 'Topic later', 'score': 20},
    ]
    before = copy.deepcopy(pool)
    result = rules.find_radar_articles(pool, {'top'}, {'one': ['topic'], 'two': ['news']}, cap=2)
    assert [a.get('hash_id') or a['link'] for a in result] == ['https://example.test/b', 'a']
    assert result[0]['_radar_topics'] == ['one', 'two']
    assert result[1]['_radar_topics'] == ['one']
    assert result[0] is not pool[3] and result[1] is not pool[1]
    assert pool == before


def test_radar_empty_tags_and_pre_normalized_tag_contract(rules):
    pool = [{'hash_id': 'a', 'title': 'Topic'}]
    assert rules.find_radar_articles(pool, set(), {}) == []
    assert rules.find_radar_articles(pool, set(), {'one': ['', 'TOPIC']}) == []
    assert rules.find_radar_articles(pool, set(), {'one': ['topic']}, cap=0) == []


def test_selection_rules_cold_import_needs_no_runtime_or_site_packages(tmp_path):
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from domains.curator.selection_rules import normalize_score
assert 'domains.curator.curator_rss_v2' not in sys.modules
assert 'core.get_secret' not in sys.modules
assert 'requests' not in sys.modules
print(normalize_score(100))
"""
    result = subprocess.run(
        [sys.executable, '-I', '-S', '-c', code, str(Path(__file__).resolve().parents[1])],
        cwd=tmp_path, text=True, capture_output=True, check=True,
    )
    assert result.stdout.strip() == '5.0'


def test_runtime_exports_preserve_rule_and_configuration_identity():
    from domains.curator import curator_rss_v2 as runtime, selection_rules
    for name in ('CATEGORIES', 'CATEGORY_PRIORITY', 'assign_category', 'normalize_score',
                 'apply_interest_boost', 'apply_priorities_boost', 'find_radar_articles'):
        assert getattr(runtime, name) is getattr(selection_rules, name)
