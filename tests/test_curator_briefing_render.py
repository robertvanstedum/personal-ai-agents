"""Byte-level briefing output compatibility, with a fixed clock and synthetic articles."""
import copy
import datetime
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


class FixedDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 28, 12, 30, 0, tzinfo=tz)


def articles():
    return [
        {'hash_id': 'abc12', 'title': "Résumé & O'Brien", 'source': "Example's news",
         'link': 'https://example.test/one?a=1&b=2', 'published': None,
         'score': 8.5, 'final_score': 9.25, 'raw_score': 123.0,
         'category': 'fiscal', 'method': 'synthetic', 'summary': 'Line one\nLine two'},
        {'title': 'Second article', 'source': 'Other', 'link': 'https://example.test/two',
         'published': FixedDatetime(2026, 10, 27, 12, 30, tzinfo=datetime.timezone.utc),
         'score': 2.0, 'summary': ''},
    ]


def cases():
    return {
        'empty': ([], {}),
        'default': (articles(), {}),
        'dry_run': (articles(), {'run_mode': 'dry-run'}),
        'haiku': (articles(), {'model': 'haiku'}),
        'sonnet': (articles(), {'model': 'sonnet'}),
        'ollama': (articles(), {'model': 'ollama'}),
        'custom_model': (articles(), {'model': 'synthetic-model'}),
        'radar': (articles(), {'radar_articles': [
            {'title': 'Radar item', 'source': 'Example', 'link': 'https://example.test/radar',
             '_radar_topics': ['topic one', 'topic two']},
        ]}),
        'published_text': ([dict(articles()[0], published='2026-10-28T10:30:00+00:00')], {}),
    }


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(datetime, 'datetime', FixedDatetime)


@pytest.fixture(params=['domains.curator.curator_rss_v2', 'domains.curator.briefing_render'])
def renderer(request):
    return importlib.import_module(request.param)


def render_html(module, entries, kwargs):
    if module.__name__.endswith('briefing_render'):
        return module.format_html(entries, xai_ranking_model='grok-4.3', **kwargs)
    return module.format_html(entries, **kwargs)


@pytest.mark.parametrize('case_name', list(cases()))
def test_html_matches_frozen_output_and_preserves_input(renderer, case_name):
    entries, kwargs = cases()[case_name]
    before = copy.deepcopy((entries, kwargs))
    html = render_html(renderer, entries, kwargs)
    expected = json.loads((Path(__file__).parent / 'fixtures/curator_briefing_hashes.json').read_text())
    assert hashlib.sha256(html.encode()).hexdigest() == expected['html'][case_name]
    assert (entries, kwargs) == before
    assert '<meta name="curator-articles"' in html
    if case_name == 'dry_run':
        assert 'Dry run — buttons disabled' in html


@pytest.mark.parametrize('name', ['format_output', 'format_telegram'])
def test_text_outputs_match_frozen_bytes(renderer, name):
    entries = articles()
    expected = json.loads((Path(__file__).parent / 'fixtures/curator_briefing_hashes.json').read_text())
    result = getattr(renderer, name)(entries)
    assert hashlib.sha256(result.encode()).hexdigest() == expected[name]
    assert 'Résumé' in result
    assert 'https://example.test/one?a=1&b=2' in result


def test_legacy_html_uses_current_runtime_model_label(monkeypatch):
    from domains.curator import curator_rss_v2 as rss
    monkeypatch.setattr(rss, 'XAI_RANKING_MODEL', 'synthetic-model-override')
    assert 'content="synthetic-model-override"' in rss.format_html([], model='xai')


def test_renderer_import_and_empty_html_need_no_runtime_or_site_packages(tmp_path):
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from domains.curator import briefing_render
assert 'domains.curator.curator_rss_v2' not in sys.modules
assert 'core.get_secret' not in sys.modules
assert 'requests' not in sys.modules
assert 'feedparser' not in sys.modules
assert '<html>' in briefing_render.format_html([], xai_ranking_model='synthetic')
print('render OK')
"""
    result = subprocess.run(
        [sys.executable, '-I', '-S', '-c', code, str(Path(__file__).resolve().parents[1])],
        cwd=tmp_path, text=True, capture_output=True, check=True,
    )
    assert result.stdout.strip() == 'render OK'


def test_existing_text_exports_remain_identical():
    from domains.curator import curator_rss_v2 as rss, briefing_render
    assert rss.format_output is briefing_render.format_output
    assert rss.format_telegram is briefing_render.format_telegram
