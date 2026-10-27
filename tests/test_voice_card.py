"""MC's voice card loads from a release and never invents a voice id."""
import json

from core.realtime_voice.voice_card import load_card


def test_an_unset_voice_uses_the_provider_default_and_says_so(tmp_path, monkeypatch):
    import core.realtime_voice.voice_card as cards
    release = tmp_path / "0.9.0"
    release.mkdir()
    (release / "voice.json").write_text(json.dumps({
        "schema": 1,
        "card": "Master Craftsman. English by default. Light Austrian accent.",
        "voices": {},
    }))
    monkeypatch.setattr(cards, "RELEASES", tmp_path)
    loaded = load_card("0.9.0")
    assert loaded["voices"] == {"openai": "marin", "xai": "eve"}
    assert loaded["notices"] == ["openai voice is unset, using marin", "xai voice is unset, using eve"]
    assert "Austrian" in loaded["card"]


def test_a_named_voice_is_kept_and_an_unknown_one_falls_back(tmp_path, monkeypatch):
    import core.realtime_voice.voice_card as cards
    release = tmp_path / "0.9.1"
    release.mkdir()
    (release / "voice.json").write_text(json.dumps({
        "schema": 1,
        "card": "Master Craftsman.",
        "voices": {"openai": "cedar", "xai": "not-a-voice"},
    }))
    monkeypatch.setattr(cards, "RELEASES", tmp_path)
    loaded = load_card("0.9.1")
    assert loaded["voices"]["openai"] == "cedar"
    assert loaded["voices"]["xai"] == "eve"
    assert loaded["notices"] == ["xai voice is unset, using eve"]
