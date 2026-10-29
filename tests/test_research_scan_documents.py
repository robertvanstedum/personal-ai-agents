"""Synthetic Scan document contracts frozen before separation from Flask."""
import datetime
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

CASES = json.loads((Path(__file__).parent / 'fixtures/research/scan_documents.json').read_text())
ALIASES = {'parse_scan': '_parse_scan_md', 'parse_bibliography': '_parse_scan_bibliography',
           'build_session': '_build_session_from_scan', 'research_directions': '_scan_research_directions'}

@pytest.fixture(params=['domains.curator.research_routes', 'domains.curator.research.scan_documents'])
def operations(request):
    module = importlib.import_module(request.param)
    return lambda name: getattr(module, ALIASES[name] if request.param.endswith('research_routes') else name)

@pytest.mark.parametrize('case', CASES, ids=[c['name'] for c in CASES])
def test_frozen_scan_contracts(operations, tmp_path, case):
    path = tmp_path / 'synthetic.md'
    path.write_text(case['markdown'])
    parsed = operations('parse_scan')(path)
    assert parsed == case['parsed']
    assert operations('parse_bibliography')(path) == case['parsed']['bibliography']
    session = operations('build_session')(parsed, 'abc12', 'synthetic-topic')
    assert session.replace(datetime.date.today().isoformat(), '<TODAY>') == case['session']
    searches, targets = operations('research_directions')(parsed)
    assert [searches, targets] == case['directions']
    assert path.read_text() == case['markdown']
    assert list(tmp_path.iterdir()) == [path]


def test_missing_document_still_raises(operations, tmp_path):
    for name in ('parse_scan', 'parse_bibliography'):
        with pytest.raises(FileNotFoundError):
            operations(name)(tmp_path / 'missing.md')
    assert list(tmp_path.iterdir()) == []


def test_directions_are_ordered_unique_and_bounded(operations):
    questions = ''.join(f'<li>Question {i}?</li><li>Question {i}?</li>' for i in range(12))
    dd = {'title': 'Title', 'metadata': {'interest': 'Interest', 'focus': 'Focus'},
          'analysis_html': '<h2>Next questions</h2><ul>' + questions + '</ul><h2>End</h2><ul><li>Ignored</li></ul>'}
    searches, targets = operations('research_directions')(dd)
    assert searches == ['Title'] + [f'Question {i}?' for i in range(7)]
    assert targets == ['Interest', 'Focus'] + [f'Question {i}?' for i in range(4)]


def test_session_bounds_findings_and_sources_but_preserves_reviewed_count(operations):
    dd = {'title': 'Title', 'analysis_html': ''.join(f'<h3>{i}. Heading</h3><p>A sufficiently long synthetic paragraph provides the evidence for finding {i}.</p>' for i in range(1, 9)),
          'bibliography': [{'title': f'Source {i}', 'url': f'https://example.test/{i}'} for i in range(12)]}
    session = operations('build_session')(dd, 'abc12', 'topic')
    assert 'sources_reviewed: 12' in session
    assert '- 5. Heading:' in session and '- 6. Heading:' not in session
    assert '[10] example.test.' in session and '[11]' not in session


def test_route_exports_retain_the_shared_operations():
    from domains.curator import research_routes
    from domains.curator.research import scan_documents
    for name, alias in ALIASES.items():
        assert getattr(research_routes, alias) is getattr(scan_documents, name)


def test_document_module_import_is_independent_of_runtime(tmp_path):
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from domains.curator.research import scan_documents
assert callable(scan_documents.parse_scan)
assert 'flask' not in sys.modules
assert 'core.get_secret' not in sys.modules
assert 'domains.curator.research_routes' not in sys.modules
assert 'domains.curator.research.paths' not in sys.modules
print('isolated scan import OK')
"""
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code, str(Path(__file__).resolve().parents[1])],
                            cwd=tmp_path, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == 'isolated scan import OK'
