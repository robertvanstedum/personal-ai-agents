"""German answer text normalization and optional spelling feedback.

Importing the module requires only the standard library. The optional German
spelling dictionary is loaded only when feedback is requested; failures retain
the established no-feedback result. No learner state or provider access.
"""
import re


DE_CONTRACTIONS = {
    "beim": "bei dem",
    "im":   "in dem",
    "am":   "an dem",
    "vom":  "von dem",
    "zum":  "zu dem",
    "zur":  "zu der",
    "ans":  "an das",
    "ins":  "in das",
    "aufs": "auf das",
    "ums":  "um das",
}


DE_CONTRACTION_RE = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in DE_CONTRACTIONS) + r')\b', re.IGNORECASE
)


def expand_contractions(text: str) -> str:
    """Expand German preposition+article contractions to canonical long form."""
    return DE_CONTRACTION_RE.sub(lambda m: DE_CONTRACTIONS[m.group(0).lower()], text)


def normalize_answer(text: str) -> str:
    """Lowercase, strip punctuation, expand contractions, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = expand_contractions(text)
    return " ".join(text.split())


def spell_feedback(normalized_answer: str, expected: str = "") -> str | None:
    """Return feedback for misspelled German words. Input must already be normalized (no punctuation, lowercase).
    If expected is given, only suggest corrections that appear in the expected answer."""
    try:
        from spellchecker import SpellChecker
        from difflib import SequenceMatcher
        checker = SpellChecker(language="de")
        expected_words = set(expected.split()) if expected else set()
        words = [w for w in normalized_answer.split() if len(w) >= 3]
        unknown = checker.unknown(words)
        if not unknown:
            return None
        parts = []
        for w in unknown:
            candidates = checker.candidates(w) or set()
            best = max(candidates, key=lambda c: SequenceMatcher(None, w, c).ratio(), default=None)
            if not best or SequenceMatcher(None, w, best).ratio() < 0.80:
                continue
            if expected_words and best not in expected_words:
                continue
            parts.append(f"'{w}' → '{best}'?")
        return ("Check spelling: " + ", ".join(parts)) if parts else None
    except Exception:
        return None
