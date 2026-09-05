"""Deterministic, content-free approval matching. Pure functions, no I/O.

Approval is a two-step act on purpose. The first step proposes and pins: it
names the exact artifact, pins its digest, mints an identifier and sets a
deadline. The second step confirms *that* identifier. Nothing in between can
turn a warm remark into a decision.

The phrase set here is closed and small, and it is deliberately not a
sentiment model. "Looks good", "great" and "ship it" are encouragement, not
instructions; treating them as approval would mean recording a decision
Robert did not make, against text he may not have finished reading. A phrase
outside the set changes nothing at all — it does not partially match, it does
not ask a follow-up question of its own, and it never selects a pending.

Nothing here decides anything. It reports what a phrase means; a later gate
wires it to a conversation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .envelope import PROPOSED_STATES

#: The exact phrases that confirm each proposable state. Matching is on the
#: whole message, case-folded, with surrounding punctuation and whitespace
#: removed — never on a substring, so "I would not approve this yet" cannot
#: match "approve".
CONFIRMING_PHRASES: dict[str, frozenset[str]] = {
    "approved_text": frozenset(
        {
            "/approve",
            "approve",
            "approved",
            "yes approve it",
            "yes, approve it",
            "i approve this",
            "approve this text",
            "use this text",
        }
    ),
    "closed": frozenset(
        {
            "/close",
            "close",
            "close this",
            "do not apply",
            "don't apply",
            "do not send",
            "we are not applying",
        }
    ),
    "unresolved": frozenset(
        {
            "/unresolved",
            "leave it unresolved",
            "stop here",
            "park this",
            "leave this undecided",
        }
    ),
}

#: Encouragement that must never be read as a decision. Named explicitly so a
#: regression is visible as a change to this list rather than as a silent
#: behaviour change.
AMBIGUOUS_PHRASES: frozenset[str] = frozenset(
    {
        "looks good",
        "looks great",
        "great",
        "nice",
        "ship it",
        "send it",
        "lgtm",
        "perfect",
        "that works",
        "sounds good",
        "yes",
        "ok",
        "okay",
        "sure",
    }
)

_TRIM = re.compile(r"^[\s\"'“”‘’(\[]+|[\s\"'“”‘’)\]!.,;:]+$")
_COLLAPSE = re.compile(r"\s+")

MAX_PHRASE_CHARS = 120


def normalise(message: str) -> str:
    """Case-fold and trim one message to the form the phrase set is matched on."""
    if not isinstance(message, str):
        return ""
    text = _COLLAPSE.sub(" ", message.strip().casefold())
    previous = None
    while previous != text:
        previous = text
        text = _TRIM.sub("", text)
    return text


@dataclass(frozen=True)
class Match:
    """What one message means, if anything."""

    state: str | None
    ambiguous: bool

    @property
    def confirms(self) -> bool:
        """True only when the message confirms one exact proposable state."""
        return self.state is not None


def match(message: str) -> Match:
    """Classify one message against the closed phrase set."""
    text = normalise(message)
    if not text or len(text) > MAX_PHRASE_CHARS:
        return Match(state=None, ambiguous=False)
    if text in AMBIGUOUS_PHRASES:
        return Match(state=None, ambiguous=True)
    for state, phrases in CONFIRMING_PHRASES.items():
        if text in phrases:
            return Match(state=state, ambiguous=False)
    return Match(state=None, ambiguous=False)


def confirmation_sentence(proposed_state: str, pending_id: str) -> str:
    """The sentence the confirming step is asked to answer.

    It carries the identifier and the decision, never the text: a
    confirmation prompt that quoted the artifact would put a body somewhere a
    body is not allowed to be.
    """
    if proposed_state not in PROPOSED_STATES:
        raise ValueError("that is not a proposable state")
    verb = {
        "approved_text": "approve this exact text",
        "closed": "close this work item without applying it",
        "unresolved": "leave this work item unresolved",
    }[proposed_state]
    return f"Reply with the decision to {verb} ({pending_id})."


__all__ = [
    "AMBIGUOUS_PHRASES",
    "CONFIRMING_PHRASES",
    "MAX_PHRASE_CHARS",
    "Match",
    "confirmation_sentence",
    "match",
    "normalise",
]
