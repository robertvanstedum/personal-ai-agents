"""V2 §9 for B1 (c): payment details typed into a note or a post-it never reach
a row, a log, an API answer or a page. Synthetic values only."""
from __future__ import annotations

import logging
import re
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


def _id(value) -> str:
    """A test id that never prints a card-like number: every digit masked."""
    return re.sub(r"\d", "#", str(value))[:48]


@pytest.mark.parametrize("text", SYNTHETIC, ids=_id)
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
                                  "ts 1727450000123", "run id 20260927103000123"], ids=_id)
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
                                  "a" * 32000, "a@" * 16000, "1" * 32000], ids=lambda s: f"{len(s)}-chars-{_id(s[:6])}")
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


@pytest.mark.parametrize("text", LEAKS, ids=_id)
def test_no_group_of_a_card_number_survives(text):
    out = scrub(text)
    assert _card_digits_left(out) == [], (text, out)
    assert REMOVED in out


@pytest.mark.parametrize("text", LEAKS, ids=_id)
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
# Values are the well-known synthetic test numbers. Test ids are indexes or
# masked, and failure messages mask every digit, so no card-like number is
# printed.
import random  # noqa: E402


def _masked(text: str) -> str:
    return _re.sub(r"\d", "#", text)


def _mask_id(value) -> str:
    return _masked(str(value))[:60]


# Luhn-valid synthetic cards: Visa, Mastercard, Amex, Visa, Mastercard 2-series.
_CARD_GROUPS = [["4242", "4242", "4242", "4242"], ["5555", "5555", "5555", "4444"], ["3782", "822463", "10005"],
                ["4000", "0566", "5566", "5556"], ["2223", "0031", "2200", "3222"]]
# Separators a card is copied or typed with. No card word is needed with these.
_STRONG = [" ", "-", " ", " ", " ", " ", "　", "−", "‐", "–", "—",
           "​", "  ", "    ", "\t", ".", " - ", " . ", "-  "]
# Separators that also join ordinary lists: a card word must be near.
_WEAK = [",", ", ", "/", "_", "|", "\n", "\n    ", "\r\n", ";", ":"]
LEAKS_WIDE = ([f"note {sep.join(g)} today" for sep in _STRONG for g in _CARD_GROUPS]
              + [f"paid {sep.join(g)} today" for sep in _WEAK for g in _CARD_GROUPS]
              + [f"Visa {'.'.join(_CARD_GROUPS[0][:3])}", f"{'/'.join(_CARD_GROUPS[0][:3])} Visa",
                 "４２４２ 4242 4242 4242",                   # fullwidth digits
                 "card 4242 4242 4242 4243",                                # mistyped, with a card word
                 "card no 4242424242424243", "Amex 378282246310006",        # mistyped single groups, with a card word
                 "card 2223 0031 2200 3223"])                               # mistyped 2-series, with a card word


@pytest.mark.parametrize("n", range(len(LEAKS_WIDE)))
def test_no_group_of_a_card_survives_any_separator(n):
    text = LEAKS_WIDE[n]
    out = scrub(text)
    assert _card_digits_left(out) == [] and not _re.search(r"\d{3,}", out.replace("today", "")), _masked(out)
    assert REMOVED in out and scrub(out) == out, _masked(out)


# Kept on purpose (documented): a mistyped card with no card word near, and a
# number that fails Luhn with a wide separator (wide separators need Luhn).
KEPT_WITHOUT_A_CARD_WORD = ["4242 4242 4242 4243", "4242424242424243"]


@pytest.mark.parametrize("n", range(len(KEPT_WITHOUT_A_CARD_WORD)))
def test_a_mistyped_card_needs_a_card_word(n):
    text = KEPT_WITHOUT_A_CARD_WORD[n]
    assert scrub(text) == text, _masked(scrub(text))
    assert REMOVED in scrub(f"card {text}"), _masked(scrub(f"card {text}"))


def test_a_wide_separator_needs_a_luhn_valid_number():
    for text in ("4242.4242.4242.4243", "card 4242.4242.4242.4243", "paid 4242, 4242, 4242, 4243"):
        assert scrub(text) == text, _masked(scrub(text))


# ── The false-positive corpus: ordinary technical text, 0% altered ────────────

def _corpus():
    rnd = random.Random(242)
    ip = lambda: ".".join(str(rnd.randint(0, 255)) for _ in range(4))                       # noqa: E731
    ver = lambda: f"{rnd.randint(0, 20)}.{rnd.randint(0, 40)}.{rnd.randint(0, 99)}"         # noqa: E731
    price = lambda: f"{rnd.randint(1, 999)}.{rnd.randint(0, 99):02d}"                       # noqa: E731
    phone = lambda: f"{rnd.randint(200, 999)}-{rnd.randint(200, 999)}-{rnd.randint(0, 9999):04d}"  # noqa: E731
    date = lambda: f"{rnd.randint(1990, 2035)}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"  # noqa: E731
    port = lambda: str(rnd.randint(1000, 9999))                                             # noqa: E731
    ident = lambda: str(rnd.randint(1000, 999999))                                          # noqa: E731
    ms = lambda: str(rnd.randint(1_500_000_000_000, 1_999_999_999_999))                     # noqa: E731
    us = lambda: str(rnd.randint(1_500_000_000_000_000, 1_999_999_999_999_999))             # noqa: E731
    stamp_id = lambda: f"{rnd.randint(2020, 2035)}{rnd.randint(1, 12):02d}{rnd.randint(1, 28):02d}{rnd.randint(0, 99999999):08d}"  # noqa: E731
    shapes = {
        "ISO dates, comma": lambda: f"dates {date()}, {date()}",
        "ISO dates, one per line": lambda: "\n".join(date() for _ in range(4)),
        "IPv4 list, comma": lambda: f"hosts {ip()}, {ip()}",
        "IPv4 list, one per line": lambda: "\n".join(ip() for _ in range(3)),
        "IPv4 and port": lambda: f"db at {ip()}:{port()}",
        "versions, comma": lambda: "versions " + ", ".join(ver() for _ in range(4)),
        "prices, comma": lambda: "prices " + ", ".join(f"${price()}" for _ in range(5)),
        "prices, one per line": lambda: "\n".join(price() for _ in range(5)),
        "phone numbers, comma": lambda: f"call {phone()}, {phone()}",
        "port list, comma": lambda: "ports " + ", ".join(port() for _ in range(4)),
        "port list, comma no space": lambda: "ports " + ",".join(port() for _ in range(4)),
        "port list, one per line": lambda: "\n".join(port() for _ in range(4)),
        "port list, slash": lambda: "/".join(port() for _ in range(4)),
        "ids, one per line": lambda: "\n".join(ident() for _ in range(4)),
        "ids, pipe table": lambda: " | ".join(ident() for _ in range(4)),
        "millisecond times": lambda: f"ts {ms()}",
        "microsecond times": lambda: f"at {us()}",
        "date-based ids": lambda: f"run id {stamp_id()}",
        "times hh:mm:ss": lambda: f"{rnd.randint(0, 23):02d}:{rnd.randint(0, 59):02d}:{rnd.randint(0, 59):02d}",
    }
    return {name: [make() for _ in range(400)] for name, make in shapes.items()}


CORPUS = _corpus()


@pytest.mark.parametrize("shape", sorted(CORPUS))
def test_ordinary_technical_text_is_never_altered(shape):
    altered = [s for s in CORPUS[shape] if scrub(s) != s]
    assert not altered, f"{shape}: {len(altered)}/{len(CORPUS[shape])} altered, e.g. {_masked(altered[0])!r}"


def test_ordinary_text_with_unicode_spaces_comes_back_exactly():
    for text in ("Queue item 158 is in build", "a​b", "10 000 steps", "range 1–5"):
        assert scrub(text) == text


@pytest.mark.parametrize("text", ["1." * 1000, "1 - " * 500, "1\n" * 1000, "4242, " * 400, "Visa " + "1/" * 1000,
                                  "1_2" * 700, "1 , " * 500, "1 " * 1000, "1 \n " * 500, "1      " * 300,
                                  "4242 | " * 300],
                         ids=lambda s: f"{len(s)}-chars")
def test_scrub_stays_fast_with_the_wider_separators(text):
    start = time.perf_counter()
    scrub(text)
    assert time.perf_counter() - start < 0.5
