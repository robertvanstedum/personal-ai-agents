"""#298 part 2.3: the meeting a conversation opens is named from the topic, seeded once with real context, reports who is where without ever saying 'joined', and is followed up
with one note per milestone and one digest in the source conversation. A scripted Records; no model, no network."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from flask import Flask, session

from minimoi_portal.guild_ui import meeting_action as M
from minimoi_portal.guild_ui import meeting_followup as MF


class FakeRooms:
    """Just enough of Records for the meeting action: rooms, invitations, events with sequence numbers, participants and turns."""
    def __init__(self, approved=("mc", "claude-code", "codex")):
        self.rooms, self.calls, self.approved = {}, [], set(approved)
        self.reach = {}
        self.turns = []

    def __call__(self, method, path, payload=None, operation_key=None):
        self.calls.append((method, path, payload, operation_key))
        p = path.replace("/api/v1", "")
        if method == "POST" and p == "/rooms":
            for rid, r in self.rooms.items():
                if r["key"] == operation_key:
                    return 201, {"result": {"id": rid}}
            rid = f"room-{len(self.rooms) + 1}"
            self.rooms[rid] = {"key": operation_key, "title": payload["title"], "purpose": payload["purpose"], "events": [], "members": {}, "keys": set(), "state": "active"}
            return 201, {"result": {"id": rid}}
        if p == "/approved-agents":
            return 200, {"agents": [{"agent": a, "approved": a in self.approved} for a in ("mc", "claude-code", "codex")]}
        parts = p.strip("/").split("/")
        room = self.rooms.get(parts[1]) if len(parts) > 1 else None
        if room is None:
            return 404, {}
        if method == "GET" and len(parts) == 2:
            return 200, {"state": room["state"], "events": room["events"]}
        if method == "POST" and parts[2:] == ["invite"]:
            room["members"][payload["actor"]] = "invited"
            return 200, {}
        if method == "POST" and parts[2:] == ["events"]:
            if operation_key in room["keys"]:
                return 200, {"result": {"seq": next(e["seq"] for e in room["events"] if e.get("key") == operation_key)}}
            room["keys"].add(operation_key)
            seq = len(room["events"]) + 1
            room["events"].append({"seq": seq, "actor": "robert", "kind": payload["kind"], "body": payload["body"], "key": operation_key})
            return 201, {"result": {"seq": seq}}
        if method == "GET" and parts[2:] == ["turns"]:
            return 200, {"participants": [{"id": a, "rsvp": "accepted" if a == "claude-code" else "invited", "reach": self.reach.get(a, {"state": "reachable", "reason": None})}
                                          for a in room["members"]], "turns": self.turns}
        return 404, {}

    def reply(self, room, actor, text):
        r = self.rooms[room]
        r["events"].append({"seq": len(r["events"]) + 1, "actor": actor, "kind": "message", "body": text})


class Floor:
    def __init__(self):
        self.notes = {}

    def add_note(self, nid, text, author):
        self.notes.setdefault(nid, {"id": nid, "text": text})
        return SimpleNamespace(outcome="kept")

    def get_note(self, nid):
        return self.notes[nid]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMOI_MC_MEETINGS", "1")
    monkeypatch.setattr(M, "_mc_last_answer", lambda conv, note_id: "The two issues are about limits and about evidence for agents.")
    monkeypatch.setattr(M, "_github_items", lambda text: [(296, "Operate limits", "https://github.com/x/y/issues/296"), (297, "Evidence to agents", "https://github.com/x/y/issues/297")])
    app = Flask(__name__)
    app.secret_key = "t"
    rooms = FakeRooms()
    app.extensions["rooms_who"] = lambda: (SimpleNamespace(ok=True, principal="robert"), None)
    app.extensions["rooms_access"] = SimpleNamespace(directory=str(tmp_path))
    app.extensions["rooms_call"] = rooms
    ctx = app.test_request_context("/")
    ctx.push()
    session["user"] = {"username": "robert", "tier": "owner"}
    yield SimpleNamespace(app=app, rooms=rooms, floor=Floor(), dir=str(tmp_path))
    ctx.pop()


NOTE = {"text": "Can you also open a new room and have claude and codex join so we can discuss these two issues #296 and #297"}
CONV = {"id": "c-test"}


def run(note_id="n1", note=NOTE, title="Discuss #296 and #297"):
    return M.execute(f"MEETING mc,claude-code,codex: {title}", note, note_id, CONV)


def test_the_room_is_named_from_the_topic_and_seeded_once_with_the_context_addressed_to_those_invited(world):
    reply = run()
    [room] = world.rooms.rooms.values()
    assert room["title"] == "Discuss #296 and #297" and room["purpose"].endswith("Topic: Discuss #296 and #297")
    [seed] = room["events"]
    assert seed["body"].startswith("@mc @claude-code @codex\nOpening context, posted by the platform at Robert's request")
    for part in ("Topic: Discuss #296 and #297", "Robert asked: Can you also open a new room", "Master Craftsman's last answer in that conversation: The two issues are about limits",
                 "#296 Operate limits (https://github.com/x/y/issues/296)", "Replies in this room do not start work"):
        assert part in seed["body"]
    assert "[Open your recorded meeting: Discuss #296 and #297](/guild-next/guild/rooms?room=room-1)" in reply
    assert "Now: Master Craftsman: invited, reachable; Claude Code: accepted, reachable; Codex: invited, reachable." in reply
    assert "The room opens with the topic and context." in reply and "Nobody counts as joined until they have replied in the room." in reply
    assert "joined the meeting" not in reply


def test_a_replay_of_the_same_request_finds_the_same_room_and_never_seeds_twice_while_a_new_request_makes_a_new_room(world):
    run("n1")
    run("n1", title="Changed wording")
    assert len(world.rooms.rooms) == 1 and len(world.rooms.rooms["room-1"]["events"]) == 1
    assert len([c for c in world.rooms.calls if c[1] == "/api/v1/rooms/room-1/events"]) == 1               # the replay posted nothing
    run("n2")
    assert len(world.rooms.rooms) == 2 and len(world.rooms.rooms["room-2"]["events"]) == 1


def test_an_agent_that_is_not_approved_is_named_and_is_not_invited_or_addressed(world):
    world.rooms.approved.discard("codex")
    reply = run()
    room = world.rooms.rooms["room-1"]
    assert set(room["members"]) == {"mc", "claude-code"} and room["events"][0]["body"].startswith("@mc @claude-code\n")
    assert "Codex: not approved, so not invited" in reply


def test_with_nobody_approved_nothing_is_seeded_and_the_reply_says_so(world):
    world.rooms.approved.clear()
    reply = run()
    assert world.rooms.rooms["room-1"]["events"] == [] and reply.count("not approved, so not invited") == 3
    assert "I will post here" not in reply and "The room opens" not in reply


def test_a_seed_that_cannot_be_posted_says_nobody_has_been_asked(world, monkeypatch):
    real = world.rooms

    def flaky(method, path, payload=None, operation_key=None):
        if method == "POST" and path.endswith("/events"):
            return 500, {}
        return real(method, path, payload, operation_key)
    world.app.extensions["rooms_call"] = flaky
    reply = run()
    assert "its opening context could not be posted, so no one has been asked anything yet" in reply and "I will post here" not in reply


# ── the follow-up ─────────────────────────────────────────────────────────────

def follow(world, now):
    return MF.sync(CONV, "account:robert", world.dir, lambda m, p: world.rooms(m, "/api/v1" + p)[1], lambda nid, text: world.floor.get_note(nid) if world.floor.add_note(nid, text, None) else None,
                   clock=lambda: now)


def test_nothing_is_said_until_someone_replies_then_one_note_per_agent_and_one_digest_and_then_it_stops(world):
    run()
    t0 = time.time()
    notes, pending = follow(world, t0 + 5)
    assert notes == [] and pending == 1
    world.rooms.reply("room-1", "codex", "My view: start with the registry, then the evidence reference. " + "x" * 400)
    notes, pending = follow(world, t0 + 40)
    assert [n["id"] for n in notes] == ["mtg-room-1-codex-replied"] and pending == 1
    assert notes[0]["text"].startswith("**Codex replied in the meeting:** My view: start with the registry") and "…" in notes[0]["text"] and "room=room-1" in notes[0]["text"]
    world.rooms.reply("room-1", "claude-code", "Agreed on the order.")
    world.rooms.reply("room-1", "mc", "I will track both tickets.")
    notes, pending = follow(world, t0 + 70)
    ids = [n["id"] for n in notes]
    assert "mtg-room-1-digest" in ids and pending == 0
    digest = world.floor.notes["mtg-room-1-digest"]["text"]
    assert digest.startswith("**Meeting digest (excerpts of what each said, in their own words; nothing here is a summary or a decision):**")
    assert "- Codex: My view: start with the registry" in digest and "- Claude Code: Agreed on the order." in digest and "- Master Craftsman: I will track both tickets." in digest
    again, pending = follow(world, t0 + 100)
    assert again == [] and pending == 0                                                              # finished: nothing more, nothing repeated
    assert len(world.floor.notes) == 4


def test_the_seed_itself_is_never_counted_as_a_reply(world):
    run()
    notes, pending = follow(world, time.time() + 10)
    assert notes == [] and pending == 1 and not any("replied" in n["text"] for n in world.floor.notes.values())


def test_an_agent_that_is_away_is_named_with_the_reason_after_a_few_minutes_and_the_digest_comes_when_time_is_up(world):
    run()
    world.rooms.reach["codex"] = {"state": "away", "reason": "its worker has not checked in recently"}
    world.rooms.reply("room-1", "claude-code", "Here.")
    world.rooms.reply("room-1", "mc", "Here too.")
    t0 = time.time()
    early, _ = follow(world, t0 + 60)
    assert not [n for n in early if "trouble" in n["id"]]                                            # too soon to call it
    notes, pending = follow(world, t0 + MF.AWAY_AFTER_S + 30)
    assert "mtg-room-1-codex-trouble" in [n["id"] for n in notes]
    assert world.floor.notes["mtg-room-1-codex-trouble"]["text"].startswith("**Codex is away: its worker has not checked in recently.** The meeting is still open; nothing was retried.")
    assert "mtg-room-1-digest" in world.floor.notes and "- Codex: is away" in world.floor.notes["mtg-room-1-digest"]["text"] and pending == 0


def test_a_failed_turn_is_reported_with_its_reason_and_the_other_agents_still_count(world):
    run()
    world.rooms.turns = [{"addressee": "codex", "state": "failed", "disposition": "relay_busy"}]
    world.rooms.reply("room-1", "claude-code", "I can take the build.")
    notes, _ = follow(world, time.time() + 10)
    assert "mtg-room-1-codex-trouble" in [n["id"] for n in notes] and "could not answer: relay busy" in world.floor.notes["mtg-room-1-codex-trouble"]["text"]
    assert "mtg-room-1-claude-code-replied" in world.floor.notes


def test_when_time_is_up_the_digest_says_so_and_files_what_arrived(world):
    run()
    world.rooms.reply("room-1", "mc", "Only me so far.")
    notes, pending = follow(world, time.time() + MF.WATCH_S + 5)
    digest = world.floor.notes["mtg-room-1-digest"]["text"]
    assert digest.startswith("**Meeting so far (time is up for now):**") and "- Claude Code: no reply yet" in digest and pending == 0


def test_another_conversations_meetings_are_never_followed_here(world):
    run()
    other = MF.sync({"id": "c-other"}, "account:robert", world.dir, lambda m, p: {}, lambda *a: None)
    assert other == ([], 0)
    assert MF.sync(CONV, "account:someone-else", world.dir, lambda m, p: {}, lambda *a: None) == ([], 0)


def test_follow_is_silent_when_meetings_are_off_or_there_is_no_owner_session(world, monkeypatch):
    monkeypatch.delenv("MINIMOI_MC_MEETINGS")
    assert M.follow(CONV, world.floor) == ([], 0)
