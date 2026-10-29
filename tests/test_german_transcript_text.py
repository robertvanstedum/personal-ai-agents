"""Freeze import and review parsing as distinct text contracts."""
import importlib

import pytest


@pytest.fixture(params=['legacy', 'module'])
def parsers(request):
    if request.param == 'module':
        module = importlib.import_module('domains.german.transcript_text')
        return module.parse_header, module.split_inline_turns, module.parse_import_turns, module.parse_review_turns
    command = importlib.import_module('parse_transcript')
    domain = importlib.import_module('german_domain')
    return command._parse_header, command._split_inline_turns, command._parse_turns, domain._parse_transcript_turns


@pytest.mark.parametrize('lines,expected', [
    ([], {}),
    (['Date: 2026-10-28', 'PERSONA: Anna', 'Ignored: no'], {'date': '2026-10-28', 'persona': 'Anna'}),
    (['Date: 2026-10-28 Persona: Anna Scenario: im Café Mode: voice'],
     {'date': '2026-10-28', 'persona': 'Anna', 'scenario': 'im Café', 'mode': 'voice'}),
    (['Drill: true Drill-session: 2 of 3 Duration: 5'], {'drill': 'true', 'drill-session': '2 of 3', 'duration': '5'}),
    (['Mode: voice', 'Mode: writing'], {'mode': 'writing'}),
    (['unstructured', 'Scenario: Uhrzeit 10:30'], {'scenario': 'Uhrzeit 10:30'}),
])
def test_header_fields(parsers, lines, expected):
    assert parsers[0](lines) == expected


@pytest.mark.parametrize('line,expected', [
    ('Anna: Guten Tag!', ['Anna: Guten Tag!']),
    ('Anna: Hallo. Robert: Guten Tag!', ['Anna: Hallo.', 'Robert: Guten Tag!']),
    ('Dr. Müller: Guten Tag! Anna: Hallo!', ['Dr. Müller: Guten Tag!', 'Anna: Hallo!']),
    ('Anna: Uhrzeit 10:30. Robert: Danke.', ['Anna: Uhrzeit 10:30.', 'Robert: Danke.']),
    ('robert: hallo. anna: tag.', ['robert: hallo. anna: tag.']),
])
def test_inline_speakers(parsers, line, expected):
    assert parsers[1](line) == expected


@pytest.mark.parametrize('lines,expected', [
    ([], []),
    (['not a turn', ': missing speaker', 'Anna:'], []),
    (['Anna: Hallo — schön!\u00a0Wie geht’s?', 'Robert: Gut – danke.'],
     [{'speaker': 'Anna', 'text': 'Hallo - schön! Wie geht’s?'}, {'speaker': 'Robert', 'text': 'Gut - danke.'}]),
    (['Anna: Hallo. Robert: Guten Tag!', 'continuation is ignored'],
     [{'speaker': 'Anna', 'text': 'Hallo.'}, {'speaker': 'Robert', 'text': 'Guten Tag!'}]),
    (['robot: 10:30', 'x' * 45 + ': content'],
     [{'speaker': 'robot', 'text': '10:30'}, {'speaker': 'x' * 45, 'text': 'content'}]),
])
def test_import_turns(parsers, lines, expected):
    assert parsers[2](lines) == expected


@pytest.mark.parametrize('text,expected', [
    ('', [{'speaker': 'Transcript', 'text': ''}]),
    ('Preface\nAnna: Hallo!\ncontinued here',
     [{'speaker': 'Transcript', 'text': 'Preface'}, {'speaker': 'Anna', 'text': 'Hallo! continued here'}]),
    ('Anna: Hallo — schön!\u00a0Wie geht’s?\nRobert: Gut.',
     [{'speaker': 'Anna', 'text': 'Hallo — schön!\u00a0Wie geht’s?'}, {'speaker': 'Robert', 'text': 'Gut.'}]),
    ('Anna: Hallo. Robert: Guten Tag!', [{'speaker': 'Anna', 'text': 'Hallo. Robert: Guten Tag!'}]),
    ('x' * 40 + ': content', [{'speaker': 'Transcript', 'text': 'x' * 40 + ': content'}]),
    ('Anna:\n: content', [{'speaker': 'Transcript', 'text': 'Anna: : content'}]),
])
def test_review_turns(parsers, text, expected):
    assert parsers[3](text) == expected


def test_compatibility_aliases_keep_identity():
    command = importlib.import_module('parse_transcript')
    domain = importlib.import_module('german_domain')
    module = importlib.import_module('domains.german.transcript_text')
    for old, new in [('_parse_header', 'parse_header'), ('_split_inline_turns', 'split_inline_turns'),
                     ('_parse_turns', 'parse_import_turns'), ('HEADER_FIELDS', 'HEADER_FIELDS'),
                     ('_INLINE_SPEAKER_RE', 'INLINE_SPEAKER_RE')]:
        assert getattr(command, old) is getattr(module, new)
    assert domain._parse_transcript_turns is module.parse_review_turns


def test_text_module_imports_without_runtime_or_site_packages():
    from pathlib import Path
    import subprocess
    import sys
    code = '''
import sys
sys.path.insert(0, sys.argv[1])
from domains.german.transcript_text import parse_import_turns
assert parse_import_turns(['Anna: Hallo. Robert: Guten Tag!']) == [
    {'speaker': 'Anna', 'text': 'Hallo.'}, {'speaker': 'Robert', 'text': 'Guten Tag!'}]
assert not any(name in sys.modules for name in (
    'german_domain', 'domains.german.german_domain', 'parse_transcript', 'core.get_secret'))
'''
    subprocess.run([sys.executable, '-I', '-S', '-c', code,
                    str(Path(__file__).resolve().parents[1])], check=True, capture_output=True, text=True)
