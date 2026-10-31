"""Session sequence growth must never select an already-existing file."""
import importlib

import pytest


DATE = '2026-10-28'


@pytest.fixture(params=['command', 'domain'])
def next_id(request, tmp_path, monkeypatch):
    if request.param == 'command':
        module = importlib.import_module('parse_transcript')
        return lambda: module._next_session_id(DATE, tmp_path)
    module = importlib.import_module('german_domain')
    monkeypatch.setattr(module, '_SESSIONS_DIR', tmp_path)
    return lambda: module._next_session_filename(DATE)


@pytest.mark.parametrize('suffixes,expected', [
    ([], '001'), (['001', '003'], '004'), (['999', '1000'], '1001'),
    (['9999', '10000'], '10001'), (['001', '009', 'summary'], '010'),
])
def test_next_id_uses_numeric_sequence(next_id, tmp_path, suffixes, expected):
    original = {}
    for suffix in suffixes:
        path = tmp_path / f'{DATE}_{suffix}.json'
        path.write_text('existing ' + suffix)
        original[path] = path.read_bytes()
    (tmp_path / '2026-10-27_99999.json').write_text('another date')
    assert next_id() == f'{DATE}_{expected}'
    for path, content in original.items():
        assert path.read_bytes() == content


def test_import_after_1000_preserves_existing_sessions(tmp_path):
    parser = importlib.import_module('parse_transcript')
    old = {}
    for suffix in ['999', '1000']:
        path = tmp_path / f'{DATE}_{suffix}.json'
        path.write_text('preserve original ' + suffix)
        old[path] = path.read_bytes()
    raw = f'##{DATE}|Anna|cafe|5|voice\nAnna: Hallo!\nRobert: Guten Tag!'
    result = parser.parse_transcript(raw, tmp_path)
    assert result == tmp_path / f'{DATE}_1001.json'
    for path, content in old.items():
        assert path.read_bytes() == content
