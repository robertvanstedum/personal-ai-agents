"""Scan navigation policy preserves source provenance and text labels."""
import copy
from html.parser import HTMLParser

import pytest
from domains.curator import research_routes as routes
from domains.curator.research.scan_render import render_scan_page


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.links, self.buttons = [], []
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('target') == '_blank': self.links.append(attrs)
        if tag == 'button' and 'data-url' in attrs: self.buttons.append(attrs)


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'JaVaScRiPt:alert(1)',
    'data:text/html,<script>alert(1)</script>', 'file:///private/example',
    '//example.test/path', '/relative', 'https:///missing-host', 'https://[broken',
    'https://example.test:bad', 'https://example.test:99999'])
def test_route_source_link_is_inert_for_non_web_or_malformed_url(tmp_path, monkeypatch, url):
    source = tmp_path / 'abc12-synthetic.md'
    markdown = f'# Example\n**Source:** Source label\n**URL:** {url}\n'
    source.write_text(markdown)
    monkeypatch.setattr(routes, 'SCANS_DIR', tmp_path)
    page, status, _ = routes.research_scan_view('abc12')
    assert status == 200
    assert Page(page).links[0]['href'] == '#'
    assert 'Source label' in page
    assert source.read_text() == markdown
    assert routes._parse_scan_md(source)['metadata']['url'] == url


@pytest.mark.parametrize('url,expected', [
    ('https://example.test/path?a=1&b="two"#part', 'https://example.test/path?a=1&b="two"#part'),
    ('http://example.test/story', 'http://example.test/story'),
    ('https://example.test:bad', '#'),
    ('javascript:alert(1)', '#'),
])
def test_rendered_links_preserve_data_reference_and_input(url, expected):
    dd = {'title': 'Title', 'metadata': {'url': url, 'source': 'Source'},
          'bibliography': [{'title': 'Reference', 'raw': 'Raw reference', 'author': 'Author',
                            'url': url, 'has_url': True, 'bracket_note': None}],
          'analysis_html': '', 'cost': None}
    before = copy.deepcopy(dd)
    page = Page(render_scan_page(dd, 'abc12'))
    assert [a['href'] for a in page.links] == [expected, expected]
    assert page.buttons[0]['data-url'] == url
    assert dd == before


def test_briefing_compatibility_export_uses_same_navigation_policy():
    from domains.curator import briefing_render
    from utils.html_links import safe_web_href
    assert briefing_render._safe_href is safe_web_href
