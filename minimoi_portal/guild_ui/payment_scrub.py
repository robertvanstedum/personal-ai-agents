"""Payment details are stripped at ingress, before any write or log (V2 §9).

Notes and post-its are free text the server now keeps, so every text-bearing
write passes through ``scrub`` before it reaches the floor store. Stripped
text reads ``[payment detail removed]``; several removals in a row read as one.

Order and patterns (V2 §9, the Grok review's refinements, and the B1c
reviews' tightening to real card, account and routing shapes):
- IBANs (two letters, two check digits, 11 to 30 more letters or digits);
- email addresses;
- US routing numbers: "routing", "ABA" or "RTN" followed by 9 digits;
- card numbers, before any brand rule: in a run of digit groups joined by
  single spaces or dashes, every stretch of whole groups that holds 13 to 19
  digits and either passes the Luhn check or has a standard card grouping
  (4-4-4-4, 4-6-5, 4-6-4, 4-4-4-4-3) is removed, so a card followed by a
  CVV, an expiry or another number is still caught. A single 13 to 19 digit
  group must pass Luhn, so an id or a millisecond time usually stays;
- a card brand word with the digit groups right beside it: "Visa 4242",
  "Mastercard - 1234", "Visa **** 4242", "4242 4242 4242 Visa". A bare
  brand word stays ("I like Visa", "we discover 3 bugs"), and so does a
  year after a plain space ("my visa 2027 renewal");
- "ending in 4242", "ending with 1234", "last four 1234", "last 4: 1234"
  (exactly four digits, so "ending in 30 minutes" stays);
- masked groups that stand alone before four digits: "•••• 1234",
  "**** 1234", "xxxx-1234" (not "**42**" or "FIXXX 100").

Cost. Callers cap every field before calling ``scrub`` (api.py: 2,000
characters at most), and the patterns start only at token boundaries.
"""
from __future__ import annotations

import re

REMOVED = "[payment detail removed]"

_BRAND = r"(?:visa|master\s?card|amex|american\s+express|discover|jcb|diners(?:\s+club)?|union\s?pay)"
_MASK_RUN = r"(?:[•*xX]{2,}+[\s\-]?+)++"
_FOUR = r"\d{4}(?!\d)"
_YEAR = r"(?:19|20)\d\d(?!\d)"
# The digit groups beside a brand word, joined by one space or dash; at most
# five more, as a card has, which keeps each try short.
_GROUPS_AFTER = r"(?:[ \-]\d{1,6}(?!\d)){0,5}"
_CARD_SHAPES = {(4, 4, 4, 4), (4, 6, 5), (4, 6, 4), (4, 4, 4, 4, 3)}


def _luhn_ok(digits: str) -> bool:
    total, double = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if double:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        double = not double
    return total % 10 == 0


def _is_card(groups: list[str]) -> bool:
    digits = "".join(groups)
    if not 13 <= len(digits) <= 19:
        return False
    return _luhn_ok(digits) or tuple(len(g) for g in groups) in _CARD_SHAPES


_DIGIT_RUN = re.compile(r"(?<!\d)\d++(?:[ \-]\d++)*+")
_GROUP = re.compile(r"\d+")


def _cards(match: re.Match) -> str:
    """Remove every card-shaped stretch of whole groups inside one digit run."""
    run = match.group(0)
    groups = [(m.start(), m.end()) for m in _GROUP.finditer(run)]
    if sum(e - s for s, e in groups) < 13:
        return run
    cuts, i = [], 0
    while i < len(groups):
        best, digits = None, 0
        for j in range(i, len(groups)):
            digits += groups[j][1] - groups[j][0]
            if digits > 19:
                break
            if digits >= 13 and _is_card([run[s:e] for s, e in groups[i:j + 1]]):
                best = j
        if best is None:
            i += 1
            continue
        cuts.append((groups[i][0], groups[best][1]))
        i = best + 1
    if not cuts:
        return run
    out, last = [], 0
    for s, e in cuts:
        out.append(run[last:s])
        out.append(REMOVED)
        last = e
    out.append(run[last:])
    return "".join(out)


_BEFORE = [
    # IBAN first, before its digits are taken by a digit rule.
    re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]++@[A-Za-z0-9\-]++(?:\.[A-Za-z0-9\-]++)++"),
    re.compile(r"(?<![A-Za-z])(?:routing|aba|rtn)(?:\s*+(?:number|no\.?|#))?\s*+[:#]?\s*+\d{9}(?!\d)", re.IGNORECASE),
]
_AFTER = [
    # Brand, then "ending in", a mask, or a separator, then four digits and the groups after them.
    re.compile(rf"(?<![A-Za-z]){_BRAND}(?![A-Za-z])(?:\s*+[:#\-–—•]\s*+|\s++ending\s++(?:in|with)\s++|\s*+{_MASK_RUN})"
               rf"(?:{_MASK_RUN})?{_FOUR}{_GROUPS_AFTER}", re.IGNORECASE),
    # Brand, a plain space, then four digits that are not a year, and the groups after them.
    re.compile(rf"(?<![A-Za-z]){_BRAND}(?![A-Za-z])\s++(?!{_YEAR}){_FOUR}{_GROUPS_AFTER}", re.IGNORECASE),
    # Digit groups ending in four digits, then the brand: "4242 4242 4242 Visa".
    re.compile(rf"(?<![\d\-])(?:\d{{1,6}}[ \-]){{0,5}}{_FOUR}[\s:#\-–—]*+{_BRAND}(?![A-Za-z])", re.IGNORECASE),
    re.compile(rf"(?<![A-Za-z])ending\s++(?:in|with)\s*+[:#]?\s*+{_FOUR}", re.IGNORECASE),
    re.compile(rf"(?<![A-Za-z])last\s++(?:four|4)(?:\s++digits)?\s*+[:#]?\s*+{_FOUR}", re.IGNORECASE),
    re.compile(rf"(?<!\S){_MASK_RUN}{_FOUR}"),
]
_REPEATS = re.compile(rf"{re.escape(REMOVED)}(?:[\s,;·\-]*+{re.escape(REMOVED)})++")
# A brand word left beside a removed card number goes with it.
_BRAND_BESIDE = re.compile(rf"(?<![A-Za-z]){_BRAND}[\s:#\-–—]*+{re.escape(REMOVED)}|{re.escape(REMOVED)}[\s:#\-–—]*+{_BRAND}(?![A-Za-z])",
                           re.IGNORECASE)


def scrub(text: str) -> str:
    """Return ``text`` with every payment detail replaced; idempotent."""
    out = str(text)
    for pattern in _BEFORE:
        out = pattern.sub(REMOVED, out)
    out = _DIGIT_RUN.sub(_cards, out)
    for pattern in _AFTER:
        out = pattern.sub(REMOVED, out)
    out = _BRAND_BESIDE.sub(REMOVED, out)
    return _REPEATS.sub(REMOVED, out)


def scrubbed(text: str) -> bool:
    return scrub(text) != text
