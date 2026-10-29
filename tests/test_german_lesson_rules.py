"""Lesson choice contracts, including current ordering and fallback behavior."""
import copy
import pytest
from domains.german import reviewer


@pytest.mark.parametrize('history,expected', [([], 'A'), (['A'], 'B'), (['B'], 'A'),
    (['A', 'B'], 'A'), (['A', 'B', 'A'], 'A'), (['A', 'B', 'Frau Berger'], 'A')])
def test_persona_choice_preserves_order_and_existing_fallback(history, expected):
    personas = [{'name': 'Frau Berger'}, {'name': 'A'}, {'name': 'B'}]
    progress = {'personas_practiced': history}
    before = copy.deepcopy((personas, progress))
    chosen = reviewer._pick_next_persona(personas, progress)
    assert chosen['name'] == expected
    assert chosen is next(p for p in personas if p['name'] == expected)
    assert (personas, progress) == before


@pytest.mark.parametrize('personas,expected', [([], None), ([{'name': 'Frau Berger'}], 'Frau Berger'),
                                           ([{'name': 'A'}], 'A')])
def test_persona_empty_and_single_options(personas, expected):
    selected = reviewer._pick_next_persona(personas, {'personas_practiced': ['A']})
    assert (selected['name'] if selected else None) == expected


@pytest.mark.parametrize('covered,expected', [([], 'one'), (['one'], 'two'), (['two'], 'one'),
                                           (['one', 'two'], 'one')])
def test_scenario_order_and_cycle(covered, expected):
    persona = {'scenarios': ['one', 'two']}
    progress = {'scenarios_covered': covered}
    before = copy.deepcopy((persona, progress))
    assert reviewer._pick_next_scenario(persona, progress) == expected
    assert (persona, progress) == before


def test_empty_scenarios_keep_existing_error():
    with pytest.raises(IndexError):
        reviewer._pick_next_scenario({'scenarios': []}, {})


@pytest.mark.parametrize('counts,expected', [({}, 'gender'), ({'vocabulary': 0}, 'gender'),
    ({'word_order': 2, 'gender': 2}, 'word_order'), ({'gender': 1, 'vocabulary': 3}, 'vocabulary')])
def test_top_error_default_and_tie_order(counts, expected):
    assert reviewer._top_error_type({'cumulative_error_counts': counts}) == expected


def test_carry_forward_filters_and_keeps_first_two_without_mutation():
    errors = [{'type': kind, 'original': f'old{i}', 'correction': f'neu{i}'}
              for i, kind in enumerate(['gender', 'word_order', 'gender', 'gender'])]
    before = copy.deepcopy(errors)
    assert reviewer._carry_forward_errors({'errors': errors}, 'gender', '2026-10-29') == [
        'neu0 (was: old0) — session 2026-10-29', 'neu2 (was: old2) — session 2026-10-29']
    assert reviewer._carry_forward_errors({}, 'gender', '2026-10-29') == []
    assert errors == before


def test_compatibility_exports_use_the_pure_module():
    from domains.german import lesson_rules
    for name in ['pick_next_persona', 'pick_next_scenario', 'top_error_type', 'carry_forward_errors']:
        assert getattr(reviewer, '_' + name) is getattr(lesson_rules, name)
    assert reviewer.ANCHOR_PERSONA == lesson_rules.ANCHOR_PERSONA


def test_lesson_module_imports_without_runtime_or_site_packages():
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    code = f"import sys; sys.path.insert(0, {str(root)!r}); import domains.german.lesson_rules; assert 'german_domain' not in sys.modules; assert 'domains.german.reviewer' not in sys.modules"
    subprocess.run([sys.executable, '-B', '-I', '-S', '-c', code], check=True, capture_output=True)
