"""Research document parsing/rendering contracts using synthetic text only."""
import importlib
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture(params=['domains.curator.research_routes', 'domains.curator.research.document_text'])
def document(request):
    module = importlib.import_module(request.param)
    legacy = {'parse_session_header': '_parse_session_header',
              'parse_session_sections': '_parse_session_sections',
              'escape_inline': '_dd_escape', 'render_analysis_html': '_dd_md_to_html'}
    return lambda name: getattr(module, legacy[name] if request.param.endswith('research_routes') else name)


SESSION = '''# Synthetic research session
<!-- MACHINE-READABLE HEADER — preserve -->
date: 2026-10-28
topic: example:fixture
ignored line
<!-- END HEADER -->

## Key Findings
- First finding.
- Second finding.

## Sources
[1] example.test. "Source One".
  https://example.test/one
[2] second.test. "Source Two".
  http://second.test/two

## Threads to Continue
- Next question.

## Agent Notes
Synthetic notes only.

## Cost Breakdown
| stage | cost |
| test | 0 |
---
This trailing text is outside the cost table.
'''


def test_session_header_and_sections(document):
    assert document('parse_session_header')(SESSION) == {'date': '2026-10-28', 'topic': 'example:fixture'}
    assert document('parse_session_sections')(SESSION) == {
        'findings': ['First finding.', 'Second finding.'],
        'sources': [
            {'num': 1, 'domain': 'example.test', 'title': 'Source One', 'url': 'https://example.test/one'},
            {'num': 2, 'domain': 'second.test', 'title': 'Source Two', 'url': 'http://second.test/two'},
        ],
        'threads': ['Next question.'], 'agent_notes': 'Synthetic notes only.',
        'cost_table': '| stage | cost |\n| test | 0 |',
    }


def test_missing_or_partial_header_and_sections(document):
    assert document('parse_session_header')('date: not in a header') == {}
    assert document('parse_session_header')('<!-- MACHINE-READABLE HEADER -->\ndate: incomplete') == {}
    assert document('parse_session_sections')('Unstructured text') == {
        'findings': [], 'sources': [], 'threads': [], 'agent_notes': '', 'cost_table': '',
    }
    assert document('parse_session_header')(
        '<!-- MACHINE-READABLE HEADER -->\ntopic: old\ntopic: latest\n<!-- END HEADER -->'
    ) == {'topic': 'latest'}


@pytest.mark.parametrize('value,expected', [
    ('<script>alert("x")</script> & **bold** *italics*',
     '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; <strong>bold</strong> <em>italics</em>'),
    (None, 'None'), (123, '123'), ('plain', 'plain'),
])
def test_inline_html_escaping_before_markup(document, value, expected):
    assert document('escape_inline')(value) == expected


def test_analysis_block_transitions_and_html_escape(document):
    text = '## Title <unsafe>\nparagraph one\ncontinued\n\n- first **bold**\n* second\n### Next\nlast\n---\nend'
    assert document('render_analysis_html')(text) == (
        '<h2>Title &lt;unsafe&gt;</h2>\n<p>paragraph one continued</p>\n'
        '<ul>\n<li>first <strong>bold</strong></li>\n<li>second</li>\n</ul>\n'
        '<h3>Next</h3>\n<p>last</p>\n<hr>\n<p>end</p>'
    )
    assert document('render_analysis_html')('') == ''
    assert document('render_analysis_html')('- one\nparagraph') == '<ul>\n<li>one</li>\n</ul>\n<p>paragraph</p>'


def test_document_text_cold_import_is_independent_of_flask_and_state(tmp_path):
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from domains.curator.research.document_text import escape_inline
assert 'flask' not in sys.modules
assert 'domains.curator.research_routes' not in sys.modules
assert 'domains.curator.research.paths' not in sys.modules
assert 'core.get_secret' not in sys.modules
print(escape_inline('<safe>'))
"""
    result = subprocess.run(
        [sys.executable, '-I', '-S', '-c', code, str(Path(__file__).resolve().parents[1])],
        cwd=tmp_path, text=True, capture_output=True, check=True,
    )
    assert result.stdout.strip() == '&lt;safe&gt;'


def test_existing_route_helper_exports_remain_identical():
    from domains.curator import research_routes as routes
    from domains.curator.research import document_text as text
    assert routes._parse_session_header is text.parse_session_header
    assert routes._parse_session_sections is text.parse_session_sections
    assert routes._dd_escape is text.escape_inline
    assert routes._dd_md_to_html is text.render_analysis_html
