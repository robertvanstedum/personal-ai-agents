"""The shared Media library (Guild 1.1 slice 3, spec §5.2 and the v0.2
addendum): image files on the file system, a small index in Postgres.

Files (file-first, R5): originals and thumbnails live only under the media
root (``MINIMOI_MEDIA_DIR``, default /app/runtime/media, a persistent host
bind mount on staging):

    <root>/<owner>/<sha256[:2]>/<sha256>.<ext>
    <root>/<owner>/<sha256[:2]>/thumb/<sha256>.webp

Each is written to a temporary file in the same folder, fsynced, then
renamed into place (the same sha256 gives the same path, so a rewrite is
harmless). The index row is inserted afterwards; a file without a row is an
orphan that only the reconcile job collects, after 24 hours.

Upload processing (R6): the type comes from the decoded image (JPEG, PNG,
WebP or GIF), never the filename; the pixel count is checked from the header
before any full decode (width x height <= 40,000,000); a corrupt image is
refused; a GIF (or any animation) keeps its first frame only, as a still;
every image is re-encoded from its pixels, so no EXIF, GPS, XMP, ICC or
comment survives; the sha256 is taken over the sanitised bytes and is the
per-owner dedup key.

Index (media.assets, media."references", media.library_meta): the library
Trash keeps an asset served to the placements it already has and refuses new
ones; a permanent purge needs a trashed asset with no live reference, checks
the owner's library_trash_rev and the exact {id, version} set, and sets the
purged_at tombstone. After the commit the collector deletes the bytes, but
only when no asset row that is not purged uses the same storage key.
Creation and collection are serialised per storage key with a Postgres
advisory lock on (owner, sha256), so neither can race a physical delete.

Nothing here calls a model or the network.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import threading
import time
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path

from . import stores as S

DEFAULT_ROOT = "/app/runtime/media"
MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 40_000_000
THUMB_EDGE = 480
ORPHAN_GRACE_S = 24 * 3600
FORMATS = {"JPEG": ("image/jpeg", "jpg"), "MPO": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"),
           "WEBP": ("image/webp", "webp"),
           "GIF": ("image/png", "png")}           # a GIF is stored as its first frame, a PNG still
# MPO is the multi-picture JPEG many phones write (the main photo plus a depth
# or gain map): it is stored as a plain JPEG of its first picture.
JPEG_LIKE = ("JPEG", "MPO")
SANITIZE_WAIT_S = 30
# One decode at a time per portal process: a 40 MP image needs several hundred
# MB while it is decoded and re-encoded, so two at once could exhaust memory.
_SANITIZING = threading.BoundedSemaphore(1)
OWNER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
KINDS = ("photo", "icon", "emoji")


class MediaRejected(Exception):
    """An upload that is refused: ``status`` 413 (too large), 415 (not an
    image type we take), 422 (corrupt) or 503 (another image is still being
    processed; try again)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Sanitized:
    data: bytes
    thumb: bytes
    mime: str
    ext: str
    width: int
    height: int
    sha256: str


def media_root(env=None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("MINIMOI_MEDIA_DIR") or DEFAULT_ROOT)


# ── sanitising ───────────────────────────────────────────────────────────────

def sanitize(raw: bytes) -> Sanitized:
    """Decode, check and re-encode one image, one at a time. Raises MediaRejected."""
    if len(raw) > MAX_BYTES:
        raise MediaRejected(413, f"The image is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    if not _SANITIZING.acquire(timeout=SANITIZE_WAIT_S):
        raise MediaRejected(503, "Another image is still being processed. Try again in a moment.")
    try:
        return _sanitize(raw)
    finally:
        _SANITIZING.release()


def _sanitize(raw: bytes) -> Sanitized:
    from PIL import Image, ImageOps, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS          # a backstop only: the explicit check below decides
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(raw))           # lazy: reads the header only
            fmt = probe.format
            width, height = probe.size
    except Image.DecompressionBombError:
        raise MediaRejected(413, f"The image has more than {MAX_PIXELS:,} pixels.") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise MediaRejected(422, "That file is not a readable image. Nothing was added.") from None
    if fmt not in FORMATS:
        raise MediaRejected(415, "Only JPEG, PNG, WebP and GIF images can be added.")
    if width <= 0 or height <= 0:
        raise MediaRejected(422, "That image has no size. Nothing was added.")
    if width * height > MAX_PIXELS:                       # explicit, before any full decode
        raise MediaRejected(413, f"The image has more than {MAX_PIXELS:,} pixels.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(raw))
            img.seek(0)                                   # the first frame of a GIF or animation
            img.load()
            img = ImageOps.exif_transpose(img) if fmt != "GIF" else img
    except Exception:
        raise MediaRejected(422, "That image could not be decoded. Nothing was added.") from None
    mime, ext = FORMATS[fmt]
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    if fmt in JPEG_LIKE:
        mode, save_as, opts = "RGB", "JPEG", {"quality": 88, "optimize": True}
    elif fmt == "WEBP":
        mode, save_as, opts = ("RGBA" if has_alpha else "RGB"), "WEBP", {"quality": 88}
    else:                                                 # PNG, and a GIF's first frame
        mode, save_as, opts = ("RGBA" if has_alpha else "RGB"), "PNG", {"optimize": True}
    pixels = img if img.mode == mode else img.convert(mode)
    del img
    # A fresh image from the pixels only: no info dict, so no EXIF, GPS, XMP,
    # ICC profile, comment or animation can be carried over. paste() copies
    # the pixels without the extra full-size bytes buffer tobytes() made.
    clean = Image.new(mode, pixels.size)
    clean.paste(pixels)
    del pixels
    width, height = clean.size
    out = io.BytesIO()
    clean.save(out, save_as, **opts)
    data = out.getvalue()
    del out
    clean.thumbnail((THUMB_EDGE, THUMB_EDGE))            # in place: the full-size copy is no longer needed
    tout = io.BytesIO()
    clean.save(tout, "WEBP", quality=80)
    return Sanitized(data=data, thumb=tout.getvalue(), mime=mime, ext=ext, width=width,
                     height=height, sha256=hashlib.sha256(data).hexdigest())


# ── files ────────────────────────────────────────────────────────────────────

def storage_key(owner: str, sha: str, ext: str) -> str:
    return f"{owner}/{sha[:2]}/{sha}.{ext}"


def thumb_key(key: str) -> str:
    folder, name = key.rsplit("/", 1)
    return f"{folder}/thumb/{name.rsplit('.', 1)[0]}.webp"


def _safe_path(root: Path, key: str) -> Path:
    path = (root / key).resolve()
    if not str(path).startswith(str(root.resolve()) + os.sep):
        raise ValueError("storage key outside the media root")
    return path


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size == len(data):
        return                                            # the same sha256: the same bytes
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def write_files(root: Path, key: str, item: Sanitized) -> None:
    _write_atomic(_safe_path(root, key), item.data)
    _write_atomic(_safe_path(root, thumb_key(key)), item.thumb)


def delete_files(root: Path, key: str) -> None:
    for k in (key, thumb_key(key)):
        try:
            _safe_path(root, k).unlink()
        except FileNotFoundError:
            pass


def root_problem(root: Path) -> str | None:
    if not root.is_dir():
        return "the media folder is not there"
    if not os.access(root, os.W_OK):
        return "the media folder is not writable"
    return None


# ── the index ────────────────────────────────────────────────────────────────

_ASSET_COLS = ("id, owner, kind, sha256, mime, bytes, width, height, storage_key, emoji, title, created_at, "
               "trashed_at, trashed_by, purged_at, version")


def _asset(row) -> dict:
    (aid, owner, kind, sha, mime, size, width, height, key, emoji, title, created, trashed, trashed_by,
     purged, version) = row
    return {"id": str(aid), "owner": owner, "kind": kind, "sha256": sha, "mime": mime,
            "bytes": int(size) if size is not None else None, "width": width, "height": height,
            "storage_key": key, "emoji": emoji, "title": title, "created_at": S.iso(created),
            "trashed_at": S.iso(trashed), "trashed_by": trashed_by, "purged_at": S.iso(purged),
            "version": int(version or 1),
            "state": "purged" if purged is not None else ("trash" if trashed is not None else "active")}


class MediaStore:
    """The library index for one portal, on the floor store's database."""

    def __init__(self, db: S.FloorStores, root: Path):
        self.db = db
        self.root = Path(root)

    # plumbing
    @property
    def _for_update(self) -> str:
        return self.db._for_update

    def _key_lock(self, q, owner: str, sha: str) -> None:
        """Serialise creation and collection per storage key (Postgres); on
        SQLite the transaction already holds the write lock."""
        if not self.db._qmark:
            q("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [f"media:{owner}:{sha}"])

    def _requests_floor(self, owner: str) -> str:
        return f"media/{owner}"

    def _claim(self, q, owner, key, op, target, now):
        """The floor store's idempotency claim, under this owner's media key
        space (``media/<owner>`` in guild.floor_requests). The shared floor
        store is never mutated: requests on other threads use it too."""
        floor = self._requests_floor(owner)
        rows = q("INSERT INTO guild.floor_requests (floor, idempotency_key, principal, op, target, created_at) "
                 "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (floor, idempotency_key) DO NOTHING "
                 "RETURNING idempotency_key", [floor, key, owner, op, target, now])
        if rows:
            return None
        rows = q("SELECT op, target, outcome, result_ref, principal FROM guild.floor_requests "
                 "WHERE floor = %s AND idempotency_key = %s", [floor, key])
        if not rows:
            raise S.FloorStoreUnavailable("idempotency key neither claimed nor found")
        op0, target0, outcome, ref, principal0 = rows[0]
        return {"same": op0 == op and target0 == target and principal0 == owner, "outcome": outcome, "ref": ref}

    def _settle(self, q, owner, key, outcome, ref):
        q("UPDATE guild.floor_requests SET outcome = %s, result_ref = %s WHERE floor = %s AND idempotency_key = %s",
          [outcome, None if ref is None else str(ref), self._requests_floor(owner), key])

    def _library_meta(self, q, owner) -> int:
        q("INSERT INTO media.library_meta (owner) VALUES (%s) ON CONFLICT (owner) DO NOTHING", [owner])
        rows = q(f"SELECT library_trash_rev FROM media.library_meta WHERE owner = %s{self._for_update}", [owner])
        return int(rows[0][0])

    def _library_rev(self, q, owner) -> int:
        rows = q("SELECT library_trash_rev FROM media.library_meta WHERE owner = %s", [owner])
        return int(rows[0][0]) if rows else 0

    def _uses(self, q, asset_id) -> list[dict]:
        rows = q('SELECT domain, ref_kind, ref_id, created_at FROM media."references" WHERE asset_id = %s '
                 "AND released_at IS NULL ORDER BY id", [str(asset_id)])
        return [{"domain": r[0], "ref_kind": r[1], "ref_id": r[2], "created_at": S.iso(r[3])} for r in rows]

    def _get(self, q, owner, asset_id, lock=False):
        try:
            uuid.UUID(str(asset_id))
        except ValueError:
            return None
        rows = q(f"SELECT {_ASSET_COLS} FROM media.assets WHERE id = %s AND owner = %s"
                 f"{self._for_update if lock else ''}", [str(asset_id), owner])
        return _asset(rows[0]) if rows else None

    # reads
    def library(self, owner: str, *, state: str = "active", kind: str | None = None):
        def work(q):
            cond = {"active": "trashed_at IS NULL AND purged_at IS NULL",
                    "trash": "trashed_at IS NOT NULL AND purged_at IS NULL"}[state]
            params = [owner]
            sql = f"SELECT {_ASSET_COLS} FROM media.assets WHERE owner = %s AND {cond}"
            if kind:
                sql += " AND kind = %s"
                params.append(kind)
            assets = [_asset(r) for r in q(sql + " ORDER BY created_at DESC, id", params)]
            for a in assets:
                a["uses"] = len(self._uses(q, a["id"]))
            counts = {s: int(q(f"SELECT COUNT(*) FROM media.assets WHERE owner = %s AND "
                               f"{'trashed_at IS NULL' if s == 'active' else 'trashed_at IS NOT NULL'} "
                               "AND purged_at IS NULL", [owner])[0][0]) for s in ("active", "trash")}
            return {"assets": assets, "library_trash_rev": self._library_rev(q, owner), "counts": counts}
        return self.db._read(work, fresh_for_s=30)

    def get(self, owner: str, asset_id: str) -> dict | None:
        return self.db._run(lambda q: self._get(q, owner, asset_id), write=False)

    def uses(self, owner: str, asset_id: str) -> list[dict] | None:
        def work(q):
            return self._uses(q, asset_id) if self._get(q, owner, asset_id) else None
        return self.db._run(work, write=False)

    # writes
    def create(self, owner: str, item: Sanitized, *, kind: str = "photo", title: str | None = None,
               idempotency_key: str):
        """Write the files and the index row under the storage-key lock.
        Outcome "added", "duplicate" (the owner's existing asset with these
        bytes, even if it is in the library Trash) or "idempotency_mismatch"."""
        if not OWNER_RE.fullmatch(owner or ""):
            raise ValueError("owner")
        now = S.utc_now()
        key = storage_key(owner, item.sha256, item.ext)

        def work(q):
            prior = self._claim(q, owner, idempotency_key, "media.upload", item.sha256, now)
            if prior is not None:
                if not prior["same"]:
                    return S.WriteResult("idempotency_mismatch", None, True)
                return S.WriteResult(prior["outcome"], self._get(q, owner, prior["ref"]), True)
            self._key_lock(q, owner, item.sha256)
            rows = q(f"SELECT {_ASSET_COLS} FROM media.assets WHERE owner = %s AND sha256 = %s "
                     "AND purged_at IS NULL", [owner, item.sha256])
            if rows:
                asset = _asset(rows[0])
                write_files(self.root, asset["storage_key"], item)     # heal missing bytes; idempotent
                self._settle(q, owner, idempotency_key, "duplicate", asset["id"])
                return S.WriteResult("duplicate", asset, False)
            write_files(self.root, key, item)                           # files first, then the row
            aid = str(uuid.uuid4())
            rows = q(f"INSERT INTO media.assets (id, owner, kind, sha256, mime, bytes, width, height, storage_key, "
                     f"title, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_ASSET_COLS}",
                     [aid, owner, kind, item.sha256, item.mime, len(item.data), item.width, item.height, key,
                      title, now])
            self._settle(q, owner, idempotency_key, "added", aid)
            return S.WriteResult("added", _asset(rows[0]), False)
        return self.db._run(work, write=True)

    def set_trashed(self, owner: str, asset_id: str, trashed: bool, *, expect_version: int, idempotency_key: str):
        """Library Trash or Restore. Outcome "trashed" / "restored",
        "already_trashed" / "already_active", "conflict" (stale version, value:
        the current asset), "gone" (purged) or "not_found"."""
        now = S.utc_now()
        op = "media.trash" if trashed else "media.restore"

        def work(q):
            prior = self._claim(q, owner, idempotency_key, op, f"{asset_id}:{int(expect_version)}", now)
            if prior is not None:
                if not prior["same"]:
                    return S.WriteResult("idempotency_mismatch", None, True)
                return S.WriteResult(prior["outcome"], self._get(q, owner, asset_id), True)
            self._library_meta(q, owner)
            cur = self._get(q, owner, asset_id, lock=True)
            if cur is None:
                outcome, value = "not_found", None
            elif cur["purged_at"]:
                outcome, value = "gone", cur
            elif cur["version"] != int(expect_version):
                outcome, value = "conflict", cur
            elif trashed and cur["trashed_at"]:
                outcome, value = "already_trashed", cur
            elif not trashed and not cur["trashed_at"]:
                outcome, value = "already_active", cur
            else:
                sets = "trashed_at = %s, trashed_by = %s" if trashed else "trashed_at = NULL, trashed_by = NULL"
                params = [now, owner] if trashed else []
                rows = q(f"UPDATE media.assets SET {sets}, version = version + 1 WHERE id = %s AND owner = %s "
                         f"RETURNING {_ASSET_COLS}", params + [str(asset_id), owner])
                q("UPDATE media.library_meta SET library_trash_rev = library_trash_rev + 1 WHERE owner = %s", [owner])
                outcome, value = ("trashed" if trashed else "restored"), _asset(rows[0])
            self._settle(q, owner, idempotency_key, outcome, asset_id)
            return S.WriteResult(outcome, value, False)
        return self.db._run(work, write=True)

    def purge(self, owner: str, *, library_trash_rev: int, items: list[dict], idempotency_key: str):
        """Permanently delete trashed assets (R1, R2). In one transaction:
        lock the owner's library meta, check library_trash_rev and that every
        named asset is still trashed at that version, lock each asset row and
        refuse ("in_use", value: where each is used) if any has a live
        reference. Otherwise set purged_at on each and keep a receipt. The
        bytes are collected after the commit. A retry returns the receipt."""
        now = S.utc_now()
        wanted = sorted((str(i["id"]), int(i["version"])) for i in items)
        target = hashlib.sha256(json.dumps([int(library_trash_rev), wanted]).encode()).hexdigest()[:24]

        def work(q):
            prior = self._claim(q, owner, idempotency_key, "media.purge", target, now)
            if prior is not None:
                if not prior["same"]:
                    return S.WriteResult("idempotency_mismatch", None, True)
                if prior["outcome"] == "purged":
                    return S.WriteResult("purged", json.loads(prior["ref"]), True)
                return S.WriteResult(prior["outcome"], {"library_trash_rev": self._library_rev(q, owner)}, True)
            rev = self._library_meta(q, owner)
            current = {}
            for aid, _v in wanted:
                a = self._get(q, owner, aid, lock=True)
                if a is not None:
                    current[aid] = a
            stale = rev != int(library_trash_rev) or len(current) != len(wanted) or any(
                current[aid]["version"] != v or current[aid]["state"] != "trash" for aid, v in wanted)
            if not wanted or stale:
                self._settle(q, owner, idempotency_key, "conflict", None)
                return S.WriteResult("conflict", {"library_trash_rev": rev,
                                                  "trash": [a for a in current.values()]}, False)
            in_use = {aid: self._uses(q, aid) for aid, _v in wanted}
            in_use = {aid: u for aid, u in in_use.items() if u}
            if in_use:
                self._settle(q, owner, idempotency_key, "in_use", None)
                return S.WriteResult("in_use", {"in_use": in_use, "library_trash_rev": rev}, False)
            for aid, _v in wanted:
                q("UPDATE media.assets SET purged_at = %s, version = version + 1 WHERE id = %s AND owner = %s",
                  [now, aid, owner])
            q("UPDATE media.library_meta SET library_trash_rev = library_trash_rev + 1 WHERE owner = %s", [owner])
            receipt = {"receipt_id": f"m-{now[:19].replace('-', '').replace(':', '')}-{target[:6]}",
                       "items": [{"id": a, "version": v} for a, v in wanted], "count": len(wanted),
                       "principal": owner, "at": now, "library_trash_rev": rev + 1,
                       "collect": [{"owner": owner, "sha256": current[a]["sha256"],
                                    "storage_key": current[a]["storage_key"]} for a, _v in wanted]}
            self._settle(q, owner, idempotency_key, "purged", json.dumps(receipt, sort_keys=True))
            return S.WriteResult("purged", receipt, False)
        done = self.db._run(work, write=True)
        if done.outcome == "purged" and not done.repeated:
            for c in done.value.get("collect", []):
                try:
                    self.collect(c["owner"], c["sha256"], c["storage_key"])
                except Exception:          # the reconcile job retries; the purge itself is done
                    pass
        return done

    def collect(self, owner: str, sha: str, key: str) -> bool:
        """Delete the bytes of a purged asset, under the storage-key lock, only
        when no asset row that is not purged uses the same storage key."""
        def work(q):
            self._key_lock(q, owner, sha)
            live = q("SELECT COUNT(*) FROM media.assets WHERE storage_key = %s AND purged_at IS NULL", [key])
            if int(live[0][0]):
                return False
            delete_files(self.root, key)
            return True
        return self.db._run(work, write=True)

    def reconcile(self, *, now: float | None = None, grace_s: int = ORPHAN_GRACE_S) -> dict:
        """The reconcile job (run on demand or on a timer): collect the bytes
        of purged assets that are still on disk, report rows whose file is
        missing, and collect orphan files (no row at all) older than the
        grace period. Returns what it found and did; never raises on a
        single file."""
        now = time.time() if now is None else now
        report = {"collected": [], "orphans_collected": [], "orphans_waiting": [], "missing": []}
        rows = self.db._run(lambda q: q("SELECT owner, sha256, storage_key, purged_at FROM media.assets "
                                        "WHERE storage_key IS NOT NULL"), write=False)
        known = {}
        for owner, sha, key, purged in rows:
            known.setdefault(key, []).append((owner, sha, purged is None))
        for key, entries in known.items():
            live = any(alive for _o, _s, alive in entries)
            exists = _safe_path(self.root, key).exists()
            if live and not exists:
                report["missing"].append(key)
            elif not live and exists:
                owner, sha, _alive = entries[0]
                if self.collect(owner, sha, key):
                    report["collected"].append(key)
        if self.root.is_dir():
            for path in sorted(self.root.rglob("*")):
                if not path.is_file():
                    continue
                age = now - path.stat().st_mtime
                rel = path.relative_to(self.root).as_posix()
                if path.name.startswith(".tmp-"):           # an interrupted write
                    if age >= grace_s:
                        path.unlink(missing_ok=True)
                        report["orphans_collected"].append(rel)
                    continue
                if "/thumb/" in rel or rel in known or rel.count("/") != 2:
                    continue
                if age < grace_s:
                    report["orphans_waiting"].append(rel)
                    continue
                owner, _two, name = rel.split("/", 2)
                if self.collect(owner, name.rsplit(".", 1)[0], rel):
                    report["orphans_collected"].append(rel)
        return report


def media_of(services) -> MediaStore | None:
    floor = getattr(services, "floor", None)
    if floor is None:
        return None
    return MediaStore(floor, Path(getattr(services, "media_dir", None) or media_root()))
