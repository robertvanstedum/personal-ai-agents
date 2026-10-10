"""The topic workshop API (Workshop W1): guards, the whole record cycle, idempotent retries, and what a browser may see."""
from __future__ import annotations

import io
import json

import pytest

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored  # noqa: F401  (pytest fixtures)

API = "/guild-next/api/v1"
CARD = "5555 5555 5555 4444"


def _k(n=[0]):
    n[0] += 1
    return f"key-{n[0]:08d}-topics"


def _post(client, token, path, body=None, mode="on_record"):
    return client.post(f"{API}{path}", json={"idempotency_key": _k(), **(body or {})}, headers=write_headers(token, mode=mode))


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 80), (200, 180, 140)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def ws(floored):
    client = floored.owner()
    token = floored.csrf(client)
    r = _post(client, token, "/topics/create", {"title": "Guild chat improvements", "summary": "Design and fixes"})
    assert r.status_code == 200, r.get_json()
    floored.extra["tid"] = r.get_json()["topic"]["id"]
    return floored, client, token, floored.extra["tid"]


def test_a_topic_is_created_with_its_own_conversation_and_listed_and_found_by_words(ws):
    portal, client, token, tid = ws
    got = client.get(f"{API}/topics/{tid}").get_json()
    assert got["topic"]["title"] == "Guild chat improvements" and got["topic"]["conversation_id"]
    assert got["conversation"]["id"] == got["topic"]["conversation_id"] and got["conversation"]["title"] == "Guild chat improvements"
    assert [t["id"] for t in client.get(f"{API}/topics?q=chat").get_json()["topics"]] == [tid]
    assert client.get(f"{API}/topics?q=nothing-like-this").get_json()["topics"] == []


def test_every_write_is_guarded_like_the_rest_of_the_floor(ws):
    portal, client, token, tid = ws
    body = {"idempotency_key": _k(), "kind": "note", "title": "x", "text": "y"}
    assert client.post(f"{API}/topics/{tid}/items", json=body).status_code == 403                                # no CSRF token
    off = client.post(f"{API}/topics/{tid}/items", json=body, headers=write_headers(token, mode="off_record"))
    assert off.status_code == 409 and off.get_json()["error"] == "not_listening"                                  # off the record: refused
    nokey = client.post(f"{API}/topics/{tid}/items", json={"kind": "note", "title": "x", "text": "y"}, headers=write_headers(token))
    assert nokey.status_code == 422                                                                              # needs an idempotency key
    guest = portal.guest().post(f"{API}/topics/{tid}/items", json=body, headers=write_headers(token))
    assert guest.status_code in (401, 403)
    assert portal.client().get(f"{API}/topics").status_code in (401, 403)


def test_the_whole_card_cycle_text_revisions_comments_disposition_archive(ws):
    portal, client, token, tid = ws
    d = _post(client, token, f"/topics/{tid}/items", {"kind": "document", "title": "Spec · topic records", "text": "# One\n\nFirst."}).get_json()["item"]
    iid = d["id"]
    assert d["kind"] == "document" and d["current_rev"] == "A" and "file" not in json.dumps(d)                      # no file names or paths to the browser
    r2 = _post(client, token, f"/topics/{tid}/items/{iid}/revisions", {"text": "# One\n\nSecond.", "note": "after Robert's comment"}).get_json()["item"]
    assert r2["current_rev"] == "B"
    c = _post(client, token, f"/topics/{tid}/items/{iid}/comments", {"text": "Say what archive means", "rev": "A", "anchor": {"type": "block", "index": 1, "quote": "First."}}).get_json()["comment"]
    assert c["rev"] == "A" and c["anchor"]["index"] == 1 and c["by"]["name"]
    rep = _post(client, token, f"/topics/{tid}/items/{iid}/comments", {"text": "Noted", "reply_to": c["id"]}).get_json()["comment"]
    assert rep["rev"] == "A"
    res = _post(client, token, f"/topics/{tid}/items/{iid}/comments/{c['id']}/resolve", {}).get_json()
    assert res["comment"]["status"] == "resolved"
    disp = _post(client, token, f"/topics/{tid}/items/{iid}/comments/{c['id']}/disposition", {"value": "accepted"}).get_json()["comment"]
    assert disp["disposition"]["value"] == "accepted" and disp["disposition"]["rev"] == "B"
    assert _post(client, token, f"/topics/{tid}/items/{iid}/comments/{c['id']}/disposition", {"value": "approved"}).status_code == 422
    view = client.get(f"{API}/topics/{tid}/items/{iid}").get_json()
    assert view["text"].endswith("Second.") and len(view["comments"]) == 2
    assert client.get(f"{API}/topics/{tid}/items/{iid}?rev=A").get_json()["text"].endswith("First.")             # the earlier revision reads as written
    away = _post(client, token, f"/topics/{tid}/items/{iid}/archive", {"archived": True}).get_json()
    assert away["result"] == "archived" and away["item"]["archived"] is True
    late = _post(client, token, f"/topics/{tid}/items/{iid}/revisions", {"text": "late"})
    assert late.status_code == 409 and late.get_json()["error"] == "archived"                                       # no edits while it is put away
    back = _post(client, token, f"/topics/{tid}/items/{iid}/archive", {"archived": False}).get_json()
    assert back["result"] == "restored" and len(client.get(f"{API}/topics/{tid}/items/{iid}").get_json()["comments"]) == 2


def test_the_same_write_twice_is_one_record(ws):
    portal, client, token, tid = ws
    body = {"idempotency_key": "same-key-0001", "kind": "note", "title": "Once", "text": "only once"}
    a = client.post(f"{API}/topics/{tid}/items", json=body, headers=write_headers(token)).get_json()
    b = client.post(f"{API}/topics/{tid}/items", json=body, headers=write_headers(token)).get_json()
    assert a["item"]["id"] == b["item"]["id"] and b.get("repeated") is True
    assert len(client.get(f"{API}/topics/{tid}").get_json()["items"]) == 1


def test_payment_details_are_removed_from_everything_the_owner_types(ws):
    portal, client, token, tid = ws
    n = _post(client, token, f"/topics/{tid}/items", {"kind": "note", "title": "Card", "text": f"my card is {CARD} ok"}).get_json()["item"]
    assert CARD not in client.get(f"{API}/topics/{tid}/items/{n['id']}").get_json()["text"]
    c = _post(client, token, f"/topics/{tid}/items/{n['id']}/comments", {"text": f"and {CARD}"}).get_json()["comment"]
    assert CARD not in c["text"]
    e = _post(client, token, f"/topics/{tid}/journal/add", {"kind": "note", "text": f"card {CARD}"}).get_json()["entry"]
    assert CARD not in e["text"]


def test_a_request_card_has_a_recipient_and_a_stage_set_by_hand(ws):
    portal, client, token, tid = ws
    r = _post(client, token, f"/topics/{tid}/items", {"kind": "request", "title": "Review the Files layout", "text": "please",
                                                     "request": {"to": "Codex", "included": "revision B"}}).get_json()["item"]
    assert r["request"]["stage"] == "queued" and r["request"]["to"] == "Codex"
    s = _post(client, token, f"/topics/{tid}/items/{r['id']}/stage", {"stage": "delivered"}).get_json()["item"]
    assert s["request"]["stage"] == "delivered" and s["request"]["stage_source"] == "set by the owner"
    assert _post(client, token, f"/topics/{tid}/items/{r['id']}/stage", {"stage": "approved"}).status_code == 422
    re = _post(client, token, f"/topics/{tid}/items/{r['id']}/stage", {"recipient": "Grok CLI"}).get_json()["item"]
    assert re["request"]["to"] == "Grok CLI"


def test_a_design_is_an_uploaded_image_sanitised_with_revisions_and_served_only_to_the_owner(ws):
    portal, client, token, tid = ws
    up = client.post(f"{API}/topics/{tid}/items/design", data={"file": (io.BytesIO(_png()), "files.png"), "title": "Files panel · phone"},
                     headers={"X-CSRF-Token": token, "X-Record-Mode": "on_record", "Idempotency-Key": "upload-key-0001"}, content_type="multipart/form-data")
    assert up.status_code == 200, up.get_json()
    item = up.get_json()["item"]
    assert item["kind"] == "design" and item["revisions"][0]["width"] == 40 and item["revisions"][0]["height"] == 80
    full = client.get(f"{API}/topics/{tid}/items/{item['id']}/image")
    assert full.status_code == 200 and full.data[:8] == b"\x89PNG\r\n\x1a\n" and full.mimetype == "image/png"
    assert client.get(f"{API}/topics/{tid}/items/{item['id']}/image?v=thumb").mimetype == "image/webp"
    assert client.get(f"{API}/topics/{tid}/items/{item['id']}/image?v=huge").status_code == 404
    assert portal.client().get(f"{API}/topics/{tid}/items/{item['id']}/image").status_code in (401, 403)
    c = _post(client, token, f"/topics/{tid}/items/{item['id']}/comments", {"text": "This name is cut off", "anchor": {"type": "point", "x": 0.3, "y": 0.5, "w": 390}}).get_json()["comment"]
    assert c["anchor"] == {"type": "point", "x": 0.3, "y": 0.5, "w": 390}
    bad = client.post(f"{API}/topics/{tid}/items/design", data={"file": (io.BytesIO(b"not an image"), "x.png")},
                      headers={"X-CSRF-Token": token, "X-Record-Mode": "on_record", "Idempotency-Key": "upload-key-0002"}, content_type="multipart/form-data")
    assert bad.status_code in (415, 422)
    nope = _post(client, token, f"/topics/{tid}/items", {"kind": "design", "title": "x"})
    assert nope.status_code == 422                                                                                 # a design is only added by upload


def test_layout_and_journal(ws):
    portal, client, token, tid = ws
    a = _post(client, token, f"/topics/{tid}/items", {"kind": "note", "title": "a", "text": "x"}).get_json()["item"]["id"]
    b = _post(client, token, f"/topics/{tid}/items", {"kind": "note", "title": "b", "text": "y"}).get_json()["item"]["id"]
    lay = _post(client, token, f"/topics/{tid}/layout", {"order": [b, a], "wide": [a], "last_view": "split"}).get_json()["layout"]
    assert lay == {"order": [b, a], "wide": [a], "last_view": "split"} and client.get(f"{API}/topics/{tid}").get_json()["layout"]["last_view"] == "split"
    assert _post(client, token, f"/topics/{tid}/layout", {"order": [a, "i-000000000000"]}).status_code == 422
    e = _post(client, token, f"/topics/{tid}/journal/add", {"kind": "decision", "text": "Go with F.", "refs": [{"type": "item", "ref": a}]}).get_json()["entry"]
    assert e["author"]["kind"] == "owner" and e["via"] == "ui" and e["verified"] is True
    assert [x["id"] for x in client.get(f"{API}/topics/{tid}/journal").get_json()["entries"]] == [e["id"]]
    assert _post(client, token, f"/topics/{tid}/journal/add", {"kind": "gossip", "text": "x"}).status_code == 422


def test_unknown_topics_and_items_are_404_and_a_bad_id_never_reaches_a_path(ws):
    portal, client, token, tid = ws
    for path in ("/topics/t-000000000000", "/topics/..%2F..%2Fetc", f"/topics/{tid}/items/i-000000000000", f"/topics/{tid}/items/..%2Fx"):
        assert client.get(f"{API}{path}").status_code == 404
    assert _post(client, token, "/topics/t-000000000000/items", {"kind": "note", "title": "x", "text": "y"}).status_code == 404


def test_the_page_never_sees_file_names_or_paths(ws):
    portal, client, token, tid = ws
    _post(client, token, f"/topics/{tid}/items", {"kind": "document", "title": "Spec", "text": "x"})
    blob = json.dumps(client.get(f"{API}/topics/{tid}").get_json())
    assert "A.md" not in blob and "revisions" in blob


def test_a_document_is_rendered_safely_with_numbered_blocks_for_comments(ws):
    portal, client, token, tid = ws
    d = _post(client, token, f"/topics/{tid}/items", {"kind": "document", "title": "Spec",
              "text": "# Title\n\nFirst <script>alert(1)</script> para with [a link](javascript:alert(1)).\n\n- one\n- two\n\n```\ncode\n```"}).get_json()["item"]
    got = client.get(f"{API}/topics/{tid}/items/{d['id']}").get_json()
    assert got["blocks"] == 4 and '<h1 data-block="0">' in got["html"] and '<p data-block="1">' in got["html"] and '<pre data-block="3">' in got["html"]
    assert "<script" not in got["html"] and "&lt;script&gt;" in got["html"] and 'href="javascript' not in got["html"] and "<a " not in got["html"]
    assert got["text"].startswith("# Title")                                                                     # the stored text is the original
    g = client.post(f"{API}/topics/{tid}/items/design", data={"file": (io.BytesIO(_png()), "x.png")},
                    headers={"X-CSRF-Token": token, "X-Record-Mode": "on_record", "Idempotency-Key": "upload-key-0009"}, content_type="multipart/form-data").get_json()["item"]
    assert client.get(f"{API}/topics/{tid}/items/{g['id']}").get_json()["html"] == ""


def test_a_topics_conversation_never_becomes_the_chat_pages_landing_conversation(ws):
    portal, client, token, tid = ws
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    topic_conv = client.get(f"{API}/topics/{tid}").get_json()["topic"]["conversation_id"]
    assert topic_conv and f'data-conv="{topic_conv}"' in page                                              # it is in the history list…
    assert 'data-conv-current-title>Guild chat improvements' not in page                                 # …but the Chat page does not open on it
