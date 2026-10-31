"""Answer normalization and spelling feedback contracts without a dictionary service."""
import importlib
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture(params=['german_domain', 'domains.german.answer_text'])
def answers(request):
    module = importlib.import_module(request.param)
    return lambda name: getattr(module, '_' + name if request.param == 'german_domain' else name)


@pytest.mark.parametrize('text,expected', [
    ('Ich BIN  müde!', 'ich bin müde'),
    ('BEIM Bahnhof, im Zug.', 'bei dem bahnhof in dem zug'),
    ('am vom zum zur ans ins aufs ums', 'an dem von dem zu dem zu der an das in das auf das um das'),
    ('Immer zumachen Amtszeit', 'immer zumachen amtszeit'),
    ('Straße, Größe — fünf Grüße!', 'straße größe fünf grüße'),
    ('  ich\n\tgehe nach Hause. ', 'ich gehe nach hause'),
    ('E-Mail / Test_1', 'e mail test_1'),
    ('', ''), ('... ! ? ', ''),
])
def test_normalize_answer(answers, text, expected):
    assert answers('normalize_answer')(text) == expected


def test_expansion_preserves_unmatched_case_and_punctuation(answers):
    assert answers('expand_contractions')('BEIM Bahnhof, im Zug!') == 'bei dem Bahnhof, in dem Zug!'


@pytest.mark.parametrize('expected,result', [
    ('', "Check spelling: 'hauss' → 'haus'?"),
    ('das haus', "Check spelling: 'hauss' → 'haus'?"),
    ('das auto', None),
])
def test_spelling_suggestion_respects_expected_words(answers, monkeypatch, expected, result):
    class Checker:
        def __init__(self, *, language):
            assert language == 'de'
        def unknown(self, words):
            assert words == ['hauss']  # Two-letter words remain excluded.
            return {'hauss'}
        def candidates(self, word):
            assert word == 'hauss'
            return {'haus'}
    monkeypatch.setitem(sys.modules, 'spellchecker', SimpleNamespace(SpellChecker=Checker))
    assert answers('spell_feedback')('im hauss', expected) == result


@pytest.mark.parametrize('candidates', [None, set(), {'auto'}])
def test_spelling_ignores_missing_or_distant_candidates(answers, monkeypatch, candidates):
    class Checker:
        def __init__(self, **kwargs): pass
        def unknown(self, words): return {'hauss'}
        def candidates(self, word): return candidates
    monkeypatch.setitem(sys.modules, 'spellchecker', SimpleNamespace(SpellChecker=Checker))
    assert answers('spell_feedback')('hauss') is None


def test_spelling_is_optional_and_failures_return_no_feedback(answers, monkeypatch):
    monkeypatch.setitem(sys.modules, 'spellchecker', None)
    assert answers('spell_feedback')('hauss') is None
    def broken(**kwargs):
        raise RuntimeError('dictionary unavailable')
    monkeypatch.setitem(sys.modules, 'spellchecker', SimpleNamespace(SpellChecker=broken))
    assert answers('spell_feedback')('hauss') is None


def test_cold_import_needs_no_runtime_or_spelling_package(tmp_path):
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from domains.german.answer_text import normalize_answer
assert 'spellchecker' not in sys.modules
assert 'german_domain' not in sys.modules
assert 'domains.german.german_domain' not in sys.modules
assert 'core.get_secret' not in sys.modules
print(normalize_answer('Im Zug!'))
"""
    result = subprocess.run(
        [sys.executable, '-I', '-S', '-c', code, str(Path(__file__).resolve().parents[1])],
        cwd=tmp_path, text=True, capture_output=True, check=True,
    )
    assert result.stdout.strip() == 'in dem zug'


def test_legacy_exports_remain_identical():
    import german_domain
    from domains.german import answer_text
    for name in ('DE_CONTRACTIONS', 'DE_CONTRACTION_RE', 'expand_contractions',
                 'normalize_answer', 'spell_feedback'):
        assert getattr(german_domain, '_' + name) is getattr(answer_text, name)
