"""Spec 164 §6 on the real portal and a SQLite floor: a live or recovering direct-path voice session's records are
withheld from every view of the conversation (the notes list, the page, the voice export), then shown once revealed.
No provider, no microphone, no network."""
import uuid
from datetime import datetime, timedelta, timezone

from floor_helpers import load_portal, write_headers  # noqa:F401
from floor_db_helpers import floor_db, floored, keyed  # noqa:F401
from core.realtime_voice.transcripts import note_parts
from minimoi_portal.guild_ui import mc_voice_reveal as reveal
from minimoi_portal.guild_ui.conversations import conversations_of
from minimoi_portal.guild_ui.mc_voice_transcripts import AUTHOR_PREFIX, PAGE
from minimoi_portal.guild_ui.stores import Author

API = "/guild-next/api/v1"


def setup(portal):
    client = portal.owner()
    token = portal.csrf(client)
    made = client.post(API + "/conversations", json=keyed(), headers=write_headers(token))
    cid = made.get_json()["conversation"]["id"]
    config = portal.app.extensions["guild_ui_next"]
    conv = conversations_of(config["services"]).get(cid, "robert")
    floor = config["services"].floor.for_floor(conv["notes_floor"])
    sid = str(uuid.uuid4())
    reveal.mark_start(floor, sid, principal="robert", conversation_id=cid, max_minutes=30)
    for key, body in note_parts(sid, "user", "item_u1", 0, "the kiln seal question", False, limit=2000):
        floor.add_note(key, body, Author(AUTHOR_PREFIX + sid, "platform", "Voice transcript"), page=PAGE)
    floor.add_note("typed-note-1", "a typed note", Author("robert", "owner", "Robert"))
    return client, cid, conv, floor, sid, config


def test_the_notes_list_the_page_and_the_export_withhold_a_recovering_session_then_show_it_once(floored):
    client, cid, conv, floor, sid, config = setup(floored)
    listed = client.get(API + f"/notes?conversation={cid}").get_json()
    assert [n["text"] for n in listed["notes"]] == ["a typed note"]
    assert listed["voice_sessions"] == [{"session_id": sid, "state": "recovering", "line": reveal.RECOVERY,
                                         "started_at": listed["voice_sessions"][0]["started_at"], "ended_at": None,
                                         "summary": None}]
    page = client.get(f"/guild-next/guild/build?c={cid}")
    assert page.status_code == 200 and "a typed note" in page.text and "kiln seal" not in page.text
    export = client.get(API + f"/conversations/{cid}/voice-transcript")
    assert export.status_code == 200 and "kiln seal" not in export.text

    reveal.mark_end(floor, sid)
    listed = client.get(API + f"/notes?conversation={cid}").get_json()
    voice = [n for n in listed["notes"] if "voice" in n]
    assert [n["voice"] for n in voice] == [{"session_id": sid, "kind": "owner_turn"}]
    assert listed["voice_sessions"][0]["state"] == "ended" and listed["voice_sessions"][0]["line"] is None
    assert listed["voice_sessions"][0]["summary"] == "none"
    assert not any("[Voice session]" in n["text"] for n in listed["notes"])           # markers are never messages
    assert "kiln seal" in client.get(API + f"/conversations/{cid}/voice-transcript").text
    assert "kiln seal" in client.get(f"/guild-next/guild/build?c={cid}").text
    assert client.get(API + f"/notes?conversation={cid}").get_json()["notes"] == listed["notes"]


def test_a_session_without_end_is_shown_after_its_maximum(floored, monkeypatch):
    client, cid, _conv, _floor, sid, _config = setup(floored)
    later = datetime.now(timezone.utc) + timedelta(minutes=31)
    monkeypatch.setattr(reveal, "now", lambda: later)
    listed = client.get(API + f"/notes?conversation={cid}").get_json()
    assert listed["voice_sessions"][0]["state"] == "dropped"
    assert [n["voice"]["kind"] for n in listed["notes"] if "voice" in n] == ["owner_turn"]
