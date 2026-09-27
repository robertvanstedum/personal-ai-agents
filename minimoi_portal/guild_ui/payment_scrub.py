"""Payment details are stripped at ingress, before any write or log (V2 §9).

Notes and post-its are free text the server now keeps, so every text-bearing
write passes through ``scrub`` before it reaches the floor store. Stripped
text reads ``[payment detail removed]``; several removals in a row read as one.

Patterns (V2 §9, the Grok review's refinements, and the B1c review's
tightening to real card, account and routing shapes):
- a card brand word right beside a four-digit group: "Visa 4242",
  "Mastercard - 1234", "Visa **** 4242", "5555 Amex". A bare brand word
  stays ("I like Visa", "we discover 3 bugs"), and so does a year after a
  plain space ("my visa 2027 renewal");
- "ending in 4242", "ending with 1234", "last four 1234", "last 4: 1234"
  (exactly four digits, so "ending in 30 minutes" stays);
- masked groups that stand alone before four digits: "•••• 1234",
  "**** 1234", "xxxx-1234" (not "**42**" or "FIXXX 100");
- 13 to 19 digit runs, with or without spaces or dashes, that pass the
  Luhn check a card number passes (an id or a millisecond time usually not);
- email addresses;
- IBANs (two letters, two check digits, 11 to 30 more letters or digits);
- US routing numbers: "routing", "ABA" or "RTN" followed by 9 digits.

Cost. Callers cap every field before calling ``scrub`` (api.py), and every
pattern starts only at a token boundary (a look-behind), so a failed match
is abandoned at once and the work stays linear in the (capped) input.
"""
from __future__ import annotations

import re

REMOVED = "[payment detail removed]"

_BRAND = r"(?:visa|master\s?card|amex|american\s+express|discover|jcb|diners(?:\s+club)?|union\s?pay)"
_MASK_RUN = r"(?:[•*xX]{2,}+[\s\-]?+)++"
_FOUR = r"\d{4}(?!\d)"
_YEAR = r"(?:19|20)\d\d(?!\d)"


def _luhn_ok(digits: str) -> bool:
    total, double = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if double:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        double = not double
    return total % 10 == 0


def _card_run(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return REMOVED if 13 <= len(digits) <= 19 and _luhn_ok(digits) else match.group(0)


_PATTERNS = [
    # IBAN first, before its digits are taken by a digit rule.
    re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]++@[A-Za-z0-9\-]++(?:\.[A-Za-z0-9\-]++)++"),
    re.compile(r"(?<![A-Za-z])(?:routing|aba|rtn)(?:\s*+(?:number|no\.?|#))?\s*+[:#]?\s*+\d{9}(?!\d)", re.IGNORECASE),
    # Brand, then "ending in", a mask, or a separator, then four digits (a year only with one of those).
    re.compile(rf"(?<![A-Za-z]){_BRAND}(?![A-Za-z])(?:\s*+[:#\-–—•]\s*+|\s++ending\s++(?:in|with)\s++|\s*+{_MASK_RUN})"
               rf"(?:{_MASK_RUN})?{_FOUR}", re.IGNORECASE),
    re.compile(rf"(?<![A-Za-z]){_BRAND}(?![A-Za-z])\s++(?!{_YEAR}){_FOUR}", re.IGNORECASE),
    # Four digits, then the brand: "5555 Amex".
    re.compile(rf"(?<!\d){_FOUR}[\s:#\-–—]*+{_BRAND}(?![A-Za-z])", re.IGNORECASE),
    re.compile(rf"(?<![A-Za-z])ending\s++(?:in|with)\s*+[:#]?\s*+{_FOUR}", re.IGNORECASE),
    re.compile(rf"(?<![A-Za-z])last\s++(?:four|4)(?:\s++digits)?\s*+[:#]?\s*+{_FOUR}", re.IGNORECASE),
    re.compile(rf"(?<!\S){_MASK_RUN}{_FOUR}"),
]
_CARD_RUN = re.compile(r"(?<![\d\-])\d(?:[ \-]?+\d){12,18}(?![\d])")
_REPEATS = re.compile(rf"{re.escape(REMOVED)}(?:[\s,;·\-]*+{re.escape(REMOVED)})++")


def scrub(text: str) -> str:
    """Return ``text`` with every payment detail replaced; idempotent."""
    out = str(text)
    for pattern in _PATTERNS:
        out = pattern.sub(REMOVED, out)
    out = _CARD_RUN.sub(_card_run, out)
    return _REPEATS.sub(REMOVED, out)


def scrubbed(text: str) -> bool:
    return scrub(text) != text
