"""Guild 1.1 dev, slice 2: conversations on the Shop floor, and a real Master
Craftsman session per conversation. File-first, server-owned metadata; every
housekeeping action is an owner-guarded, CSRF-checked write that calls no
model. The relay is mocked (FakeRuntime); no network, no model call."""
from __future__ import annotations

import json

import pytest

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from test_mc_turns import FakeRuntime, _openclaw, _turn_on

from minimoi_portal.guild_ui.conversations import (LEGACY_ID, ConversationStore, conversations_of,
                                                   session_conversation_id, title_from)

API = "/guild-next/api/v1"


def _new(client, token, **extra):
    r = client.post(f"{API}/conversations", json=keyed(**extra), headers=write_headers(token))
    assert r.status_code == 200, r.get_json()
    return r.get_json()["conversation"]


def _act(client, token, cid, action, **extra):
    return client.post(f"{API}/conversations/{cid}/{action}", json=keyed(**extra), headers=write_headers(token))


def _note(client, token, text, cid=None):
    body = keyed(text=text, **({"conversation_id": cid} if cid else {}))
    r = client.post(f"{API}/notes", json=body, headers=write_headers(token))
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _listing(client, view="active"):
    return client.get(f"{API}/conversations?view={view}").get_json()["conversations"]


@pytest.fixture
def turned(floored):
    runtime = FakeRuntime()
    _turn_on(floored, _openclaw(runtime))
    floored.extra["runtime"] = runtime
    return floored


# ── migration: today's thread is the first conversation, and still opens ──────

def test_the_existing_thread_becomes_the_first_conversation_and_still_opens(floored):
    client = floored.owner()
    token = floored.csrf(client)
    old = _note(client, token, "A note from before conversations existed")          # no conversation_id: the thread
    rows = _listing(client)
    assert [(r["id"], r["title"], r["legacy"]) for r in rows] == [(LEGACY_ID, "Shop floor thread", True)]
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert "A note from before conversations existed" in page and 'data-conv="shop-floor-thread"' in page
    listed = client.get(f"{API}/notes?conversation={LEGACY_ID}").get_json()["notes"]
    assert [n["request_id"] for n in listed] == [old["note"]["request_id"]]
    # Its notes keep the floor's own key: the store rows are unchanged.
    assert [r["floor"] for r in floored.extra["floor"].rows("floor_messages")] == [floored.app.extensions["guild_ui_next"]["services"].floor.floor]


# ── the list and every action ──────────────────────────────────────────────────

def test_new_rename_pin_archive_and_restore_with_newest_first_and_pinned_on_top(floored):
    client = floored.owner()
    token = floored.csrf(client)
    a = _new(client, token)
    assert a["title"] == "New conversation" and not a["legacy"]
    b = _new(client, token)
    _note(client, token, "## Rooms spec: **what** is left?\nSecond line", cid=a["id"])   # a is now the newest
    rows = _listing(client)
    assert [r["id"] for r in rows] == [a["id"], b["id"], LEGACY_ID]
    assert rows[0]["title"] == "Rooms spec: what is left?"                              # from the first message, no model
    assert _act(client, token, b["id"], "rename", title="  Lock   timeout  ").get_json()["conversation"]["title"] == "Lock timeout"
    assert _act(client, token, LEGACY_ID, "pin").status_code == 200
    assert [r["id"] for r in _listing(client)] == [LEGACY_ID, a["id"], b["id"]]          # pinned on top
    assert _act(client, token, LEGACY_ID, "unpin").status_code == 200
    removed = _act(client, token, a["id"], "archive").get_json()["conversation"]
    assert removed["archived"] and removed["archived_at"]
    assert a["id"] not in [r["id"] for r in _listing(client)]
    assert [r["id"] for r in _listing(client, "archived")] == [a["id"]]
    # Removing is a view action: the notes are still there and the conversation still opens.
    assert "Rooms spec" in client.get(f"/guild-next/guild/build?c={a['id']}").get_data(as_text=True)
    assert _act(client, token, a["id"], "restore").get_json()["conversation"]["archived"] is False
    assert a["id"] in [r["id"] for r in _listing(client)] and _listing(client, "archived") == []


def test_create_is_idempotent_and_can_link_a_work_item(floored):
    client = floored.owner()
    token = floored.csrf(client)
    body = keyed(about_item=12)
    one = client.post(f"{API}/conversations", json=body, headers=write_headers(token)).get_json()
    two = client.post(f"{API}/conversations", json=body, headers=write_headers(token)).get_json()
    assert one["result"] == "created" and two["result"] == "repeated"
    assert one["conversation"]["id"] == two["conversation"]["id"]
    assert one["conversation"]["work_item"]["ref"] == "12" and one["conversation"]["work_item"]["label"].startswith("#12")
    page = client.get(f"/guild-next/guild/build?c={one['conversation']['id']}").get_data(as_text=True)
    assert "data-linked-link" in page and "#12 Floor API" in page and "Build Log item" in page
    assert "This conversation is about" not in page and "Last opened" not in page


def test_notes_belong_to_their_own_conversation(floored):
    client = floored.owner()
    token = floored.csrf(client)
    a, b = _new(client, token), _new(client, token)
    _note(client, token, "only in a", cid=a["id"])
    _note(client, token, "only in b", cid=b["id"])
    _note(client, token, "only in the thread")
    texts = lambda cid: [n["text"] for n in client.get(f"{API}/notes?conversation={cid}").get_json()["notes"]]  # noqa: E731
    assert texts(a["id"]) == ["only in a"] and texts(b["id"]) == ["only in b"] and texts(LEGACY_ID) == ["only in the thread"]
    page_a = client.get(f"/guild-next/guild/build?c={a['id']}").get_data(as_text=True)
    thread = page_a[page_a.index("data-mc-thread"):page_a.index("</ol>", page_a.index("data-mc-thread"))]
    assert "only in a" in thread and "only in b" not in thread and "only in the thread" not in thread


# ── access control and errors ─────────────────────────────────────────────────

def test_only_the_owner_reads_or_writes_conversations(floored):
    anon, guest = floored.client(), floored.guest()
    assert anon.get(f"{API}/conversations").status_code == 401
    assert guest.get(f"{API}/conversations").status_code == 403
    assert anon.post(f"{API}/conversations", json=keyed()).status_code == 401
    assert guest.post(f"{API}/conversations", json=keyed()).status_code == 403
    assert guest.post(f"{API}/conversations/{LEGACY_ID}/archive", json=keyed()).status_code == 403


def test_writes_need_csrf_the_record_and_a_key_and_bad_input_is_refused(floored):
    client = floored.owner()
    token = floored.csrf(client)
    assert client.post(f"{API}/conversations", json=keyed()).status_code in (400, 403)          # no CSRF token
    off = client.post(f"{API}/conversations", json=keyed(), headers=write_headers(token, mode="off_record"))
    assert off.status_code == 409                                                               # off the record: nothing
    assert client.post(f"{API}/conversations", json={}, headers=write_headers(token)).status_code == 422
    assert _act(client, token, "c-000000000000", "pin").status_code == 404
    assert _act(client, token, "..%2F..%2Fetc", "pin").status_code == 404
    assert _act(client, token, LEGACY_ID, "rename", title="   ").status_code == 422
    assert _act(client, token, LEGACY_ID, "rename", title=7).status_code == 422
    bad_item = client.post(f"{API}/conversations", json=keyed(about_item="12"), headers=write_headers(token))
    assert bad_item.status_code == 422
    assert client.get(f"{API}/conversations?view=everything").status_code == 422
    assert client.get(f"{API}/notes?conversation=c-ffffffffffff").status_code == 404
    unknown = _note_status(client, token, "x", "c-ffffffffffff")
    assert unknown == 404


def _note_status(client, token, text, cid):
    return client.post(f"{API}/notes", json=keyed(text=text, conversation_id=cid), headers=write_headers(token)).status_code


def test_an_unknown_conversation_on_the_page_falls_back_honestly(floored):
    page = floored.owner().get("/guild-next/guild/build?c=c-ffffffffffff").get_data(as_text=True)
    assert "That conversation is unavailable; a current conversation is shown." in page


# ── restart: the state is in files ────────────────────────────────────────────

def test_a_restart_keeps_every_conversation_and_its_state(floored):
    client = floored.owner()
    token = floored.csrf(client)
    a = _new(client, token)
    _act(client, token, a["id"], "rename", title="Kept across restarts")
    _act(client, token, a["id"], "pin")
    b = _new(client, token)
    _act(client, token, b["id"], "archive")
    services = floored.app.extensions["guild_ui_next"]["services"]
    fresh = ConversationStore(services.store.folder, base_floor=services.floor.floor)       # a new process's store
    owner = json.loads(open(fresh._path(a["id"])).read())["principal"]
    rows = fresh.list(owner)
    assert rows[0]["id"] == a["id"] and rows[0]["title"] == "Kept across restarts" and rows[0]["pinned"]
    assert [r["id"] for r in fresh.list(owner, archived=True)] == [b["id"]]
    assert conversations_of(services).get(a["id"], owner)["title"] == "Kept across restarts"


def test_conversation_files_are_owner_only_and_atomic(floored):
    import os
    client = floored.owner()
    token = floored.csrf(client)
    a = _new(client, token)
    folder = os.path.join(floored.app.extensions["guild_ui_next"]["services"].store.folder, "conversations")
    path = os.path.join(folder, f"{a['id']}.json")
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert not [n for n in os.listdir(folder) if n.startswith(".tmp-")]
    data = json.loads(open(path).read())
    assert data["notes_floor"].endswith(f"/{a['id']}") and "created_key" in data
    listed = client.get(f"{API}/conversations").get_json()["conversations"][0]
    assert "notes_floor" not in listed and "created_key" not in listed and "principal" not in listed


def test_title_from_the_first_message():
    assert title_from("**Bold** start\nrest") == "Bold start"
    assert title_from("   \n\n  ") == "New conversation"
    long = title_from("word " * 40)
    assert len(long) <= 81 and long.endswith("…")


# ── no model calls during housekeeping ────────────────────────────────────────

def test_housekeeping_and_page_loads_call_no_model(turned):
    client = turned.owner()
    token = turned.csrf(client)
    a = _new(client, token)
    _act(client, token, a["id"], "rename", title="Quiet")
    for action in ("pin", "unpin", "archive", "restore"):
        assert _act(client, token, a["id"], action).status_code == 200
    _listing(client)
    _listing(client, "archived")
    client.get(f"/guild-next/guild/build?c={a['id']}")
    client.get("/guild-next/guild/build?view=archived")
    client.get(f"{API}/floor")
    assert turned.extra["runtime"].sent == []


# ── a real MC session per conversation (relay mocked) ─────────────────────────

def _ask(client, token, note_id, cid=None):
    body = {"note_request_id": note_id, "record_mode": "on_record", **({"conversation_id": cid} if cid else {})}
    return client.post(f"{API}/mc/turns", json=body, headers=write_headers(token))


def test_each_conversation_is_its_own_session_and_sends_only_its_own_note(turned):
    client = turned.owner()
    token = turned.csrf(client)
    a, b = _new(client, token), _new(client, token)
    na = _note(client, token, "about the relay", cid=a["id"])["note"]
    nb = _note(client, token, "about the rooms spec", cid=b["id"])["note"]
    nt = _note(client, token, "on the old thread")["note"]
    assert _ask(client, token, na["request_id"], a["id"]).get_json()["status"] == "answered"
    assert _ask(client, token, nb["request_id"], b["id"]).get_json()["status"] == "answered"
    assert _ask(client, token, nt["request_id"]).get_json()["status"] == "answered"
    na2 = _note(client, token, "and again on the relay", cid=a["id"])["note"]
    assert _ask(client, token, na2["request_id"], a["id"]).get_json()["status"] == "answered"
    sent = turned.extra["runtime"].sent
    users = [s["body"]["user"] for s in sent]
    assert users[0] != users[1] != users[2] and users[0] != users[2]        # three sessions
    assert users[3] == users[0]                                             # the same conversation, the same session
    assert all(u.startswith("guild-mc:") for u in users)
    for s, own in zip(sent, ("about the relay", "about the rooms spec", "on the old thread", "and again on the relay")):
        text = json.dumps(s["body"]["messages"])
        assert own in text                                                  # only this note is sent:
        for other in {"about the relay", "about the rooms spec", "on the old thread", "and again on the relay"} - {own}:
            assert other not in text                                        # never another conversation's text
        assert len(s["body"]["messages"]) == 1
    # MC's replies are kept in their own conversation.
    texts = lambda cid: [n["text"] for n in client.get(f"{API}/notes?conversation={cid}").get_json()["notes"]]  # noqa: E731
    assert texts(a["id"]).count("Item 12 is in build.") == 2 and texts(b["id"]).count("Item 12 is in build.") == 1


def test_a_note_cannot_be_sent_under_another_conversation(turned):
    client = turned.owner()
    token = turned.csrf(client)
    a, b = _new(client, token), _new(client, token)
    na = _note(client, token, "belongs to a", cid=a["id"])["note"]
    assert _ask(client, token, na["request_id"], b["id"]).status_code == 404
    assert turned.extra["runtime"].sent == []


def test_the_session_mapping_is_the_adapters(turned):
    """The conversation id is the platform's; each backend maps it to its own
    session (OpenClaw: a hash in its `user` field). Stable per conversation,
    never per day."""
    backend = turned.app.extensions["guild_ui_next"]["services"].mc
    one = session_conversation_id({"id": "c-0123456789ab"}, "robert")
    assert backend._user(one) == backend._user(session_conversation_id({"id": "c-0123456789ab"}, "robert"))
    assert backend._user(one) != backend._user(session_conversation_id({"id": "c-ba9876543210"}, "robert"))
    assert backend._user(one) != backend._user(session_conversation_id({"id": "c-0123456789ab"}, "someone"))
    assert "c-0123456789ab" not in backend._user(one)                       # an opaque key, not the id


def test_the_page_shows_the_conversation_list_and_actions(floored):
    client = floored.owner()
    token = floored.csrf(client)
    a = _new(client, token)
    _act(client, token, a["id"], "pin")
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert page.count("data-conv-link") == 2 and "data-conv-new" in page
    assert 'data-conv-act="unpin"' in page and 'data-conv-act="archive"' in page and "Remove from list" in page
    archived = client.get("/guild-next/guild/build?view=archived").get_data(as_text=True)
    assert "Nothing archived." in archived
    wall = client.get("/guild-next/guild/build/bench").get_data(as_text=True)
    assert "New chat" in wall and ">History</a>" in wall  # context history is explicit


def test_unreadable_conversation_files_leave_the_thread_working_and_say_so(floored):
    import os
    folder = floored.app.extensions["guild_ui_next"]["services"].store.folder
    with open(os.path.join(folder, "conversations"), "w") as f:       # a file where the folder should be
        f.write("x")
    client = floored.owner()
    token = floored.csrf(client)
    kept = _note(client, token, "still kept on the thread")                 # the thread works on its own notes
    assert kept["result"] == "kept" and kept["conversation"]["id"] == LEGACY_ID
    assert client.get(f"{API}/conversations").status_code == 503
    assert client.post(f"{API}/conversations", json=keyed(), headers=write_headers(token)).status_code == 503
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert "Conversations unavailable — treat as unknown." in page and "still kept on the thread" in page


# ── #265 review fix round ─────────────────────────────────────────────────────

def test_the_thread_keeps_its_daily_session_and_new_conversations_keep_theirs():
    """F1 (Robert's decision to confirm): the Shop floor thread starts fresh
    every day, exactly as before slice 2; a new conversation keeps its
    context until it is archived."""
    legacy = {"id": LEGACY_ID, "legacy": True, "notes_floor": "shop"}
    assert session_conversation_id(legacy, "robert", day="2026-09-29") == "shop:robert:2026-09-29"     # the pre-slice-2 key
    assert session_conversation_id(legacy, "robert", day="2026-09-30") != session_conversation_id(legacy, "robert", day="2026-09-29")
    fresh = {"id": "c-0123456789ab", "legacy": False, "notes_floor": "shop/c-0123456789ab"}
    assert session_conversation_id(fresh, "robert", day="2026-09-29") == session_conversation_id(fresh, "robert", day="2026-10-30")


def test_mcs_context_policy_is_explicit_in_its_openclaw_config():
    from pathlib import Path
    cfg = json.loads((Path(__file__).resolve().parents[3] / "docker/mc-agent/openclaw.json").read_text())
    assert cfg["session"] == {"reset": {"mode": "none"}}                  # the portal decides when a session is new
    comp = cfg["agents"]["defaults"]["compaction"]
    window = cfg["models"]["providers"]["minimoi-gateway-mc"]["models"][0]["contextWindow"]
    assert comp["enabled"] is True and comp["keepRecentTokens"] < window // 2   # below the trigger, so it cannot loop


def test_a_second_owner_account_has_its_own_thread_record(floored, tmp_path):
    services = floored.app.extensions["guild_ui_next"]["services"]
    store = conversations_of(services)
    robert = store.get(LEGACY_ID, "robert")
    admin = store.get(LEGACY_ID, "admin")                                  # no "unavailable", no NotFound
    assert robert["principal"] == "robert" and admin["principal"] == "admin"
    store.rename(LEGACY_ID, "admin", "Admin's view")
    assert store.get(LEGACY_ID, "robert")["title"] == "Shop floor thread"   # separate records
    assert [c["id"] for c in store.list("admin")] == [LEGACY_ID]


def test_one_corrupt_file_is_skipped_and_counted_not_fatal(floored):
    import os
    client = floored.owner()
    token = floored.csrf(client)
    a, b = _new(client, token), _new(client, token)
    folder = os.path.join(floored.app.extensions["guild_ui_next"]["services"].store.folder, "conversations")
    with open(os.path.join(folder, f"{b['id']}.json"), "w") as f:
        f.write("{ not json")
    listing = client.get(f"{API}/conversations").get_json()
    assert [c["id"] for c in listing["conversations"]] == [a["id"], LEGACY_ID] and listing["unreadable"] == 1
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert "1 conversation couldn't be read." in page and 'data-conv="%s"' % a["id"] in page
    c = _new(client, token)                                               # creating still works
    assert c["id"] not in (a["id"], b["id"])


def test_daily_area_chats_rotate_without_erasing_history(tmp_path, monkeypatch):
    from minimoi_portal.guild_ui import conversations as mod
    monkeypatch.setattr(mod, 'local_day', lambda: '2026-10-05')
    store = ConversationStore(str(tmp_path), base_floor='test')
    first = store.current_for_scope('robert', 'operate')
    assert store.current_for_scope('robert', 'operate')['id'] == first['id']
    assert store.current_for_scope('robert', 'workshop')['id'] != first['id']
    assert store.current_for_scope('other', 'operate')['id'] != first['id']
    fresh, _ = store.create('robert', key='new-chat-key', scope='operate')
    assert store.current_for_scope('robert', 'operate')['id'] == fresh['id']
    monkeypatch.setattr(mod, 'local_day', lambda: '2026-10-06')
    today = store.current_for_scope('robert', 'operate')
    assert today['id'] != fresh['id']
    assert store.get(first['id'], 'robert')['id'] == first['id']
    store.set_archived(today['id'], 'robert', True)
    assert store.current_for_scope('robert', 'operate')['id'] != today['id']


def test_popup_does_not_replay_build_but_explicit_history_still_opens(floored):
    client = floored.owner()
    token = floored.csrf(client)
    old = _new(client, token)
    _note(client, token, 'old build acceptance conversation', cid=old['id'])
    page = client.get('/guild-next/guild/operate').get_data(as_text=True)
    assert 'old build acceptance conversation' not in page
    assert 'New chat' in page and '>History</a>' in page
    assert 'old build acceptance conversation' in client.get(
        f"/guild-next/guild/operate?c={old['id']}").get_data(as_text=True)
    assert client.post(f'{API}/conversations', json=keyed(scope='evil'),
                       headers=write_headers(token)).status_code == 422


def test_area_selection_does_not_replace_main_chat(tmp_path):
    store = ConversationStore(str(tmp_path), base_floor='test')
    main, _ = store.create('robert', key='main-chat-key')
    store.current_for_scope('robert', 'operate')
    assert store.current('robert')['id'] == main['id']


def test_missing_area_conversation_cannot_write_into_legacy(floored):
    client = floored.owner()
    token = floored.csrf(client)
    result = client.post(f'{API}/notes', json=keyed(text='must not leak', chat_scope='operate'),
                         headers=write_headers(token))
    assert result.status_code == 503
    assert floored.extra['floor'].rows('floor_messages') == []
