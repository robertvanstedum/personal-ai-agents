"""Preserve the Dive generator's broader document parsing contract."""
import importlib
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture(params=['domains.curator.research.scripts.generate_dive', 'domains.curator.research.document_text'])
def parser(request):
    module = importlib.import_module(request.param)
    if request.param.endswith('generate_dive'):
        return module._parse_session_header, module._parse_session_sections
    return module.parse_session_header, module.parse_dive_sections


def test_header_keeps_colons_and_last_duplicate(parser):
    header, _ = parser
    assert header('<!-- MACHINE-READABLE HEADER -->\ntopic: first\ntopic: second:detail\n<!-- END HEADER -->') == {'topic': 'second:detail'}
    assert header('No header') == {}


def test_dive_sections_keep_its_broader_heading_and_source_contract(parser):
    _, sections = parser
    text = '''## KEY FINDINGS
• First finding
- Second finding
### subheading omitted
## SOURCES
[1] www.example.test. "Inline". https://example.test/one
[2] second.test. "Continuation".
    https://second.test/two
[3] third.test. "No URL".
## Further Sources
[4] excluded.test. "Not a source section". https://excluded.test
## Continue investigation
- Follow up
## Notes for reviewer
Keep this note.
## Cost summary
cost 0
---
retained footer
'''
    assert sections(text) == {
        'findings': ['First finding', 'Second finding'],
        'sources': [
            {'num': 1, 'domain': 'www.example.test', 'title': 'Inline', 'url': 'https://example.test/one'},
            {'num': 2, 'domain': 'second.test', 'title': 'Continuation', 'url': 'https://second.test/two'},
            {'num': 3, 'domain': 'third.test', 'title': 'No URL', 'url': ''},
        ],
        'threads': ['Follow up'], 'agent_notes': 'Keep this note.',
        'cost_table': 'cost 0\n---\nretained footer',
    }


def test_repeated_findings_accumulate_but_later_notes_replace(parser):
    _, sections = parser
    result = sections('## Key Findings\n- one\n## Key Finding extra\n- two\n## Notes\nold\n## Agent Notes\nlatest')
    assert result['findings'] == ['one', 'two']
    assert result['agent_notes'] == 'latest'


def test_unknown_or_empty_sections_return_empty_shape(parser):
    _, sections = parser
    assert sections('## Unrecognized\ntext') == {
        'findings': [], 'sources': [], 'threads': [], 'agent_notes': '', 'cost_table': '',
    }


def test_generator_help_from_other_directory_needs_no_site_packages(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'domains/curator/research/scripts/generate_dive.py'
    result = subprocess.run([sys.executable, '-I', '-S', str(script), '--help'],
                            cwd=tmp_path, text=True, capture_output=True, check=True)
    assert '--topic' in result.stdout
    assert '--dry-run' in result.stdout


def test_generator_uses_shared_header_and_explicit_dive_variant():
    from domains.curator.research.scripts import generate_dive
    from domains.curator.research import document_text
    assert generate_dive._parse_session_header is document_text.parse_session_header
    assert generate_dive._parse_session_sections is document_text.parse_dive_sections
    # Do not silently replace the route parser: it intentionally expects a
    # narrower document shape and handles cost-table terminators differently.
    text = '## KEY FINDINGS\n• First finding'
    assert document_text.parse_dive_sections(text)['findings'] == ['First finding']
    assert document_text.parse_session_sections(text)['findings'] == []
