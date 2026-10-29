"""Frozen Scan page bytes and route response contract; synthetic documents only."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from domains.curator import research_routes as routes

CASES = json.loads((Path(__file__).parent / 'fixtures/research/scan_documents.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=[c['name'] for c in CASES])
def test_scan_route_retains_frozen_page_and_response(tmp_path, monkeypatch, case):
    source = tmp_path / 'abc12-synthetic.md'
    source.write_text(case['markdown'])
    monkeypatch.setattr(routes, 'SCANS_DIR', tmp_path)
    page, status, headers = routes.research_scan_view('abc12')
    assert status == 200 and headers == {'Content-Type': 'text/html'}
    assert hashlib.sha256(page.encode()).hexdigest() == case['page_sha256']
    assert source.read_text() == case['markdown']


@pytest.mark.parametrize('hash_id', ['abc12', '../abc12', 'ABC12', 'abc123'])
def test_missing_or_invalid_scan_remains_not_found(tmp_path, monkeypatch, hash_id):
    monkeypatch.setattr(routes, 'SCANS_DIR', tmp_path)
    page, status = routes.research_scan_view(hash_id)
    assert status == 404 and 'not found' in page
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('case', CASES, ids=[c['name'] for c in CASES])
def test_renderer_retains_frozen_page_without_files(case):
    from domains.curator.research.scan_render import render_scan_page
    assert hashlib.sha256(render_scan_page(case['parsed'], 'abc12').encode()).hexdigest() == case['page_sha256']


def test_renderer_import_has_no_runtime_or_site_dependency(tmp_path):
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from domains.curator.research.scan_render import render_scan_page
assert callable(render_scan_page)
assert 'flask' not in sys.modules
assert 'domains.curator.research_routes' not in sys.modules
assert 'domains.curator.research.paths' not in sys.modules
assert 'core.get_secret' not in sys.modules
print('isolated scan rendering OK')
"""
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code, str(Path(__file__).resolve().parents[1])],
                            cwd=tmp_path, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == 'isolated scan rendering OK'
