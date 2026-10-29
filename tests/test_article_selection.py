"""Shared display cap contracts, frozen against both original domain callers."""
import importlib
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(params=['utils.article_selection', 'domains.german.reading_rules', 'domains.portuguese.html_server'])
def select_articles(request):
    module = importlib.import_module(request.param)
    name = '_apply_source_cap' if 'portuguese' in request.param else 'apply_source_cap'
    return getattr(module, name)


@pytest.mark.parametrize('sources,config,expected', [
    ('AAABCBC', {}, [0, 1, 2, 3, 4, 5, 6]),
    ('AAABCBC', {'max_per_category': 4, 'max_per_source_per_category': 2, 'min_per_category': 1}, [0, 1, 3, 4]),
    ('AAAAA', {'max_per_source_per_category': 1, 'min_per_category': 3}, [0, 1, 2]),
    ('AAABC', {'max_per_category': 2, 'max_per_source_per_category': 1, 'min_per_category': 4}, [0, 3, 1, 2]),
    ('', {}, []),
    ('A', {}, [0]),
    ('ABC', {'max_per_category': 0, 'min_per_category': 0}, [0]),
    ('ABC', {'max_per_source_per_category': 0, 'min_per_category': 2}, [0, 1]),
])
def test_order_limits_and_existing_fallback_behavior(select_articles, sources, config, expected):
    articles = [{'id': i, 'source': source} for i, source in enumerate(sources)]
    before = [dict(a) for a in articles]
    original_config = dict(config)
    result = select_articles(articles, config)
    assert [a['id'] for a in result] == expected
    assert all(a is articles[index] for a, index in zip(result, expected))
    assert articles == before and config == original_config


def test_missing_source_and_equal_but_distinct_objects(select_articles):
    a, b = {'title': 'same'}, {'title': 'same'}
    result = select_articles([a, b], {'max_per_source_per_category': 1, 'min_per_category': 2})
    assert len(result) == 2 and result[0] is a and result[1] is b


def test_repeated_reference_keeps_existing_minimum_fallback(select_articles):
    a = {'source': 'same'}
    result = select_articles([a, a], {'max_per_source_per_category': 1, 'min_per_category': 2})
    assert result == [a] and result[0] is a


@pytest.mark.parametrize('config', [{'max_per_category': None}, {'max_per_source_per_category': '3'}, {'min_per_category': None}])
def test_invalid_config_still_raises(select_articles, config):
    with pytest.raises(TypeError):
        select_articles([{'source': 'a'}], config)


def test_all_compatibility_exports_reference_the_shared_operation():
    from utils.article_selection import apply_source_cap
    from domains.german import reading_rules
    from domains.portuguese import html_server
    import german_domain
    assert reading_rules.apply_source_cap is apply_source_cap
    assert german_domain._apply_source_cap is apply_source_cap
    assert html_server._apply_source_cap is apply_source_cap


def test_shared_rule_imports_without_domains_or_site_packages(tmp_path):
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from utils.article_selection import apply_source_cap
assert apply_source_cap([], {}) == []
assert not any(k.startswith(('domains', 'core', 'flask')) for k in sys.modules)
print('isolated selection import OK')
"""
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code, str(Path(__file__).resolve().parents[1])],
                            cwd=tmp_path, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == 'isolated selection import OK'
