"""Review M-3 (REVIEW_a4_fix.md, "Targeted check at 06e9bad7"): the note and post-it grammars run in linear time.

a889cde1's filler pattern repeated a group of overlapping fillers ("um+|hm+|mm+") with an optional separator, so
"save a note" plus 38 m's plus "x" took 13.9 s, holding the GIL: one Telegram, Confer, MC or /chat message could stall the
single-process portal or cos-scheduler. The filler check is now word by word with fixed sets (owner_words.only_filler),
the request's closing "?" is dropped with string work (owner_words.without_question_mark), a lone separator word is a set
lookup (board_commands._his_postit), and every "okay" prefix reads "a comma then spaces, or spaces" without the
overlapping "\\s*,?\\s+". Each adversarial input below, about 10 KB (the Board reads at most 2,000 characters, so it is
also given that much), must be answered in under BOUND_S; a SIGALRM after ALARM_S turns a hang into a failure, so this
file fails on a889cde1's regex instead of stalling the run. No provider, no network."""
from __future__ import annotations

import signal
import time
from contextlib import contextmanager

import pytest

from domains.cos import owner_words
from domains.cos.confer_service import InvalidConferTurn, parse_explicit_note
from minimoi_portal.guild_ui import board_commands, owner_actions

BOUND_S = 0.05
ALARM_S = 2.0
N = 10_000
SPACES = " " * N

ADVERSARIAL = {
    "the reviewer's m-run": "save a note " + "m" * N + "x",
    "after a comma": "save a note, " + "m" * N + "x",
    "after called": "make a note called " + "m" * N + "x",
    "hums": "save a note " + "um" * (N // 2) + "x",
    "hum-mh": "save a note " + "hmmh" * (N // 4) + "x",
    "fillers then x": "save a note " + "uh, " * (N // 4) + "x",
    "okay then spaces": "okay" + SPACES + "x",
    "okay comma then spaces": "okay," + SPACES + "x",
    "asked, then spaces": "can you save a note x" + SPACES + "y",
    "asked, spaces, a mark": "can you save a note x" + SPACES + "?a",
    "okay before a post-it": "okay" + SPACES + "put x on the board",
}
# The Board stops at 2,000 characters, so its own shapes are also tried at the most it reads.
BOARD = {
    "okay then spaces": "okay" + " " * 1990 + "x",
    "asked then spaces": "can you" + " " * 1990 + "put x on the board",
    "a lone separator then spaces": "schreib ein post-it mit" + " " * 1970 + "x",
    "say then spaces": "add a post-it say" + " " * 1975 + "x",
    "comma then spaces": "add a post-it," + " " * 1980 + "x",
}


@contextmanager
def deadline(seconds):
    """A hang becomes a failure: SIGALRM interrupts the regex engine, which checks for signals while it runs."""
    def fire(*_args):
        raise TimeoutError(f"still running after {seconds} s")
    previous = signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def took(fn, text):
    with deadline(ALARM_S):
        began = time.perf_counter()
        try:
            fn(text)
        except InvalidConferTurn:
            pass
        return time.perf_counter() - began


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
@pytest.mark.parametrize("grammar", ["note", "owner_actions", "board"])
def test_each_grammar_answers_an_adversarial_10kb_message_quickly(grammar, name):
    fn = {"note": parse_explicit_note, "owner_actions": owner_actions.parse, "board": board_commands.parse}[grammar]
    assert took(fn, ADVERSARIAL[name]) < BOUND_S, (grammar, name)


@pytest.mark.parametrize("name", sorted(BOARD))
def test_the_board_answers_its_longest_adversarial_message_quickly(name):
    assert took(board_commands.parse, BOARD[name]) < BOUND_S, name


@pytest.mark.parametrize("words", ["m" * N + "x", "um" * (N // 2), "uh, " * (N // 4) + "x", "x" + SPACES + "?",
                                   "actually" + SPACES + "sorry"],
                         ids=["m-run", "hums", "fillers", "spaces-then-mark", "fillers-far-apart"])
def test_the_stop_rule_itself_is_linear(words):
    assert took(owner_words.not_his_note, words) < BOUND_S
    assert took(owner_words.only_filler, words) < BOUND_S
    assert took(owner_words.without_question_mark, words) < BOUND_S


def test_the_answers_are_still_right():
    assert parse_explicit_note("save a note " + "m" * 38 + "x") == "m" * 38 + "x"   # the reviewer's input is his word
    assert owner_words.only_filler("hmmmm, um, let me think") is True
    assert owner_words.only_filler("m" * 38 + "x") is False
    assert owner_words.only_filler("um " * 30) is False                  # longer than FILLER_MAX: never filler-only
    assert owner_words.without_question_mark("test, please?") == "test"
    assert owner_words.without_question_mark("Test, bitte?", polite=("please", "bitte")) == "Test"
    assert owner_words.without_question_mark("test?!") == "test?!"       # only a closing "?" goes
    assert parse_explicit_note("Can you save a note test?") == "test"
    assert board_commands.parse("Schreib ein Post-it mit") == "" and board_commands.parse("Add a post-it say thanks") == "say thanks"
