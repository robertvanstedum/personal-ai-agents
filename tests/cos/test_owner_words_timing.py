"""Reviews M-3 and M-4 (REVIEW_a4_fix.md, "Targeted check at 06e9bad7" and "… at 03daa046"): MC's and CoS's note,
post-it and reminder grammars answer a crafted message in a bounded, small time.

M-3: a889cde1's filler pattern repeated a group of overlapping fillers ("um+|hm+|mm+") with an optional separator, so
"save a note" plus 38 m's plus "x" took 13.9 s, holding the GIL: one Telegram, Confer, MC or /chat message could stall the
single-process portal or cos-scheduler. The filler check is now word by word with fixed sets (owner_words.only_filler),
the request's closing "?" is dropped with string work (owner_words.without_question_mark), a lone separator word is a set
lookup (board_commands._his_postit), and every "okay" prefix reads "a comma then spaces, or spaces" without the
overlapping "\\s*,?\\s+".

M-4 (older than this round): the Board's tail ("please" after "please") was exponential, 21 of them 3.3 s; the reminder
grammar was about cubic in a run of spaces, 900 of them over 20 s; the Board's pointed form took 5.1 s. The tail's
separator has one reading, the reminder grammar's separators read "(?:\\s*,\\s+|\\s+)" over whitespace runs capped at
two characters (owner_actions._capped), and the Board's lazy groups start after all the whitespace before them
(board_commands._TEXT_FIRST, _POINTED and their single-whitespace twins). claim_truth's sentence breaks start only where a
run of marks starts. What each grammar matches and keeps is unchanged (A4_FIX_RESULT.md: corpora compared with 03daa046).

Each adversarial input below must be answered in under BOUND_S: at about 10 KB, and where a grammar reads at most 2,000
characters (the Board, the reminder grammar) also at that size. A SIGALRM after ALARM_S turns a hang into a failure, so
this file fails on 03daa046's and a889cde1's patterns instead of stalling the run. Where a grammar is still quadratic in
a run of punctuation (the reminder grammar's lazy words before its end), the 2,000-character limit bounds it at about
10 ms; that is inside BOUND_S. No provider, no network."""
from __future__ import annotations

import signal
import time
from datetime import datetime, timezone
from contextlib import contextmanager

import pytest

from domains.cos import owner_words
from domains.cos.confer_service import InvalidConferTurn, parse_explicit_note
from minimoi_portal.guild_ui import board_commands, owner_actions

NOW = datetime(2026, 10, 19, 3, 30, tzinfo=timezone.utc)

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



# ── Review M-4: the Board's tail and pointed form, the reminder grammar, claim_truth's sentence breaks ──────────────────

from domains.cos import claim_truth  # noqa: E402
from minimoi_portal.guild_ui.mc_voice_direct import _is_note_command  # noqa: E402

LIMIT = 1990                                           # under the 2,000 characters the Board and the reminder grammar read
M4 = {
    "the reviewer's tail, through the voice gate": (_is_note_command, "Add milk to the board" + " please" * 21 + " x"),
    "the tail at the limit": (board_commands.parse, "add x to the board" + " please" * 280 + "x"),
    "the tail with commas": (board_commands.parse, "add x to the board" + " , please" * 190 + "x"),
    "the reviewer's reminder": (owner_actions.parse, "remind me to x" + " " * 900 + "x"),
    "the reminder at the limit": (owner_actions.parse, "remind me to x" + " " * (LIMIT - 15) + "x"),
    "a reminder with tabs and newlines": (owner_actions.parse, "remind me at 5 pm to x" + " \t\n" * 650 + "x"),
    "a set-reminder with spaces": (owner_actions.parse, "set a reminder" + " " * 600 + "to x" + " " * 600 + "at 5 pm" + " " * 600 + "x"),
    "a reminder with dots": (owner_actions.parse, "remind me to x" + "." * (LIMIT - 15) + "x"),
    "the reviewer's pointed form": (board_commands.parse, "put" + " these" * 150 + " on the board as one post-it: x" + " " * 900 + "y"),
    "put and spaces": (board_commands.parse, "put" + " " * LIMIT + "y"),
    "put, a word, spaces": (board_commands.parse, "put x" + " " * (LIMIT - 6) + "y"),
    "put and spaces, through the voice gate": (_is_note_command, "put" + " " * LIMIT + "y"),
    "a pointer and spaces": (board_commands.parse, "put these on the board:" + " " * (LIMIT - 24) + "y"),
    "the tail at 10 KB": (board_commands.parse, "add x to the board" + " please" * 1430 + "x"),
    "the reminder at 10 KB": (owner_actions.parse, "remind me to x" + " " * N + "x"),
    "the pointed form at 10 KB": (board_commands.parse, "put" + " these" * 150 + " on the board as one post-it: x" + " " * N + "y"),
    # claim_truth.enforce runs on every CoS reply (confer_service): her model's words, not his, but one long run of marks
    # stalled the turn just the same (the sweep's find, review M-4).
    "claim_truth: a reply with a run of dots": (claim_truth.enforce, "I saved it" + "." * N + "x"),
    "claim_truth: a reply with a run of marks": (claim_truth.enforce, "Done" + "?!" * (N // 2) + "x"),
}


@pytest.mark.parametrize("name", sorted(M4))
def test_m4_inputs_are_answered_quickly(name):
    fn, text = M4[name]
    assert took(fn, text) < BOUND_S, name


def test_m4_rewrites_keep_their_answers():
    assert board_commands.parse("Add milk to the board please please") == "milk"
    assert board_commands.parse("put   on the board") == ""                     # the single-whitespace reading, kept
    assert board_commands.parse("put   on the board: x") == ""
    assert board_commands.parse("Put these on the board: a, b") == "a, b"
    assert owner_actions.parse("remind me to call   Anna    at 5 pm", NOW).text == "call Anna"
    assert owner_actions.parse("remind me at 5 a.  m. to stretch", NOW) is None   # two spaces inside "a. m.": still not a clock
    assert claim_truth._sentences("v1.2 is out. Done!  Next") == ["v1.2 is out. ", "Done!  ", "Next"]
