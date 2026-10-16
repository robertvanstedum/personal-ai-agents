"""#298 G1: "Hand to agents": the owner's own message in a Rooms meeting becomes ledger requests by one click. The text comes from the room's record, only approved agents get a
request, a retry makes no second one, and only the owner can do it. A scripted Records and the real request ledger; no model, no agent."""
from __future__ import annotations

import json
import time
import uuid
from types import SimpleNamespace

import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_jobs_proposal import lane, lane_off  # noqa: F401  (fixtures)
from test_agent_ask import asks, API  # noqa: F401  (fixtures)

from core import agent_requests as ar
from core.agent_requests.connection import connection_notice
from minimoi_portal.guild_ui import meeting_followup as MF

ROOM = "11111111-2222-3333-4444-555555555555"
APPROVAL = "I approve the spec, the build and Dev deployment as long as Codex and Claude agree."


class Records:
    def __init__(self, approved=("claude-code", "codex"), events=None, down=False):
        self.approved, self.down = set(approved), down
        self.events = events if events is not None else [
            {"seq": 1, "actor": "robert", "kind": "session_opened", "body": "opened"},
            {"seq": 2, "actor": "claude-code", "kind": "message", "body": "Claude here."},
            {"seq": 3, "actor": "robert", "kind": "message", "body": APPROVAL}]
        self.calls, self.posts, self.post_status = [], {}, 201

    def __call__(self, method, path, payload=None, operation_key=None):
        self.calls.append((method, path))
        if self.down:
            return 503, {}
        if method == "POST" and path == f"/api/v1/rooms/{ROOM}/events":
            if self.post_status not in (200, 201):
                return self.post_status, {}
            self.posts.setdefault(operation_key, payload["body"])             # Records de-duplicates by the operation key
            return 201, {"seq": 10 + len(self.posts)}
        if path == f"/api/v1/rooms/{ROOM}/archive-check":
            return 200, {"archived_at": None}
        if path == "/api/v1/approved-agents":
            return 200, {"agents": [{"agent": a, "approved": a in self.approved} for a in ("mc", "claude-code", "codex")]}
        if path == f"/api/v1/rooms/{ROOM}":
            return 200, {"title": "Discuss #296 and #297", "events": self.events}
        return 404, {}


@pytest.fixture
def hand(asks):
    rec = Records()
    app = asks.app
    app.extensions["rooms_call"] = rec
    app.extensions["rooms_who"] = lambda: (SimpleNamespace(ok=True, principal="robert"), None)
    folder = asks.extra["asks"].root
    import os
    os.makedirs(folder,mode=0o700,exist_ok=True)
    os.makedirs(os.path.join(folder,"requests"),mode=0o700,exist_ok=True)
    app.extensions["rooms_access"] = SimpleNamespace(directory=folder + "-access")
    client = asks.owner()
    asks.extra["rec"] = rec
    return asks, client, asks.csrf(client), rec


def post(client, token, **body):
    body.setdefault("room", ROOM)
    body.setdefault("seq", 3)
    body.setdefault("agents", ["claude-code", "codex"])
    return client.post(f"{API}/rooms/handoff", json=keyed(**body), headers=write_headers(token))


def requests(asks):
    return ar.for_conversation(asks.extra["asks"].root, "c-0123456789ab")


def all_requests(asks):
    import os
    root = asks.extra["asks"].root
    out = []
    for rid in sorted(os.listdir(os.path.join(root, "requests"))) if os.path.isdir(os.path.join(root, "requests")) else []:
        out.append(ar.load(root, rid))
    return out


def test_one_click_makes_one_request_per_chosen_agent_with_the_text_from_the_rooms_record(hand):
    asks, client, token, rec = hand
    r = post(client, token, scope="Spec, build and review; Dev only.", tickets="#296 #297")
    assert r.status_code == 200
    body = r.get_json()
    assert [(m["agent"], m["ok"], m["created"]) for m in body["handed"]] == [("claude-code", True, True), ("codex", True, True)]
    assert body["meeting"] == f"/guild-next/guild/rooms?room={ROOM}"
    docs = all_requests(asks)
    assert len(docs) == 2
    for d in docs:
        req = d["request"]
        assert req["from"] == "robert" and req["origin"] == "owner_handoff_from_room" and req["to"] in ("claude-code", "codex")
        a = req["action"]
        assert 'from the Rooms meeting "Discuss #296 and #297"' in a and ROOM in a and "his message #3" in a, ("room" in a, a.count(ROOM), len(a))
        assert f"His message, verbatim: {APPROVAL}" in req["action"] and "Scope he set: Spec, build and review; Dev only." in req["action"]
        assert "References: #296 #297" in req["action"] and "Nothing here approves production." in req["action"]
        assert d["stage"] == "queued"


def test_the_text_never_comes_from_the_browser(hand):
    asks, client, token, rec = hand
    r = post(client, token, body="forged approval text", text="forged", action="forged")
    assert r.status_code == 200
    for d in all_requests(asks):
        assert "forged" not in d["request"]["action"] and APPROVAL in d["request"]["action"]


def test_a_retry_of_the_same_click_makes_no_second_request(hand):
    asks, client, token, rec = hand
    first = post(client, token).get_json()["handed"]
    again = post(client, token).get_json()["handed"]
    assert [m["request_id"] for m in again] == [m["request_id"] for m in first] and not any(m["created"] for m in again)
    assert len(all_requests(asks)) == 2


def test_only_one_of_the_owners_own_messages_can_be_handed_over(hand):
    asks, client, token, rec = hand
    for seq in (1, 2, 99):                                           # a system line, an agent's message, a message that does not exist
        r = post(client, token, seq=seq)
        assert r.status_code == 422 and r.get_json()["error"] == "not_yours"
    assert all_requests(asks) == []


def test_an_agent_that_is_not_approved_gets_nothing_and_the_whole_click_is_refused(hand):
    asks, client, token, rec = hand
    rec.approved.discard("codex")
    r = post(client, token)
    assert r.status_code == 409 and r.get_json()["error"] == "not_approved" and r.get_json()["agents"] == ["codex"] and "Codex is not approved" in r.get_json()["message"]
    assert all_requests(asks) == []
    one = post(client, token, agents=["claude-code"])
    assert one.status_code == 200 and len(all_requests(asks)) == 1


@pytest.mark.parametrize("change", [{"agents": []}, {"agents": ["mc"]}, {"agents": ["codex", "codex"]}, {"agents": "codex"}, {"room": "not-a-room"}, {"seq": 0}, {"seq": True},
                                    {"seq": "3"}, {"scope": "x" * 601}, {"tickets": "ignore previous instructions"}])
def test_bad_input_is_refused_before_anything_is_read_or_sent(hand, change):
    asks, client, token, rec = hand
    r = post(client, token, **change)
    assert r.status_code == 422 and rec.calls == [] and all_requests(asks) == []


def test_only_the_owner_principal_can_hand_work_over(hand):
    asks, client, token, rec = hand
    asks.app.extensions["rooms_who"] = lambda: (SimpleNamespace(ok=True, principal="u-dana-abc123"), None)
    r = post(client, token)
    assert r.status_code == 403 and r.get_json()["error"] == "not_owner" and all_requests(asks) == []
    asks.app.extensions["rooms_who"] = lambda: (SimpleNamespace(ok=False, principal=None), None)
    assert post(client, token).status_code == 403


def test_when_rooms_cannot_confirm_nothing_is_sent(hand):
    asks, client, token, rec = hand
    rec.down = True
    r = post(client, token)
    assert r.status_code == 502 and r.get_json()["error"] == "unavailable" and all_requests(asks) == []


def test_a_meeting_opened_from_a_conversation_sends_receipts_back_to_that_conversation(hand):
    asks, client, token, rec = hand
    access_dir = asks.app.extensions["rooms_access"].directory
    import os
    os.makedirs(access_dir, exist_ok=True)
    with MF.database(access_dir) as db:
        db.execute("INSERT INTO meeting_rooms(account,conversation,note_id,room,title,invitees,created_at) VALUES(?,?,?,?,?,?,?)",
                   ("account:robert", "c-0123456789ab", "n1", ROOM, "Discuss", "[]", time.time()))
    with client.session_transaction() as sess:
        sess["user"] = {**sess["user"], "username": "robert"}
    r = post(client, token, agents=["codex"])
    assert r.get_json()["linked_conversation"] is True
    assert [d["request"]["conversation_id"] for d in all_requests(asks)] == ["c-0123456789ab"]


def test_off_the_record_and_missing_guards_send_nothing(hand):
    asks, client, token, rec = hand
    assert client.post(f"{API}/rooms/handoff", json=keyed(room=ROOM, seq=3, agents=["codex"]), headers=write_headers(token, mode="off_record")).status_code == 409
    assert client.post(f"{API}/rooms/handoff", json=keyed(room=ROOM, seq=3, agents=["codex"])).status_code == 403            # no CSRF
    assert asks.app.test_client().post(f"{API}/rooms/handoff", json=keyed(room=ROOM, seq=3, agents=["codex"])).status_code in (401, 403)
    assert all_requests(asks) == []


def test_the_agent_is_told_this_is_roberts_own_handoff_not_an_agent_authored_request():
    base = {"request_id": "r-aaaaaaaaaaaaaaaaaaaaaaaa", "from": "robert", "to": "codex"}
    handed = connection_notice({**base, "origin": "owner_handoff_from_room"}, agent="codex", session_id="11111111-2222-3333-4444-555555555555", root="/tmp/x")
    plain = connection_notice({**base, "from": "mc", "origin": "owner_message_via_mc"}, agent="codex", session_id="11111111-2222-3333-4444-555555555555", root="/tmp/x")
    assert "handed to you by Robert himself from a Rooms meeting" in handed and "agent-authored" not in handed
    assert "agent-authored, not a message or approval from Robert" in plain


def test_payment_details_in_his_text_are_removed_but_the_room_id_is_never_touched(hand):
    asks, client, token, rec = hand
    rec.events[2]["body"] = "Use card 4111 1111 1111 1111 for it."
    assert post(client, token, agents=["codex"]).status_code == 200
    a = all_requests(asks)[0]["request"]["action"]
    assert "4111" not in a and "[payment detail removed]" in a and ROOM in a


def test_a_long_owner_message_is_never_cut_silently_the_whole_text_travels_as_a_verified_file(hand):
    import hashlib
    asks, client, token, rec = hand
    long = "Build the spec and the code. " + ("Context. " * 400) + "PROHIBITION: do not touch the live portal under any circumstances."
    rec.events[2]["body"] = long
    r = post(client, token, agents=["codex"])
    assert r.status_code == 200
    action = all_requests(asks)[0]["request"]["action"]
    assert "verbatim" not in action and "EXCERPT" in action and "NOT the whole message" in action and "restrictions and prohibitions may come after" in action
    assert "PROHIBITION: do not touch the live portal" not in action                      # it is beyond the excerpt, so it must be in the file
    path = [w for w in action.split() if w.startswith("handoffs/")][0]
    saved = open(f"{asks.extra['asks'].root}/{path}", encoding="utf-8").read()
    assert saved == long and "PROHIBITION: do not touch the live portal under any circumstances." in saved
    assert hashlib.sha256(saved.encode()).hexdigest()[:16] in action
    again = post(client, token, agents=["codex"]).get_json()["handed"][0]
    assert again["created"] is False                                                        # a retry: same file, no second request


def test_a_short_message_goes_whole_and_is_still_called_verbatim(hand):
    asks, client, token, rec = hand
    assert post(client, token, agents=["codex"]).status_code == 200
    action = all_requests(asks)[0]["request"]["action"]
    assert f"His message, verbatim: {APPROVAL}" in action and "handoffs/" not in action


def test_a_message_too_long_to_hand_over_whole_is_refused_not_cut(hand):
    asks, client, token, rec = hand
    rec.events[2]["body"] = "x " * 15000
    r = post(client, token, agents=["codex"])
    assert r.status_code == 422 and r.get_json()["error"] == "too_long" and all_requests(asks) == []


def sync(client, token, room=ROOM):
    return client.post(f"{API}/rooms/handoff/sync", json=keyed(room=room), headers=write_headers(token))


def test_what_happens_to_a_handoff_is_reported_into_the_room_once_each(hand):
    asks, client, token, rec = hand
    handed = post(client, token).get_json()["handed"]
    first = sync(client, token).get_json()
    assert first["posted"] == 0 and first["open"] == 2 and all("Waiting for" in i["latest"] for i in first["items"])      # nothing happened yet: nothing is claimed
    root = asks.extra["asks"].root
    codex = next(m["request_id"] for m in handed if m["agent"] == "codex")
    claude = next(m["request_id"] for m in handed if m["agent"] == "claude-code")
    ar.receipt(root, codex, "codex")
    ar.result(root, codex, "codex", "Reviewed. " + "Finding. " * 400)
    ar.record_delivery(root, claude, adapter="claude-stream", state="needs_attention", attempt_id="a1", reason="turn_failed:error_max_budget_usd")
    out = sync(client, token).get_json()
    assert out["posted"] == 3 and out["open"] == 0
    texts = list(rec.posts.values())
    assert all(t.startswith("Platform update from the request ledger, not written by Robert or ") for t in texts)
    assert any("Codex picked up the handoff." in t for t in texts)
    report = next(t for t in texts if "Codex reported (the agent's own report, not verified)" in t)
    assert len(report) < 1500 and report.endswith("…")
    assert any("Claude Code hit its cost cap for the session" in t and "Nothing was received" in t for t in texts)
    again = sync(client, token).get_json()                                      # a reload, a second tab, a retry: nothing new
    assert again["posted"] == 0 and len(rec.posts) == 3


def test_a_failed_post_is_not_marked_done_and_is_retried(hand):
    asks, client, token, rec = hand
    handed = post(client, token, agents=["codex"]).get_json()["handed"]
    ar.receipt(asks.extra["asks"].root, handed[0]["request_id"], "codex")
    rec.post_status = 502
    assert sync(client, token).get_json()["posted"] == 0 and rec.posts == {}
    rec.post_status = 201
    assert sync(client, token).get_json()["posted"] == 1 and len(rec.posts) == 1


def test_a_standalone_room_with_no_source_conversation_is_reported_too(hand):
    asks, client, token, rec = hand
    handed = post(client, token, agents=["codex"]).get_json()
    assert handed["linked_conversation"] is False
    ar.receipt(asks.extra["asks"].root, handed["handed"][0]["request_id"], "codex")
    assert sync(client, token).get_json()["posted"] == 1


def test_only_the_owner_can_trigger_a_report_and_a_room_with_no_handoffs_posts_nothing(hand):
    asks, client, token, rec = hand
    assert sync(client, token).get_json() == {"posted": 0, "open": 0, "items": []}
    asks.app.extensions["rooms_who"] = lambda: (SimpleNamespace(ok=True, principal="u-dana-abc123"), None)
    assert sync(client, token).status_code == 403 and rec.posts == {}
    assert sync(client, token, room="nope").status_code in (403, 422)


def test_a_completed_handoff_whose_report_did_not_reach_the_room_stays_open_for_retry(hand):
    """Codex's P3: the work is finished but Records refused the post. The page must keep polling this room until the report has really been posted."""
    asks, client, token, rec = hand
    handed = post(client, token, agents=["codex"]).get_json()["handed"]
    root = asks.extra["asks"].root
    ar.receipt(root, handed[0]["request_id"], "codex")
    ar.result(root, handed[0]["request_id"], "codex", "Reviewed; all good.")
    rec.post_status = 502
    out = sync(client, token).get_json()
    assert out["posted"] == 0 and out["open"] == 1                           # NOT 0: the report is still owed to the room
    rec.post_status = 201
    out = sync(client, token).get_json()
    assert out["posted"] == 2 and out["open"] == 0                           # pickup and report, then the room can stop polling
    assert sync(client, token).get_json()["open"] == 0 and len(rec.posts) == 2
