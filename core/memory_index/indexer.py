"""Keeping the index in step with the shelf: ``update`` (embed-on-file) and ``rebuild`` (v0.5 C3/C5/C7).

``update`` is incremental and idempotent. For each shelf record it compares the file's size and mtime (then its
sha256) with what was indexed; an unchanged record costs one ``stat``. A changed record is re-chunked; vectors are
reused for chunks whose exact text was already embedded for that record (an appended event shifts every line
number but changes no text), and only new text is embedded. Records gone from the shelf lose their chunks.
Every record is its own transaction, so an interrupted run leaves a consistent index that the next run finishes.

``rebuild`` is the recovery story: wipe nothing in place; build a complete new file next to the old one from the
shelf alone (no vector is carried over), then swap it in atomically. A failed rebuild leaves the old index as it was.

Both run under the index lock (one writer), check headroom first (``disk_low``: skip, never delete to make room;
a rebuild needs room for a second copy), and check the embedder pin before embedding anything. Neither writes to
the shelf. A failed embedding never blocks filing: the record simply stays unindexed and the freshness check says so.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import sqlite_vec

from core.jobs import batch_window
from core.memory_index import chunker, policy, store
from core.memory_index.embedder import Embedder, EmbedderUnavailable, Identity, PinMismatch
from core.memory_shelf import fsio
from core.memory_shelf.shelf import Shelf, read_front_matter

INDEX_DIR = "memory-index"          # a sibling of the shelf root: the index never writes inside the shelf
LOCK_WAIT_S = 60.0
OUTSIDE_WINDOW = "outside_window"

# Result codes (fixed vocabulary).
OK, DISK_LOW, BUSY, EMBEDDER_UNAVAILABLE, PIN_MISMATCH, INCOMPATIBLE = (
    "ok", "disk_low", "index_busy", "embedder_unavailable", "pin_mismatch", "rebuild_needed")


class IndexBusy(RuntimeError):
    """Another process holds the index lock."""


def default_index_path(shelf_root: str | Path) -> Path:
    """``<shelf root>/../memory-index/<shelf folder name>.sqlite`` (staging: ``data/memory-index/memory.sqlite``): the
    same host and data folder as the records, outside the shelf itself, so the shelf's tools and backups never carry
    a cache they cannot use."""
    root = Path(shelf_root)
    return root.parent / INDEX_DIR / f"{root.name}.sqlite"


def _utc(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def index_lock(index_path: Path, wait: float | None = None):
    wait = LOCK_WAIT_S if wait is None else wait
    fsio.ensure_dir(index_path.parent)
    fd = os.open(index_path.parent / f"{index_path.name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + wait
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise IndexBusy("index is being written by another process") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def shelf_entries(shelf_root: Path) -> list[tuple[str, str, Path]]:
    """(record id, path relative to the shelf root, absolute path) for every record the shelf lists."""
    shelf = Shelf(shelf_root, min_free_bytes=None)
    out = []
    for entry in shelf.index().values():
        path = shelf.main_path(entry)
        out.append((entry["id"], str(path.relative_to(shelf_root)), path))
    return sorted(out)


def check_compatible(con, ident: Identity | None) -> str | None:
    """``None`` when this index can be updated/queried by this code and embedder, else a result code."""
    meta = store.get_meta(con)
    if meta.get("schema_version") != str(store.SCHEMA_VERSION) or \
            meta.get("policy_version", str(policy.POLICY_VERSION)) != str(policy.POLICY_VERSION) or \
            meta.get("chunker_version", str(chunker.CHUNKER_VERSION)) != str(chunker.CHUNKER_VERSION):
        return INCOMPATIBLE
    if ident is not None and meta.get("embedder_digest") not in (None, ident.digest):
        return PIN_MISMATCH
    return None


class _Run:
    def __init__(self, con, shelf_root: Path, embedder: Embedder, ident: Identity, now: Callable[[], datetime]):
        self.con, self.root, self.embedder, self.ident, self.now = con, shelf_root, embedder, ident, now
        self.counts: Counter = Counter()
        self.held: Counter = Counter()

    def _upsert(self, rid: str, rel: str, st: os.stat_result, sha: str | None, meta: dict | None, state: str,
                reason: str | None, n: int) -> None:
        m = meta or {}
        self.con.execute(
            "INSERT OR REPLACE INTO records(record_id, rel, edition, edition_hash, file_size, file_mtime_ns, file_sha256,"
            " provider, chair, kind, tier, scope, canary, created, normalizer, state, reason, chunks, indexed_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, rel, m.get("edition"), m.get("edition_hash"), st.st_size, st.st_mtime_ns, sha,
             policy.provider_of(m) if m else None, m.get("chair"), m.get("kind"),
             policy.effective_tier(m) if m else None, m.get("scope"), int(policy.is_canary(m)) if m else 0,
             m.get("created"), (m.get("normalized") or {}).get("normalizer"), state, reason, n, _utc(self.now())))

    def _hold(self, rid, rel, st, sha, meta, reason) -> None:
        store.delete_record_chunks(self.con, rid)
        self._upsert(rid, rel, st, sha, meta, "held", reason, 0)
        self.held[reason] += 1

    def record(self, rid: str, rel: str, path: Path) -> None:
        try:
            st = path.stat()
        except OSError:
            self.counts["missing_file"] += 1
            return
        row = self.con.execute("SELECT file_size, file_mtime_ns, file_sha256, state, reason FROM records WHERE record_id=?",
                               (rid,)).fetchone()
        if row and row[0] == st.st_size and row[1] == st.st_mtime_ns:
            self.counts["unchanged"] += 1
            if row[3] == "held":
                self.held[row[4]] += 1
            return
        try:
            head = read_front_matter(path)
        except (OSError, ValueError):
            head = None
        early = policy.record_gate(head) if isinstance(head, dict) else None
        if early and head.get("id") == rid:
            # Held on its front matter alone (most often awaiting_reprocess): the body is never read or parsed.
            self._hold(rid, rel, st, None, head, early)
            self.con.commit()
            return
        try:
            data = path.read_bytes()
        except OSError:
            self.counts["missing_file"] += 1
            return
        sha = hashlib.sha256(data).hexdigest()
        if row and row[2] == sha:
            self.con.execute("UPDATE records SET file_size=?, file_mtime_ns=? WHERE record_id=?",
                             (st.st_size, st.st_mtime_ns, rid))
            self.con.commit()
            self.counts["unchanged"] += 1
            if row[3] == "held":
                self.held[row[4]] += 1
            return
        try:
            meta, chunks = chunker.chunk_record(data)
        except chunker.ChunkRefused as exc:
            self._hold(rid, rel, st, sha, None, exc.code)
            self.con.commit()
            return
        if meta.get("id") != rid:
            self._hold(rid, rel, st, sha, None, policy.INVALID)
            self.con.commit()
            return
        reason = policy.record_gate(meta)
        if reason:
            self._hold(rid, rel, st, sha, meta, reason)
            self.con.commit()
            return
        reuse = store.old_vectors(self.con, rid)
        todo = [c for c in chunks if c.text_hash not in reuse]
        fresh = self.embedder.embed_documents([c.text for c in todo], chunker.EMBED_MAX_CHARS) if todo else []
        vectors = {c.text_hash: sqlite_vec.serialize_float32(v) for c, v in zip(todo, fresh)}
        self.counts["embedded_chunks"] += len(todo)
        self.counts["reused_vectors"] += len(chunks) - len(todo)
        store.delete_record_chunks(self.con, rid)
        tier, canary, edition_hash = policy.effective_tier(meta), int(policy.is_canary(meta)), meta["edition_hash"]
        for c in chunks:
            cur = self.con.execute(
                "INSERT INTO chunks(chunk_id, record_id, edition_hash, ordinal, turn, speaker, rel, start_line, end_line,"
                " text_hash, embedder, embedder_version, tier, scope, canary, created, truncated)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (c.chunk_id(rid, edition_hash), rid, edition_hash, c.ordinal, c.turn, c.speaker, rel, c.start_line,
                 c.end_line, c.text_hash, self.ident.name, self.ident.version, tier, meta["scope"], canary,
                 meta.get("created"), int(len(c.text) > chunker.EMBED_MAX_CHARS)))
            self.con.execute("INSERT INTO fts(rowid, text) VALUES(?, ?)", (cur.lastrowid, c.text))
            self.con.execute("INSERT INTO vec(id, embedding, tier, canary) VALUES(?, ?, ?, ?)",
                             (cur.lastrowid, reuse.get(c.text_hash) or vectors[c.text_hash], tier, canary))
        self._upsert(rid, rel, st, sha, meta, "indexed", None, len(chunks))
        self.con.commit()
        self.counts["reindexed" if row else "indexed"] += 1

    def drop_missing(self, present: set[str]) -> None:
        for (rid,) in self.con.execute("SELECT record_id FROM records").fetchall():
            if rid not in present:
                store.delete_record_chunks(self.con, rid)
                self.con.execute("DELETE FROM records WHERE record_id=?", (rid,))
                self.counts["removed"] += 1
        self.con.commit()

    def result(self, code: str, started: float) -> dict:
        total = self.con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"code": code, **dict(sorted(self.counts.items())), "held": dict(sorted(self.held.items())),
                "chunks_total": total, "seconds": round(time.monotonic() - started, 2)}


def _sync(con, shelf_root: Path, embedder: Embedder, ident: Identity, now, only: Iterable[str] | None,
          limit: int | None) -> dict:
    started = time.monotonic()
    run = _Run(con, shelf_root, embedder, ident, now)
    entries = shelf_entries(shelf_root)
    if only is not None:
        wanted = set(only)
        entries = [e for e in entries if e[0] in wanted]
    if limit is not None:
        entries = entries[:limit]
    try:
        for rid, rel, path in entries:
            batch_window.remaining()
            run.record(rid, rel, path)
    except batch_window.BatchDeadline as exc:
        con.commit()
        return run.result(exc.code, started)
    except EmbedderUnavailable:
        con.commit()
        return run.result(EMBEDDER_UNAVAILABLE, started)
    finally:
        embedder.release()
    try:
        batch_window.remaining()
    except batch_window.BatchDeadline as exc:
        con.commit()
        return run.result(exc.code, started)
    if only is None and limit is None:
        run.drop_missing({e[0] for e in entries})
    store.set_meta(con, updated_at=_utc(now()))
    con.commit()
    return run.result(OK, started)


def _pin(embedder: Embedder) -> tuple[Identity | None, str | None]:
    try:
        return embedder.check_pin(), None
    except PinMismatch:
        return None, PIN_MISMATCH
    except EmbedderUnavailable:
        return None, EMBEDDER_UNAVAILABLE


def update(shelf_root: str | Path, embedder: Embedder, *, index_path: str | Path | None = None,
           min_free_bytes: int | None = fsio.DEFAULT_MIN_FREE_BYTES, now: Callable[[], datetime] | None = None,
           only: Iterable[str] | None = None, limit: int | None = None) -> dict:
    """Bring the index up to date with the shelf (creating it if missing). Returns counts and a result code."""
    shelf_root = Path(shelf_root)
    index_path = Path(index_path) if index_path else default_index_path(shelf_root)
    now = now or (lambda: datetime.now(timezone.utc))
    if fsio.check_headroom(shelf_root, min_free_bytes, None):
        return {"code": DISK_LOW}
    ident, code = _pin(embedder)
    if code:
        return {"code": code}
    try:
        with index_lock(index_path):
            con = store.connect(index_path, create=True)
            try:
                code = check_compatible(con, ident)
                if code:
                    return {"code": code}
                if "embedder_digest" not in store.get_meta(con):
                    store.set_meta(con, embedder=ident.name, embedder_digest=ident.digest, dims=ident.dims,
                                   policy_version=policy.POLICY_VERSION, chunker_version=chunker.CHUNKER_VERSION,
                                   built_at=_utc(now()))
                return _sync(con, shelf_root, embedder, ident, now, only, limit)
            finally:
                con.close()
    except IndexBusy:
        return {"code": BUSY}


def rebuild(shelf_root: str | Path, embedder: Embedder, *, index_path: str | Path | None = None,
            min_free_bytes: int | None = fsio.DEFAULT_MIN_FREE_BYTES, now: Callable[[], datetime] | None = None) -> dict:
    """Build a complete new index from the shelf alone and swap it in. The old file stays until the swap."""
    shelf_root = Path(shelf_root)
    index_path = Path(index_path) if index_path else default_index_path(shelf_root)
    now = now or (lambda: datetime.now(timezone.utc))
    current = index_path.stat().st_size if index_path.is_file() else 0
    floor = (min_free_bytes or 0) + current            # room for a second copy beside the old one
    if fsio.check_headroom(shelf_root, floor if min_free_bytes is not None else None, None):
        return {"code": DISK_LOW}
    ident, code = _pin(embedder)
    if code:
        return {"code": code}
    try:
        with index_lock(index_path):
            tmp = index_path.parent / f".{index_path.name}.rebuild-{os.getpid()}"
            tmp.unlink(missing_ok=True)
            con = store.connect(tmp, create=True)
            try:
                store.set_meta(con, embedder=ident.name, embedder_digest=ident.digest, dims=ident.dims,
                               policy_version=policy.POLICY_VERSION, chunker_version=chunker.CHUNKER_VERSION,
                               built_at=_utc(now()))
                result = _sync(con, shelf_root, embedder, ident, now, None, None)
            except BaseException:
                con.close()
                tmp.unlink(missing_ok=True)
                raise
            con.close()
            if result["code"] != OK:
                tmp.unlink(missing_ok=True)                  # the old index stays as it was
                return {**result, "swapped": False}
            fd = os.open(tmp, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, index_path)
            fsio.fsync_dir(index_path.parent)
            return {**result, "swapped": True, "index_bytes": index_path.stat().st_size}
    except IndexBusy:
        return {"code": BUSY}


__all__ = ["update", "rebuild", "default_index_path", "index_lock", "check_compatible", "shelf_entries",
           "OK", "DISK_LOW", "BUSY", "EMBEDDER_UNAVAILABLE", "PIN_MISMATCH", "INCOMPATIBLE"]
