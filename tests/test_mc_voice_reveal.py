"""Synthetic hide-and-reveal on a floor-shaped store (Spec 164 §6). No microphone and no provider."""
import uuid
from datetime import datetime, timedelta, timezone

from core.realtime_voice.transcripts import note_parts
from minimoi_portal.guild_ui import mc_voice_reveal as reveal
from minimoi_portal.guild_ui.mc_voice_transcripts import AUTHOR_PREFIX, HEADER, PAGE
from minimoi_portal.guild_ui.stores import Author, WriteResult

T0 = datetime(2026, 10, 27, 20, 0, tzinfo=timezone.utc)


class Floor:
    floor = "f"

    def __init__(self):
        self.rows = {}

    def add_note(self, key, text, author, page=None, **_):
        if key in self.rows:
            old = self.rows[key]
            return WriteResult("kept" if old["text"] == text and old["who"] == author.id else "idempotency_mismatch",
                               old if old["text"] == text else None, True)
        row = {"id": len(self.rows) + 1, "request_id": key, "text": text, "who": author.id, "author_kind": author.kind,
               "context": {"page": page}, "created_at": "2026-10-27T20:00:00+00:00"}
        self.rows[key] = row
        return WriteResult("kept", row, False)

    def get_note(self, key):
        return self.rows.get(key)

    def all(self):
        return sorted(self.rows.values(), key=lambda r: r["id"])


def session(floor, minutes=15):
    sid = str(uuid.uuid4())
    assert reveal.mark_start(floor, sid, principal="robert", conversation_id="c-1", max_minutes=minutes, started=T0)
    for role, text in (("user", "What is stuck?"), ("assistant", "Let me check.")):
        for key, body in note_parts(sid, role, "item_" + role, 0, text, False, limit=2000,
                                    started="2026-10-27T20:00:01.000Z", ended="2026-10-27T20:00:02.500Z"):
            floor.add_note(key, body, Author(AUTHOR_PREFIX + sid, "platform", "Voice transcript"), page=PAGE)
    floor.add_note(reveal.consult_note_id(sid, 1), "What is stuck?", Author("robert", "owner", "Robert"), page="voice")
    reply = next(iter(reveal.consult_reply_ids(sid)))
    floor.add_note(reply, "Item 12.", Author("master_craftsman", "agent", "Master Craftsman"))
    floor.add_note("typed-1", "an ordinary typed note", Author("robert", "owner", "Robert"))
    return sid


def test_a_live_session_is_withheld_in_every_view_and_revealed_once_on_end():
    floor = Floor()
    sid = session(floor)
    shown, sessions = reveal.view(floor, floor.all(), live=lambda s: s == sid, when=T0 + timedelta(minutes=1))
    assert [r["request_id"] for r in shown] == ["typed-1"]
    assert sessions == [{"session_id": sid, "state": "live", "started_at": "2026-10-27T20:00:00.000Z",
                         "ended_at": None, "line": None, "summary": None}]
    reveal.mark_end(floor, sid, ended=T0 + timedelta(minutes=2))
    first, sessions = reveal.view(floor, floor.all(), live=lambda s: True, when=T0 + timedelta(minutes=3))
    again, _ = reveal.view(floor, floor.all(), live=lambda s: True, when=T0 + timedelta(minutes=4))
    kinds = [r["voice"]["kind"] for r in first if "voice" in r]
    assert kinds == ["owner_turn", "assistant_turn", "consult_note", "consult_reply"]
    assert first == again and sessions[0]["state"] == "ended" and sessions[0]["ended_at"] == "2026-10-27T20:02:00.000Z"
    assert not any((r.get("context") or {}).get("page") == reveal.PAGE_SESSION for r in first)
    # A second End keeps the first end time: the reveal does not move.
    reveal.mark_end(floor, sid, ended=T0 + timedelta(minutes=9))
    assert reveal.read_marker(floor, sid, "end")["ended_at"] == "2026-10-27T20:02:00.000Z"


def test_a_dropped_session_shows_the_recovery_line_then_reveals_after_its_maximum():
    floor = Floor()
    sid = session(floor, minutes=15)
    shown, sessions = reveal.view(floor, floor.all(), live=lambda s: False, when=T0 + timedelta(minutes=5))
    assert [r["request_id"] for r in shown] == ["typed-1"]
    assert sessions[0]["state"] == "recovering" and sessions[0]["line"] == reveal.RECOVERY
    assert reveal.RECOVERY == "Voice session ended unexpectedly; recovering what was said."
    shown, sessions = reveal.view(floor, floor.all(), live=lambda s: True, when=T0 + timedelta(minutes=15))
    assert sessions[0]["state"] == "dropped" and sessions[0]["line"] is None and len(shown) == 5


def test_timestamps_and_interruption_are_kept_and_read_back():
    sid = str(uuid.uuid4())
    (_, body), = note_parts(sid, "assistant", "a1", 0, "Item twelve is", True, limit=2000, interrupted=True,
                            started="2026-10-27T20:00:01.000Z", ended="2026-10-27T20:00:01.900Z")
    match = HEADER.match(body)
    assert match["start"] == "2026-10-27T20:00:01.000Z" and match["end"] == "2026-10-27T20:00:01.900Z"
    assert match["interrupted"] == "true" and match["partial"] == "true" and body[match.end():] == "Item twelve is"
    # An older note, without times, reads as before; a session without a start marker is never withheld.
    (_, old), = note_parts(sid, "user", "u1", 0, "hello", False, limit=2000)
    assert old == "[Voice transcript: owner ASR; partial=false; part=1/1]\nhello" and HEADER.match(old)["start"] is None
    floor = Floor()
    floor.add_note("k", old, Author(AUTHOR_PREFIX + sid, "platform", "Voice transcript"), page=PAGE)
    shown, sessions = reveal.view(floor, floor.all(), live=lambda s: True)
    assert shown == floor.all() and sessions == []


def test_an_unreadable_marker_fails_closed():
    floor = Floor()
    sid = session(floor)

    class Broken(Floor):
        def get_note(self, key):
            raise RuntimeError("store down")
    broken = Broken()
    broken.rows = floor.rows
    shown, sessions = reveal.view(broken, broken.all(), live=lambda s: False)
    assert [r["request_id"] for r in shown] == ["typed-1"]
    assert sessions[0]["state"] == "unknown" and sessions[0]["line"] == reveal.RECOVERY
