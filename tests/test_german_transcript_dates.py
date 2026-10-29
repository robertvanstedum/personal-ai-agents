"""Transcript dates must stay calendar values, never output path fragments."""
import importlib
import json

import pytest


TURNS = 'Anna: Guten Tag!\nRobert: Schön, dich zu sehen.\n'


def transcript(date, compact=False):
    if compact:
        return f'##{date}|Anna|cafe|5|voice\n{TURNS}'
    return (f'---SESSION---\nDate: {date}\nPersona: Anna\nScenario: cafe\n'
            f'Duration: 5\nMode: voice\n\n{TURNS}---END---')


@pytest.mark.parametrize('date', [
    '../outside', '/absolute', '2026/10/28', '2026-2-03', '2026-10-8',
    '2026-02-29', '2026-13-01', '2026-00-01', '2026-10-32', '0000-01-01',
    '2026-10-28-extra', '２０２６-１０-２８', '',
])
def test_invalid_header_date_is_rejected_without_files(tmp_path, date):
    parser = importlib.import_module('parse_transcript')
    sessions = tmp_path / 'sessions'
    sessions.mkdir()
    # The absolute-path case stays inside this disposable fixture even on the old code.
    if date == '/absolute':
        date = str(tmp_path / 'absolute')
    with pytest.raises(ValueError, match='YYYY-MM-DD'):
        parser.parse_transcript(transcript(date), sessions)
    assert list(tmp_path.rglob('*.json')) == []


@pytest.mark.parametrize('date', ['2026-02-29', '2026-13-01', '0000-01-01'])
def test_compact_format_rejects_impossible_calendar_dates(tmp_path, date):
    parser = importlib.import_module('parse_transcript')
    with pytest.raises(ValueError, match='YYYY-MM-DD'):
        parser.parse_transcript(transcript(date, compact=True), tmp_path)
    assert list(tmp_path.rglob('*.json')) == []


@pytest.mark.parametrize('compact', [False, True])
@pytest.mark.parametrize('date', ['2024-02-29', '2026-10-28', '2000-02-29'])
def test_valid_dates_keep_numbering_and_session_payload(tmp_path, compact, date):
    parser = importlib.import_module('parse_transcript')
    first = parser.parse_transcript(transcript(date, compact), tmp_path)
    second = parser.parse_transcript(transcript(date, compact), tmp_path)
    assert first == tmp_path / f'{date}_001.json'
    assert second == tmp_path / f'{date}_002.json'
    data = json.loads(second.read_text())
    assert data == {
        'session_id': f'{date}_002', 'date': date, 'persona': 'Anna', 'scenario': 'cafe',
        'duration_estimate_min': 5, 'mode': 'voice', 'source': 'manual',
        'raw_transcript': [{'speaker': 'Anna', 'text': 'Guten Tag!'},
                           {'speaker': 'Robert', 'text': 'Schön, dich zu sehen.'}],
        'reviewer_output': None, 'anki_generated': False, 'next_lesson_generated': False,
        'drill_mode': False, 'drill_session': 0, 'drill_total': 0,
    }


@pytest.mark.parametrize('date,expected_code', [('../outside', 1), ('2026-10-28', 0)])
def test_standalone_cli_reports_date_error_without_writing(tmp_path, date, expected_code):
    from pathlib import Path
    import shutil
    import subprocess
    import sys

    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    source = Path(__file__).resolve().parents[1] / 'domains/german/parse_transcript.py'
    script = checkout / source.name
    shutil.copyfile(source, script)
    text_module = checkout / 'domains/german/transcript_text.py'
    text_module.parent.mkdir(parents=True)
    shutil.copyfile(source.with_name('transcript_text.py'), text_module)
    # The entrypoint itself is real; only domain config resolution is synthetic.
    (checkout / 'german_domain.py').write_text(
        'from pathlib import Path\nGERMAN_DIR = Path(__file__).parent\n'
        'GERMAN_STATE_DIR = GERMAN_DIR / "unused-state"\n'
    )
    state = tmp_path / 'state'
    result = subprocess.run(
        [sys.executable, '-I', '-S', str(script), '--stdin', '--base-dir', str(state)],
        input=transcript(date), text=True, capture_output=True, cwd=tmp_path,
    )
    assert result.returncode == expected_code, result.stderr
    if expected_code:
        assert 'YYYY-MM-DD' in result.stderr
        assert list(tmp_path.rglob('*.json')) == []
    else:
        assert (state / 'sessions/2026-10-28_001.json').is_file()
