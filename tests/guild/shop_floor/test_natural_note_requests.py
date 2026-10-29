"""A4-1 (A4_FIX_BRIEF.md item 2): the natural ways Robert asks for a note or a Board post-it, typed and spoken.

On dev he said "Can you put a test note on the board, just a post-it note, say test?" and "Make a note called Test."; both
were refused ("Say 'make a note that' and the note"). The one note grammar (CoS's parse_explicit_note, which
owner_actions.parse uses) now also reads "make a note called X" and "note that X"; the Board grammar (board_commands.parse)
reads a one-word kind ("a test note"), an aside (", just a post-it note"), "say X" / "that says X", "okay," before the
request, and a question's closing mark, with German equivalents. mc_voice_direct._is_note_command reads both, so the
spoken path changes with the typed one. The rule stays: his own words ask, and the words saved are his, never the model's;
a question or a mention is not a command. No network, no model call, no provider."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from minimoi_portal.guild_ui import board_commands, owner_actions as oa
from minimoi_portal.guild_ui.mc import CachedHealth
from minimoi_portal.guild_ui.mc.cos import ChiefOfStaffBackend
from minimoi_portal.guild_ui.mc_voice_direct import _is_note_command

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from test_owner_actions import CosRoute, Resp

API = "/guild-next/api/v1"
ROBERTS_POSTIT = "Can you put a test note on the board, just a post-it note, say test?"


# ── notes: owner_actions.parse, through the one CoS grammar ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,payload", [
    ("Make a note called Test.", "Test."),                               # A4-1, as he said it
    ("make a note called the kiln plan", "the kiln plan"),
    ("Can you make a note called Glaze order", "Glaze order"),
    ("make a note: the gate code is 4411", "the gate code is 4411"),
    ("note that the build is green", "the build is green"),
    ("Note that the kiln is hot.", "the kiln is hot."),
    ("Okay, note that the release slipped", "the release slipped"),
    ("please note that I am away on Friday", "I am away on Friday"),
    ("could you note that the glaze is ready", "the glaze is ready"),
    ("Note that too many builds failed", "too many builds failed"),     # "too" starts his words here, not a pointer
    ("take a note: call Anna back", "call Anna back"),
])
def test_natural_note_requests_are_notes_with_his_words(text, payload):
    assert oa.parse(text) == oa.Action("note", text=payload)
    assert _is_note_command(text) is True


@pytest.mark.parametrize("text", [
    "Did you make a note?", "did you make a note that the kiln is hot?", "I noted that yesterday", "I made a note called Test",
    "note that down", "Note that down, please.", "note that for later", "note that one", "Can you note that?", "Note that.",
    "note that's fine", "Note: the build is green", "make a note of that", "a note called Test", "make a note calledTest",
    "Has anyone made a note called Test?", "What did the note called Test say?",
])
def test_a_question_a_mention_or_a_pointer_is_not_a_note(text):
    assert oa.parse(text) is None
    assert _is_note_command(text) is False


def test_a_named_note_with_no_words_is_refused_not_saved():
    action = oa.parse("make a note called")
    assert action.kind == "note" and action.text is None and "Nothing was saved." in action.refusal


# ── post-its: board_commands.parse ──────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,payload", [
    (ROBERTS_POSTIT, "test"),                                                         # A4-1, as he said it
    ("put a post-it note on the board saying buy milk", "buy milk"),
    ("Put a note on the board saying buy milk", "buy milk"),
    ("put a post-it on the board, say buy milk", "buy milk"),
    ("put a post-it on the board say buy milk", "buy milk"),
    ("put a post-it on the board, say: buy milk", "buy milk"),
    ("Put a post-it note on the board that says call the bank", "call the bank"),
    ("Can you put a post-it on the board saying test?", "test"),                      # the question's mark is not his words
    ("Could you put a quick note on the board, say ship Friday, please?", "ship Friday"),
    ("Okay, put a post-it on the board saying test", "test"),
    ("add a post-it: buy milk", "buy milk"),
    ("Add a post-it - renew the passport", "renew the passport"),
    ('Can you put a post-it on the board saying "test?"', "test?"),                   # quoted words are literal
    ("Can you make a note on the Board: Test voice tomorrow", "Test voice tomorrow"), # unchanged
])
def test_natural_postit_requests_save_his_words(text, payload):
    assert board_commands.parse(text) == payload
    assert _is_note_command(text) is True


@pytest.mark.parametrize("text,payload", [
    ("Mach ein Post-it ans Board: Milch kaufen", "Milch kaufen"),
    ("Häng einen Zettel ans Board mit dem Text Milch kaufen", "Milch kaufen"),
    ("Klebe ein Post-it aufs Board, sag Test", "Test"),
    ("Mach ein Post-it ans Board, sag: Milch kaufen", "Milch kaufen"),
    ("Schreib eine Notiz ans Board, auf dem steht Ofen prüfen", "Ofen prüfen"),
    ("Kannst du ein Post-it ans Board hängen, mit dem Text Milch kaufen?", "Milch kaufen"),
    ("Kannst du eine Notiz ans Board machen, sag Test?", "Test"),
    ("Bitte erstelle eine Notiz auf dem Board: Morgen weiterbauen", "Morgen weiterbauen"),   # unchanged
])
def test_german_postit_requests_save_his_words(text, payload):
    assert board_commands.parse(text) == payload


@pytest.mark.parametrize("text", [
    "Did you put a post-it on the board saying test?", "He said put a post-it on the board saying test",
    "Can you tell me about Board notes?", "Do not put a post-it on the board saying test",
    "Hast du ein Post-it ans Board gehängt?", "Mach eine Notiz: Milch kaufen", "Ich habe einen Zettel ans Board gehängt",
    "erstelle eine Notiz auf dem Boardwalk morgen", "Is there a post-it on the board that says test?",
])
def test_a_question_a_report_or_a_plain_german_note_is_not_a_postit(text):
    assert board_commands.parse(text) is None


@pytest.mark.parametrize("text", ["put a test note on the board", "Can you put a quick post-it on the board?",
                                  "Kannst du ein Post-it ans Board hängen?", "make a post-it saying your last answer"])
def test_a_postit_request_without_its_words_asks_for_them(text):
    assert board_commands.parse(text) == ""


# ── typed, on the real portal ─────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def cos_portal(floored):
    route = CosRoute()
    services = floored.app.extensions["guild_ui_next"]["services"]
    services.mc = ChiefOfStaffBackend("http://cos-scheduler:8769", http_post=route.post, http_get=lambda *a, **k: Resp(200, {}))
    services.mc_health = CachedHealth(services.mc)
    services.mc_turns = True
    floored.extra["route"] = route
    return floored


def _say(portal, text):
    client = portal.owner(); token = portal.csrf(client)
    kept = client.post(f"{API}/notes", json=keyed(text=text), headers=write_headers(token))
    assert kept.status_code == 200, kept.get_json()
    return client.post(f"{API}/mc/turns", json={"note_request_id": kept.get_json()["note"]["request_id"],
                                               "record_mode": "on_record"},
                       headers=write_headers(token, mode="on_record")).get_json()


def test_typed_make_a_note_called_saves_his_words_through_cos(cos_portal):
    body = _say(cos_portal, "Make a note called Test.")
    assert [s["text"] for s in cos_portal.extra["route"].sent] == ["/note Test."]
    assert body["backend_kind"] == "platform" and body["operation"]["saved"] is True
    assert body["reply_note"]["text"] == "Saved to Chief of Staff notes: Test."


def test_typed_note_that_saves_his_words_through_cos(cos_portal):
    body = _say(cos_portal, "Note that the kiln is hot")
    assert [s["text"] for s in cos_portal.extra["route"].sent] == ["/note the kiln is hot"]
    assert body["operation"]["saved"] is True


def test_typed_postit_as_robert_said_it_lands_on_the_board(cos_portal):
    body = _say(cos_portal, ROBERTS_POSTIT)
    assert body["backend_kind"] == "platform" and body["operation"]["type"] == "board_postit"
    assert body["operation"]["saved"] is True and body["message"].endswith(": test")
    assert not cos_portal.extra["route"].sent                                # a post-it is the Board's, not CoS's


def test_typed_question_about_a_note_still_reaches_the_model(cos_portal):
    _say(cos_portal, "Did you make a note called Test?")
    sent = cos_portal.extra["route"].sent
    assert len(sent) == 1 and not sent[0]["text"].startswith("/note")


# ── spoken, on MC's direct voice path ─────────────────────────────────────────────────────────────────────────────────

class Board:
    """The Board's post-it store, as the typed turn reaches it (api._floor): a real id per idempotency key."""

    def __init__(self):
        self.rows = {}

    def add_postit(self, text, author, *, idempotency_key):
        old = self.rows.get(idempotency_key)
        row = old or {"id": len(self.rows) + 1, "text": text, "author": author.id}
        self.rows[idempotency_key] = row
        return SimpleNamespace(outcome="added", value=row, repeated=old is not None)


@pytest.fixture
def voice(monkeypatch, tmp_path):
    from minimoi_portal.guild_ui import api
    from test_mc_voice_direct import ON, build
    r = build(monkeypatch, tmp_path, dict(ON))
    r.board = Board()
    monkeypatch.setattr(api, "_floor", lambda: r.board)
    return r


def test_spoken_make_a_note_called_is_saved_with_its_receipt(voice):
    from test_mc_voice_direct import call, start
    sid = start(voice)["voice_session_id"]
    saved = call(voice, sid, call_id="n1", name="save_note", words="Make a note called Test.").get_json()
    assert saved["saved"] is True and saved["receipt"] and saved["spoken"] == "Saved to Chief of Staff notes: Test."
    noted = call(voice, sid, call_id="n2", name="save_note", words="Note that the kiln is hot").get_json()
    assert noted["saved"] is True
    assert voice.cos.calls == ["/note Test.", "/note the kiln is hot"] and not voice.turns


def test_spoken_postit_as_robert_said_it_is_saved_on_the_board(voice):
    from test_mc_voice_direct import call, start
    sid = start(voice)["voice_session_id"]
    saved = call(voice, sid, call_id="p1", name="save_note", words=ROBERTS_POSTIT).get_json()
    assert saved["saved"] is True and saved["receipt"] == 1, saved
    assert [row["text"] for row in voice.board.rows.values()] == ["test"]
    assert not voice.cos.calls and not voice.turns


@pytest.mark.parametrize("words", ["Did you make a note?", "note that down", "Did you put a post-it on the board saying test?"])
def test_spoken_question_or_pointer_saves_nothing(voice, words):
    from minimoi_portal.guild_ui import mc_voice_direct as direct
    from test_mc_voice_direct import call, start
    sid = start(voice)["voice_session_id"]
    said = call(voice, sid, call_id="q1", name="save_note", words=words).get_json()
    assert said["saved"] is False and said["spoken"] == direct.NOT_A_NOTE
    assert not voice.cos.calls and not voice.board.rows and not voice.turns



# ── A4-2's own words: "save a note test", and a comma where he paused (one grammar, both hosts) ──────────────────────

@pytest.mark.parametrize("text,payload", [("save a note test", "test"), ("make a note, buy milk", "buy milk"),
                                          ("Save a note, call Anna back.", "call Anna back.")])
def test_save_a_note_with_no_separator_or_a_comma_is_a_note(text, payload):
    assert oa.parse(text) == oa.Action("note", text=payload) and _is_note_command(text) is True


@pytest.mark.parametrize("text", ["did you save a note?", "save a note later", "save a note", "make a note, please"])
def test_save_a_note_questions_and_framing_words_are_not_notes(text):
    assert oa.parse(text) is None and _is_note_command(text) is False


def test_typed_save_a_note_test_saves_test_through_cos(cos_portal):
    body = _say(cos_portal, "save a note test")
    assert [s["text"] for s in cos_portal.extra["route"].sent] == ["/note test"]
    assert body["operation"]["saved"] is True and body["reply_note"]["text"] == "Saved to Chief of Staff notes: test"


def test_spoken_save_a_note_test_is_saved_and_save_a_note_later_is_not(voice):
    from minimoi_portal.guild_ui import mc_voice_direct as direct
    from test_mc_voice_direct import call, start
    sid = start(voice)["voice_session_id"]
    saved = call(voice, sid, call_id="s1", name="save_note", words="save a note test").get_json()
    assert saved["saved"] is True and saved["spoken"] == "Saved to Chief of Staff notes: test"
    later = call(voice, sid, call_id="s2", name="save_note", words="save a note later").get_json()
    assert later["saved"] is False and later["spoken"] == direct.NOT_A_NOTE
    assert voice.cos.calls == ["/note test"] and not voice.turns
