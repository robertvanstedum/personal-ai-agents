"""Provider-free response boundary contracts and downstream compatibility."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from domains.german import review_output as output
from domains.german import reviewer


def valid():
    return {
        'overall_summary': 'Grüße — gut gesprochen',
        'errors': [{'type': 'gender', 'original': 'die Tisch', 'correction': 'der Tisch',
                    'explanation': 'Masculine', 'context': 'Ich sehe die Tisch.'}],
        'error_type_counts': {key: int(key == 'gender') for key in output.ERROR_TYPES},
        'vocabulary_highlights': [{'german': 'Grüße', 'english': 'greetings',
                                   'note': 'new', 'tags': ['german', 'übung']}],
        'strengths': ['Clear pronunciation'], 'next_focus': 'Articles',
        'extension': {'nested': [1, {'two': 2}]},
    }


@pytest.mark.parametrize('wrap', [lambda s: s, lambda s: f'```json\n{s}\n```',
                                  lambda s: f'```\n{s}\n```',
                                  lambda s: f'Here is feedback:\n```json\n{s}\n```\nDone.'])
def test_valid_feedback_preserved(wrap):
    value = valid()
    assert output.parse_llm_response(wrap(json.dumps(value, ensure_ascii=False))) == (value, True)


@pytest.mark.parametrize('value', [None, [], 1, True, 'feedback', {}, {'errors': []}])
def test_non_review_json_uses_failure_path(value, capsys):
    result, ok = output.parse_llm_response(json.dumps(value))
    assert not ok
    assert result == output.empty_reviewer_output()
    assert 'parse failed' in capsys.readouterr().err


MUTATIONS = [
    ('overall_summary', None), ('next_focus', []), ('errors', {}),
    ('errors', [None]), ('errors', [{'type': 'unknown'}]),
    ('vocabulary_highlights', None), ('vocabulary_highlights', ['word']),
    ('strengths', 'good'), ('strengths', [{}]), ('error_type_counts', []),
]


@pytest.mark.parametrize('field,bad', MUTATIONS)
def test_wrong_top_level_fields(field, bad):
    value = valid(); value[field] = bad
    assert output.parse_llm_response(json.dumps(value))[1] is False


@pytest.mark.parametrize('field', ['overall_summary', 'next_focus', 'errors',
                                  'vocabulary_highlights', 'strengths', 'error_type_counts'])
def test_missing_required_fields(field):
    value = valid(); del value[field]
    assert not output.valid_review_output(value)


@pytest.mark.parametrize('field', ['original', 'correction', 'explanation', 'context'])
@pytest.mark.parametrize('bad', [None, [], 4])
def test_error_text_types(field, bad):
    value = valid(); value['errors'][0][field] = bad
    assert not output.parse_llm_response(json.dumps(value))[1]


@pytest.mark.parametrize('field', ['german', 'english', 'note'])
@pytest.mark.parametrize('bad', [None, [], 4])
def test_vocabulary_text_types(field, bad):
    value = valid(); value['vocabulary_highlights'][0][field] = bad
    assert not output.parse_llm_response(json.dumps(value))[1]


@pytest.mark.parametrize('bad', [None, 'german', [1], [{}]])
def test_tag_types(bad):
    value = valid(); value['vocabulary_highlights'][0]['tags'] = bad
    assert not output.parse_llm_response(json.dumps(value))[1]


@pytest.mark.parametrize('bad', [-1, True, False, 1.5, float('nan'), float('inf'), '1', None])
def test_counts_are_nonnegative_integers(bad):
    value = valid(); value['error_type_counts']['gender'] = bad
    assert not output.parse_llm_response(json.dumps(value))[1]


def test_exact_count_categories():
    value = valid(); value['error_type_counts']['surprise'] = 1
    assert not output.valid_review_output(value)
    del value['error_type_counts']['surprise']; del value['error_type_counts']['gender']
    assert not output.valid_review_output(value)


def test_fallback_is_fresh_and_compatible(capsys):
    first, ok = output.parse_llm_response('not JSON')
    assert not ok
    first['errors'].append('changed'); first['error_type_counts']['gender'] = 99
    second, ok = output.parse_llm_response('```json\n{broken}\n```')
    assert not ok and second == output.empty_reviewer_output()
    assert output.valid_review_output(second)
    assert reviewer._parse_llm_response is output.parse_llm_response
    assert reviewer._empty_reviewer_output is output.empty_reviewer_output
    assert reviewer._strip_llm_fences is output.strip_llm_fences


def test_valid_response_flows_through_anki_and_progress(tmp_path):
    value, ok = output.parse_llm_response(json.dumps(valid()))
    before = copy.deepcopy(value)
    session = {'date': '2026-10-28', 'scenario': 'synthetic', 'persona': 'Synthetic',
               'duration_estimate_min': 3}
    progress = reviewer._load_progress(tmp_path / 'missing.json')
    assert reviewer._generate_anki_csv(value, session, tmp_path, progress) == 1
    reviewer._update_progress(progress, session, value, 1)
    assert progress['cumulative_error_counts']['gender'] == 1
    assert progress['vocabulary_seen'] == ['Grüße']
    assert 'Grüße' in (tmp_path / '2026-10-28_anki.csv').read_text()
    assert value == before


def test_module_import_has_no_runtime_dependencies():
    root = Path(__file__).resolve().parents[1]
    code = f"import sys; sys.path.insert(0, {str(root)!r}); import domains.german.review_output; assert 'german_domain' not in sys.modules; assert 'anthropic' not in sys.modules"
    subprocess.run([sys.executable, '-B', '-I', '-S', '-c', code], check=True,
                   capture_output=True, env={'PATH': os.environ.get('PATH', '')})
