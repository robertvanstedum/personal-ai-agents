"""Guild 1.1 slice 3 (spec §5.1-5.2, §11): the Board and Media library API.
Guards on every write (CSRF, record mode, keys, the upload's own guard),
versions and revisions, R1/R2/R3/R6, pinning from Chat by note id, serving
only to the owner, the 410 tombstone, honest unavailability, no model or
network call, and the rollback check (older code on the new schema)."""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import uuid

import pytest
from PIL import Image

from minimoi_portal.guild_ui.stores import Author

from floor_helpers import REPO, write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import SqliteFloor, floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from board_media_helpers import image_bytes
from test_phone_briefing_no_paid_calls import (PROVIDER_MODULES, _mock_probe, assert_no_outbound,  # noqa: F401
                                               no_outbound)

API = "/guild-next/api/v1"
ROBERT = Author("robert", "owner", "Robert")


@pytest.fixture
def board(floored, tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    floored.app.extensions["guild_ui_next"]["services"].media_dir = str(media)
    floored.extra["media"] = media
    return floored


def _post(client, token, path, mode="on_record", **body):
    return client.post(f"{API}{path}", json=keyed(**body), headers=write_headers(token, mode=mode))


def _add(client, token, text="a note", **kw):
    r = _post(client, token, "/postits", text=text, **kw)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["postit"]


def _board(client):
    return client.get(f"{API}/board").get_json()


def _upload(client, token, raw, key=None, mode="on_record", name="photo.jpg", extra=None):
    data = {"file": (io.BytesIO(raw), name)}
    if extra:
        data.update(extra)
    headers = write_headers(token, mode=mode)
    if key is not False:
        headers["Idempotency-Key"] = key or uuid.uuid4().hex
    return client.post(f"{API}/media", data=data, headers=headers, content_type="multipart/form-data")


# ── the Board ───────────────────────────────────────────────────────────────

def test_the_board_page_and_read(board):
    client = board.owner()
    token = board.csrf(client)
    a = _add(client, token, "decide the fade time", label="decide", item_ref=12)
    page = client.get("/guild-next/guild/board")
    assert page.status_code == 200 and 'id="board-data"' in page.get_data(as_text=True)
    body = _board(client)
    assert body["status"] == "ok" and body["counts"] == {"active": 1, "done": 0, "trash": 0}
    note = body["active"][0]
    assert note["id"] == a["id"] and note["label"] == "decide" and note["item"]["title"] == "Floor API"
    assert body["labels"] == ["decide", "blocked", "remember", "followup", "idea", "fyi"]
    for client_ in (board.guest(), board.client()):
        assert client_.get("/guild-next/guild/board").status_code in (302, 403)
        assert client_.get(f"{API}/board").status_code in (401, 403)


def test_an_unreachable_floor_makes_the_board_unknown(load_portal):
    from floor_db_helpers import DownFloor, attach
    portal = load_portal()
    attach(portal, DownFloor())
    page = portal.owner().get("/guild-next/guild/board").get_data(as_text=True)
    assert "data-bd-unknown" in page and "<strong>unknown</strong>" in page
    body = portal.owner().get(f"{API}/board").get_json()
    assert body["status"] == "unknown" and body["active"] is None


def test_done_label_link_with_versions_and_linked_work_untouched(board):
    client = board.owner()
    token = board.csrf(client)
    queue_before = board.queue_path.read_bytes()
    a = _add(client, token, "about the floor API")
    assert _post(client, token, f"/postits/{a['id']}/done").status_code == 422          # no version
    done = _post(client, token, f"/postits/{a['id']}/done", version=a["version"])
    assert done.status_code == 200 and done.get_json()["postit"]["state"] == "done"
    stale = _post(client, token, f"/postits/{a['id']}/undone", version=a["version"])
    assert stale.status_code == 409 and stale.get_json()["postit"]["state"] == "done"
    v = done.get_json()["postit"]["version"]
    assert _post(client, token, f"/postits/{a['id']}/undone", version=v).status_code == 200
    v += 1
    assert _post(client, token, f"/postits/{a['id']}/label", label="urgent", version=v).status_code == 422
    lab = _post(client, token, f"/postits/{a['id']}/label", label="remember", version=v)
    assert lab.status_code == 200 and lab.get_json()["postit"]["label"] == "remember"
    v += 1
    assert _post(client, token, f"/postits/{a['id']}/link", item_ref=999, version=v).status_code == 422
    link = _post(client, token, f"/postits/{a['id']}/link", item_ref=12, version=v)
    assert link.status_code == 200 and link.get_json()["postit"]["item"]["title"] == "Floor API"
    assert board.queue_path.read_bytes() == queue_before and board.journal() == []   # linked work untouched


def test_r3_reorder_conflict_returns_the_current_order(board):
    client = board.owner()
    token = board.csrf(client)
    a, b, c = (_add(client, token, t) for t in ("a", "b", "c"))
    rev = _board(client)["order_rev"]
    ok = _post(client, token, "/postits/reorder", id=a["id"], before_id=c["id"], expect_order_rev=rev)
    assert ok.status_code == 200 and ok.get_json()["order"]["order"] == [a["id"], c["id"], b["id"]]
    late = _post(client, token, "/postits/reorder", id=b["id"], before_id=a["id"], expect_order_rev=rev)
    assert late.status_code == 409 and late.get_json()["order"]["order"] == [a["id"], c["id"], b["id"]]
    for bad in ({"id": a["id"], "expect_order_rev": rev}, {"id": a["id"], "before_id": b["id"], "after_id": c["id"],
                                                          "expect_order_rev": rev}):
        assert _post(client, token, "/postits/reorder", **bad).status_code == 422


def test_r2_empty_trash_needs_confirmation_conflicts_on_a_different_set_and_retries_safely(board):
    client = board.owner()
    token = board.csrf(client)
    notes = [_add(client, token, f"old {i}") for i in range(3)]
    for p in notes[:2]:
        _post(client, token, f"/postits/{p['id']}/bin")
    trash = _board(client)
    items = [{"id": p["id"], "version": p["version"]} for p in trash["trash"]]
    assert _post(client, token, "/postits/trash/empty", trash_rev=trash["trash_rev"], items=items).status_code == 422
    # Same count, different ids: restore one, bin the third.
    _post(client, token, f"/postits/{notes[0]['id']}/restore")
    _post(client, token, f"/postits/{notes[2]['id']}/bin")
    now = _board(client)
    assert len(now["trash"]) == len(items)
    conflict = _post(client, token, "/postits/trash/empty", trash_rev=now["trash_rev"], items=items, confirm="empty")
    assert conflict.status_code == 409
    assert sorted(p["id"] for p in conflict.get_json()["current"]["trash"]) == sorted(p["id"] for p in now["trash"])
    assert len(_board(client)["trash"]) == 2                                        # nothing deleted
    good = [{"id": p["id"], "version": p["version"]} for p in now["trash"]]
    key = uuid.uuid4().hex
    body = {"trash_rev": now["trash_rev"], "items": good, "confirm": "empty", "idempotency_key": key}
    first = client.post(f"{API}/postits/trash/empty", json=body, headers=write_headers(token))
    again = client.post(f"{API}/postits/trash/empty", json=body, headers=write_headers(token))
    assert first.status_code == 200 and first.get_json()["receipt"]["count"] == 2
    assert again.get_json()["repeated"] and again.get_json()["receipt"] == first.get_json()["receipt"]
    assert _board(client)["trash"] == [] and len(_board(client)["active"]) == 1


def test_still_no_delete_route_and_writes_keep_their_guards(board):
    client = board.owner()
    token = board.csrf(client)
    a = _add(client, token)
    for path in (f"/postits/{a['id']}", "/postits/trash", "/media", "/board"):
        assert client.delete(f"{API}{path}", json=keyed(), headers=write_headers(token)).status_code in (404, 405)
    for path, body in ((f"/postits/{a['id']}/done", {"version": 1}), ("/postits/reorder", {"id": 1}),
                       ("/postits/trash/empty", {"confirm": "empty"}), ("/postits/photo", {"asset_id": "x"})):
        assert _post(client, token, path, mode="off_record", **body).status_code == 409
        assert client.post(f"{API}{path}", json=keyed(**body), headers={"X-Record-Mode": "on_record"}).status_code == 403
        assert client.post(f"{API}{path}", json=body, headers=write_headers(token)).status_code == 422   # no key


# ── pinning from Chat (the slice 1 review's carry-forward) ───────────────────

def test_a_pin_must_be_text_from_one_stored_on_the_record_note(board):
    client = board.owner()
    token = board.csrf(client)
    note = board.extra["floor"].store().add_note("note-pin-0001", "Ship the **Build Log** today, then rest.",
                                                 ROBERT).value
    other = board.extra["floor"].store("guild-elsewhere").add_note("note-pin-0002", "Somebody else's floor", ROBERT).value
    ok = _post(client, token, "/postits", text="Build Log today", source_note_id=note["id"])
    assert ok.status_code == 200                                    # rendered selection vs stored Markdown
    for text, source in (("Ship it tomorrow", note["id"]), ("Somebody else", other["id"]), ("x", 999999),
                         ("Build Log", True)):
        r = _post(client, token, "/postits", text=text, source_note_id=source)
        assert r.status_code == 422, (text, source)
    conv_note = board.extra["floor"].store("guild/c-0123456789ab").add_note("note-pin-0003", "From a conversation",
                                                                             ROBERT).value
    assert _post(client, token, "/postits", text="a conversation", source_note_id=conv_note["id"]).status_code == 200
    assert len(_board(client)["active"]) == 2


# ── the Media library ───────────────────────────────────────────────────────

def test_r6_upload_guards_limits_and_types(board, monkeypatch):
    client = board.owner()
    token = board.csrf(client)
    raw = image_bytes()
    assert _upload(client, token, raw, mode="off_record").status_code == 409
    assert _upload(client, token, raw, key=False).status_code == 422
    assert client.post(f"{API}/media", data={"file": (io.BytesIO(raw), "a.jpg")},
                       headers={"X-Record-Mode": "on_record", "Idempotency-Key": "k" * 12},
                       content_type="multipart/form-data").status_code == 403                    # no CSRF token
    assert client.post(f"{API}/media", json={"file": "x"}, headers={**write_headers(token),
                                                                     "Idempotency-Key": "k" * 12}).status_code == 403
    assert _upload(client, token, raw, extra={"title": "x"}).status_code == 422               # one part only
    assert _upload(client, token, b"not an image").status_code == 422
    bmp = io.BytesIO()
    Image.new("RGB", (4, 4)).save(bmp, "BMP")
    assert _upload(client, token, bmp.getvalue()).status_code == 415
    from minimoi_portal.guild_ui import media as M
    monkeypatch.setattr(M, "MAX_BYTES", 2000)
    import minimoi_portal.guild_ui.board_api as BA
    monkeypatch.setattr(BA, "MAX_BYTES", 2000)
    big = _upload(client, token, raw + b"\x00" * 3000)
    assert big.status_code == 413
    assert list(board.extra["media"].rglob("*")) == []                                  # nothing written


def test_upload_strips_exif_dedups_retries_and_serves_only_the_owner(board):
    client = board.owner()
    token = board.csrf(client)
    key = uuid.uuid4().hex
    first = _upload(client, token, image_bytes(), key=key)
    asset = first.get_json()["asset"]
    assert first.status_code == 200 and first.get_json()["result"] == "added" and "storage_key" not in asset
    again = _upload(client, token, image_bytes(), key=key).get_json()
    assert again["repeated"] and again["asset"]["id"] == asset["id"]
    dup = _upload(client, token, image_bytes()).get_json()
    assert dup["result"] == "duplicate" and dup["asset"]["id"] == asset["id"]
    full = client.get(asset["full_url"])
    assert full.status_code == 200 and full.mimetype == "image/jpeg"
    stored = Image.open(io.BytesIO(full.data))
    assert len(stored.getexif()) == 0 and b"Exif\x00\x00" not in full.data and b"TestCam" not in full.data
    assert client.get(asset["thumb_url"]).mimetype == "image/webp"
    assert "img-src 'self'" in full.headers["Content-Security-Policy"] and "no-store" in full.headers["Cache-Control"]
    for other in (board.guest(), board.client()):
        assert other.get(asset["full_url"]).status_code in (302, 403)
    listing = client.get(f"{API}/media").get_json()
    assert [a["id"] for a in listing["assets"]] == [asset["id"]] and listing["files"] == "ok"


def test_r1_trash_then_refused_purge_then_restore_and_410_after_a_real_purge(board):
    client = board.owner()
    token = board.csrf(client)
    asset = _upload(client, token, image_bytes()).get_json()["asset"]
    photo = _post(client, token, "/postits/photo", asset_id=asset["id"], caption="Monday coffee")
    assert photo.status_code == 200 and photo.get_json()["postit"]["thumb_url"] == asset["thumb_url"]
    pid = photo.get_json()["postit"]["id"]
    _post(client, token, f"/postits/{pid}/bin")                                           # the placement is in the Trash
    t = _post(client, token, f"/media/{asset['id']}/trash", version=asset["version"]).get_json()["asset"]
    assert _post(client, token, "/postits/photo", asset_id=asset["id"]).status_code == 409   # no new placements
    assert client.get(asset["thumb_url"]).status_code == 200                             # still served
    rev = client.get(f"{API}/media?state=trash").get_json()["library_trash_rev"]
    refused = _post(client, token, "/media/purge", library_trash_rev=rev, items=[{"id": asset["id"], "version": t["version"]}],
                    confirm="purge")
    assert refused.status_code == 409 and refused.get_json()["result"] == "in_use"
    assert refused.get_json()["current"]["in_use"][asset["id"]][0]["ref_id"] == str(pid)
    assert client.get(f"{API}/media/{asset['id']}/uses").get_json()["uses"][0]["ref_kind"] == "postit"
    # Restore the placement: the photo shows again.
    assert _post(client, token, f"/postits/{pid}/restore").status_code == 200
    assert client.get(asset["thumb_url"]).status_code == 200
    # Now remove the placement for good, and the purge goes through: 410 afterwards.
    _post(client, token, f"/postits/{pid}/bin")
    trash = _board(client)
    _post(client, token, "/postits/trash/empty", trash_rev=trash["trash_rev"], confirm="empty",
          items=[{"id": p["id"], "version": p["version"]} for p in trash["trash"]])
    rev = client.get(f"{API}/media?state=trash").get_json()["library_trash_rev"]
    done = _post(client, token, "/media/purge", library_trash_rev=rev, items=[{"id": asset["id"], "version": t["version"]}],
                 confirm="purge")
    assert done.status_code == 200 and done.get_json()["receipt"]["count"] == 1 and "collect" not in done.get_json()["receipt"]
    assert client.get(asset["full_url"]).status_code == 410
    assert not any(board.extra["media"].rglob("*.jpg"))


def test_a_missing_media_folder_is_unavailable_never_a_silent_drop(board, tmp_path):
    board.app.extensions["guild_ui_next"]["services"].media_dir = str(tmp_path / "not-mounted")
    client = board.owner()
    token = board.csrf(client)
    r = _upload(client, token, image_bytes())
    assert r.status_code == 503 and "media folder" in r.get_json()["message"]
    assert client.get(f"{API}/media").get_json()["files"] == "unavailable"


def test_the_board_and_library_make_no_model_or_network_call(load_portal, no_outbound, tmp_path):
    portal = load_portal(ops_url="http://ops.invalid:8768/status")
    _mock_probe(portal)
    db = SqliteFloor(tmp_path / "floor")
    services = portal.app.extensions["guild_ui_next"]["services"]
    services.floor = db.store()
    services.media_dir = str(tmp_path / "media")
    (tmp_path / "media").mkdir()
    client = portal.owner()
    token = portal.csrf(client)
    before = set(sys.modules)
    a = _add(client, token, "quiet")
    asset = _upload(client, token, image_bytes()).get_json()["asset"]
    assert _post(client, token, "/postits/photo", asset_id=asset["id"]).status_code == 200
    assert _post(client, token, f"/postits/{a['id']}/done", version=a["version"]).status_code == 200
    for url in ("/guild-next/guild/board", "/guild-next/guild/media", f"{API}/board", f"{API}/media", asset["thumb_url"]):
        assert client.get(url).status_code == 200, url
    new = set(sys.modules) - before
    assert not [m for m in new if m.split(".")[0] in {p.split(".")[0] for p in PROVIDER_MODULES}]
    assert_no_outbound(no_outbound)


# ── rollback: the previous code on the new schema, with real Board data ──────

def _old_stores(folder):
    try:
        source = subprocess.run(["git", "-C", str(REPO), "show", "0d2e7df3:minimoi_portal/guild_ui/stores.py"],
                                capture_output=True, text=True, check=True, timeout=10).stdout
    except Exception:
        pytest.skip("the integration base 0d2e7df3 is not in this checkout")
    path = folder / "old_stores.py"           # outside the package; its relative imports resolve by name
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("minimoi_portal.guild_ui._old_stores_for_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module            # dataclasses look their module up while building
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_r5_the_previous_code_keeps_working_on_the_new_schema_with_board_data(tmp_path):
    db = SqliteFloor(tmp_path / "floor")
    new = db.store()
    a = new.add_postit("decide", ROBERT, idempotency_key="rb-0001", label="decide", item_ref=12).value
    b = new.add_postit("done one", ROBERT, idempotency_key="rb-0002").value
    new.set_done(b["id"], True, ROBERT, expect_version=b["version"], idempotency_key="rb-0003")
    c = new.add_postit("binned", ROBERT, idempotency_key="rb-0004").value
    new.bin_postit(c["id"], ROBERT, idempotency_key="rb-0005")
    rev = new.board().data["order_rev"]
    new.reorder(a["id"], before_id=b["id"], after_id=None, expect_order_rev=rev, by=ROBERT, idempotency_key="rb-0006")
    old_mod = _old_stores(tmp_path)
    old = old_mod.FloorStores(lambda: "sqlite:test", connect=db.connect, paramstyle="qmark")
    listed = old.list_postits()
    assert listed.ok and {p["text"] for p in listed.data["postits"]} == {"decide", "done one"}   # older code ignores Done
    assert old.list_bin().data["total"] == 1
    added = old.add_postit("written by the previous code", old_mod.Author("robert", "owner", "Robert"),
                           idempotency_key="rb-0007")
    assert added.outcome == "added"
    assert old.restore_postit(c["id"], old_mod.Author("robert", "owner", "Robert"),
                              idempotency_key="rb-0008").outcome == "restored"
    # And forward again: the new code reads all of it, the old code's note on top.
    after = new.board().data
    assert after["active"][0]["text"] == "written by the previous code" and after["active"][0]["kind"] == "note"
    assert {p["text"] for p in after["done"]} == {"done one"}
    assert next(p for p in after["active"] if p["id"] == a["id"])["label"] == "decide"
