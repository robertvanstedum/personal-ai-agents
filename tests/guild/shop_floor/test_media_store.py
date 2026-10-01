"""Guild 1.1 slice 3 (spec §5.2, the v0.2 addendum, §11 R1/R2/R6): the Media
library's sanitising, files and index, on SQLite and (when
GUILD_FLOOR_TEST_DATABASE_URL names a disposable Postgres) on Postgres."""
from __future__ import annotations

import hashlib
import io
import os
import struct
import time
import zlib

import pytest
from PIL import Image

from minimoi_portal.guild_ui import media as M

from board_media_helpers import board, image_bytes, key  # noqa: F401  (fixtures)


def _png_header_only(width: int, height: int) -> bytes:
    """A PNG whose header claims width x height, with almost no pixel data: a
    decoder would need width*height*3 bytes, the header check needs none."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00" * 16)) + chunk(b"IEND", b"")


# ── sanitising (R6) ───────────────────────────────────────────────────────────

def test_a_jpeg_loses_its_exif_and_gps_and_the_hash_is_over_the_stored_bytes():
    raw = image_bytes("JPEG")
    assert Image.open(io.BytesIO(raw)).getexif().get_ifd(0x8825)            # the input has GPS
    item = M.sanitize(raw)
    out = Image.open(io.BytesIO(item.data))
    assert len(out.getexif()) == 0 and "exif" not in out.info and "icc_profile" not in out.info
    assert b"Exif\x00\x00" not in item.data and b"TestCam" not in item.data
    assert item.sha256 == hashlib.sha256(item.data).hexdigest()
    assert (item.mime, item.ext, item.width, item.height) == ("image/jpeg", "jpg", 64, 48)
    assert Image.open(io.BytesIO(item.thumb)).format == "WEBP"


@pytest.mark.parametrize("fmt,mime", [("PNG", "image/png"), ("WEBP", "image/webp")])
def test_png_and_webp_are_taken(fmt, mime):
    assert M.sanitize(image_bytes(fmt)).mime == mime


def test_a_gif_keeps_its_first_frame_only_as_a_still():
    item = M.sanitize(image_bytes("GIF", frames=3))
    out = Image.open(io.BytesIO(item.data))
    assert out.format == "PNG" and getattr(out, "n_frames", 1) == 1 and item.mime == "image/png"
    assert out.convert("RGB").getpixel((1, 1)) == (200, 120, 40)            # frame one's colour


def test_the_pixel_cap_is_checked_from_the_header_before_any_decode():
    with pytest.raises(M.MediaRejected) as too_many:
        M.sanitize(_png_header_only(10_000, 4_001))                          # 40,010,000 pixels
    assert too_many.value.status == 413
    # Exactly at the cap is allowed past the check (and then fails as corrupt, not as too large).
    with pytest.raises(M.MediaRejected) as at_cap:
        M.sanitize(_png_header_only(10_000, 4_000))
    assert at_cap.value.status == 422


@pytest.mark.parametrize("raw,status", [
    (b"not an image at all", 422),
    (image_bytes("JPEG")[:120], 422),                                        # truncated
    (b"x" * (M.MAX_BYTES + 1), 413),
])
def test_corrupt_unknown_and_oversized_files_are_refused(raw, status):
    with pytest.raises(M.MediaRejected) as err:
        M.sanitize(raw)
    assert err.value.status == status


def test_a_type_we_do_not_take_is_refused():
    out = io.BytesIO()
    Image.new("RGB", (8, 8)).save(out, "BMP")
    with pytest.raises(M.MediaRejected) as err:
        M.sanitize(out.getvalue())
    assert err.value.status == 415


# ── files and the index ───────────────────────────────────────────────────────

def _create(board, raw=None, k=None):
    return board.media.create(board.owner, M.sanitize(raw or image_bytes()), idempotency_key=k or key())


def test_create_writes_files_first_then_the_row_with_dedup_and_retry(board):
    k = key()
    first = _create(board, k=k)
    a = first.value
    assert first.outcome == "added" and a["kind"] == "photo" and a["version"] == 1
    path = board.media_root / a["storage_key"]
    assert a["storage_key"] == f"{board.owner}/{a['sha256'][:2]}/{a['sha256']}.jpg"
    assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == a["sha256"]
    assert (board.media_root / M.thumb_key(a["storage_key"])).is_file()
    assert not [p for p in board.media_root.rglob(".tmp-*")]
    again = _create(board, k=k)
    assert again.repeated and again.value["id"] == a["id"]
    dup = _create(board)                                                     # the same image, a new key
    assert dup.outcome == "duplicate" and dup.value["id"] == a["id"]
    other = _create(board, raw=image_bytes(color=(1, 2, 3)), k=k)           # the key reused for other bytes
    assert other.outcome == "idempotency_mismatch"
    assert len(board.media.library(board.owner).data["assets"]) == 1


def test_trash_restore_and_versions(board):
    a = _create(board).value
    t = board.media.set_trashed(board.owner, a["id"], True, expect_version=a["version"], idempotency_key=key())
    assert t.outcome == "trashed" and t.value["state"] == "trash"
    stale = board.media.set_trashed(board.owner, a["id"], False, expect_version=a["version"], idempotency_key=key())
    assert stale.outcome == "conflict"
    r = board.media.set_trashed(board.owner, a["id"], False, expect_version=t.value["version"], idempotency_key=key())
    assert r.outcome == "restored" and r.value["state"] == "active"
    assert board.media.set_trashed("someone_else", a["id"], True, expect_version=r.value["version"],
                                   idempotency_key=key()).outcome == "not_found"


def _trash(board, a):
    return board.media.set_trashed(board.owner, a["id"], True, expect_version=a["version"],
                                   idempotency_key=key()).value


def test_r1_purge_is_refused_while_a_placement_is_restorable_and_says_where(board):
    a = _create(board).value
    photo = board.floor.add_photo(a["id"], board.owner, "Coffee", board.author, idempotency_key=key()).value
    board.floor.bin_postit(photo["id"], board.author, idempotency_key=key())      # the placement is in the Trash
    t = _trash(board, a)
    rev = board.media.library(board.owner, state="trash").data["library_trash_rev"]
    refused = board.media.purge(board.owner, library_trash_rev=rev, items=[{"id": a["id"], "version": t["version"]}],
                                idempotency_key=key())
    assert refused.outcome == "in_use"
    assert refused.value["in_use"][a["id"]][0]["ref_id"] == str(photo["id"])
    assert (board.media_root / a["storage_key"]).is_file()
    # Restoring the placement brings the image back; it is still served while trashed.
    assert board.floor.restore_postit(photo["id"], board.author, idempotency_key=key()).outcome == "restored"
    assert board.media.get(board.owner, a["id"])["purged_at"] is None


def test_purge_sets_the_tombstone_collects_the_bytes_and_a_retry_returns_the_receipt(board):
    a = _create(board).value
    t = _trash(board, a)
    rev = board.media.library(board.owner, state="trash").data["library_trash_rev"]
    k = key()
    done = board.media.purge(board.owner, library_trash_rev=rev, items=[{"id": a["id"], "version": t["version"]}],
                             idempotency_key=k)
    assert done.outcome == "purged" and done.value["count"] == 1 and done.value["receipt_id"].startswith("m-")
    gone = board.media.get(board.owner, a["id"])
    assert gone["purged_at"] and gone["state"] == "purged"                       # the tombstone row stays
    assert not (board.media_root / a["storage_key"]).exists()
    assert not (board.media_root / M.thumb_key(a["storage_key"])).exists()
    again = board.media.purge(board.owner, library_trash_rev=rev, items=[{"id": a["id"], "version": t["version"]}],
                              idempotency_key=k)
    assert again.repeated and again.value["receipt_id"] == done.value["receipt_id"]
    # The same image uploaded again is a new asset, and its bytes are written again.
    back = _create(board).value
    assert back["id"] != a["id"] and (board.media_root / back["storage_key"]).is_file()


@pytest.mark.parametrize("change", ["old_rev", "other_version", "not_trashed"])
def test_r2_a_purge_set_that_differs_is_a_conflict_and_deletes_nothing(board, change):
    a = _create(board).value
    t = _trash(board, a)
    rev = board.media.library(board.owner, state="trash").data["library_trash_rev"]
    items = [{"id": a["id"], "version": t["version"]}]
    if change == "old_rev":
        rev -= 1
    elif change == "other_version":
        items[0]["version"] += 1
    else:
        r = board.media.set_trashed(board.owner, a["id"], False, expect_version=t["version"], idempotency_key=key())
        rev = board.media.library(board.owner, state="trash").data["library_trash_rev"]
        items = [{"id": a["id"], "version": r.value["version"]}]
    out = board.media.purge(board.owner, library_trash_rev=rev, items=items, idempotency_key=key())
    assert out.outcome == "conflict"
    assert board.media.get(board.owner, a["id"])["purged_at"] is None
    assert (board.media_root / a["storage_key"]).is_file()


def test_the_collector_keeps_bytes_that_a_live_row_still_uses(board):
    a = _create(board).value
    assert board.media.collect(board.owner, a["sha256"], a["storage_key"]) is False
    assert (board.media_root / a["storage_key"]).is_file()


def test_reconcile_collects_old_orphans_waits_on_new_ones_and_reports_missing_files(board):
    live = _create(board).value
    orphan_old = board.media_root / board.owner / "ab" / ("ab" + "1" * 62 + ".png")
    orphan_new = board.media_root / board.owner / "cd" / ("cd" + "2" * 62 + ".png")
    for path in (orphan_old, orphan_new):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"orphan")
    old = time.time() - 25 * 3600
    os.utime(orphan_old, (old, old))
    missing = _create(board, raw=image_bytes(color=(9, 9, 9))).value
    (board.media_root / missing["storage_key"]).unlink()
    report = board.media.reconcile()
    assert f"{board.owner}/ab/{orphan_old.name}" in report["orphans_collected"] and not orphan_old.exists()
    assert f"{board.owner}/cd/{orphan_new.name}" in report["orphans_waiting"] and orphan_new.exists()
    assert report["missing"] == [missing["storage_key"]]
    assert (board.media_root / live["storage_key"]).is_file()
