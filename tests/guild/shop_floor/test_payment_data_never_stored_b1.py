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
                                  "Queue item 158 is in build", "ping x@y later", "x" * 140])
def test_ordinary_text_is_left_alone(text):
    assert scrub(text) == text


def test_scrub_is_idempotent():
    once = scrub(" · ".join(SYNTHETIC))
    assert scrub(once) == once


@pytest.mark.parametrize("text", ["x" * 2000, "*" * 2000, "Visa " + "x" * 2000, "1 " * 1000, "Visa " + "* " * 999,
                                  "1-" * 1000 + "Visa", "a@" * 1000, "xx " * 700, "ending in " * 200])
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
