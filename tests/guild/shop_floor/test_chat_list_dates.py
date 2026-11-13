"""MC's chat list after Robert's production test, 12 Nov 2026: a date on every chat before today (in his zone, the same
rule on the server and in the browser), one "Show older chats" control instead of Newer · Older, and no empty
"New chat" rows (hidden at once, removed after a day, and never a chat with anything in it)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

from minimoi_portal.guild_ui.adapters.contract import SourceResult
from minimoi_portal.guild_ui.conversations import LEGACY_ID, ConversationStore, is_empty_chat
from minimoi_portal.guild_ui.pages import _notes_probe, chat_when

API = "/guild-next/api/v1"
REPO = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc)          # Thu 12 Nov 2026, 2:00 PM in Chicago


# ── the date label: today's time, Yesterday, a short date, the year when it isn't this year ───────────────────────

@pytest.mark.parametrize("iso,label", [
    ("2026-11-12T21:24:00Z", "3:24 PM"),                 # today
    ("2026-11-12T06:01:00+00:00", "12:01 AM"),           # just after Chicago midnight: today
    ("2026-11-12T05:59:00Z", "Yesterday"),               # still the 11th in Chicago although the 12th in UTC
    ("2026-11-11T12:00:00Z", "Yesterday"),
    ("2026-11-10T12:00:00Z", "Tue 10 Nov"),
    ("2026-01-02T12:00:00Z", "Fri 2 Jan"),
    ("2025-12-30T12:00:00Z", "Tue 30 Dec 2025"),         # another year
    ("2026-11-12T12:00:00", "6:00 AM"),                  # no zone: read as UTC
])
def test_chat_list_label_follows_roberts_day(iso, label):
    assert chat_when(iso, NOW)["label"] == label


def test_late_evening_in_chicago_is_still_today_though_utc_has_moved_on():
    late = datetime(2026, 11, 13, 5, 30, tzinfo=timezone.utc)      # 11:30 PM on the 12th in Chicago
    assert chat_when("2026-11-12T06:30:00Z", late)["label"] == "12:30 AM"
    assert chat_when("2026-11-11T23:00:00Z", late)["label"] == "Yesterday"


def test_across_the_daylight_saving_change_yesterday_is_still_one_day():
    now = datetime(2026, 3, 9, 15, 0, tzinfo=timezone.utc)          # Mon 9 Mar, the day after clocks went forward
    assert chat_when("2026-03-08T05:30:00Z", now)["label"] == "Sat 7 Mar"           # 11:30 PM CST on the 7th
    assert chat_when("2026-03-08T18:00:00Z", now)["label"] == "Yesterday"
    assert chat_when("2026-03-07T18:00:00Z", now)["label"] == "Sat 7 Mar"


def test_full_moment_for_the_tooltip_and_unreadable_input():
    assert chat_when("2026-11-10T21:24:00Z", NOW)["full"] == "Tue 10 Nov 2026, 3:24 PM"
    assert chat_when("", NOW) == {"label": "", "full": ""}
    assert chat_when("not a date", NOW)["label"] == "not a date"


def test_the_browser_rule_matches_the_servers():
    """chat-sidebar.js's MiniMoiChatSidebar.when gives the same labels as pages.chat_when for the same moments."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    cases = ["2026-11-12T21:24:00Z", "2026-11-12T06:01:00Z", "2026-11-12T05:59:00Z", "2026-11-11T12:00:00Z",
             "2026-11-10T12:00:00Z", "2026-09-15T12:00:00Z", "2025-12-30T12:00:00Z", "2026-01-02T00:30:00Z"]
    script = (
        "global.window = {}; global.matchMedia = () => ({});"
        f"require({json.dumps(str(REPO / 'core/founders_ui/static/chat-sidebar.js'))});"
        f"const now = new Date({json.dumps(NOW.isoformat())});"
        f"console.log(JSON.stringify({json.dumps(cases)}.map(i => window.MiniMoiChatSidebar.when(i, now))));")
    done = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr
    browser = json.loads(done.stdout)
    for iso, got in zip(cases, browser):
        want = chat_when(iso, NOW)
        assert (got["label"], got["full"]) == (want["label"], want["full"]), iso


# ── what counts as an empty chat ───────────────────────────────────────────────────────────────────────────────

def _store(tmp_path):
    return ConversationStore(str(tmp_path), base_floor="shop")


def _age(store, cid, days):
    """Make a chat look created ``days`` ago (created and updated together, as a new chat is)."""
    path = os.path.join(store.dir, f"{cid}.json")
    with open(path) as f:
        conv = json.load(f)
    when = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - days * 86400, timezone.utc).isoformat()
    conv["created_at"] = conv["updated_at"] = when
    with open(path, "w") as f:
        json.dump(conv, f)


def test_a_new_chat_is_empty_until_anything_is_kept_in_it(tmp_path):
    store = _store(tmp_path)
    fresh, _ = store.create("owner", key="k-fresh-0001")
    assert is_empty_chat(fresh)
    touched, _ = store.create("owner", key="k-touch-0001")
    assert not is_empty_chat(store.touch(touched["id"], "owner"))                       # a reply, even without a title
    titled, _ = store.create("owner", key="k-title-0001")
    assert not is_empty_chat(store.touch(titled["id"], "owner", first_text="Hello"))
    renamed, _ = store.create("owner", key="k-renam-0001")
    assert not is_empty_chat(store.rename(renamed["id"], "owner", "Named"))
    pinned, _ = store.create("owner", key="k-pinne-0001")
    assert not is_empty_chat(store.set_pinned(pinned["id"], "owner", True))
    turned, _ = store.create("owner", key="k-turne-0001")
    assert not is_empty_chat(store.record_route_turn(turned["id"], "owner", "mc", {"route_id": "x"}))
    assert not is_empty_chat({**fresh, "attachments": [{"asset_id": "a"}]})
    assert not is_empty_chat({**fresh, "documents": [{"id": "d"}]})
    assert not is_empty_chat({**fresh, "something_new": 1})                             # an unknown field is content
    assert not is_empty_chat(store.get(LEGACY_ID, "owner"))                             # the Shop floor thread, always


def test_lists_hide_empty_chats_but_keep_the_open_one(tmp_path):
    store = _store(tmp_path)
    empty, _ = store.create("owner", key="k-empty-0001")
    real, _ = store.create("owner", key="k-real--0001")
    store.touch(real["id"], "owner", first_text="Something")
    assert {c["id"] for c in store.list("owner")} == {empty["id"], real["id"], LEGACY_ID}       # the default is unchanged
    assert {c["id"] for c in store.list("owner", hide_empty=True)} == {real["id"], LEGACY_ID}
    assert empty["id"] in {c["id"] for c in store.list("owner", hide_empty=True, keep=empty["id"])}


# ── pruning: only empty, only old, only with the notes store's firm "nothing here" ─────────────────────────────

def test_prune_removes_old_empty_chats_and_nothing_else(tmp_path):
    store = _store(tmp_path)
    old_empty, _ = store.create("owner", key="k-oldem-0001")
    new_empty, _ = store.create("owner", key="k-newem-0001")
    old_real, _ = store.create("owner", key="k-oldre-0001")
    open_one, _ = store.create("owner", key="k-open--0001")
    others, _ = store.create("someone", key="k-other-0001")
    for cid in (old_empty["id"], old_real["id"], open_one["id"]):
        _age(store, cid, 3)
    _age(store, others["id"], 3)
    store.touch(old_real["id"], "owner")
    removed = store.prune_empty("owner", has_notes=lambda c: False, keep=open_one["id"])
    assert removed == [old_empty["id"]]
    left = {c["id"] for c in store.list("owner")}
    assert old_empty["id"] not in left and {new_empty["id"], old_real["id"], open_one["id"]} <= left
    assert store.get(others["id"], "someone")                                         # another account's chats are its own


@pytest.mark.parametrize("answer", [True, None, "yes", RuntimeError("database down")])
def test_any_doubt_about_notes_keeps_the_chat(tmp_path, answer):
    store = _store(tmp_path)
    conv, _ = store.create("owner", key="k-doubt-0001")
    _age(store, conv["id"], 3)

    def has_notes(_c):
        if isinstance(answer, Exception):
            raise answer
        return answer
    assert store.prune_empty("owner", has_notes=has_notes) == []
    assert store.get(conv["id"], "owner")


def test_the_notes_probe_says_false_only_on_a_clean_empty_read():
    class Floor:
        floor = "shop"

        def __init__(self, result):
            self.result, self.asked = result, []

        def for_floor(self, nf):
            self.asked.append(nf)
            return self

        def list_notes(self, limit):
            return self.result
    conv = {"notes_floor": "shop/c-0123456789ab"}
    empty = Floor(SourceResult("live", "ok", {"notes": [], "more": False}))
    assert _notes_probe(empty)(conv) is False and empty.asked == ["shop/c-0123456789ab"]
    assert _notes_probe(Floor(SourceResult("live", "ok", {"notes": [{"id": 1}]})))(conv) is True
    assert _notes_probe(Floor(SourceResult("live", "unknown", None)))(conv) is None
    assert _notes_probe(None)(conv) is None


# ── through the portal: the page, the API, a real note in the floor database ───────────────────────────────────

def _new(client, token):
    r = client.post(f"{API}/conversations", json=keyed(), headers=write_headers(token))
    assert r.status_code == 200, r.get_json()
    return r.get_json()["conversation"]


def _note(client, token, text, cid):
    r = client.post(f"{API}/notes", json=keyed(text=text, conversation_id=cid), headers=write_headers(token))
    assert r.status_code == 200, r.get_json()


def _folder(floored):
    return floored.app.extensions["guild_ui_next"]["services"].store.folder


def test_empty_new_chats_leave_the_list_but_starting_one_and_typing_still_works(floored):
    client = floored.owner()
    token = floored.csrf(client)
    empties = [_new(client, token)["id"] for _ in range(3)]
    started = _new(client, token)["id"]
    page = client.get(f"/guild-next/guild/build?c={started}").get_data(as_text=True)
    assert f'data-conv="{started}"' in page                                            # the open one is listed
    assert not any(f'data-conv="{cid}"' in page for cid in empties)                   # the untouched ones are not
    _note(client, token, "First words in the new chat", started)
    assert set(empties) <= {c["id"] for c in client.get(f"{API}/conversations").get_json()["conversations"]}  # the API's default is unchanged
    listed = client.get(f"{API}/conversations?hide_empty=1").get_json()["conversations"]
    assert started in [c["id"] for c in listed] and not set(empties) & {c["id"] for c in listed}
    assert next(c for c in listed if c["id"] == started)["title"] == "First words in the new chat"
    kept = client.get(f"{API}/conversations?hide_empty=1&keep={empties[0]}").get_json()["conversations"]
    assert empties[0] in [c["id"] for c in kept]


def test_a_chat_with_one_message_is_never_pruned_even_when_its_record_looks_untouched(floored):
    """A note kept while the conversation record could not be bumped (touch is best effort) still saves the chat:
    the notes store is asked before anything is removed."""
    client = floored.owner()
    token = floored.csrf(client)
    with_note = _new(client, token)["id"]
    empty = _new(client, token)["id"]
    _note(client, token, "one message", with_note)
    store = ConversationStore(_folder(floored), base_floor="shop")
    path = os.path.join(store.dir, f"{with_note}.json")
    with open(path) as f:
        conv = json.load(f)
    for k in ("kept_at",):                                                            # make the record look untouched
        conv.pop(k, None)
    conv["title"], conv["title_source"] = "New conversation", "default"
    with open(path, "w") as f:
        json.dump(conv, f)
    for cid in (with_note, empty):
        _age(store, cid, 3)
    client.get("/guild-next/guild/build")                                             # the page's housekeeping runs
    ids = {c.get("id") for c in store._all()}
    assert with_note in ids and empty not in ids
    assert "one message" in client.get(f"/guild-next/guild/build?c={with_note}").get_data(as_text=True)


def test_the_list_shows_dates_and_one_show_older_control(floored):
    client = floored.owner()
    token = floored.csrf(client)
    made = []
    for index in range(13):
        cid = _new(client, token)["id"]
        _note(client, token, f"Chat {index}", cid)
        made.append(cid)
    store = ConversationStore(_folder(floored), base_floor="shop")
    old_path = os.path.join(store.dir, f"{made[0]}.json")
    with open(old_path) as f:
        conv = json.load(f)
    conv["updated_at"] = "2025-06-03T15:00:00+00:00"
    with open(old_path, "w") as f:
        json.dump(conv, f)
    page = client.get(f"/guild-next/guild/build?c={made[-1]}").get_data(as_text=True)
    assert page.count("data-conv-link") == 10
    assert "data-conv-older" in page and ">Show older chats<" in page and "data-conv-top" in page
    assert ">Newer<" not in page and ">Older<" not in page and "history_page" not in page
    assert "Archived chats" in page                                                   # kept
    assert 'data-shown="10"' in page and "data-chat-when=" in page
    rows = client.get(f"{API}/conversations?offset=10&limit=10&hide_empty=1").get_json()
    assert rows["total"] == 14 and len(rows["conversations"]) == 4 and rows["more"] is False
    assert made[0] in [c["id"] for c in rows["conversations"]]
    first = client.get(f"{API}/conversations?offset=0&limit=10&hide_empty=1").get_json()
    assert len(first["conversations"]) == 10 and first["more"] is True
    page = client.get(f"/guild-next/guild/build?c={made[0]}").get_data(as_text=True)  # an older open chat stays on the page
    assert page.count("data-conv-link") == 14 and 'data-conv-older hidden' in page
    assert ">Tue 3 Jun 2025<" in page


@pytest.mark.parametrize("query", ["offset=-1", "limit=0", "limit=101"])
def test_bad_paging_is_refused(floored, query):
    client = floored.owner()
    assert client.get(f"{API}/conversations?{query}").status_code == 422
