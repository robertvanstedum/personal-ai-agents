"""Frozen drill behavior shared by the domain's compatibility API and its module."""
import copy
import importlib
import itertools
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture(params=['german_domain', 'domains.german.drills'])
def drills(request):
    module = importlib.import_module(request.param)
    # The old facade retains private names; the extracted module has public names.
    def operation(name):
        return getattr(module, name if request.param.endswith('.drills') else '_' + name)
    return operation


ENTRY = {
    'verb': 'gehen', 'english': 'to go',
    'conjugations': {'ich': 'gehe', 'du': 'gehst', 'er': 'geht',
                     'wir': 'gehen', 'ihr': 'geht', 'sie': 'gehen'},
}


def test_start_state_and_prompts(drills, monkeypatch):
    import random
    monkeypatch.setattr(random, 'sample', lambda people, count: list(reversed(people)))
    entry = copy.deepcopy(ENTRY)
    state = drills('start_drill_state')(entry)
    assert state == {
        'verb': 'gehen', 'english': 'to go', 'conjugations': ENTRY['conjugations'],
        'queue': ['sie', 'ihr', 'wir', 'er', 'du', 'ich'], 'pos': 0, 'current': 'sie',
        'score': 0, 'total': 0, 'retry': False, 'items': [],
        'hint_used_current': False, 'l1_worst_tag': 'drill-clean',
    }
    assert state['conjugations'] is entry['conjugations']
    assert drills('drill_prompt')(state) == 'gehen (to go) — 1/6\n\nsie ___?'
    assert drills('l2_prompt')({'pos': 1, 'queue': [1, 0], 'phrases': [
        {'english': 'I go home.'}, {'english': 'We go.'},
    ]}) == '2/2\n\nHow do you say:\n"I go home."'


@pytest.mark.parametrize('wrong,hint,reveal', list(itertools.product(range(4), [False, True], [False, True])))
def test_scoring_and_phrase_card_payload(drills, wrong, hint, reveal):
    expected = ('needs-practice' if hint or reveal or wrong >= 2 else
                'drill-reinforced' if wrong == 1 else 'drill-clean')
    assert drills('item_tag')(wrong, hint, reveal) == expected
    state = {'items': [], 'hint_used_current': hint, 'verb': 'gehen'}
    phrase = {'english': 'I go.', 'german': 'Ich gehe.'}
    drills('record_l2_item')(state, phrase, wrong, reveal)
    assert state == {'items': [{
        'front': 'I go.', 'back': 'Ich gehe.', 'result': expected,
        'tags': f'{expected} Vienna phrase gehen',
    }], 'hint_used_current': False, 'verb': 'gehen'}
    assert phrase == {'english': 'I go.', 'german': 'Ich gehe.'}


def test_level_one_worst_result_never_downgrades_and_preserves_cards(drills):
    state = drills('start_drill_state')(copy.deepcopy(ENTRY))
    drills('finalize_l1_items')(state)
    assert state['items'] == []
    drills('record_l1_person')(state, 1, False)
    assert state['l1_worst_tag'] == 'drill-reinforced'
    state['hint_used_current'] = True
    drills('record_l1_person')(state, 0, False)
    drills('record_l1_person')(state, 0, False)
    assert state['l1_worst_tag'] == 'needs-practice'
    assert state['hint_used_current'] is False
    drills('finalize_l1_items')(state)
    assert state['items'] == [{
        'front': 'gehen — to go',
        'back': 'ich gehe / du gehst / er/sie/es geht / wir gehen / ihr geht / sie/Sie gehen',
        'result': 'needs-practice', 'tags': 'needs-practice Vienna conjugation',
    }]


def test_missing_optional_fields_and_append_semantics(drills):
    state = drills('start_drill_state')({'verb': 'unknown'})
    assert state['english'] == ''
    assert state['conjugations'] == {}
    state['l1_worst_tag'] = 'drill-reinforced'
    drills('finalize_l1_items')(state)
    assert state['items'][0]['back'] == 'ich ? / du ? / er/sie/es ? / wir ? / ihr ? / sie/Sie ?'
    drills('finalize_l1_items')(state)
    assert len(state['items']) == 2  # Preserve existing append behavior; callers own finalization.
    assert state['items'][0] == state['items'][1]


def test_required_input_still_refuses_missing_verb(drills):
    with pytest.raises(KeyError, match='verb'):
        drills('start_drill_state')({})


def test_drills_import_without_domain_runtime_or_site_packages():
    root = str(Path(__file__).resolve().parents[1])
    code = """
import json, sys
sys.path.insert(0, sys.argv[1])
from domains.german.drills import start_drill_state
assert 'german_domain' not in sys.modules
assert 'domains.german.german_domain' not in sys.modules
assert 'core.get_secret' not in sys.modules
print(json.dumps(start_drill_state({'verb': 'gehen'})['queue']))
"""
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code, root],
                            text=True, capture_output=True, check=True)
    assert sorted(json.loads(result.stdout)) == ['du', 'er', 'ich', 'ihr', 'sie', 'wir']


def test_legacy_exports_are_the_same_operations():
    import german_domain
    from domains.german import drills
    for name in ('item_tag', 'drill_prompt', 'l2_prompt', 'start_drill_state',
                 'record_l2_item', 'record_l1_person', 'finalize_l1_items',
                 'DRILL_PERSONS', 'PERSONS_DISPLAY', 'PERSONS_POOL'):
        assert getattr(german_domain, '_' + name) is getattr(drills, name)
