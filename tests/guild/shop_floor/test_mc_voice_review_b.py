"""Codex's Chunk B review (REVIEW_codex_chunk_B.md): the server half of B1, B2, B4, B5 and B7 as regressions that assert the
CORRECT behaviour, on MC's real direct-voice host (the synthetic Guild mount of test_mc_voice_direct.py). Converted from
review-evidence-codex/test_repro_backend.py, which asserted the defects. They use only the fixture and request fields, so
they also run (and fail) against 93bf00c6. No provider, no Keychain, no microphone, no network."""
import json

from minimoi_portal.guild_ui import mc_voice_reveal as reveal
from test_mc_voice_direct import BASE, CID, call, post, ready, shown, start, summarize, turn, voice_records  # noqa: F401


def sessions_of(r):
    return r.config["mc_direct_voice_live"].__self__


# ── B1: one owner turn, one effect, however many calls dispatch it ──────────────────────────────────────────────────────

def test_two_calls_for_one_owner_turn_save_one_note_and_the_second_gets_the_first_answer(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    words = "Make a note that the kiln needs a new seal"
    first = call(r, sid, call_id="host-1", name="save_note", words=words, owner_turn="u1").get_json()
    second = call(r, sid, call_id="provider-2", name="save_note", words=words, owner_turn="u1").get_json()
    assert first["saved"] is True and len(r.cos.calls) == 1
    assert second["receipt"] == first["receipt"] and second.get("same_turn") is True


def test_a_manual_check_then_the_models_note_on_the_same_words_is_one_effect(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    words = "Make a note that the glaze is ready"
    call(r, sid, call_id="host-1", name="consult", words=words, owner_turn="u1")                 # Check with MC
    call(r, sid, call_id="call_model", name="save_note", words=words, owner_turn="u1")          # the model's own call
    assert len(r.cos.calls) == 1


def test_the_same_words_said_again_are_a_new_turn_and_a_new_effect(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    words = "Make a note that the kiln is hot"
    call(r, sid, call_id="a", name="save_note", words=words, owner_turn="u1")
    call(r, sid, call_id="b", name="save_note", words=words, owner_turn="u2")
    assert len(r.cos.calls) == 2


def test_a_second_call_while_the_first_runs_is_in_flight_with_words_and_runs_nothing(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    seen = []
    r.during.append(lambda: seen.append(call(r, sid, call_id="provider-2", owner_turn="u1")))
    first = call(r, sid, call_id="host-1", owner_turn="u1")
    assert first.get_json()["answered"] is True and len(r.turns) == 1
    assert seen[0].status_code == 409 and seen[0].get_json()["error"] == "in flight" and seen[0].get_json()["spoken"]


def test_a_call_without_its_owner_turn_is_refused_before_anything_runs(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    bare = r.client.post(BASE + "/consult", headers=r.headers, json={
        "session_id": sid, "call_id": "c1", "name": "consult", "owner_transcript_item": "What is stuck?"})
    assert bare.status_code == 400 and not r.turns


# ── B7: a kept answer can be asked for again, read only, even after the session ended ───────────────────────────────────

def test_an_answer_lost_after_end_is_returned_again_for_the_same_call_without_running_it(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    result = call(r, sid, call_id="call-1", owner_turn="u1").get_json()
    r.client.post(BASE + f"/session/{sid}/end", headers=r.headers)
    again = call(r, sid, call_id="call-1", owner_turn="u1")
    assert again.status_code == 200 and again.get_json() == result and len(r.turns) == 1
    assert call(r, sid, call_id="call-2", owner_turn="u2").status_code == 409             # nothing new after End


def test_an_answer_is_returned_again_after_the_session_ran_out_but_nothing_new_runs(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    result = call(r, sid, call_id="call-1", owner_turn="u1").get_json()
    r.clock[0] += 31 * 60                                                                 # past the maximum duration
    assert call(r, sid, call_id="call-1", owner_turn="u1").get_json() == result
    assert call(r, sid, call_id="call-2", owner_turn="u2").status_code == 409 and len(r.turns) == 1
