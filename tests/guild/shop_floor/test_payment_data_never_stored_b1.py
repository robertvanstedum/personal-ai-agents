"""V2 §9 for B1 (c): payment details typed into a note or a post-it never reach
a row, a log, an API answer or a page. Synthetic values only."""
from __future__ import annotations

import logging
import time

import pytest

from minimoi_portal.guild_ui.payment_scrub import REMOVED, scrub

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

API = "/guild-next/api/v1"
SYNTHETIC = [
    "Mastercard - 1234",
    "Visa ending in 4242",
    "4242 4242 4242 4242",
    "4242-4242-4242-4242",
    "billing@example.com",
    "Visa **** **** 4242",
    "•••• 9876",
    "xxxx-5555",
    "ending with 1881",
    "last four 7777",
    "GB82 WEST 1234 5698 7654 32",
    "routing 021000021",
    "ABA # 021000021",
    "5555 Amex",
]
RAW_BITS = ["1234", "4242", "billing@example.com", "9876", "5555", "1881", "7777", "GB82", "021000021"]


@pytest.mark.parametrize("text", SYNTHETIC)
def test_each_pattern_is_removed(text):
    out = scrub(f"note: {text} ok")
    assert REMOVED in out and out.startswith("note: ") and out.endswith(" ok")
    for bit in RAW_BITS:
        if bit in text:
            assert bit not in out, (text, out)


@pytest.mark.parametrize("text", ["I like Visa", "Discover what blocks #31", "see #12 at 10:42 on 2026-09-27",
                                  "Queue item 158 is in build", "ping x@y later", "x" * 140,
                                  # review B1c #6: ordinary build notes stay as written
                                  "sprint ending in 30 minutes", "we discover 3 bugs", "Step 2 discover the cause",
                                  "My visa 2027 renewal", "**42** items", "FIXXX 100",
                                  "ts 1727450000123", "run id 20260927103000123"])
def test_ordinary_text_is_left_alone(text):
    assert scrub(text) == text


def test_a_card_number_is_removed_only_when_it_passes_the_card_check():
    from minimoi_portal.guild_ui.payment_scrub import _luhn_ok
    assert _luhn_ok("4242424242424242") and not _luhn_ok("1727450000123")
    assert scrub("card 4000 0566 5566 5556 ok") == f"card {REMOVED} ok"


def test_scrub_is_idempotent():
    once = scrub(" · ".join(SYNTHETIC))
    assert scrub(once) == once


@pytest.mark.parametrize("text", ["x" * 2000, "*" * 2000, "Visa " + "x" * 2000, "1 " * 1000, "Visa " + "* " * 999,
                                  "1-" * 1000 + "Visa", "a@" * 1000, "xx " * 700, "ending in " * 200,
                                  "a" * 32000, "a@" * 16000, "1" * 32000])
def test_scrub_is_fast_on_hostile_input(text):
    start = time.perf_counter()
    scrub(text)
    assert time.perf_counter() - start < 0.5


def test_payment_details_never_reach_a_row_a_log_an_answer_or_a_page(floored, caplog):
    db = floored.extra["floor"]
    client = floored.owner()
    token = floored.csrf(client)
    answers = []
    with caplog.at_level(logging.DEBUG):
        for n, text in enumerate(SYNTHETIC):
            note = client.post(f"{API}/notes", json=keyed(text=f"paid with {text} today",
                                                          context={"area": f"Build {text}", "page": text}),
                               headers=write_headers(token))
            assert note.status_code == 200, note.get_json()
            answers.append(note.get_data(as_text=True))
            postit = client.post(f"{API}/postits", json=keyed(text=f"{text}"[:140]), headers=write_headers(token))
            assert postit.status_code == 200, postit.get_json()
            answers.append(postit.get_data(as_text=True))
        for url in (f"{API}/notes", f"{API}/postits", f"{API}/floor", "/guild-next/guild/build",
                    "/guild-next/guild/build/postits", "/guild-next/guild/build/bench"):
            answers.append(client.get(url).get_data(as_text=True))
    stored = db.all_text()
    haystacks = {"rows": stored, "logs": caplog.text, "answers": "\n".join(answers)}
    for where, hay in haystacks.items():
        for text in SYNTHETIC:
            assert text not in hay, (where, text)
        for bit in ("4242 4242", "billing@example.com", "021000021", "GB82 WEST"):
            assert bit not in hay, (where, bit)
    assert REMOVED in stored


# ── Re-check at 3d75415e, R1: full card numbers must never survive ───────────
import re as _re  # noqa: E402

LEAKS = [
    "Visa 4242 4242 4242 4242",
    "Mastercard 5555 5555 5555 4444",
    "visa 4242-4242-4242-4242",
    "4242 4242 4242 4242 Visa",
    "Amex 3782 822463 10005",
    "4242 4242 4242 4242 123",
    "card 4242 4242 4242 4242 12/27",
    "Visa 4242 4242 4242",
    "4242 4242 4242 Visa",
    "paid 4242 4242 4242 4242 and 5555 5555 5555 4444 ok",
    "order -4242424242424242 today",
]


def _old_scrub_c9ea596c(text: str) -> str:
    """The scrub as it was at c9ea596c, frozen here as the baseline: whatever it
    removed from a card number must stay removed."""
    brand = r"(?:visa|master\s?card|amex|american\s+express|discover|jcb|diners(?:\s+club)?|union\s?pay)"
    sep = r"[\s:#.\-–—]*+"
    mask = r"(?:[•*xX]{2,}+" + sep + r")*+"
    patterns = [
        _re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b"),
        _re.compile(r"[A-Za-z0-9._%+\-]++@[A-Za-z0-9\-]++(?:\.[A-Za-z0-9\-]++)++"),
        _re.compile(r"\b(?:routing|aba|rtn)(?:\s*+(?:number|no\.?|#))?\s*+[:#]?\s*+\d{9}\b", _re.IGNORECASE),
        _re.compile(rf"\b{brand}\b{sep}(?:ending\s++(?:in|with)\s*+)?{mask}\d(?:[\s\-]?+\d)*+", _re.IGNORECASE),
        _re.compile(rf"\b\d[\d\s\-]*+[:#.–—]*+\s*+{brand}\b", _re.IGNORECASE),
        _re.compile(r"\bending\s++(?:in|with)\s*+[:#]?\s*+\d{2,4}\b", _re.IGNORECASE),
        _re.compile(r"\blast\s++(?:four|4)(?:\s++digits)?\s*+[:#]?\s*+\d{2,4}\b", _re.IGNORECASE),
        _re.compile(r"[•*xX]{2,}+(?:[\s\-]++[•*xX]++)*+[\s\-]*+\d{2,4}\b"),
        _re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)"),
    ]
    for pattern in patterns:
        text = pattern.sub(REMOVED, text)
    return text


def _card_digits_left(text: str) -> list[str]:
    return _re.findall(r"\d{4,}", text)


@pytest.mark.parametrize("text", LEAKS)
def test_no_group_of_a_card_number_survives(text):
    out = scrub(text)
    assert _card_digits_left(out) == [], (text, out)
    assert REMOVED in out


@pytest.mark.parametrize("text", LEAKS)
def test_nothing_the_old_scrub_removed_comes_back(text):
    old, new = _old_scrub_c9ea596c(text), scrub(text)
    assert _card_digits_left(old) == []                    # the baseline removed every group
    assert set(_card_digits_left(new)) <= set(_card_digits_left(old)), (text, old, new)


def test_a_card_is_found_inside_a_longer_run_of_groups():
    assert scrub("ref 12 4242 4242 4242 4242 99") == f"ref 12 {REMOVED} 99"
    assert scrub("Amex 3782 822463 10005 exp 09/28") == f"{REMOVED} exp 09/28"


def test_ordinary_numbers_next_to_each_other_stay():
    for text in ("order 1234 5678 then 99", "build 2026 0927 steps", "ts 1727450000123 and 1727450000999"):
        assert scrub(text) == text


# ── #242: wider card-group separators, mistyped cards, fewer false positives ──
# Values are the well-known synthetic test numbers. Test ids are indexes and
# failure messages mask every digit, so no card-like number is printed.

def _masked(text: str) -> str:
    return _re.sub(r"\d", "#", text)


_CARD_GROUPS = [["4242", "4242", "4242", "4242"], ["5555", "5555", "5555", "4444"], ["3782", "822463", "10005"],
                ["4000", "0566", "5566", "5556"]]
_WIDE_SEPARATORS = [".", "  ", " - ", "\n", "\t", "–", "—", "_", "/", ",", ", ", " . ", "\r\n", "-  "]
LEAKS_WIDE = [f"paid {sep.join(g)} today" for sep in _WIDE_SEPARATORS for g in _CARD_GROUPS] + [
    f"Visa {'.'.join(_CARD_GROUPS[0][:3])}",               # 12 digits beside a brand: the brand rule, widened
    f"{'/'.join(_CARD_GROUPS[0][:3])} Visa",
    "card 4242 4242 4242 4243",                             # mistyped (fails Luhn), grouped
    "4242 4242 4242 4243",                                  # mistyped, starts like a card, no word needed
    "4242424242424243",                                     # mistyped, a single 16-digit group
    "378282246310006",                                      # mistyped, a single 15-digit group
    "cc 1234.5678.9012.3456",                               # a card grouping with a card word near
]


@pytest.mark.parametrize("n", range(len(LEAKS_WIDE)))
def test_no_group_of_a_card_survives_any_separator(n):
    text = LEAKS_WIDE[n]
    out = scrub(text)
    assert _card_digits_left(out) == [], _masked(out)
    assert REMOVED in out and scrub(out) == out, _masked(out)


STAYS = [
    "ports 8766 8767 8768 8769",                            # a card grouping, no Luhn, no card start, no card word
    "ports 8766, 8767, 8768, 8769",
    "ports 8080\n8081\n8082\n8083",
    "ids 1234-5678-9012-3457 in the log",
    "micros 1727450000123456",                              # a 16-digit time: starts with 1
    "release 2026.2027.2028.2029",
    "v1.2.3 on 2026/09/27 at 10:42",
    "call 312.555.0100 or 312-555-0199",
    "invoice 1,234,567 and 7,654,321",
    "run id 20260927103000123",
]


@pytest.mark.parametrize("n", range(len(STAYS)))
def test_ordinary_numbers_stay_with_the_wider_separators(n):
    assert scrub(STAYS[n]) == STAYS[n], _masked(scrub(STAYS[n]))


@pytest.mark.parametrize("text", ["1." * 1000, "1 - " * 500, "1\n" * 1000, "4242, " * 400, "Visa " + "1/" * 1000,
                                  "1_2" * 700, "1 , " * 500], ids=lambda s: f"{len(s)}-chars")
def test_scrub_stays_fast_with_the_wider_separators(text):
    start = time.perf_counter()
    scrub(text)
    assert time.perf_counter() - start < 0.5
