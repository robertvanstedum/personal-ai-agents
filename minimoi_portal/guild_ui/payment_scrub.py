"""Payment details are stripped at ingress, before any write or log (V2 §9).

Notes and post-its are free text the server now keeps, so every text-bearing
write passes through ``scrub`` before it reaches the floor store. Stripped
text reads ``[payment detail removed]``; several removals in a row read as one.

Order and patterns (V2 §9, the Grok review's refinements, and the B1c
reviews' tightening to real card, account and routing shapes):
- IBANs (two letters, two check digits, 11 to 30 more letters or digits);
- email addresses;
- US routing numbers: "routing", "ABA" or "RTN" followed by 9 digits;
- card numbers, before any brand rule. Matching runs on a normalised copy:
  Unicode spaces (NBSP, thin, figure...) and zero-width characters count as
  a space, Unicode dashes and the minus sign as a dash, fullwidth digits as
  digits. In a run of digit groups, every stretch of whole groups that holds
  13 to 19 digits and is a card is removed, so a card followed by a CVV, an
  expiry or another number is still caught (see _is_card):
  * plain separators (one space or one dash), as before: it passes Luhn; a
    mistyped card (a standard grouping that fails Luhn) needs a card word
    near it ("card", "cc", "paid", a brand...);
  * wide separators (several spaces, a tab, a dot, a spaced dash): Luhn, one
    separator throughout, every group 3 digits or more and the first 4 or
    more;
  * weak separators (comma, slash, underscore, pipe, colon, semicolon,
    bullet, a line break): all that, and a card word near;
  * a single group: Luhn and a card start (3 to 6, or 22 to 27), or a card
    word near; a mistyped 15 or 16 digit group needs a card word.
  So IP lists, versions, prices, phone lists, port lists, dates, ids and
  timestamps stay (tested as a corpus);
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
# What joins the groups of a card (after normalising, see _NORMAL):
# - plain: one space or one dash, as on main;
# - wide: two to six spaces or tabs, a tab, a dot, or a dash with spaces round it;
# - weak: a comma, slash, underscore, pipe, colon, semicolon or bullet (with up
#   to two spaces round it), or a line break (with an indent).
_SEP = r"(?:[ \t]{0,2}+[\-_./,|:;•][ \t]{0,2}+|[ \t]{0,2}+\n[ \t]{0,4}+|[ \t]{1,6}+)"
# The digit groups beside a brand word; at most five more, as a card has,
# which keeps each try short.
_GROUPS_AFTER = rf"(?:{_SEP}\d{{1,6}}(?!\d)){{0,5}}"
_CARD_SHAPES = {(4, 4, 4, 4), (4, 6, 5), (4, 6, 4), (4, 4, 4, 4, 3)}
_NEAR = 40                            # characters either side searched for a card word

# Copy-paste separators, one character for one character (so positions hold):
# Unicode spaces become a space, line and paragraph separators a newline,
# Unicode dashes and the minus sign a dash, zero-width characters a space,
# fullwidth digits plain digits.
_NORMAL = {c: " " for c in "             "
                              "  　​‌‍⁠﻿\r\x0b\x0c"}
_NORMAL.update({c: "\n" for c in "  \x85"})
_NORMAL.update({c: "-" for c in "‐‑‒–—―−﹘﹣－"})
_NORMAL.update({chr(0xFF10 + d): str(d) for d in range(10)})
_NORMAL = str.maketrans(_NORMAL)


def _luhn_ok(digits: str) -> bool:
    total, double = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if double:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        double = not double
    return total % 10 == 0


def _starts_like_a_card(digits: str) -> bool:
    """The card ranges: 3 (Amex, JCB, Diners), 4 (Visa), 5 (Mastercard, Maestro),
    6 (Discover, UnionPay), and 22 to 27 (Mastercard 2-series, Mir)."""
    return digits[0] in "3456" or "22" <= digits[:2] <= "27"


def _sep_kind(sep: str) -> str | None:
    if sep in (" ", "-"):
        return "plain"
    core = sep.strip(" \t")
    if "\n" in sep:
        return "weak"
    if core == "":
        return "wide"                                   # several spaces, or a tab
    if core in ("-", "."):
        return "wide"
    if core in (",", "/", "_", "|", ":", ";", "•"):
        return "weak"
    return None


def _is_card(groups: list[str], seps: list[str], card_word_near: bool = False) -> bool:
    """Whether these whole groups, joined by these separators, are a card.

    - one group: it passes Luhn and starts like a card (or a card word is
      near); a single 15 or 16 digit group that fails Luhn (a mistyped card)
      needs a card word near;
    - plain separators (main's rule): it passes Luhn; or it has a standard
      card grouping and a card word is near (a mistyped card);
    - wide separators: the same one throughout, card-like groups (every group
      3 digits or more, the first 4 or more) and Luhn;
    - weak separators: all that, and a card word near.
    """
    digits = "".join(groups)
    if not 13 <= len(digits) <= 19:
        return False
    luhn = _luhn_ok(digits)
    shape = tuple(len(g) for g in groups)
    if len(groups) == 1:
        if luhn:
            return _starts_like_a_card(digits) or card_word_near
        return len(digits) in (15, 16) and card_word_near
    kinds = {_sep_kind(s) for s in seps}
    if kinds == {"plain"}:
        return luhn or (shape in _CARD_SHAPES and card_word_near)
    if None in kinds or len(set(seps)) != 1 or not luhn:
        return False
    if any(n < 3 for n in shape) or shape[0] < 4:
        return False
    return kinds == {"wide"} or card_word_near


_DIGIT_RUN = re.compile(rf"(?<!\d)\d++(?:{_SEP}\d++)*+")
_GROUP = re.compile(r"\d+")


def _cards(match: re.Match) -> str:
    """Remove every card-shaped stretch of whole groups inside one digit run."""
    run = match.group(0)
    text, start, end = match.string, match.start(), match.end()
    near = bool(_CARD_WORD.search(text, max(0, start - _NEAR), start) or _CARD_WORD.search(text, end, end + _NEAR))
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
            if digits >= 13:
                window = groups[i:j + 1]
                seps = [run[window[k][1]:window[k + 1][0]] for k in range(len(window) - 1)]
                if _is_card([run[s:e] for s, e in window], seps, near):
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
    re.compile(rf"(?<![\d\-])(?:\d{{1,6}}{_SEP}){{0,5}}{_FOUR}[\s:#\-–—]*+{_BRAND}(?![A-Za-z])", re.IGNORECASE),
    re.compile(rf"(?<![A-Za-z])ending\s++(?:in|with)\s*+[:#]?\s*+{_FOUR}", re.IGNORECASE),
    re.compile(rf"(?<![A-Za-z])last\s++(?:four|4)(?:\s++digits)?\s*+[:#]?\s*+{_FOUR}", re.IGNORECASE),
    re.compile(rf"(?<!\S){_MASK_RUN}{_FOUR}"),
]
_CARD_WORD = re.compile(rf"(?<![A-Za-z])(?:{_BRAND}|cards?|credit|debit|cc|cvv|cvc|exp(?:iry|ires)?|payment|paid|pay|billing)(?![A-Za-z])",
                        re.IGNORECASE)
_REPEATS = re.compile(rf"{re.escape(REMOVED)}(?:[\s,;·\-]*+{re.escape(REMOVED)})++")
# A brand word left beside a removed card number goes with it.
_BRAND_BESIDE = re.compile(rf"(?<![A-Za-z]){_BRAND}[\s:#\-–—]*+{re.escape(REMOVED)}|{re.escape(REMOVED)}[\s:#\-–—]*+{_BRAND}(?![A-Za-z])",
                           re.IGNORECASE)


def scrub(text: str) -> str:
    """Return ``text`` with every payment detail replaced; idempotent.

    Matching runs on a normalised copy (Unicode spaces, dashes, zero-width
    characters, fullwidth digits; see _NORMAL). Text with nothing to remove
    comes back exactly as given; text with a removal comes back normalised."""
    original = str(text)
    out = original.translate(_NORMAL)
    normalised = out
    for pattern in _BEFORE:
        out = pattern.sub(REMOVED, out)
    out = _DIGIT_RUN.sub(_cards, out)
    for pattern in _AFTER:
        out = pattern.sub(REMOVED, out)
    out = _BRAND_BESIDE.sub(REMOVED, out)
    out = _REPEATS.sub(REMOVED, out)
    return original if out == normalised else out


def scrubbed(text: str) -> bool:
    return scrub(text) != text
