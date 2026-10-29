"""Untrusted briefing fields stay text and cannot introduce active page markup."""
import copy
from html.parser import HTMLParser

import pytest

from domains.curator.briefing_render import format_html


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.text = []
        self.feed(text)
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
    def handle_data(self, data):
        self.text.append(data)


def article(**updates):
    value = {'hash_id': 'abc12', 'title': 'An ordinary article', 'source': 'Example',
             'link': 'https://example.test/story', 'published': None,
             'category': 'fiscal', 'final_score': 8.5}
    value.update(updates)
    return value


def render(entries, **kwargs):
    return Page(format_html(entries, xai_ranking_model='synthetic', **kwargs))


def assert_no_injected_markup(page):
    assert not any(tag in {'img', 'iframe', 'svg'} for tag, _ in page.tags)
    assert sum(tag == 'script' for tag, _ in page.tags) == 1  # Existing fixed application script.
    for tag, attrs in page.tags:
        for name, value in attrs.items():
            if name.startswith('on'):
                assert tag == 'button' and name == 'onclick'
                assert value.startswith('showFeedback(this, ')


def test_article_fields_cannot_create_tags_or_event_attributes():
    payload = '\"><img src=x onerror="alert(1)"><script>injected()</script>'
    value = article(title=payload, source=payload, hash_id=payload, category=payload)
    before = copy.deepcopy(value)
    page = render([value])
    assert_no_injected_markup(page)
    row = next(attrs for tag, attrs in page.tags if tag == 'tr' and 'data-rank' in attrs)
    assert row['data-title'] == row['data-source'] == row['data-hash-id'] == row['data-category'] == payload
    assert payload in ''.join(page.text)
    assert value == before


def test_feedback_data_round_trips_apostrophes_unicode_and_ampersands():
    title = "O'Brien & Grüße — <analysis>"
    source = "Readers' News & Views"
    url = 'https://example.test/story?a=1&b="two"'
    page = render([article(title=title, source=source, link=url)])
    row = next(attrs for tag, attrs in page.tags if tag == 'tr' and 'data-rank' in attrs)
    assert row['data-title'] == title
    assert row['data-source'] == source
    assert row['data-url'] == url
    assert ('a', {'href': url, 'target': '_blank'}) in page.tags
    assert title in ''.join(page.text)


@pytest.mark.parametrize('url', [
    'javascript:alert(1)', 'JaVaScRiPt:alert(1)', '\tjavascript:alert(1)',
    'data:text/html,<script>alert(1)</script>', 'file:///private/example',
    'about:blank', '//example.test/path', '/relative/path', 'https://[broken',
    'https:///missing-host', 'https://example.test:bad', 'https://example.test:99999',
    'java\nscript:alert(1)', '', '#',
])
def test_non_web_or_malformed_navigation_is_inert_but_reference_is_preserved(url):
    page = render([article(link=url)])
    row = next(attrs for tag, attrs in page.tags if tag == 'tr' and 'data-rank' in attrs)
    assert row['data-url'] == url
    article_link = next(attrs for tag, attrs in page.tags if tag == 'a' and attrs.get('target') == '_blank')
    assert article_link['href'] == '#'


@pytest.mark.parametrize('url', ['https://example.test/path?a=1&b=2', 'http://example.test/story'])
def test_http_links_still_navigate(url):
    page = render([article(link=url)])
    assert any(tag == 'a' and attrs.get('href') == url and attrs.get('target') == '_blank'
               for tag, attrs in page.tags)


def test_radar_fields_and_metadata_are_escaped_and_link_is_inert():
    payload = '\"><svg onload="alert(1)">'
    radar = [{'title': payload, 'source': payload, 'link': 'javascript:alert(1)',
              '_radar_topics': [payload]}]
    original = copy.deepcopy(radar)
    page = render([], radar_articles=radar, model=payload, run_mode=payload)
    assert_no_injected_markup(page)
    assert ('meta', {'name': 'curator-model', 'content': payload}) in page.tags
    assert ('body', {'data-run-mode': payload}) in page.tags
    assert any(tag == 'a' and attrs.get('target') == '_blank' and attrs['href'] == '#'
               for tag, attrs in page.tags)
    assert payload in ''.join(page.text)
    assert radar == original


def test_feedback_buttons_and_dry_run_remain_unchanged():
    live = render([article()])
    buttons = [attrs for tag, attrs in live.tags if tag == 'button' and 'action-btn' in attrs.get('class', '')]
    assert len(buttons) == 3
    assert all('onclick' in attrs and 'disabled' not in attrs for attrs in buttons)
    dry = render([article()], run_mode='dry-run')
    buttons = [attrs for tag, attrs in dry.tags if tag == 'button' and 'action-btn' in attrs.get('class', '')]
    assert len(buttons) == 3
    assert all('disabled' in attrs and 'onclick' not in attrs for attrs in buttons)
