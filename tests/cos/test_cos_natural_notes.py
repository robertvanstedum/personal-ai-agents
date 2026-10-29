"""A4-1 on Chief of Staff's host (A4_FIX_BRIEF.md item 2): "make a note called X" and "note that X" are explicit notes in
the one note grammar (confer_service.parse_explicit_note), which her turn service, her direct voice's save_note gate and
the MC portal all read. Typed Confer and her voice change together; every earlier negative still holds. Her real adapter
and turn service on the core's real routes (cos_voice_rig.py). No provider, no Keychain, no microphone, no network."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cos_voice_rig as rig  # noqa: E402

from domains.cos import direct_voice  # noqa: E402
from domains.cos.confer_service import (ConferTurnRequest, ConferTurnService, InvalidConferTurn,  # noqa: E402
                                        parse_explicit_note)


@pytest.mark.parametrize("command,note", [
    ("Make a note called Test.", "Test."),
    ("make me a note called the kiln plan", "the kiln plan"),
    ("note that the gate code is 4411", "the gate code is 4411"),
    ("Okay, note that the release slipped", "the release slipped"),
    ("Please note that I am away on Friday", "I am away on Friday"),
    ("Could you note that the glaze is ready", "the glaze is ready"),
    ("Note that one is broken", "one is broken"),
    ("Note that for me the release is late", "for me the release is late"),
])
def test_named_and_note_that_forms_are_explicit_notes(command, note):
    assert parse_explicit_note(command) == note


@pytest.mark.parametrize("text", [
    "note that down", "Note that down, please.", "note that for later", "note that too", "note that one", "note that please",
    "Note that, please.", "Note that for the record.", "note that down: buy milk", "Note that.", "Can you note that?",
    "note that's fine", "note thatcher", "Did you make a note?", "did you note that the kiln is hot?", "I noted that",
    "I made a note called Test", "make a note calledTest", "a note called Test",
])
def test_pointers_questions_and_mentions_stay_ordinary_chat(text):
    assert parse_explicit_note(text) is None


@pytest.mark.parametrize("command", ["make a note called", "note that", "Note that   "])
def test_the_new_forms_without_words_are_refused(command):
    with pytest.raises(InvalidConferTurn, match="must not be empty"):
        parse_explicit_note(command)


def test_typed_confer_saves_a_note_called_x_and_never_asks_the_model():
    saved, asked = [], []
    service = ConferTurnService(call_backend=lambda *a: asked.append(a) or "a model answer", build_context=lambda: {},
                                increment_chat=lambda: None, backend_metadata=lambda: ("COS", "m"),
                                save_note=lambda text, op: saved.append(text) or {"saved": True})
    result = service.handle(ConferTurnRequest(text="Make a note called Test.", channel="html_text"))
    assert saved == ["Test."] and not asked and result.operation["type"] == "note_save"


@pytest.fixture
def r(monkeypatch, tmp_path):
    return rig.build(monkeypatch, tmp_path)


@pytest.mark.parametrize("words,note", [("Make a note called Test.", "Test."), ("Note that the kiln is hot", "the kiln is hot")])
def test_her_voice_saves_the_new_forms_by_save_note_and_by_check(r, words, note):
    sid = rig.start(r)
    by_tool = rig.ask(r, sid, "call_1", "t1", words=words, name="save_note").get_json()
    assert by_tool["outcome"] == "done" and by_tool["saved"] is True and by_tool["spoken"] == "Saved."
    by_check = rig.ask(r, sid, "host-2", "t2", words=words).get_json()
    assert by_check["saved"] is True
    assert list(r.notes.saved.values()) == [note, note] and r.runtime.calls == []


@pytest.mark.parametrize("words", ["Did you make a note?", "note that down", "I made a note called Test"])
def test_her_voice_refuses_save_note_on_a_question_or_a_pointer(r, words):
    sid = rig.start(r)
    said = rig.ask(r, sid, "call_1", "t1", words=words, name="save_note").get_json()
    assert said["saved"] is False and said["spoken"] == direct_voice.NOT_A_NOTE
    assert not r.notes.saved and not r.turns


# ── A4-2's own words: "save a note test" (no separator), and a comma where he paused ─────────────────────────────────

@pytest.mark.parametrize("command,note", [
    ("save a note test", "test"),                                                    # as he said it
    ("Save a note test.", "test."),
    ("save a note buy milk", "buy milk"),
    ("Okay, save a note the kiln is hot", "the kiln is hot"),
    ("Can you save a note test?", "test?"),
    ("make a note, buy milk", "buy milk"),
    ("Make a note, that the kiln is hot.", "the kiln is hot."),                       # a pause before "that"
    ("save a note, call Anna", "call Anna"),
    ("Could you make me a note, glaze order Friday", "glaze order Friday"),
])
def test_save_a_note_with_no_separator_or_a_comma_is_his_note(command, note):
    assert parse_explicit_note(command) == note


@pytest.mark.parametrize("text", [
    "did you save a note?", "Did you make a note, though?", "I want to save a note test", "save a note", "save a note?",
    "Can you save a note?", "make a note,", "save a note later", "save a note now", "save a note tomorrow",
    "save a note thanks", "save a note this", "save a note that's it", "make a note, please.", "save a note, please",
    "make a note, if you can", "make a note, that", "save a note for me", "save a note of that",
    "save a note about the meeting", "save a note to the board: buy milk", "save this note test", "save a note-taking app",
    "take a note, buy milk", "make a note buy milk", "save a note, what do you think?",
])
def test_with_no_separator_or_a_comma_a_question_a_framing_word_or_nothing_stays_chat(text):
    assert parse_explicit_note(text) is None


def test_her_voice_saves_save_a_note_test_as_he_said_it(r):
    sid = rig.start(r)
    saved = rig.ask(r, sid, "call_1", "t1", words="save a note test", name="save_note").get_json()
    assert saved["outcome"] == "done" and saved["saved"] is True and list(r.notes.saved.values()) == ["test"]
    refused = rig.ask(r, sid, "call_2", "t2", words="save a note later", name="save_note").get_json()
    assert refused["saved"] is False and refused["spoken"] == direct_voice.NOT_A_NOTE and len(r.notes.saved) == 1
