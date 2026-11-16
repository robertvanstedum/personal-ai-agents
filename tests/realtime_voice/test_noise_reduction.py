"""#651: every OpenAI voice session asks the provider for input noise reduction, with a switch to turn it off."""
from unittest import mock

from core.realtime_voice.providers import openai_realtime


def test_default_is_near_field_and_off_switch_works():
    assert openai_realtime.noise_reduction({}) == {"noise_reduction": {"type": "near_field"}}
    assert openai_realtime.noise_reduction({"MINIMOI_VOICE_NOISE_REDUCTION": "far_field"}) == {"noise_reduction": {"type": "far_field"}}
    assert openai_realtime.noise_reduction({"MINIMOI_VOICE_NOISE_REDUCTION": "off"}) == {}
    assert openai_realtime.noise_reduction({"MINIMOI_VOICE_NOISE_REDUCTION": "loud"}) == {"noise_reduction": {"type": "near_field"}}


def test_the_minted_session_carries_it():
    sent = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"value": "ek_x", "expires_at": 1}

    def post(url, json=None, **_):
        sent.update(json)
        return R()

    with mock.patch.object(openai_realtime, "_require_api_key", return_value="k"), \
         mock.patch.object(openai_realtime.requests, "post", side_effect=post), \
         mock.patch.dict("os.environ", {}, clear=False):
        openai_realtime.mint_ephemeral_credential(instructions="i", voice="v", turn_detection={"type": "server_vad"},
                                                   transcription_language="de", user_id_for_safety_identifier="u")
    assert sent["session"]["audio"]["input"]["noise_reduction"] == {"type": "near_field"}
