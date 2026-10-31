"""Diagnostic reports must not provision or modify their source logs."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts import cost_report


def test_absent_curator_log_is_empty_without_initialization(tmp_path, monkeypatch):
    log = tmp_path / 'curator_costs.json'
    monkeypatch.setattr(cost_report, 'CURATOR_LOG', log)
    assert cost_report.load_curator_costs() == []
    assert not log.exists()


def test_missing_log_does_not_require_write_permission(tmp_path, monkeypatch):
    monkeypatch.setattr(cost_report, 'CURATOR_LOG', tmp_path / 'missing.json')
    def refuse_write(*args, **kwargs):
        raise PermissionError('report source is read-only')
    monkeypatch.setattr(Path, 'write_text', refuse_write)
    assert cost_report.load_curator_costs() == []


@pytest.mark.parametrize('contents', ['{"runs": [{"cost_usd": 0.17}]}', '{}'])
def test_existing_log_is_read_without_rewriting(tmp_path, monkeypatch, contents):
    log = tmp_path / 'curator_costs.json'
    log.write_text(contents)
    monkeypatch.setattr(cost_report, 'CURATOR_LOG', log)
    assert cost_report.load_curator_costs() == json.loads(contents).get('runs', [])
    assert log.read_text() == contents


def test_corrupt_log_is_not_silently_reset(tmp_path, monkeypatch):
    log = tmp_path / 'curator_costs.json'
    log.write_text('not json')
    monkeypatch.setattr(cost_report, 'CURATOR_LOG', log)
    with pytest.raises(json.JSONDecodeError):
        cost_report.load_curator_costs()
    assert log.read_text() == 'not json'


@pytest.mark.parametrize('period', ['today', 'week', 'month', 'year'])
def test_cli_empty_sources_leave_disposable_checkout_unchanged(tmp_path, period):
    root = Path(__file__).resolve().parents[2]
    checkout = tmp_path / 'checkout'
    paths = ['scripts/cost_report.py', 'services/model_gateway/cost_reporting.py']
    for path in paths:
        target = checkout / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / path, target)
    home = tmp_path / 'home'
    home.mkdir()
    import os
    env = {**os.environ, 'HOME': str(home), 'PYTHONDONTWRITEBYTECODE': '1'}
    result = subprocess.run(
        [sys.executable, '-B', '-I', '-S', str(checkout / paths[0]), period],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert 'Cost Report' in result.stdout
    assert '$0.00' in result.stdout
    assert sorted(str(p.relative_to(checkout)) for p in checkout.rglob('*') if p.is_file()) == sorted(paths)
    assert list(home.rglob('*')) == []
