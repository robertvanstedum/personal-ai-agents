"""Owner Board requests in ordinary wording (issue #348): quoted and polite phrasing saves exactly
the stated words; a request that only points at something ("this", "your last answer") or names a
generic thing asks once for the words and saves nothing; reported, quoted, negative and drafting
wording never writes; retries never duplicate."""
from types import SimpleNamespace

import pytest

from minimoi_portal.guild_ui.board_commands import execute, parse


class Store:
    floor = "synthetic-owner-conversation"

    def __init__(self):
        self.rows, self.notes = {}, {}

    def add_postit(self, text, author, *, idempotency_key):
        old = self.rows.get(idempotency_key)
        if old and old["text"] != text:
            return SimpleNamespace(outcome="idempotency_mismatch", value=None, repeated=True)
        row = old or {"id": len(self.rows) + 1, "text": text}
        self.rows[idempotency_key] = row
        return SimpleNamespace(outcome="added", value=row, repeated=old is not None)

    def add_note(self, key, text, author, **kwargs):
        self.notes[key] = {"text": text}
        return SimpleNamespace(outcome="kept", value=self.notes[key])


def run(text, turn="turn-1", board=None):
    board = board or Store()
    return execute(text, board=board, conversation=Store(), principal="robert", turn_key=turn), board


# ── quoted words are saved exactly, without their quotation marks ─────────────

@pytest.mark.parametrize("text,payload", [
    ('put "buy milk" on the board', "buy milk"),
    ("put 'buy milk' on the board", "buy milk"),
    ("put “buy milk” on the board", "buy milk"),
    ("put ‘buy milk’ on the board", "buy milk"),
    ('Write "do not deploy tonight" on a post-it.', "do not deploy tonight"),
    ('make a post-it: "buy milk"', "buy milk"),
    ('put "this" on the board', "this"),                    # quoted words are literal, even a pointer word
    ('put Robert\'s "v2" plan on the board', 'Robert\'s "v2" plan'),
])
def test_quoted_words_are_the_note(text, payload):
    assert parse(text) == payload


@pytest.mark.parametrize("text,payload", [
    ("put James' notes on the board", "James' notes"),
    ("put don't forget the keys on the board", "don't forget the keys"),
    ("write Robert’s call list on a post-it", "Robert’s call list"),
])
def test_apostrophes_in_words_are_not_quotation(text, payload):
    assert parse(text) == payload


def test_quoted_request_saves_once_and_receipt_names_the_words():
    first, board = run('put "buy milk" on the board')
    again, _ = run('put "buy milk" on the board', board=board)
    assert first["operation"]["saved"] and first["operation"]["id"] == 1
    assert again["operation"]["repeated"] and len(board.rows) == 1
    assert next(iter(board.rows.values()))["text"] == "buy milk"
    assert first["reply"] == "Saved Board post-it #1: buy milk"


# ── polite wording works ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Could you please put buy milk on the board?",
    "Can you put buy milk on the board, please?",
    "Would you add buy milk to the board?",
    "Will you put buy milk on the board for me?",
    "Please write buy milk on a post-it, thanks.",
    "Can you kindly put buy milk on the board?",
    "Put buy milk on the board, thank you!",
])
def test_polite_requests(text):
    assert parse(text) == "buy milk"


# ── a pointer or a generic thing is not the note: ask once, save nothing ──────

@pytest.mark.parametrize("text", [
    "can you put your last answer on the board?",
    "put what you just said on the board",
    "write the summary above on a post-it",
    "Could you please put this on the board?",
    "please put that on the board",
    "put it on the board, please",
    "add the last reply to the board",
    "put your previous message on the board",
    "put everything above on the board",
    "write what we discussed on a post-it",
    "make a post-it: this",
    "make a post-it saying your last answer",
    "add a reminder to the board",
    "add a note to the board",
    "put a task on the board",
    "add something to the board",
])
def test_pointer_or_generic_asks_for_the_words(text):
    assert parse(text) == ""
    out, board = run(text)
    assert out["operation"] == {"type": "board_postit", "saved": False, "reason": "needs_text"}
    assert not board.rows
    assert out["reply"].startswith("Which words should go on the Board?") and "Nothing was added" in out["reply"]
    assert len(out["reply"]) < 160                         # one concise question


@pytest.mark.parametrize("text,payload", [
    ("put check the summary above the fold on the board", "check the summary above the fold"),
    ("put this week's review on the board", "this week's review"),
    ("add renew the passport to the board", "renew the passport"),
    ("put reminder: call the bank on the board", "reminder: call the bank"),
])
def test_real_words_that_contain_pointer_words_are_saved(text, payload):
    assert parse(text) == payload


# ── reported, quoted, negative, conditional and drafting wording never writes ──

@pytest.mark.parametrize("text", [
    'He said "put buy milk on the board"',
    '"put buy milk on the board"',
    "“Put buy milk on the board.”",
    'She asked me to "put buy milk on the board".',
    "Don't put buy milk on the board.",
    'Do not put "buy milk" on the board.',
    "Should I put buy milk on the board?",
    "How would I put buy milk on the board?",
    "If you can, put buy milk on the board.",
    "Draft a post-it saying buy milk",
    "Can you draft a post-it for buy milk?",
    "What would you put on the board?",
    "Example: `put buy milk on the board`",
    "> put buy milk on the board",
    "    put buy milk on the board",
    "Summarize this: put buy milk on the board.",
])
def test_not_a_request_never_writes(text):
    assert parse(text) is None
    out, board = run(text)
    assert out is None and not board.rows


def test_quote_around_the_board_words_is_not_a_request():
    assert parse('put buy milk on the "board"') is None


# ── unchanged guarantees ──────────────────────────────────────────────────────

def test_too_long_still_refused_with_its_own_reason():
    out, board = run("put " + "x" * 281 + " on the board")
    assert out["operation"] == {"type": "board_postit", "saved": False, "reason": "too_long"} and not board.rows


def test_two_different_requests_still_ask():
    out, board = run('put "buy milk" on the board. put "call the bank" on the board.')
    assert out["operation"]["saved"] is False and not board.rows


def test_same_turn_retry_with_different_words_is_refused():
    board = Store()
    run('put "buy milk" on the board', turn="t", board=board)
    out, _ = run('put "buy bread" on the board', turn="t", board=board)
    assert out["operation"]["saved"] is False and len(board.rows) == 1


def test_scrubbed_payment_data_never_reaches_the_board():
    out, board = run('put "card 4242 4242 4242 4242" on the board')
    assert out["operation"]["saved"] and "4242" not in next(iter(board.rows.values()))["text"]


# ── a quotation is one payload: its sentences and lines are the owner's words ─

@pytest.mark.parametrize("text,payload", [
    ('put "Call Anna. Buy milk" on the board', "Call Anna. Buy milk"),
    ('put "Call Anna\nBuy milk" on the board', "Call Anna\nBuy milk"),
    ('put "- Call Anna\n- Buy milk\n- Book flights" on the board', "- Call Anna\n- Buy milk\n- Book flights"),
    ('put "Today:\n    - Call Anna\n    - Buy milk" on the board', "Today:\n    - Call Anna\n    - Buy milk"),
    ('Could you please write "Call Anna! Buy milk?" on a post-it?', "Call Anna! Buy milk?"),
    ('make a post-it: "Call Anna.\nBuy milk."', "Call Anna.\nBuy milk."),
    ("put ‘Robert’s plan. Then lunch’ on the board", "Robert’s plan. Then lunch"),
])
def test_quoted_sentences_and_bullets_stay_one_note(text, payload):
    assert parse(text) == payload


def test_quoted_bullets_save_exactly_once():
    text = 'put "- Call Anna\n- Buy milk" on the board'
    first, board = run(text)
    again, _ = run(text, board=board)
    assert first["operation"]["saved"] and again["operation"]["repeated"] and len(board.rows) == 1
    assert next(iter(board.rows.values()))["text"] == "- Call Anna\n- Buy milk"


# ── sentence punctuation after the closing quotation mark ─────────────────────

@pytest.mark.parametrize("text", [
    'make a post-it: "buy milk".',
    'make a post-it: "buy milk"!',
    "make a post-it: 'buy milk'.",
    "make a post-it: “buy milk”.",
    'Please make a post-it: "buy milk"...',
])
def test_punctuation_after_the_closing_quote_is_not_saved(text):
    assert parse(text) == "buy milk"


# ── quotation marks that frame the command, or several, still never write ────

@pytest.mark.parametrize("text", [
    '"put Call Anna. Buy milk on the board"',
    '"Put buy milk on the board." That is what I wrote yesterday.',
    'He said: "Call Anna. Put buy milk on the board."',
    'The note says "put buy milk\non the board"',
    'Don\'t put "Call Anna. Buy milk" on the board.',
    'If it helps, put "Call Anna\nBuy milk" on the board.',
    '> put "buy milk" on the board',
])
def test_quoted_or_framed_commands_never_write(text):
    assert parse(text) is None
    out, board = run(text)
    assert out is None and not board.rows


def test_two_quoted_requests_on_separate_lines_still_ask():
    out, board = run('put "Call Anna" on the board\nput "Buy milk" on the board')
    assert out["operation"]["saved"] is False and out["operation"]["reason"] == "needs_text" and not board.rows


@pytest.mark.parametrize("text,payload", [
    ('put "unclosed note on the board', ""),                  # opening mark with no close: ask
    ('put "v2" plan notes on the board', '"v2" plan notes'),  # partly quoted words stay literal
])
def test_unclosed_or_partial_quotes(text, payload):
    assert parse(text) == payload
