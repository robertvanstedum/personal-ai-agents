"""A spoken answer does not start with a category label (#641).

The three lines are from the 04:10Z call, mc-voice-call-91296989.txt. A sentence that only uses the word stays.
"""
from core.realtime_voice.spoken import for_speech, strip_spoken_label

LINES = (
    "Recommendation: we need the live issue list to answer that, and it isn’t available from this call.",
    "Recommendation: yes, I can draft the issue text here, but I can’t write it into the tracker for you.",
    "Recommendation: yes, you can use this conversation as a transcript if your client or interface supports saving or exporting it.",
)


def test_the_three_call_lines_lose_the_label():
    for line in LINES:
        spoken = for_speech(line)
        assert not spoken.lower().startswith("recommendation")
        assert spoken.startswith(line.split(": ", 1)[1][:12])


def test_a_sentence_that_uses_the_word_stays():
    said = "My recommendation is to leave the pin where it is."
    assert for_speech(said) == said
    assert strip_spoken_label("Answer - look at the log.") == "look at the log."
    assert strip_spoken_label("Next step: open the issue.") == "open the issue."
    assert for_speech("Decision: keep the call file.") == "Decision: keep the call file."
    assert for_speech("Task: refresh the baseline.") == "Task: refresh the baseline."
