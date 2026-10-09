"""Payment and identity numbers must not be kept (v0.6 D8; Codex's open item on Unit 2).

Detects, with the check digits that real numbers carry, so ordinary numbers are left alone:

- card numbers of 13 to 19 digits (spaces or hyphens between groups of four, or as 4-6-5 / 4-6-4-1), Luhn valid;
- IBANs of 15 to 34 characters, ISO 7064 mod 97 valid;
- US Social Security numbers written ``123-45-6789`` with a possible area, group and serial;
- a bank routing or account number named as such (``routing number 021000021``, ``account no: 12345678``).

A number glued to letters or hyphens (a UUID, a hash, a version) is never a card. ``scrub`` replaces each find in place and counts
them; ``found`` is the yes/no used to refuse free text. Nothing here logs or returns what matched.
"""
from __future__ import annotations

import re

REMOVED = "[payment detail removed]"

_CARD = re.compile(r"(?<![0-9A-Za-z-])(?:\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,4}|\d{4}[ -]\d{6}[ -]\d{4,5}|\d{13,19})(?![0-9A-Za-z-])")
_IBAN = re.compile(r"(?<![0-9A-Za-z])[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?(?![0-9A-Za-z])")
_SSN = re.compile(r"(?<![0-9A-Za-z-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![0-9A-Za-z-])")
_NAMED = re.compile(r"(?i)\b(?:routing|aba|account|acct|iban|swift|sort code)\s*(?:number|no\.?|num|#)?\s*[:=#]?\s*(\d[\d -]{5,}\d)")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _iban_ok(text: str) -> bool:
    flat = text.replace(" ", "")
    if not 15 <= len(flat) <= 34:
        return False
    moved = flat[4:] + flat[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


def _spans(text: str) -> list[tuple[int, int]]:
    out = []
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and len(set(digits)) > 1 and _luhn(digits):
            out.append(m.span())
    out += [m.span() for m in _IBAN.finditer(text) if _iban_ok(m.group())]
    out += [m.span() for m in _SSN.finditer(text)]
    out += [m.span(1) for m in _NAMED.finditer(text)]
    return sorted(out)


def found(text: str) -> bool:
    return bool(_spans(text))


def scrub(text: str) -> tuple[str, int]:
    """(text with each payment or identity number replaced, how many were replaced)."""
    pieces, last, count = [], 0, 0
    for start, end in _spans(text):
        if start < last:
            continue
        pieces.append(text[last:start])
        pieces.append(REMOVED)
        last, count = end, count + 1
    pieces.append(text[last:])
    return "".join(pieces), count
