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


# ── B5: end intent heard once per turn, confirmed only by a later turn; Keep talking withdraws it ───────────────────────

def test_a_replayed_were_done_event_is_not_a_second_confirmation(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    event = turn(r, "user", "same-event", "Yes, we're done.")
    first = post(r, sid, [event]).get_json()
    replay = post(r, sid, [event]).get_json()
    assert first.get("propose_end") is True and not first.get("end_confirmed")
    assert replay.get("propose_end") is True and not replay.get("end_confirmed")          # the same answer, nothing moved
    assert len(voice_records(r, sid)) == 1
    later = post(r, sid, [turn(r, "user", "next-event", "Yes.", 3)]).get_json()
    assert later.get("end_confirmed") is True                                            # only his next turn confirms


def test_keep_talking_withdraws_the_proposal_so_a_later_yes_ends_nothing(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    assert post(r, sid, [turn(r, "user", "u1", "We're done.")]).get_json().get("propose_end") is True
    assert r.client.post(BASE + f"/session/{sid}/transcript", headers=r.headers,
                         json={"events": [], "proposal": "withdrawn"}).status_code == 200
    assert not post(r, sid, [turn(r, "user", "u2", "Yes, the glaze first.", 3)]).get_json().get("end_confirmed")


def test_a_proposal_and_a_later_yes_in_one_post_confirm(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    both = post(r, sid, [turn(r, "user", "u1", "We're done."), turn(r, "user", "u2", "Yes.", 3)]).get_json()
    assert both.get("end_confirmed") is True


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


# ── B2: the summary's cutoff; B4: what was not kept is said, after a reload too ─────────────────────────────────────────

def test_a_consult_still_running_at_the_summary_is_outside_its_cutoff_and_it_says_so(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    post(r, sid, [turn(r, "user", "u1", "Check the kiln, then we're done.")])
    sessions_of(r).admit(sid, "call-running")                                             # admitted, still running
    summary = summarize(r, sid).get_json()
    assert summary["state"] == "proposed"
    assert "still running" in r.cos.prompts[-1].text and "still running" in summary["written"]


def test_a_summary_asked_with_capture_incomplete_marks_the_session_and_says_so(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    post(r, sid, [turn(r, "user", "u1", "Some words that were kept.")])
    summary = summarize(r, sid, capture="incomplete").get_json()
    assert reveal.read_marker(r.notes, sid, "end")["capture"] == "incomplete"
    assert "could not be kept" in r.cos.prompts[-1].text and "could not be kept" in summary["written"]
    assert summary["covers"]["omitted"] is True


def test_an_end_saying_capture_incomplete_is_kept_and_shown_after_a_reload(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    post(r, sid, [turn(r, "user", "u1", "Kept words.")])
    ended = r.client.post(BASE + f"/session/{sid}/end", headers=r.headers, json={"capture": "incomplete"})
    assert ended.get_json() == {"ok": True, "ended": True}
    assert reveal.read_marker(r.notes, sid, "end")["capture"] == "incomplete"
    _rows, sessions = shown(r)                                                            # any later view, any device
    assert sessions[0]["state"] == "ended" and sessions[0]["capture"] == "incomplete"


def test_events_sent_with_end_that_are_refused_mark_the_session_incomplete(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    ended = r.client.post(BASE + f"/session/{sid}/end", headers=r.headers, json={"events": [{"role": "system"}]})
    assert ended.get_json() == {"ok": True, "ended": True}
    assert reveal.read_marker(r.notes, sid, "end")["capture"] == "incomplete"


def test_a_whole_session_keeps_its_end_marker_as_before(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    post(r, sid, [turn(r, "user", "u1", "All kept.")])
    r.client.post(BASE + f"/session/{sid}/end", headers=r.headers)
    marker = reveal.read_marker(r.notes, sid, "end")
    assert "capture" not in marker and "capture" not in shown(r)[1][0]
    assert json.loads(r.notes.get_note(reveal.key(sid, "end"))["text"][len(reveal.MARKER):]).keys() == {
        "event", "session_id", "reason", "ended_at"}
