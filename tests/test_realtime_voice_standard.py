from core.realtime_voice.standards import (
    conversation_turn_detection,
    memo_turn_detection,
)


def test_openai_conversation_standard_uses_server_vad():
    assert conversation_turn_detection("openai") == {
        "type": "server_vad",
        "prefix_padding_ms": 300,
        "silence_duration_ms": 1200,
    }


def test_chained_conversation_disables_provider_response():
    config = conversation_turn_detection("openai", create_response=False)
    assert config["create_response"] is False
    assert config["interrupt_response"] is False


def test_memo_standard_has_no_silence_commit():
    assert memo_turn_detection() is None
