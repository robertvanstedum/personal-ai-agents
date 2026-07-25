"""
tests/test_realtime_voice_transcript.py — normalized event ordering and
transcript deduplication/finalization.

Per _working/CLAUDE_CODE_BUILD_SPEC_voice_realtime_2026-07-24.md Section 10.
Must handle both providers' actual transcript delivery shapes (verified
2026-07-24 against official docs):
  - OpenAI: streaming deltas per item (response.output_audio_transcript.delta
    / input transcription deltas) -- concatenate chunks for the same item.
  - xAI: cumulative updates per item
    (conversation.item.input_audio_transcription.updated is "cumulative,
    not delta") -- replace, not concatenate, for the same item.
"""
import pytest

from core.realtime_voice.transcript import TranscriptAccumulator, TranscriptEvent


def test_delta_events_for_same_item_are_concatenated():
    acc = TranscriptAccumulator()
    acc.add(TranscriptEvent(sequence=1, speaker="assistant", item_id="a1",
                             text="Guten ", is_delta=True, completed=False))
    acc.add(TranscriptEvent(sequence=2, speaker="assistant", item_id="a1",
                             text="Tag!", is_delta=True, completed=True))
    turns = acc.finalize()
    assert len(turns) == 1
    assert turns[0]["text"] == "Guten Tag!"
    assert turns[0]["speaker"] == "assistant"


def test_cumulative_events_for_same_item_replace_not_concatenate():
    acc = TranscriptAccumulator()
    acc.add(TranscriptEvent(sequence=1, speaker="user", item_id="u1",
                             text="Ich habe", is_delta=False, completed=False))
    acc.add(TranscriptEvent(sequence=2, speaker="user", item_id="u1",
                             text="Ich habe eine Frage", is_delta=False, completed=True))
    turns = acc.finalize()
    assert len(turns) == 1
    assert turns[0]["text"] == "Ich habe eine Frage"  # not "Ich habeIch habe eine Frage"


def test_turns_are_ordered_by_first_appearance_not_last_update():
    acc = TranscriptAccumulator()
    acc.add(TranscriptEvent(sequence=1, speaker="user", item_id="u1",
                             text="Hallo", is_delta=False, completed=True))
    acc.add(TranscriptEvent(sequence=2, speaker="assistant", item_id="a1",
                             text="Guten Tag", is_delta=False, completed=True))
    # A late-arriving update to u1 (e.g. a corrected transcript) must not
    # move u1 after a1 in turn order.
    acc.add(TranscriptEvent(sequence=3, speaker="user", item_id="u1",
                             text="Hallo!", is_delta=False, completed=True))
    turns = acc.finalize()
    assert [t["speaker"] for t in turns] == ["user", "assistant"]
    assert turns[0]["text"] == "Hallo!"


def test_reprocessing_the_same_event_id_does_not_duplicate():
    acc = TranscriptAccumulator()
    event = TranscriptEvent(sequence=1, speaker="user", item_id="u1",
                             text="Hallo", is_delta=False, completed=True,
                             provider_event_id="evt-123")
    acc.add(event)
    acc.add(event)  # simulates a reconnect re-delivering the same event
    turns = acc.finalize()
    assert len(turns) == 1


def test_incomplete_item_still_appears_in_finalize_marked_incomplete():
    acc = TranscriptAccumulator()
    acc.add(TranscriptEvent(sequence=1, speaker="assistant", item_id="a1",
                             text="Guten...", is_delta=True, completed=False))
    turns = acc.finalize()
    assert len(turns) == 1
    assert turns[0]["completed"] is False


def test_empty_accumulator_finalizes_to_empty_list():
    acc = TranscriptAccumulator()
    assert acc.finalize() == []


def test_mixed_speakers_interleaved_correctly():
    acc = TranscriptAccumulator()
    acc.add(TranscriptEvent(sequence=1, speaker="user", item_id="u1",
                             text="Hallo", is_delta=False, completed=True))
    acc.add(TranscriptEvent(sequence=2, speaker="assistant", item_id="a1",
                             text="Guten Tag", is_delta=False, completed=True))
    acc.add(TranscriptEvent(sequence=3, speaker="user", item_id="u2",
                             text="Wie geht es Ihnen?", is_delta=False, completed=True))
    turns = acc.finalize()
    assert [t["speaker"] for t in turns] == ["user", "assistant", "user"]


def test_finalize_is_idempotent():
    acc = TranscriptAccumulator()
    acc.add(TranscriptEvent(sequence=1, speaker="user", item_id="u1",
                             text="Hallo", is_delta=False, completed=True))
    first = acc.finalize()
    second = acc.finalize()
    assert first == second


# ── Partial-session persistence (disconnect) ─────────────────────────────────

def test_mark_partial_flags_the_finalized_transcript():
    acc = TranscriptAccumulator()
    acc.add(TranscriptEvent(sequence=1, speaker="user", item_id="u1",
                             text="Hallo", is_delta=False, completed=True))
    acc.mark_partial(reason="disconnect")
    result = acc.finalize_with_metadata()
    assert result["partial"] is True
    assert result["partial_reason"] == "disconnect"
    assert len(result["turns"]) == 1


def test_not_marked_partial_by_default():
    acc = TranscriptAccumulator()
    result = acc.finalize_with_metadata()
    assert result["partial"] is False
    assert result["partial_reason"] is None
