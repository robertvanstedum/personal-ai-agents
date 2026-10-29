"""Frozen Lesen URL/identity and display-selection contracts; no store or network."""
import importlib
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture(params=['german_domain', 'domains.german.reading_rules'])
def rules(request):
    module = importlib.import_module(request.param)
    aliases = {'normalize_url': '_normalize_lesen_url',
               'article_id': '_lesen_article_id', 'apply_source_cap': '_apply_source_cap'}
    return lambda name: getattr(module, aliases[name] if request.param == 'german_domain' else name)


@pytest.mark.parametrize('url,expected', [
    (' HTTPS://News.Example:443/story///one/?utm_source=rss&b=2&a=1#section ',
     'https://news.example/story/one?a=1&b=2'),
    ('http://EXAMPLE.test:80/', 'http://example.test'),
    ('https://example.test:8443/path/', 'https://example.test:8443/path'),
    ('https://example.test/?x=&x=two&FBCLID=abc&gclid=def&UtM_medium=feed',
     'https://example.test?x=&x=two'),
    ('https://example.test/straße?q=Grüße', 'https://example.test/straße?q=Gr%C3%BC%C3%9Fe'),
    ('https://example.test/?b=2&a=3&a=1', 'https://example.test?a=1&a=3&b=2'),
    ('https://example.test/a%2Fb', 'https://example.test/a%2Fb'),
    ('https://example.test:bad/a', ''),
    ('https://example.test:99999/a', ''),
    ('https://[broken/a', ''),
    ('/relative/path', ''), ('not a url', ''), ('', ''), (None, ''),
])
def test_url_canonicalization(rules, url, expected):
    assert rules('normalize_url')(url) == expected


def test_ids_are_url_based_and_keep_legacy_date_argument(rules):
    # Stable expected ID prevents a refactor from silently changing future keys.
    url = 'https://news.test/a'
    assert rules('article_id')(url) == 'art_url_05a9f82841c9faa12c1dbc5f'
    assert rules('article_id')(url, '2026-01-01') == rules('article_id')(url, '2026-10-28')
    assert rules('article_id')('https://news.test/b') != rules('article_id')(url)


def test_source_cap_preserves_order_and_object_identity(rules):
    articles = [{'id': i, 'source': source} for i, source in enumerate('AAABCBC')]
    original = [dict(a) for a in articles]
    selected = rules('apply_source_cap')(articles, {
        'max_per_category': 4, 'max_per_source_per_category': 2, 'min_per_category': 1,
    })
    assert [a['id'] for a in selected] == [0, 1, 3, 4]
    assert all(a is articles[a['id']] for a in selected)
    assert articles == original


def test_minimum_display_count_can_relax_source_cap(rules):
    articles = [{'id': i, 'source': 'same'} for i in range(5)]
    selected = rules('apply_source_cap')(articles, {
        'max_per_category': 4, 'max_per_source_per_category': 1, 'min_per_category': 3,
    })
    assert selected == articles[:3]
    assert rules('apply_source_cap')([], {}) == []
    assert rules('apply_source_cap')(articles[:2], {}) == articles[:2]


def test_missing_source_is_one_bucket_and_equal_objects_remain_distinct(rules):
    first, second = {'title': 'same'}, {'title': 'same'}
    selected = rules('apply_source_cap')([first, second], {
        'max_per_source_per_category': 1, 'min_per_category': 2,
    })
    assert len(selected) == 2
    assert selected[0] is first and selected[1] is second


def test_rules_import_without_domain_runtime_or_site_packages():
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from domains.german.reading_rules import normalize_url
assert 'german_domain' not in sys.modules
assert 'domains.german.german_domain' not in sys.modules
assert 'core.get_secret' not in sys.modules
print(normalize_url('https://EXAMPLE.test/?utm_source=test'))
"""
    result = subprocess.run(
        [sys.executable, '-I', '-S', '-c', code, str(Path(__file__).resolve().parents[1])],
        text=True, capture_output=True, check=True,
    )
    assert result.stdout.strip() == 'https://example.test'


def test_legacy_exports_are_identical():
    import german_domain
    from domains.german import reading_rules
    assert german_domain._normalize_lesen_url is reading_rules.normalize_url
    assert german_domain._lesen_article_id is reading_rules.article_id
    assert german_domain._apply_source_cap is reading_rules.apply_source_cap


def test_standalone_domain_and_anki_import_with_only_domain_on_path(tmp_path):
    code = """
import sys, types
sys.path.insert(0, sys.argv[1])
# Standard-library-only proof of the entrypoint bootstrap. Optional parsing
# packages and secret resolution are not part of this import contract.
secret = types.ModuleType('core.get_secret')
def refused(*args, **kwargs):
    raise AssertionError('secret access during import')
secret.get_secret = refused
sys.modules['core.get_secret'] = secret
sys.modules['feedparser'] = types.ModuleType('feedparser')
bs4 = types.ModuleType('bs4')
bs4.BeautifulSoup = refused
sys.modules['bs4'] = bs4
import german_domain
import import_cards
from domains.german import drills, reading_rules
assert german_domain._normalize_lesen_url is reading_rules.normalize_url
assert german_domain._start_drill_state is drills.start_drill_state
assert import_cards.GERMAN_STATE_DIR == german_domain.GERMAN_STATE_DIR
print('standalone import OK')
"""
    domain = str(Path(__file__).resolve().parents[1] / 'domains/german')
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code, domain],
                            cwd=tmp_path, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == 'standalone import OK'
