"""A CoS voice call on a new chat names that chat. A chat that already has a turn keeps its name (#620)."""
from domains.cos.history import History


def test_a_new_chat_is_named_for_the_call_and_a_chat_with_words_is_not(tmp_path):
    history = History(tmp_path / "history", "dev", clock=lambda: "2026-11-15T20:29:00+00:00")
    fresh = history.create("New conversation")
    named = history.name_voice_call(fresh["conversation_id"], "Voice call · 2:29 PM · decision log")
    assert named["title"] == "Voice call · 2:29 PM · decision log"
    again = history.name_voice_call(fresh["conversation_id"], "Voice call · 9:00 AM · other")
    assert again["title"] == "Voice call · 2:29 PM · decision log"

    used = history.create("New conversation")
    turn, _replayed = history.begin({
        "schema": "minimoi.cos.send/1", "conversation_id": used["conversation_id"],
        "turn_id": "t-000000000001", "text": "The build plan", "channel": "web"})
    history.answer(used["conversation_id"], turn["turn_id"], "Noted.")
    stayed = history.name_voice_call(used["conversation_id"], "Voice call · 2:29 PM · decision log")
    assert stayed["title"] == "The build plan"

    chosen = history.create("Plans")
    assert history.name_voice_call(chosen["conversation_id"], "Voice call · 2:29 PM · decision log")["title"] == "Plans"
