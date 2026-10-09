"""Build refinement, 6 Oct (Codex review P1): an image kept with a conversation holds a live Media
reference, so the library cannot purge it from under the conversation. The reference is taken under
the asset row's lock (the lock purge takes), is released on Remove without touching the shared image,
and is given back if the conversation cannot keep it."""
from __future__ import annotations

import io
import os
import threading
import uuid

import pytest

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from board_media_helpers import board, image_bytes, key  # noqa: F401  (fixtures)
from test_media_store import _create, _trash

from minimoi_portal.guild_ui.media import media_of

API = "/guild-next/api/v1"


def _purge(board, a, t):
    rev = board.media.library(board.owner, state="trash").data["library_trash_rev"]
    return board.media.purge(board.owner, library_trash_rev=rev, items=[{"id": a["id"], "version": t["version"]}],
                             idempotency_key=key())


def test_attach_takes_a_live_reference_and_purge_refuses_while_it_is_held(board):
    a = _create(board).value
    assert board.media.attach(board.owner, a["id"], "c-aaaaaaaaaaaa").outcome == "attached"
    assert board.media.attach(board.owner, a["id"], "c-aaaaaaaaaaaa").outcome == "already"            # again: no second hold
    uses = board.media.uses(board.owner, a["id"])
    assert [(u["domain"], u["ref_kind"], u["ref_id"]) for u in uses] == [("guild", "conversation", "c-aaaaaaaaaaaa")]
    t = _trash(board, a)                                                                                # trashing is allowed; purging is not
    refused = _purge(board, a, t)
    assert refused.outcome == "in_use" and refused.value["in_use"][a["id"]][0]["ref_id"] == "c-aaaaaaaaaaaa"
    assert (board.media_root / a["storage_key"]).is_file()                                              # the bytes are still there
    assert board.media.get(board.owner, a["id"])["purged_at"] is None


def test_two_conversations_hold_it_independently_and_purge_waits_for_both(board):
    a = _create(board).value
    for cid in ("c-aaaaaaaaaaaa", "c-bbbbbbbbbbbb"):
        assert board.media.attach(board.owner, a["id"], cid).outcome == "attached"
    t = _trash(board, a)
    assert board.media.detach(board.owner, a["id"], "c-aaaaaaaaaaaa").outcome == "detached"
    assert _purge(board, a, t).outcome == "in_use"                                                      # the other still holds it
    assert board.media.detach(board.owner, a["id"], "c-bbbbbbbbbbbb").outcome == "detached"
    assert _purge(board, a, t).outcome == "purged"                                                      # nothing holds it now


def test_detach_releases_the_hold_and_never_deletes_the_shared_image(board):
    a = _create(board).value
    board.media.attach(board.owner, a["id"], "c-aaaaaaaaaaaa")
    assert board.media.detach(board.owner, a["id"], "c-aaaaaaaaaaaa").outcome == "detached"
    assert board.media.uses(board.owner, a["id"]) == []
    assert board.media.get(board.owner, a["id"])["state"] == "active" and (board.media_root / a["storage_key"]).is_file()
    assert board.media.detach(board.owner, a["id"], "c-aaaaaaaaaaaa").outcome == "detached"            # harmless twice
    assert board.media.attach(board.owner, a["id"], "c-aaaaaaaaaaaa").outcome == "attached"            # and it can be held again
    assert len(board.media.uses(board.owner, a["id"])) == 1


def test_a_trashed_purged_unknown_or_foreign_image_takes_no_hold(board):
    a = _create(board).value
    t = _trash(board, a)
    assert board.media.attach(board.owner, a["id"], "c-aaaaaaaaaaaa").outcome == "asset_trashed"       # like a Board placement
    assert _purge(board, a, t).outcome == "purged"
    assert board.media.attach(board.owner, a["id"], "c-aaaaaaaaaaaa").outcome == "gone"
    assert board.media.attach(board.owner, str(uuid.uuid4()), "c-aaaaaaaaaaaa").outcome == "not_found"
    assert board.media.attach(board.owner, "not-a-uuid", "c-aaaaaaaaaaaa").outcome == "not_found"
    b = _create(board, raw=image_bytes("PNG", color=(1, 2, 3))).value
    assert board.media.attach("someone-else", b["id"], "c-aaaaaaaaaaaa").outcome == "not_found"        # another owner's image


def test_attach_and_purge_cannot_race_a_purged_image_never_has_a_live_hold(board):
    """Many rounds of attach against trash-then-purge from two threads. The only outcomes are: attached (so purge
    refused) or purged (so attach was refused). Never both."""
    from minimoi_portal.guild_ui.stores import FloorStoreUnavailable
    rounds = int(os.environ.get("GUILD_RACE_ROUNDS", "12"))      # raise it for a longer Postgres soak
    seen = {"held": 0, "purged": 0}
    for n in range(rounds):
        a = _create(board, raw=image_bytes("PNG", color=(n * 7 % 255, 40, 200 - n))).value
        out = {}

        def do_attach():
            out["attach"] = board.media.attach(board.owner, a["id"], f"c-{n:012d}").outcome

        def do_purge():
            try:
                t = _trash(board, a)
                out["purge"] = _purge(board, a, t).outcome
            except FloorStoreUnavailable:
                out["purge"] = "unavailable"

        threads = [threading.Thread(target=do_attach), threading.Thread(target=do_purge)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert "attach" in out and "purge" in out, out
        state = board.media.get(board.owner, a["id"])
        live = board.media.uses(board.owner, a["id"]) if state["state"] != "purged" else None
        if state["state"] == "purged":
            assert out["attach"] in ("gone", "asset_trashed"), out                                       # purge won; attach was refused
            seen["purged"] += 1
        else:
            if out["attach"] == "attached":
                assert out["purge"] == "in_use" and len(live) == 1, out                                  # attach won; purge was refused
                seen["held"] += 1
    assert seen["held"] + seen["purged"] >= 1


def test_attach_is_serial_with_trash_and_purge_even_when_forced_into_the_bad_window(board, monkeypatch):
    """Deterministic version of the race (the soak above is probabilistic): attach reads the asset as active and is held
    there, inside its transaction, while another thread trashes and purges it. With the asset row locked (Postgres) or the
    write lock held (SQLite) the trash and purge must wait for attach, and purge must then refuse. Without it they would
    finish first and attach would hang a live hold on a purged image."""
    from minimoi_portal.guild_ui.media import MediaStore
    a = _create(board, raw=image_bytes("PNG", color=(9, 99, 199))).value
    real_get = MediaStore._get
    paused = threading.Event()
    seen = {"first": True}

    def slow_get(self, q, owner, asset_id, lock=False):
        got = real_get(self, q, owner, asset_id, lock=lock)
        if threading.current_thread().name == "attach-thread" and seen["first"]:
            seen["first"] = False
            paused.set()
            threading.Event().wait(1.5)            # attach stays in its window
        return got

    monkeypatch.setattr(MediaStore, "_get", slow_get)
    out = {}

    def do_attach():
        out["attach"] = board.media.attach(board.owner, a["id"], "c-cccccccccccc").outcome

    def do_purge():
        paused.wait(10)
        t = _trash(board, a)
        out["purge"] = _purge(board, a, t).outcome

    ta = threading.Thread(target=do_attach, name="attach-thread")
    tp = threading.Thread(target=do_purge, name="purge-thread")
    ta.start(); tp.start()
    ta.join(60); tp.join(60)
    monkeypatch.setattr(MediaStore, "_get", real_get)
    state = board.media.get(board.owner, a["id"])
    live = board.media.uses(board.owner, a["id"])
    assert not (state["state"] == "purged" and live), (state["state"], live, out)                      # never both
    assert out == {"attach": "attached", "purge": "in_use"} and state["state"] == "trash" and len(live) == 1, out


# ── through the API ──────────────────────────────────────────────────────────────

@pytest.fixture
def media(floored, tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    floored.app.extensions["guild_ui_next"]["services"].media_dir = str(folder)
    return floored


def _new(client, token):
    return client.post(f"{API}/conversations", json=keyed(), headers=write_headers(token)).get_json()["conversation"]["id"]


def _upload(client, token, raw=None):
    headers = write_headers(token)
    headers["Idempotency-Key"] = uuid.uuid4().hex
    return client.post(f"{API}/media", data={"file": (io.BytesIO(raw or image_bytes("PNG")), "plan.png")}, headers=headers,
                       content_type="multipart/form-data").get_json()["asset"]["id"]


def test_the_api_holds_the_image_for_the_conversation_and_remove_lets_it_go(media):
    client = media.owner()
    token = media.csrf(client)
    cid = _new(client, token)
    asset = _upload(client, token)
    m = media_of(media.app.extensions["guild_ui_next"]["services"])
    r = client.post(f"{API}/conversations/{cid}/attachments", json=keyed(asset_id=asset, name="plan.png"), headers=write_headers(token))
    assert r.status_code == 200
    assert [u["ref_id"] for u in m.uses("robert", asset)] == [cid]                                      # a real hold, not a pointer
    uses = client.get(f"{API}/media/{asset}/uses").get_json()
    assert "conversation" in str(uses)
    gone = client.post(f"{API}/conversations/{cid}/attachments/remove", json=keyed(asset_id=asset), headers=write_headers(token))
    assert gone.status_code == 200 and gone.get_json()["released"] is True
    assert gone.get_json()["conversation"]["attachments"] == []
    assert m.uses("robert", asset) == [] and m.get("robert", asset)["state"] == "active"                # the image is still in the library
    page = client.get(f"/guild-next/guild/build?c={cid}").get_data(as_text=True)
    assert asset not in page


def test_a_conversation_that_cannot_keep_it_gives_the_hold_back(media, monkeypatch):
    import minimoi_portal.guild_ui.conversations as conv_mod
    client = media.owner()
    token = media.csrf(client)
    cid = _new(client, token)
    asset = _upload(client, token)
    monkeypatch.setattr(conv_mod, "ATTACHMENTS_MAX", 0)
    r = client.post(f"{API}/conversations/{cid}/attachments", json=keyed(asset_id=asset, name="x.png"), headers=write_headers(token))
    assert r.status_code == 422
    m = media_of(media.app.extensions["guild_ui_next"]["services"])
    assert m.uses("robert", asset) == []                                                                # nothing is left holding it


def test_a_trashed_image_cannot_be_attached_through_the_api(media):
    client = media.owner()
    token = media.csrf(client)
    cid = _new(client, token)
    asset = _upload(client, token)
    m = media_of(media.app.extensions["guild_ui_next"]["services"])
    m.set_trashed("robert", asset, True, expect_version=1, idempotency_key=key())
    r = client.post(f"{API}/conversations/{cid}/attachments", json=keyed(asset_id=asset, name="x.png"), headers=write_headers(token))
    assert r.status_code == 409 and "Trash" in r.get_json()["message"]
    assert client.get(f"{API}/conversations?view=active").get_json()["conversations"][0]["attachments"] == []


def test_remove_is_guarded(media):
    client = media.owner()
    token = media.csrf(client)
    cid = _new(client, token)
    assert client.post(f"{API}/conversations/{cid}/attachments/remove", json=keyed(asset_id=str(uuid.uuid4()))).status_code == 403
    assert media.guest().post(f"{API}/conversations/{cid}/attachments/remove", json=keyed(asset_id=str(uuid.uuid4())),
                              headers=write_headers(token)).status_code in (401, 403)
    assert client.post(f"{API}/conversations/{cid}/attachments/remove", json=keyed(asset_id="nope"),
                       headers=write_headers(token)).status_code == 422


# ── Codex re-review (6 Oct): the whole lifecycle for a conversation is serial, and a failed release can be retried ──

def _post(client, token, path, **body):
    return client.post(f"{API}{path}", json=keyed(**body), headers=write_headers(token))


def test_remove_and_a_concurrent_reattach_never_leave_an_image_listed_without_its_hold(media, monkeypatch):
    """Codex's interleaving: tab A removes (the pointer goes, the hold is about to be released); tab B attaches the
    same image in that window. Before: B saw the old hold as "already", then A released it. Now the lifecycle runs under one
    lock, so B waits for A to finish, then takes a fresh hold. Whatever the order, listed and held agree."""
    from minimoi_portal.guild_ui.media import MediaStore
    client = media.owner()
    token = media.csrf(client)
    cid = _new(client, token)
    asset = _upload(client, token)
    m = media_of(media.app.extensions["guild_ui_next"]["services"])
    assert _post(client, token, f"/conversations/{cid}/attachments", asset_id=asset, name="plan.png").status_code == 200

    in_release, go = threading.Event(), threading.Event()
    real = MediaStore.detach
    order = []

    def slow_detach(self, *a, **kw):
        in_release.set()                      # A is now between "pointer removed" and "hold released"
        go.wait(1.5)                          # give B every chance to run to completion inside that window
        done = real(self, *a, **kw)
        order.append("A released")
        return done

    monkeypatch.setattr(MediaStore, "detach", slow_detach)
    results = {}

    def tab_a():
        c, t = media.owner(), None
        t = media.csrf(c)
        results["A"] = _post(c, t, f"/conversations/{cid}/attachments/remove", asset_id=asset).status_code

    def tab_b():
        in_release.wait(5)
        c = media.owner()
        t = media.csrf(c)
        results["B"] = _post(c, t, f"/conversations/{cid}/attachments", asset_id=asset, name="plan.png").status_code
        order.append("B attached")
        go.set()                              # only reachable while A is still waiting if B was NOT held back

    threads = [threading.Thread(target=tab_a), threading.Thread(target=tab_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert results == {"A": 200, "B": 200}, results
    assert order.index("A released") < order.index("B attached"), order                 # B could not slip into A's window
    rows = client.get(f"{API}/conversations?view=active").get_json()["conversations"]
    listed = next(c for c in rows if c["id"] == cid)["attachments"]
    held = m.uses("robert", asset)
    assert bool(listed) == bool(held) and listed and held, (listed, held)             # listed and held agree: both present


def test_a_failed_release_is_never_called_released_and_can_be_retried(media, monkeypatch):
    from minimoi_portal.guild_ui.media import MediaStore
    from minimoi_portal.guild_ui.stores import FloorStoreUnavailable
    client = media.owner()
    token = media.csrf(client)
    cid = _new(client, token)
    asset = _upload(client, token)
    m = media_of(media.app.extensions["guild_ui_next"]["services"])
    _post(client, token, f"/conversations/{cid}/attachments", asset_id=asset, name="plan.png")
    real = MediaStore.detach

    def broken(self, *a, **kw):
        raise FloorStoreUnavailable("down (test)")

    monkeypatch.setattr(MediaStore, "detach", broken)
    r = _post(client, token, f"/conversations/{cid}/attachments/remove", asset_id=asset)
    body = r.get_json()
    assert r.status_code == 200 and body["released"] is False and body["result"] == "release_pending"
    assert "not released" in body["message"] and "cannot be deleted" in body["message"]
    assert body["conversation"]["attachments"] == []
    assert [(p["asset_id"], p["name"]) for p in body["conversation"]["release_pending"]] == [(asset, "plan.png")]
    assert m.uses("robert", asset)                                                      # the hold is still there, and says so
    page = client.get(f"/guild-next/guild/build?c={cid}").get_data(as_text=True)
    assert f'data-file-retry="{asset}"' in page and "not released yet" in page         # the page offers a way to try again
    # Recovery: the same call retries, and only now says it was released.
    monkeypatch.setattr(MediaStore, "detach", real)
    r = _post(client, token, f"/conversations/{cid}/attachments/remove", asset_id=asset)
    assert r.get_json()["released"] is True and r.get_json()["conversation"]["release_pending"] == []
    assert m.uses("robert", asset) == []
    assert f"data-file-retry" not in client.get(f"/guild-next/guild/build?c={cid}").get_data(as_text=True)
    assert m.get("robert", asset)["state"] == "active"                                   # and the image itself was never touched


def test_attaching_again_while_a_release_is_pending_restores_the_hold_and_clears_the_marker(media, monkeypatch):
    from minimoi_portal.guild_ui.media import MediaStore
    from minimoi_portal.guild_ui.stores import FloorStoreUnavailable
    client = media.owner()
    token = media.csrf(client)
    cid = _new(client, token)
    asset = _upload(client, token)
    m = media_of(media.app.extensions["guild_ui_next"]["services"])
    _post(client, token, f"/conversations/{cid}/attachments", asset_id=asset, name="plan.png")
    real = MediaStore.detach
    monkeypatch.setattr(MediaStore, "detach", lambda self, *a, **kw: (_ for _ in ()).throw(FloorStoreUnavailable("down")))
    _post(client, token, f"/conversations/{cid}/attachments/remove", asset_id=asset)
    monkeypatch.setattr(MediaStore, "detach", real)
    r = _post(client, token, f"/conversations/{cid}/attachments", asset_id=asset, name="plan.png")
    conv = r.get_json()["conversation"]
    assert [a["asset_id"] for a in conv["attachments"]] == [asset] and conv["release_pending"] == []
    assert len(m.uses("robert", asset)) == 1


def test_a_missing_media_store_is_pending_not_released():
    from minimoi_portal.guild_ui.api import _release_hold
    assert _release_hold(None, "robert", str(uuid.uuid4()), "c-aaaaaaaaaaaa") is False
    assert _release_hold(object(), None, str(uuid.uuid4()), "c-aaaaaaaaaaaa") is False


def test_two_tabs_attaching_the_same_image_keep_one_pointer_and_one_hold(media):
    client = media.owner()
    token = media.csrf(client)
    cid = _new(client, token)
    asset = _upload(client, token)
    m = media_of(media.app.extensions["guild_ui_next"]["services"])
    codes = []

    def tab():
        c = media.owner()
        t = media.csrf(c)
        codes.append(_post(c, t, f"/conversations/{cid}/attachments", asset_id=asset, name="plan.png").status_code)

    threads = [threading.Thread(target=tab) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert codes == [200] * 4
    rows = client.get(f"{API}/conversations?view=active").get_json()["conversations"]
    assert len(next(c for c in rows if c["id"] == cid)["attachments"]) == 1 and len(m.uses("robert", asset)) == 1


# ── Codex second re-review (6 Oct): Retry may only finish a pending release; it must never remove a kept file ──

def _fail_detach(monkeypatch):
    from minimoi_portal.guild_ui.media import MediaStore
    from minimoi_portal.guild_ui.stores import FloorStoreUnavailable
    real = MediaStore.detach
    monkeypatch.setattr(MediaStore, "detach", lambda self, *a, **kw: (_ for _ in ()).throw(FloorStoreUnavailable("down")))
    return real


def test_a_stale_retry_after_a_reattach_never_removes_the_current_file(media, monkeypatch):
    """failed release -> the same image attached again -> a stale Retry (its page or another tab did not hear). The
    current attachment and its hold must survive, and the answer tells the page the true state."""
    from minimoi_portal.guild_ui.media import MediaStore
    client = media.owner()
    token = media.csrf(client)
    cid = _new(client, token)
    asset = _upload(client, token)
    m = media_of(media.app.extensions["guild_ui_next"]["services"])
    _post(client, token, f"/conversations/{cid}/attachments", asset_id=asset, name="plan.png")
    real = _fail_detach(monkeypatch)
    assert _post(client, token, f"/conversations/{cid}/attachments/remove", asset_id=asset).get_json()["released"] is False
    monkeypatch.setattr(MediaStore, "detach", real)
    other = media.owner()
    assert _post(other, media.csrf(other), f"/conversations/{cid}/attachments", asset_id=asset, name="plan.png").status_code == 200
    stale = _post(client, token, f"/conversations/{cid}/attachments/retry", asset_id=asset)           # the old page's Retry
    body = stale.get_json()
    assert stale.status_code == 200 and body["result"] == "current" and "nothing was removed" in body["message"]
    assert [a["asset_id"] for a in body["conversation"]["attachments"]] == [asset] and body["conversation"]["release_pending"] == []
    assert len(m.uses("robert", asset)) == 1                                                            # still held, still listed


def test_retry_finishes_only_what_is_pending(media, monkeypatch):
    from minimoi_portal.guild_ui.media import MediaStore
    client = media.owner()
    token = media.csrf(client)
    cid = _new(client, token)
    asset = _upload(client, token)
    m = media_of(media.app.extensions["guild_ui_next"]["services"])
    # Nothing pending: a Retry does nothing and says so; an attached file is untouched.
    _post(client, token, f"/conversations/{cid}/attachments", asset_id=asset, name="plan.png")
    r = _post(client, token, f"/conversations/{cid}/attachments/retry", asset_id=asset).get_json()
    assert r["result"] == "current" and len(m.uses("robert", asset)) == 1
    real = _fail_detach(monkeypatch)
    _post(client, token, f"/conversations/{cid}/attachments/remove", asset_id=asset)
    still = _post(client, token, f"/conversations/{cid}/attachments/retry", asset_id=asset).get_json()      # still failing
    assert still["result"] == "pending" and still["released"] is False and m.uses("robert", asset)
    assert [p["asset_id"] for p in still["conversation"]["release_pending"]] == [asset]
    monkeypatch.setattr(MediaStore, "detach", real)
    done = _post(client, token, f"/conversations/{cid}/attachments/retry", asset_id=asset).get_json()
    assert done["result"] == "released" and done["conversation"]["release_pending"] == [] and m.uses("robert", asset) == []
    again = _post(client, token, f"/conversations/{cid}/attachments/retry", asset_id=asset).get_json()
    assert again["result"] == "nothing_pending"                                                           # harmless to repeat
    assert m.get("robert", asset)["state"] == "active"


def test_retry_is_guarded(media):
    client = media.owner()
    token = media.csrf(client)
    cid = _new(client, token)
    assert client.post(f"{API}/conversations/{cid}/attachments/retry", json=keyed(asset_id=str(uuid.uuid4()))).status_code == 403
    assert media.guest().post(f"{API}/conversations/{cid}/attachments/retry", json=keyed(asset_id=str(uuid.uuid4())),
                              headers=write_headers(token)).status_code in (401, 403)
    assert client.post(f"{API}/conversations/{cid}/attachments/retry", json=keyed(asset_id="nope"),
                       headers=write_headers(token)).status_code == 422
    assert client.post(f"{API}/conversations/c-000000000000/attachments/retry", json=keyed(asset_id=str(uuid.uuid4())),
                       headers=write_headers(token)).status_code == 404


def test_retry_drops_a_stale_marker_for_a_file_that_is_attached_and_leaves_the_file(media):
    """A marker and a current attachment for the same image (a crash, or two writers) must resolve to the file kept."""
    from minimoi_portal.guild_ui.conversations import conversations_of
    client = media.owner()
    token = media.csrf(client)
    cid = _new(client, token)
    asset = _upload(client, token)
    m = media_of(media.app.extensions["guild_ui_next"]["services"])
    _post(client, token, f"/conversations/{cid}/attachments", asset_id=asset, name="plan.png")
    store = conversations_of(media.app.extensions["guild_ui_next"]["services"])
    store._change(cid, "robert", lambda c: c.setdefault("release_pending", []).append({"asset_id": asset, "name": "plan.png", "since": "x"}))
    r = _post(client, token, f"/conversations/{cid}/attachments/retry", asset_id=asset).get_json()
    assert r["result"] == "current" and r["conversation"]["release_pending"] == []
    assert [a["asset_id"] for a in r["conversation"]["attachments"]] == [asset] and len(m.uses("robert", asset)) == 1
