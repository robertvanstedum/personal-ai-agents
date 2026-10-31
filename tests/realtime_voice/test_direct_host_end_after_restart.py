"""Review S-2 (A4_FIX_BRIEF.md item 4): End closes a session the owner holds, after a restart too.

End used to check only this process's ledger (sessions.owner_of), which knows only the sessions it started, so after any
restart End answered 403 on both hosts (S3's "open sessions End" not met). Now, when the ledger does not know the session,
End reads its durable start marker (owned_marker, the check behind the summary and reconciliation): it must name this owner
AND the conversation End names, else 403 as before. Not ended yet, it is closed as End closes any session (the end marker
with its capture report), and its capture is "incomplete" with no count, since what the page posted to the old process may
be missing. A second End is harmless. Nothing else gains an owner: a consult or transcript post on that session is still
refused. The fake adapter of test_direct_host.py; a restart is a new app over the same host and store. No provider, no
Keychain, no microphone, no network."""
from flask import Flask

from core.realtime_voice.direct_host import PLAYBACK_RECORD, install_direct_voice

from test_direct_host import BASE, ENV, OWNER, consult, post, rig, start, turn  # noqa: F401


def restarted(r):
    """A new process: a fresh app and ledger over the same host and store; nothing of the old process's memory."""
    app = Flask("restarted")
    _metadata, sessions = install_direct_voice(app, r.host, url_prefix="/tp/voice", environ=dict(ENV), budget=r.budget,
                                               clock=lambda: r.clock[0], summary_http=r.gateway)
    return app.test_client(), sessions


def end(client, sid, **body):
    return client.post(BASE + f"/session/{sid}/end", headers=OWNER, json=body)


def test_end_after_a_restart_closes_the_owners_session_from_its_start_marker(rig):
    r = rig
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "The kiln needs a seal.")])
    client, sessions = restarted(r)
    assert not sessions.known(sid)
    answer = end(client, sid, conversation_id="owner", last_item_id="t1", unacknowledged=["t1"])
    assert answer.status_code == 200
    assert answer.get_json() == {"ok": True, "ended": True, "capture": "incomplete"}       # no count: it may be missing more
    marker = r.store.markers[(sid, "end")]
    assert marker["reason"] == "end" and marker["capture"] == "incomplete" and "missing" not in marker
    assert marker["boundary"] == "t1" and r.host.ends == [sid]                             # the host's ended() ran once
    again = end(client, sid, conversation_id="owner")                                       # a second End: harmless
    assert again.status_code == 200 and again.get_json() == {"ok": True, "ended": True, "capture": "incomplete"}
    assert r.host.ends == [sid]


def test_end_after_a_restart_keeps_the_playback_and_the_turns_not_acted_on(rig):
    r = rig
    sid = start(r)
    client, _sessions = restarted(r)
    assert end(client, sid, conversation_id="owner", summary_playback="drained", not_acted=["t3"],
               not_acted_why={"t3": "overtaken"}).status_code == 200
    assert r.store.kept_records[(sid, PLAYBACK_RECORD)]["heard"] is True
    assert r.store.kept_records[(sid, "not_acted")]["owner_item_ids"] == ["t3"]


def test_events_carried_by_end_after_a_restart_are_not_kept_and_capture_says_so(rig):
    r = rig
    sid = start(r)
    client, _sessions = restarted(r)
    answer = end(client, sid, conversation_id="owner", events=[turn(r, "user", "t5", "Late words.")],
                 last_item_id="t5", unacknowledged=[])
    assert answer.get_json()["capture"] == "incomplete" and not r.store.rows


def test_a_session_already_ended_before_the_restart_answers_its_first_close(rig):
    r = rig
    sid = start(r)
    assert end(r.client, sid, last_item_id="t0", unacknowledged=[]).get_json()["capture"] == "complete"
    client, _sessions = restarted(r)
    again = end(client, sid, conversation_id="owner")
    assert again.status_code == 200 and again.get_json()["capture"] == "complete"
    assert r.store.markers[(sid, "end")]["capture"] == "complete" and r.host.ends == [sid]


def test_a_forged_session_id_is_refused_after_a_restart(rig):
    import uuid
    r = rig
    start(r)
    client, _sessions = restarted(r)
    for forged in (str(uuid.uuid4()), "vs-0000000000000000", "not-a-session"):
        refused = end(client, forged, conversation_id="owner")
        assert refused.status_code == 403 and refused.get_json() == {"ok": False, "error": "not your session"}
    assert not any(what == "end" for _sid, what in r.store.markers)


def test_another_owners_session_is_refused_after_a_restart(rig):
    r = rig
    sid = start(r)
    r.store.markers[(sid, "start")]["principal"] = "anna"                    # her session, in the same conversation
    client, _sessions = restarted(r)
    refused = end(client, sid, conversation_id="owner")
    assert refused.status_code == 403 and (sid, "end") not in r.store.markers


def test_end_after_a_restart_must_name_the_sessions_own_conversation(rig):
    r = rig
    sid = start(r)
    client, _sessions = restarted(r)
    for body in ({}, {"conversation_id": "elsewhere"}, {"conversation_id": 7}):
        refused = end(client, sid, **body)
        assert refused.status_code == 403 and (sid, "end") not in r.store.markers, body
    r.store.markers[(sid, "start")]["conversation_id"] = "elsewhere"          # a marker for another conversation
    assert end(client, sid, conversation_id="owner").status_code == 403 and (sid, "end") not in r.store.markers


def test_a_session_this_process_ran_for_someone_else_is_still_refused(rig):
    r = rig
    sid = start(r)
    r.sessions._sessions[sid]["owner"] = "anna"                               # held here, but not his
    refused = end(r.client, sid, conversation_id="owner")
    assert refused.status_code == 403 and (sid, "end") not in r.store.markers


def test_end_after_a_restart_gives_no_consult_or_transcript_post_an_owner(rig):
    r = rig
    sid = start(r)
    client, _sessions = restarted(r)
    assert end(client, sid, conversation_id="owner").status_code == 200
    asked = client.post(BASE + "/consult", headers=OWNER, json={"session_id": sid, "call_id": "host-1", "name": "consult",
                                                                "owner_transcript_item": "What is on my agenda?",
                                                                "owner_item_id": "t9", "arguments": {}})
    assert asked.status_code == 403 and not r.host.turns
    kept = client.post(BASE + f"/session/{sid}/transcript", headers=OWNER, json={"events": [turn(r, "user", "t9", "x")]})
    assert kept.status_code == 403 and not r.store.rows


def test_a_store_that_cannot_keep_the_end_marker_says_so(rig):
    r = rig
    sid = start(r)
    client, _sessions = restarted(r)
    r.store.mark_end = lambda *a, **k: None
    refused = end(client, sid, conversation_id="owner")
    assert refused.status_code == 503 and refused.get_json() == {"ok": False, "error": "not kept"}
