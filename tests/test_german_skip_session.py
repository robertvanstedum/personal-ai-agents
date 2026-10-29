"""Skip CLI contracts against disposable configuration and session state."""
import json
import sys
from types import SimpleNamespace

import pytest
import get_german_session as cli


@pytest.fixture
def skip_world(tmp_path, monkeypatch):
    code = tmp_path / 'code'
    config = code / 'config'
    config.mkdir(parents=True)
    (config / 'domain.json').write_text(json.dumps({'active_persona': 'Test Persona'}))
    (config / 'personas.json').write_text('[]')
    state = tmp_path / 'state'
    (state / 'lessons').mkdir(parents=True)
    (state / 'sessions').mkdir()
    (state / 'progress.json').write_text('{}')
    monkeypatch.setattr(cli, 'GERMAN_DIR', code)
    calls = []
    def generate(feedback, session, domain, personas, progress, lessons):
        calls.append(session)
        (lessons / (session['date'] + '_lesson.json')).write_text(json.dumps({'persona': 'Next', 'scenario': 'next_step'}))
    reviewer = SimpleNamespace(_pick_next_persona=None, _pick_next_scenario=None,
                               _generate_lesson_plan=generate, _empty_reviewer_output=lambda: {})
    monkeypatch.setitem(sys.modules, 'reviewer', reviewer)
    return tmp_path, state, calls


def invoke(monkeypatch, state, *flags):
    monkeypatch.setattr(sys, 'argv', ['get_german_session.py', '--base-dir', str(state), '--date', '2026-10-28', '--skip', *flags])
    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code == 0


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else None for p in root.rglob('*')}


@pytest.mark.parametrize('flags', [[], ['--persona', 'Explicit Persona', '--scenario', 'test_scene']])
def test_skip_dry_run_writes_nothing_and_does_not_generate(skip_world, monkeypatch, capsys, flags):
    root, state, calls = skip_world
    before = snapshot(root)
    invoke(monkeypatch, state, '--dry-run', *flags)
    assert calls == []
    assert snapshot(root) == before
    assert 'Would skip' in capsys.readouterr().out


def test_normal_skip_still_records_session_and_advances_rotation(skip_world, monkeypatch):
    root, state, calls = skip_world
    invoke(monkeypatch, state)
    saved = json.loads((state / 'sessions/2026-10-28_001.json').read_text())
    assert saved['skipped'] and saved['mode'] == 'skipped' and saved['persona'] == 'Test Persona'
    assert calls == [saved]
    progress = json.loads((state / 'progress.json').read_text())
    assert progress['personas_practiced'] == ['Test Persona']
    assert (state / 'lessons/2026-10-28_lesson.json').exists()


@pytest.mark.parametrize('names,expected', [
    (['2026-10-28_999.json', '2026-10-28_1000.json'], '2026-10-28_1001.json'),
    (['2026-10-28_9999.json', '2026-10-28_10000.json'], '2026-10-28_10001.json'),
    (['2026-10-28_001.json', '2026-10-28_notes.json'], '2026-10-28_002.json'),
])
def test_skip_preserves_existing_sessions_beyond_three_digits(skip_world, monkeypatch, names, expected):
    root, state, calls = skip_world
    sessions = state / 'sessions'
    for name in names:
        (sessions / name).write_text(json.dumps({'preserve': name}))
    before = {name: (sessions / name).read_bytes() for name in names}
    invoke(monkeypatch, state)
    assert all((sessions / name).read_bytes() == raw for name, raw in before.items())
    saved = json.loads((sessions / expected).read_text())
    assert saved['session_id'] == expected.removesuffix('.json')
    assert calls == [saved]
