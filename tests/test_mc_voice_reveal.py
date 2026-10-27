"""Synthetic hide-and-reveal. No microphone and no provider."""
from minimoi_portal.guild_ui.mc_voice_reveal import VoiceReveal


def test_turns_stay_hidden_until_end_and_appear_once():
    voice = VoiceReveal()
    voice.start("s1")
    assert voice.keep("s1", {"speaker": "owner", "text": "hello"})
    assert voice.visible("s1") == []
    first = voice.end("s1")
    second = voice.end("s1")
    assert first["revealed"] is True
    assert first["turns"] == [{"speaker": "owner", "text": "hello"}]
    assert second["revealed"] is False
    assert voice.visible("s1") == first["turns"]


def test_a_drop_reveals_what_was_kept_with_the_recovery_line():
    voice = VoiceReveal()
    voice.start("s2")
    voice.keep("s2", {"speaker": "assistant", "text": "noted"})
    dropped = voice.end("s2", dropped=True)
    assert dropped["recovery"].startswith("Voice ended unexpectedly")
    assert dropped["turns"][0]["text"] == "noted"


def test_a_negated_were_done_does_not_propose_and_confirmation_authorizes_nothing():
    voice = VoiceReveal()
    assert voice.propose_summary("s3", "we're not done")["proposed"] is False
    proposed = voice.propose_summary("s3", "we're done")
    assert proposed["prompt"] == "Shall I sum up?"
    assert voice.confirm_summary("s3")["authorizes"] == "summary only"
