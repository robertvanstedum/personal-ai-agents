"""Tiny synthetic resource holds; no real conversations or large fixture files."""
import io
import json
import os
from pathlib import Path

import pytest
from core.memory_shelf import approvals, codes, events, fsio, ledger, render
from core.memory_shelf.config import SourceCfg
from tests.memory_shelf.helpers import cc_lines, make_shelf, write_tree

from core.memory_shelf import watchers


def fixture(tmp_path):
    shelf = make_shelf(tmp_path)
    root = tmp_path / 'sources'
    write_tree(root, {'project/s.jsonl': cc_lines('s', ('human', 'synthetic owner'), ('assistant', 'synthetic answer'))})
    cfg = SourceCfg('claude-code', 'claude-code', root)
    watchers.dry_run(shelf, cfg)
    approvals.approve_source(shelf, cfg.name, cfg.fingerprint(), events.OwnerAuthority('moi-approve'))
    return shelf, cfg, root / 'project/s.jsonl'


def test_oversized_source_is_not_opened_or_hashed_and_gap_stays_visible(tmp_path, monkeypatch):
    shelf, cfg, source = fixture(tmp_path)
    original = source.read_bytes()
    monkeypatch.setattr(watchers.render, 'hash_file', lambda *_: pytest.fail('oversized source hashed'))
    monkeypatch.setattr(watchers.coverage, 'parse_with_coverage', lambda *_: pytest.fail('oversized source parsed'))
    result = watchers.run_source(shelf, cfg, max_source_bytes=8)
    assert result['counts'] == {'failed': 1, 'source_over_ceiling': 1} and result['status'] == 'warn'
    assert source.read_bytes() == original and shelf.list_records() == []
    latest = list(ledger.latest(shelf).values())
    assert latest[0]['outcome'] == codes.FAILED and latest[0]['reason'] == 'source_over_ceiling'
    assert ledger.report(shelf)['missing'] == 1 and ledger.report(shelf)['ok'] is False
    status = fsio.read_json(Path(shelf.status_dir) / 'watch-claude-code.json')
    assert status.get('last_success_at') is None and status['status'] == 'warn'


def test_held_existing_record_and_old_normalizer_are_preserved_then_retried(tmp_path):
    shelf, cfg, source = fixture(tmp_path)
    assert watchers.run_source(shelf, cfg, max_source_bytes=10000)['counts']['captured'] == 1
    old_records = shelf.list_records()
    statepath = watchers._state_path(shelf, cfg.name)
    old = fsio.read_json(statepath)
    key = next(iter(old['files']))
    old['files'][key]['normalizer'] = 1
    fsio.write_json(statepath, old)
    assert watchers.run_source(shelf, cfg, max_source_bytes=8)['counts']['source_over_ceiling'] == 1
    held = fsio.read_json(statepath)['files'][key]
    for field in ('record', 'normalizer', 'sha256', 'size', 'mtime_ns'):
        assert held[field] == old['files'][key][field]
    assert held['outcome'] == codes.FAILED and shelf.list_records() == old_records
    retry = watchers.run_source(shelf, cfg, max_source_bytes=10000)
    assert retry['counts'].get('skipped_unchanged', 0) == 0
    assert fsio.read_json(statepath)['files'][key]['outcome'] in codes.OK_OUTCOMES
    assert ledger.report(shelf)['missing'] == 0


def test_growth_after_hash_is_bounded_before_parser_receives_excess_bytes(tmp_path, monkeypatch):
    shelf, cfg, source = fixture(tmp_path)
    limit = source.stat().st_size
    real_hash = render.hash_file
    def grow(path):
        result = real_hash(path)
        with source.open('ab') as f: f.write(b'x' * 32)
        return result
    monkeypatch.setattr(watchers.render, 'hash_file', grow)
    result = watchers.run_source(shelf, cfg, max_source_bytes=limit)
    assert result['counts'] == {'failed': 1, 'source_over_ceiling': 1}
    assert shelf.list_records() == [] and ledger.report(shelf)['missing'] == 1


def test_bounded_lines_refuses_long_line_and_exact_boundary_succeeds():
    assert list(watchers._bounded_lines(io.BytesIO(b'ab\ncd'), 5)) == [b'ab\n', b'cd']
    with pytest.raises(watchers.SourceTooLarge):
        list(watchers._bounded_lines(io.BytesIO(b'ab\ncde'), 5))


@pytest.mark.parametrize('limit', [0, -1, True, '8'])
def test_invalid_override_refuses_without_capture(tmp_path, limit):
    shelf, cfg, source = fixture(tmp_path)
    assert watchers.run_source(shelf, cfg, max_source_bytes=limit)['status'] == 'capture_limit_invalid'
    assert shelf.list_records() == []


def test_environment_config_and_default(tmp_path, monkeypatch):
    shelf, cfg, source = fixture(tmp_path)
    assert watchers.DEFAULT_SOURCE_CEILING_BYTES == 2 << 30 and watchers.DEFAULT_MAX_LINE_BYTES == 32 << 20
    monkeypatch.setenv('MOI_CAPTURE_SOURCE_CEILING_BYTES', '8')
    assert watchers.run_source(shelf, cfg)['counts']['source_over_ceiling'] == 1
    for name in ('MOI_CAPTURE_SOURCE_CEILING_BYTES', 'MOI_CAPTURE_MAX_LINE_BYTES', 'MOI_CAPTURE_MAX_NORMALIZED_BYTES'):
        monkeypatch.setenv(name, 'invalid')
        assert watchers.run_source(shelf, cfg)['status'] == 'capture_limit_invalid'
        monkeypatch.delenv(name)
    monkeypatch.setenv('MOI_CAPTURE_MAX_SOURCE_BYTES', '8')          # retired for capture: only caps fidelity replay now
    assert watchers.run_source(shelf, cfg)['counts']['captured'] == 1
