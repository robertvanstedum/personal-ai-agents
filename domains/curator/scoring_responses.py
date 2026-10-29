"""Validate model scores against the submitted batch's zero-based indexes.

Rows never acquire identity from their position in a response. Any second claim
on an index makes that index ambiguous permanently, even when the first claim
was malformed. Adapters retain their own missing-score and transport fallbacks.
"""
import math

from domains.curator.selection_rules import CATEGORIES


def _parse(text, count, *, categories, lowercase):
    scores = {}
    seen = set()
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) < 2:
            continue
        try:
            index = int(parts[0].strip())
        except ValueError:
            continue
        if not 0 <= index < count:
            continue
        if index in seen:
            scores.pop(index, None)
            continue
        seen.add(index)
        if len(parts) != (3 if categories else 2):
            continue
        try:
            score = float(parts[-1].strip())
        except ValueError:
            continue
        if not math.isfinite(score) or not 0 <= score <= 10:
            continue
        if categories:
            category = parts[1].strip()
            if lowercase:
                category = category.lower()
            if category not in CATEGORIES:
                continue
            scores[index] = {"category": category, "score": score}
        else:
            scores[index] = score
    return scores


def category_scores(text, count, *, lowercase=False):
    """Return valid category/score pairs; missing indexes are the caller's concern."""
    return _parse(text, count, categories=True, lowercase=lowercase)


def ranking_scores(text, count):
    """Return valid Sonnet two-column scores without inventing categories."""
    return _parse(text, count, categories=False, lowercase=False)
