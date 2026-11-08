"""Robert's explicit "keep this here" (MC front door spec §4.3, decision Q6): it means this conversation, so no cross-surface
filing and no delegation. The model carries the judgement; this is the server's backstop for the plain phrasings. It only ever
refuses. It is never evidence that something may go ahead, so a phrasing it misses is left to the model, never permitted by it."""
from __future__ import annotations

import re

_APOSTROPHE = "['\u2019]?"
_THIS = r"(?:this|that|it|these|those)"
_PATTERNS = (
    rf"\bkeep\s+{_THIS}\s+(?:here|between\s+(?:us|ourselves)|to\s+(?:ourselves|yourself)|private|in\s+this\s+(?:chat|conversation))\b",
    rf"\b(?:don{_APOSTROPHE}t|do\s+not|never)\s+(?:send|share|pass|forward|file|record|log|hand)\b[^.!?\n]{{0,40}}\b(?:{_THIS}|anywhere|anyone|anybody)\b",
    r"\b(?:just|strictly)\s+between\s+us\b",
)
_KEEP = re.compile("|".join(_PATTERNS), re.I)


def says_keep_here(text) -> bool:
    return isinstance(text, str) and _KEEP.search(text) is not None
