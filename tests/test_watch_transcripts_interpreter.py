"""Exercise watcher children without a repository venv or external services."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def isolated_watcher(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / 'domains/german/watch_transcripts.py'
    config = tmp_path / 'data/config'
    config.mkdir(parents=True)
    (config / 'sync_config.json').write_text(json.dumps({
        'base_path': str(tmp_path / 'dropbox'),
        'transcripts_inbox_dir': 'inbox',
        'transcripts_processed_dir': 'processed',
        'prompts_dir': 'prompts',
        'poll_interval_seconds': 1,
        'supported_extensions': ['.txt'],
        'pipeline_base_dir': '.',
        'send_telegram_on_start': False,
        'send_telegram_on_success': False,
        'send_telegram_on_error': False,
    }))
    # Copy just the entrypoint into a layout with no venv. The domain's
    # already-resolved interpreter is supplied without importing its runtime.
    entrypoint = tmp_path / 'checkout/domains/german/watch_transcripts.py'
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_bytes(source.read_bytes())
    monkeypatch.setitem(sys.modules, 'german_domain', SimpleNamespace(
        GERMAN_DIR=config.parent,
        GERMAN_STATE_DIR=tmp_path / 'state',
        VENV_PYTHON=Path(sys.executable),
    ))
    monkeypatch.setattr(sys, 'path', list(sys.path))
    spec = importlib.util.spec_from_file_location('isolated_watcher', entrypoint)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_tg_send', lambda text: pytest.fail('unexpected Telegram call'))
    return module


def test_watcher_runs_child_without_checkout_venv(isolated_watcher):
    watcher = isolated_watcher
    out, err, code = watcher._run(
        [str(watcher.VENV_PYTHON), '-c', 'print("child started")'], watcher.HERE,
    )
    assert (out, err, code) == ('child started\n', '', 0)


def test_watcher_pipeline_preserves_arguments_and_archives_transcript(isolated_watcher):
    watcher = isolated_watcher
    # All four stages execute real disposable Python scripts, never the
    # production parser, reviewer, Anki importer or Telegram session sender.
    record = watcher.HERE / 'calls.jsonl'
    script = (
        'import json, sys\nfrom pathlib import Path\n'
        f'with Path({str(record)!r}).open("a") as out:\n'
        '    out.write(json.dumps(sys.argv) + "\\n")\n'
    )
    for name in ('parse_transcript.py', 'reviewer.py', 'import_cards.py', 'get_german_session.py'):
        (watcher.HERE / name).write_text(script)
    watcher.PROCESSED.mkdir(parents=True)
    transcript = watcher.HERE / 'synthetic.txt'
    transcript.write_text('Synthetic fixture only.')
    watcher._process(transcript)
    calls = [json.loads(line) for line in record.read_text().splitlines()]
    assert calls == [
        ['parse_transcript.py', '--input', str(transcript)],
        ['reviewer.py', '--latest'],
        ['import_cards.py'],
        ['get_german_session.py', '--dropbox', '--send'],
    ]
    assert not transcript.exists()
    archived = list(watcher.PROCESSED.glob('*_synthetic.txt'))
    assert len(archived) == 1
    assert archived[0].read_text() == 'Synthetic fixture only.'


def test_watcher_reads_numeric_latest_drill_session(isolated_watcher, monkeypatch):
    watcher = isolated_watcher
    sessions = watcher.GERMAN_STATE_DIR / 'sessions'
    sessions.mkdir(parents=True)
    (sessions / '2026-10-28_999.json').write_text(json.dumps({'drill_mode': False}))
    (sessions / '2026-10-28_1000.json').write_text(json.dumps({'drill_mode': True, 'drill_session': 1, 'drill_total': 2}))
    calls, messages = [], []
    monkeypatch.setattr(watcher, '_run', lambda argv, **kwargs: (calls.append(argv) or ('', '', 0)))
    monkeypatch.setattr(watcher, '_tg_send', messages.append)
    watcher.PROCESSED.mkdir(parents=True)
    transcript = watcher.HERE / 'drill.txt'
    transcript.write_text('Synthetic fixture only.')
    watcher._process(transcript)
    assert [args[1] for args in calls] == ['parse_transcript.py', 'reviewer.py', 'import_cards.py']
    assert messages == ['✅ Drill session 1/2 processed. Keep going!']
