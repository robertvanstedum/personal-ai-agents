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

Meaning search (#660): with ``--embedder-url`` the index carries keyword and meaning search, but only once the full index of the
same release is a meaning index made with the pinned embedder. That full index is the **donor**: it lends the vectors of chunk text
it already embedded, so MC's index needs no backfill of its own. The career filter runs first (``_Run.record``): the donor is asked
only for the kept chunks' text hashes and only the kept chunks' missing text reaches the embedder, so MC's index holds no career
vector and no career turn is embedded on MC's behalf. With ``--release-from`` the donor is ``<that one resolved release>/index/
memory.sqlite``; it is never named separately, so the shelf and the donor are always the same release. While the full index is
still keyword-only, MC's index stays keyword-only (``meaning: meaning_waits_for_full_index``). A change of kind is a fresh build.

Production (root cron on EC2, every 15 min, offset from vault-apply), inside the current portal image with no network (with the
meaning embedder: ``--network minimoi-vault-embed`` instead of ``none``, and ``--embedder-url http://vault-embedder:11434``):

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
from core.memory_index.embedder import PIN_DIGEST_FULL
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


def _kind(meta: dict) -> str | None:
    if meta.get("keyword_only") == "1":
        return "keyword"
    return "meaning" if meta.get("embedder_digest") else None


def verify(path: Path, career, kind: str = "keyword") -> str | None:
    """``None`` when ``path`` is fit to publish for MC, else a fixed reason. ``kind``: what was built (``keyword`` | ``meaning``)."""
    try:
        con = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return "verify_unreadable"
    try:
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            return "verify_quick_check"
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        if kind == "meaning":
            if (_kind(meta) != "meaning" or meta.get("embedder_digest") != PIN_DIGEST_FULL or meta.get("dims") != "768"):
                return "verify_embedder"
            chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
            if con.execute("SELECT count(*) FROM vec_rowids").fetchone()[0] != chunks:     # plain shadow table: no extension needed
                return "verify_vector_rows"
        elif meta.get("keyword_only") != "1":
            return "verify_not_keyword_only"
        if indexer.check_career(meta, career):
            return "verify_profile"
    except sqlite3.Error:
        return "verify_unreadable"
    finally:
        con.close()
    return None


def build(shelf_root, out_path, career, *, release: str | None = None, now=None, embedder=None, donor_path=None) -> dict:
    """Update and publish MC's index. Returns ``{"code": ok|noop|<reason>, ...counts}``. ``embedder`` + ``donor_path`` (#660): a meaning
    index when the donor (the same release's full index) is a meaning index with the pinned digest; else keyword-only as before."""
    shelf_root, out_path = Path(shelf_root), Path(out_path)
    donor_meta = _meta(Path(donor_path)) if embedder is not None and donor_path and Path(donor_path).is_file() else {}
    meaning = _kind(donor_meta) == "meaning" and donor_meta.get("embedder_digest") == PIN_DIGEST_FULL
    kind = "meaning" if meaning else "keyword"
    note = {} if embedder is None else {"meaning": "on" if meaning else "meaning_waits_for_full_index"}
    now = now or (lambda: datetime.now(timezone.utc))
    fsio.ensure_dir(out_path.parent)
    with open(out_path.parent / f".{out_path.name}.build.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"code": indexer.BUSY}
        published = _meta(out_path) if out_path.is_file() else {}
        same_kind = _kind(published) == kind
        if (release and published.get("source_release") == release and same_kind
                and published.get("career_filter_digest") == career.digest and not indexer.check_career(published, career)):
            return {"code": NOOP, "release": release, **note}
        work = out_path.parent / f".{out_path.name}.work"
        work.unlink(missing_ok=True)
        args = dict(index_path=work, keyword_only=not meaning, career=career, min_free_bytes=None, now=now,
                    donor_path=donor_path if meaning else None)
        try:
            if published and same_kind and not indexer.check_career(published, career):
                shutil.copyfile(out_path, work)
                os.chmod(work, 0o600)
            result = indexer.update(shelf_root, embedder if meaning else None, **args)
            if result.get("code") in (indexer.INCOMPATIBLE, indexer.CAREER_MISMATCH):
                work.unlink(missing_ok=True)                    # another schema/filter: build afresh, never patch
                result = indexer.update(shelf_root, embedder if meaning else None, **args)
            if result.get("code") != indexer.OK:
                return {**result, "swapped": False, **note}
            con = store.connect(work, keyword_only=not meaning)
            try:
                store.set_meta(con, source_release=release or "", published_at=now().strftime("%Y-%m-%dT%H:%M:%SZ"))
                con.commit()
            finally:
                con.close()
            reason = verify(work, career, kind)
            if reason:
                return {**result, "code": reason, "swapped": False, **note}
            fd = os.open(work, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(work, out_path)
            fsio.fsync_dir(out_path.parent)
            return {**result, "swapped": True, "release": release, "career_filter": career.digest[:12],
                    "private_rules": career.has_private, "kind": kind, **note}
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
    b.add_argument("--embedder-url", help="#660: the pinned embedder (http://vault-embedder:11434); meaning once the full index is")
    b.add_argument("--donor", help="#660, without --release-from only (Dev): the full index lending its vectors")
    a = ap.parse_args(argv)
    try:
        career = career_filter.load(a.rules, a.private if a.private and (a.require_private or Path(a.private).exists()) else None,
                                    require_private=a.require_private)
    except career_filter.FilterError as exc:
        print(json.dumps({"code": exc.code}), flush=True)
        return EXIT_REFUSED
    shelf, release, donor = Path(a.shelf), None, (Path(a.donor) if a.donor else None)
    if a.release_from:
        if a.donor:
            print(json.dumps({"code": "donor_comes_from_the_release"}), flush=True)
            return EXIT_REFUSED
        target = Path(os.path.realpath(a.release_from))
        release = target.name
        inside = os.path.relpath(os.path.abspath(a.shelf), os.path.abspath(a.release_from))
        if not inside.startswith(".."):
            shelf = target / inside                     # read the one resolved release, even if current moves mid-run
        donor = target / "index" / "memory.sqlite"      # the same resolved release's full index (#660)
    embedder = None
    if a.embedder_url:
        from core.memory_index.embedder import OllamaEmbedder
        embedder = OllamaEmbedder(a.embedder_url)
    out = build(shelf, a.out, career, release=release, embedder=embedder, donor_path=donor)
    print(json.dumps(out, sort_keys=True), flush=True)
    return EXIT_OK if out.get("code") in (OK, NOOP) else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
