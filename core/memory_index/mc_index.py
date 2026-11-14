"""Master Craftsman's Vault index: the shelf, keyword-only, career material filtered out at index time (#594).

    python -m core.memory_index.mc_index build --shelf SHELF --out INDEX [--rules DEFAULT] [--private PRIVATE]
                                               [--require-private] [--release-from CURRENT]

One run brings ``INDEX`` up to date with ``SHELF`` under the career filter (``career_filter.py``) and publishes it atomically:

1. The rules load first. ``--require-private`` (production) refuses without the host's private rules file: the build stops
   (``filter_missing``) and the previous index, if any, stays as it was. A first run then leaves no index, and MC's search
   answers ``index_missing``. Fail closed.
2. ``--release-from`` names the Vault's ``current`` link. Its target is resolved once, so the whole run reads one immutable
   release. When neither that release nor the filter digest changed since the published index, the run is a no-op.
3. The published index is copied to a work file and updated incrementally. An unchanged, hard-linked record file costs one
   stat. A different filter digest, an older schema or a missing index means a full build into the work file instead.
4. Checked before it is published: ``quick_check`` passes, it is keyword-only, and its ``meta`` names ``profile=mc`` and this
   filter's digest (the meta ``vault-ask-mc`` requires). Then fsync, ``os.replace`` over ``INDEX`` and the folder is fsynced.
   Readers open a fresh connection per request, so they see either the old file or the new one, never a half-written one.

It never writes the shelf, never deletes from it, and never touches the full index. Output: one JSON line of counts, never
text or terms. Exit 0 ``ok`` | ``noop``, 1 failed, 2 refused.

Production (root cron on EC2, every 15 min, offset from vault-apply), inside the current portal image with no network:

    docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true --tmpfs /tmp:size=16m \\
      -v /opt/minimoi/vault:/vault:ro -v /opt/minimoi/vault-mc:/vault-mc \\
      "$(docker inspect minimoi-portal --format '{{.Image}}')" python -m core.memory_index.mc_index build \\
      --shelf /vault/current/shelf --release-from /vault/current --out /vault-mc/index/memory-mc.sqlite \\
      --private /vault-mc/career_filter.private.json --require-private
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from core.memory_index import career_filter, indexer, store
from core.memory_shelf import fsio

OK, NOOP = "ok", "noop"
EXIT_OK, EXIT_FAIL, EXIT_REFUSED = 0, 1, 2


def _meta(path: Path) -> dict:
    try:
        con = store.connect(path, readonly=True)
    except Exception:                                          # noqa: BLE001 - an unreadable index is simply rebuilt
        return {}
    try:
        return store.get_meta(con)
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def verify(path: Path, career) -> str | None:
    """``None`` when ``path`` is fit to publish for MC, else a fixed reason."""
    try:
        con = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return "verify_unreadable"
    try:
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            return "verify_quick_check"
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        if meta.get("keyword_only") != "1":
            return "verify_not_keyword_only"
        if indexer.check_career(meta, career):
            return "verify_profile"
    except sqlite3.Error:
        return "verify_unreadable"
    finally:
        con.close()
    return None


def build(shelf_root, out_path, career, *, release: str | None = None, now=None) -> dict:
    """Update and publish MC's index. Returns ``{"code": ok|noop|<reason>, ...counts}``."""
    shelf_root, out_path = Path(shelf_root), Path(out_path)
    now = now or (lambda: datetime.now(timezone.utc))
    fsio.ensure_dir(out_path.parent)
    with open(out_path.parent / f".{out_path.name}.build.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"code": indexer.BUSY}
        published = _meta(out_path) if out_path.is_file() else {}
        if (release and published.get("source_release") == release
                and published.get("career_filter_digest") == career.digest and not indexer.check_career(published, career)):
            return {"code": NOOP, "release": release}
        work = out_path.parent / f".{out_path.name}.work"
        work.unlink(missing_ok=True)
        try:
            if published and not indexer.check_career(published, career):
                shutil.copyfile(out_path, work)
                os.chmod(work, 0o600)
            result = indexer.update(shelf_root, None, index_path=work, keyword_only=True, career=career,
                                    min_free_bytes=None, now=now)
            if result.get("code") in (indexer.INCOMPATIBLE, indexer.CAREER_MISMATCH):
                work.unlink(missing_ok=True)                    # another schema/filter: build afresh, never patch
                result = indexer.update(shelf_root, None, index_path=work, keyword_only=True, career=career,
                                        min_free_bytes=None, now=now)
            if result.get("code") != indexer.OK:
                return {**result, "swapped": False}
            con = store.connect(work, keyword_only=True)
            try:
                store.set_meta(con, source_release=release or "", published_at=now().strftime("%Y-%m-%dT%H:%M:%SZ"))
                con.commit()
            finally:
                con.close()
            reason = verify(work, career)
            if reason:
                return {**result, "code": reason, "swapped": False}
            fd = os.open(work, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(work, out_path)
            fsio.fsync_dir(out_path.parent)
            return {**result, "swapped": True, "release": release, "career_filter": career.digest[:12],
                    "private_rules": career.has_private}
        finally:
            work.unlink(missing_ok=True)
            (out_path.parent / f"{work.name}.lock").unlink(missing_ok=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.memory_index.mc_index")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--shelf", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--rules", help="the generic rules (default: the repository's config/vault/career_filter.default.json)")
    b.add_argument("--private", help="the host's private rules file (0600, never in git)")
    b.add_argument("--require-private", action="store_true")
    b.add_argument("--release-from", help="the Vault's current link: the run reads its one target release")
    a = ap.parse_args(argv)
    try:
        career = career_filter.load(a.rules, a.private if a.private and (a.require_private or Path(a.private).exists()) else None,
                                    require_private=a.require_private)
    except career_filter.FilterError as exc:
        print(json.dumps({"code": exc.code}), flush=True)
        return EXIT_REFUSED
    shelf, release = Path(a.shelf), None
    if a.release_from:
        target = Path(os.path.realpath(a.release_from))
        release = target.name
        inside = os.path.relpath(os.path.abspath(a.shelf), os.path.abspath(a.release_from))
        if not inside.startswith(".."):
            shelf = target / inside                     # read the one resolved release, even if current moves mid-run
    out = build(shelf, a.out, career, release=release)
    print(json.dumps(out, sort_keys=True), flush=True)
    return EXIT_OK if out.get("code") in (OK, NOOP) else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
