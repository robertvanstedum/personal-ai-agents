"""Guild 1.1 dev, Build refinement (5 Oct): a conversation's linked work is empty
until Robert attaches something explicit, and an uploaded image can be kept with
a conversation. No model call, no network, no fetching of a pasted URL."""
from __future__ import annotations

import io
import uuid

import pytest

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from board_media_helpers import image_bytes

from minimoi_portal.guild_ui import linked_work

API = "/guild-next/api/v1"
REPO = "https://github.com/robertvanstedum/personal-ai-agents"


def _new(client, token):
    r = client.post(f"{API}/conversations", json=keyed(), headers=write_headers(token))
    assert r.status_code == 200, r.get_json()
    return r.get_json()["conversation"]


def _link(client, token, cid, ref, **kw):
    return client.post(f"{API}/conversations/{cid}/work-item", json=keyed(ref=ref), headers=write_headers(token, **kw))


def _clear(client, token, cid):
    return client.post(f"{API}/conversations/{cid}/work-item/clear", json=keyed(), headers=write_headers(token))


# ── what may be linked ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("12", ("number", "12", "")), ("#12", ("number", "12", "")), ("  #007  ", ("number", "7", "")),
    (f"{REPO}/issues/150", ("issue", "150", "")), (f"{REPO}/issues/150/", ("issue", "150", "")),
    (f"{REPO}/pull/9", ("pull", "9", "")),                                                       # a pull request stays a pull request
    (f"{REPO}/pull/9#issuecomment-123456", ("pull", "9", "issuecomment-123456")),
    (f"{REPO}/pull/9#discussion", ("pull", "9", "")),                                            # an unknown fragment is dropped
    (f"{REPO}/blob/main/docs/memory_ask.md", ("file", "docs/memory_ask.md", "")),
    (f"{REPO}/blob/main/docs/memory_ask.md#L10-L20", ("file", "docs/memory_ask.md", "L10-L20")),   # a line range is kept
    (f"{REPO}/blob/main/docs/memory_ask.md?plain=1#L7", ("file", "docs/memory_ask.md", "L7")),
])
def test_what_can_be_linked(text, expected):
    assert linked_work.parse(text) == expected


@pytest.mark.parametrize("text", [
    "", "   ", None, 12, "0", "#0", "abc", "1234567", "-4",
    "https://github.com/someone-else/personal-ai-agents/issues/5",              # another account
    "https://github.com/robertvanstedum/other-repo/issues/5",                  # another repository
    "http://github.com/robertvanstedum/personal-ai-agents/issues/5",           # not https
    "https://example.com/robertvanstedum/personal-ai-agents/issues/5",         # another host
    "https://github.com.evil.example/robertvanstedum/personal-ai-agents/issues/5",
    f"{REPO}/blob/main/../secrets.md", f"{REPO}/blob/feature/docs/x.md",       # traversal; a branch we do not pin
    f"{REPO}/issues/5 and more", "javascript:alert(1)", "x" * 500,
])
def test_what_is_refused(text):
    with pytest.raises(linked_work.LinkRefused):
        linked_work.parse(text)


# ── attach, replace, clear ───────────────────────────────────────────────────────

def test_linked_work_starts_empty_and_only_an_explicit_attach_sets_it(floored):
    client = floored.owner()
    token = floored.csrf(client)
    conv = _new(client, token)
    assert conv["work_item"] is None and conv["attachments"] == []
    page = client.get(f"/guild-next/guild/build?c={conv['id']}").get_data(as_text=True)
    assert 'data-linked-empty' in page and "Last opened" not in page and "This conversation is about" not in page
    # Browsing and opening an item never attaches anything.
    client.get("/guild-next/guild/build/items/12")
    client.get("/guild-next/guild/build/log")
    again = client.get(f"{API}/conversations?view=active").get_json()["conversations"]
    assert all(c["work_item"] is None for c in again)


def test_a_number_the_build_log_knows_shows_its_title_and_an_unknown_one_says_so(floored):
    client = floored.owner()
    token = floored.csrf(client)
    cid = _new(client, token)["id"]
    r = _link(client, token, cid, "12")
    assert r.status_code == 200 and r.get_json()["result"] == "linked"
    item = r.get_json()["conversation"]["work_item"]
    assert item["kind"] == "item" and item["label"] == "#12 Floor API" and item["href"].endswith("/guild/build/items/12")
    # Not in the Build Log: shown as a GitHub issue link, its title not loaded; never as an item that was found.
    item = _link(client, token, cid, "#4040").get_json()["conversation"]["work_item"]
    assert item == {"kind": "github", "github_kind": "either", "ref": "4040", "label": "#4040",
                    "href": f"{REPO}/issues/4040", "source": "number", "title_loaded": False}


def test_a_pasted_github_link_is_not_reinterpreted_as_a_build_log_item(floored):
    client = floored.owner()
    token = floored.csrf(client)
    cid = _new(client, token)["id"]
    item = _link(client, token, cid, f"{REPO}/issues/12").get_json()["conversation"]["work_item"]
    assert item["kind"] == "github" and item["href"] == f"{REPO}/issues/12" and item["source"] == "link"   # 12 is also a Build Log id


def test_a_pasted_pull_request_and_a_line_anchor_are_kept_exactly(floored):
    client = floored.owner()
    token = floored.csrf(client)
    cid = _new(client, token)["id"]
    item = _link(client, token, cid, f"{REPO}/pull/77#issuecomment-5").get_json()["conversation"]["work_item"]
    assert item["github_kind"] == "pull" and item["href"] == f"{REPO}/pull/77#issuecomment-5"
    item = _link(client, token, cid, f"{REPO}/blob/main/docs/memory_ask.md#L10-L20").get_json()["conversation"]["work_item"]
    assert item["href"] == f"{REPO}/blob/main/docs/memory_ask.md#L10-L20" and item["label"] == "memory_ask.md#L10-L20"
    page = client.get(f"/guild-next/guild/build?c={cid}").get_data(as_text=True)
    assert "memory_ask.md#L10-L20" in page and "GitHub file" in page
    item = _link(client, token, cid, f"{REPO}/pull/78").get_json()["conversation"]["work_item"]
    page = client.get(f"/guild-next/guild/build?c={cid}").get_data(as_text=True)
    assert "GitHub pull request · title not loaded" in page and "Build Log number, or a GitHub link" in page


def test_a_spec_file_link_keeps_only_a_validated_path(floored):
    client = floored.owner()
    token = floored.csrf(client)
    cid = _new(client, token)["id"]
    item = _link(client, token, cid, f"{REPO}/blob/main/docs/memory_ask.md?plain=1").get_json()["conversation"]["work_item"]
    assert item == {"kind": "github_file", "ref": "docs/memory_ask.md", "label": "memory_ask.md",
                    "href": f"{REPO}/blob/main/docs/memory_ask.md", "source": "link"}


def test_a_refused_link_changes_nothing_and_says_why(floored):
    client = floored.owner()
    token = floored.csrf(client)
    cid = _new(client, token)["id"]
    _link(client, token, cid, "12")
    for bad in ("https://example.com/x", "nonsense", ""):
        r = _link(client, token, cid, bad)
        assert r.status_code == 422 and "Nothing was changed" in r.get_json()["message"]
    got = client.get(f"{API}/conversations?view=active").get_json()["conversations"]
    assert [c["work_item"]["ref"] for c in got if c["id"] == cid] == ["12"]


def test_replace_and_clear_change_only_that_conversation_and_keep_its_messages(floored):
    client = floored.owner()
    token = floored.csrf(client)
    a, b = _new(client, token)["id"], _new(client, token)["id"]
    for cid in (a, b):
        _link(client, token, cid, "12")
    note = client.post(f"{API}/notes", json=keyed(text="kept words", conversation_id=a), headers=write_headers(token))
    assert note.status_code == 200
    assert _link(client, token, a, "31").get_json()["conversation"]["work_item"]["ref"] == "31"        # replace
    cleared = _clear(client, token, a)
    assert cleared.status_code == 200 and cleared.get_json()["conversation"]["work_item"] is None
    rows = {c["id"]: c for c in client.get(f"{API}/conversations?view=active").get_json()["conversations"]}
    assert rows[a]["work_item"] is None and rows[b]["work_item"]["ref"] == "12"                          # the other one is untouched
    assert [n["text"] for n in client.get(f"{API}/notes?conversation={a}").get_json()["notes"]] == ["kept words"]


def test_linked_work_writes_are_guarded(floored):
    client = floored.owner()
    token = floored.csrf(client)
    cid = _new(client, token)["id"]
    body = keyed(ref="12")
    assert client.post(f"{API}/conversations/{cid}/work-item", json=body).status_code == 403                                     # no CSRF
    assert _link(client, token, cid, "12", mode="off_record").status_code == 409                                                # off the record
    assert floored.guest().post(f"{API}/conversations/{cid}/work-item", json=body,
                                headers=write_headers(token)).status_code in (401, 403)                                          # not the owner
    assert _link(client, token, "c-000000000000", "12").status_code == 404                                                       # no such conversation
    assert client.get(f"{API}/conversations?view=active").get_json()["conversations"][0]["work_item"] is None


# ── a file kept with a conversation ──────────────────────────────────────────────

@pytest.fixture
def media(floored, tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    floored.app.extensions["guild_ui_next"]["services"].media_dir = str(folder)
    return floored


def _upload(client, token, raw=None, name="plan.png"):
    raw = raw or image_bytes("PNG")
    headers = write_headers(token)
    headers["Idempotency-Key"] = uuid.uuid4().hex
    return client.post(f"{API}/media", data={"file": (io.BytesIO(raw), name)}, headers=headers,
                       content_type="multipart/form-data")


def _attach(client, token, cid, asset_id, name="plan.png"):
    return client.post(f"{API}/conversations/{cid}/attachments", json=keyed(asset_id=asset_id, name=name),
                       headers=write_headers(token))


def test_an_uploaded_image_is_kept_with_the_conversation_and_listed(media):
    client = media.owner()
    token = media.csrf(client)
    cid = _new(client, token)["id"]
    asset = _upload(client, token).get_json()["asset"]["id"]
    r = _attach(client, token, cid, asset, name="C:\\fakepath\\plan.png")
    assert r.status_code == 200 and r.get_json()["result"] == "attached"
    rows = r.get_json()["conversation"]["attachments"]
    assert [(a["asset_id"], a["name"]) for a in rows] == [(asset, "plan.png")]
    assert _attach(client, token, cid, asset).get_json()["conversation"]["attachments"] == rows           # again: no duplicate
    listed = client.get(f"{API}/conversations?view=active").get_json()["conversations"]
    assert [c["attachments"][0]["asset_id"] for c in listed if c["id"] == cid] == [asset]
    # Attaching never links work and never touches a model or the note store.
    assert [c["work_item"] for c in listed if c["id"] == cid] == [None]


def test_only_your_own_real_image_can_be_attached(media):
    client = media.owner()
    token = media.csrf(client)
    cid = _new(client, token)["id"]
    for bad, status in (("not-an-id", 422), (None, 422), (str(uuid.uuid4()), 404)):
        r = client.post(f"{API}/conversations/{cid}/attachments", json=keyed(asset_id=bad), headers=write_headers(token))
        assert r.status_code == status, (bad, r.get_json())
    assert _attach(client, token, "c-000000000000", str(uuid.uuid4())).status_code in (404,)
    assert client.get(f"{API}/conversations?view=active").get_json()["conversations"][0]["attachments"] == []


def test_attach_is_guarded_and_capped(media, monkeypatch):
    client = media.owner()
    token = media.csrf(client)
    cid = _new(client, token)["id"]
    asset = _upload(client, token).get_json()["asset"]["id"]
    assert client.post(f"{API}/conversations/{cid}/attachments", json=keyed(asset_id=asset)).status_code == 403
    assert media.guest().post(f"{API}/conversations/{cid}/attachments", json=keyed(asset_id=asset),
                              headers=write_headers(token)).status_code in (401, 403)
    import minimoi_portal.guild_ui.conversations as conv_mod
    monkeypatch.setattr(conv_mod, "ATTACHMENTS_MAX", 1)
    assert _attach(client, token, cid, asset).status_code == 200
    second = _upload(client, token, raw=image_bytes("JPEG")).get_json()["asset"]["id"]
    r = _attach(client, token, cid, second)
    assert r.status_code == 422 and "at most 1 files" in r.get_json()["message"]
