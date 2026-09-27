"""Payment details are stripped at ingress, before any write or log (V2 §9).

Notes and post-its are free text the server now keeps, so every text-bearing
write passes through ``scrub`` before it reaches the floor store. Stripped
text reads ``[payment detail removed]``.

Patterns (V2 §9, with the Grok review's refinements):
- card brand words, only when a digit group sits right beside them
  ("Visa 4242", "Mastercard - 1234", "1234 Amex"); the bare word stays;
- "ending in 42", "ending with 1234", "last four 1234", "last 4: 1234";
- masked groups: "•••• 1234", "**** 1234", "xxxx-1234";
- 13 to 19 digit runs, with or without spaces or dashes;
- email addresses;
- IBANs (two letters, two check digits, 11 to 30 more letters or digits);
- US routing numbers: "routing", "ABA" or "RTN" followed by 9 digits.
"""
from __future__ import annotations

import re

REMOVED = "[payment detail removed]"

_BRAND = r"(?:visa|master\s?card|amex|american\s+express|discover|jcb|diners(?:\s+club)?|union\s?pay)"
_SEP = r"[\s:#.\-–—]*+"
_MASK = r"(?:[•*xX]{2,}+" + _SEP + r")*+"

# Possessive quantifiers (*+, ++, {2,}+) keep every pattern free of
# backtracking, so a long run of x, * or digits cannot stall a request
# (tested with 2,000-character inputs).
_PATTERNS = [
    # IBAN first, before its digits are taken by the long-run rule.
    re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b"),
    re.compile(r"[A-Za-z0-9._%+\-]++@[A-Za-z0-9\-]++(?:\.[A-Za-z0-9\-]++)++"),
    re.compile(r"\b(?:routing|aba|rtn)(?:\s*+(?:number|no\.?|#))?\s*+[:#]?\s*+\d{9}\b", re.IGNORECASE),
    re.compile(rf"\b{_BRAND}\b{_SEP}(?:ending\s++(?:in|with)\s*+)?{_MASK}\d(?:[\s\-]?+\d)*+", re.IGNORECASE),
    re.compile(rf"\b\d[\d\s\-]*+[:#.–—]*+\s*+{_BRAND}\b", re.IGNORECASE),
    re.compile(r"\bending\s++(?:in|with)\s*+[:#]?\s*+\d{2,4}\b", re.IGNORECASE),
    re.compile(r"\blast\s++(?:four|4)(?:\s++digits)?\s*+[:#]?\s*+\d{2,4}\b", re.IGNORECASE),
    re.compile(r"[•*xX]{2,}+(?:[\s\-]++[•*xX]++)*+[\s\-]*+\d{2,4}\b"),
    re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)"),
]


def scrub(text: str) -> str:
    """Return ``text`` with every payment detail replaced; idempotent."""
    out = str(text)
    for pattern in _PATTERNS:
        out = pattern.sub(REMOVED, out)
    return out


def scrubbed(text: str) -> bool:
    return scrub(text) != text
