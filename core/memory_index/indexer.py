"""Keeping the index in step with the shelf: ``update`` (embed-on-file) and ``rebuild`` (v0.5 C3/C5/C7).

``update`` is incremental and idempotent. For each shelf record it compares the file's size and mtime (then its
sha256) with what was indexed; an unchanged record costs one ``stat``. A changed record is re-chunked; vectors are
reused for chunks whose exact text was already embedded for that record (an appended event shifts every line
number but changes no text), and only new text is embedded. Records gone from the shelf lose their chunks.
Every record is its own transaction, so an interrupted run leaves a consistent index that the next run finishes.

``rebuild`` is the recovery story: wipe nothing in place; build a complete new file next to the old one from the
shelf alone (no vector is carried over), then swap it in atomically. A failed rebuild leaves the old index as it was.
``rebuild(reuse_vectors=True)`` (``moi rebuild --reuse-vectors``) is for a schema change, not for recovery: everything
is still rebuilt from the shelf, but a chunk whose exact text the old index already embedded for the same record takes
that vector instead of being embedded again, exactly as ``update`` does. Only when the old index was made with the pinned
embedder (same digest and dimensions) and the same chunker; otherwise nothing is reused and every chunk is embedded.

Both also have a **keyword-only** mode (``keyword_only=True``, production): the same chunker and policy, no embedder (pass
``None``), no vector table; chunk rows record embedder ``none``. It writes only an index made keyword-only, and never a vector one.

**Career-filtered** mode (``career=CareerFilter``, #594: Master Craftsman's index): the same procedure, but a career record is
held (``career_excluded``) and a career turn's chunks are never written (``core/memory_index/career_filter.py``). The index's
``meta`` then names ``profile=mc`` and the filter's digest and version. A filtered index is only ever updated with the same
filter, and an unfiltered one never with a filter (``career_mismatch``: rebuild): otherwise an unchanged record would keep
chunks that the other rules exclude.

Held records whose reason can heal without a new edition (``unknown_provider``: the code learned the provider;
``awaiting_reprocess``: the retrieval floor moved) are re-checked on every visit instead of being skipped as unchanged.

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

try:
    import sqlite_vec
except ImportError:                         # building an index needs it; reading one for keyword search does not
    sqlite_vec = None

from core.jobs import batch_window
from core.memory_index import career_filter, chunker, policy, store
from core.memory_index.embedder import Embedder, EmbedderUnavailable, Identity, PinMismatch
from core.memory_shelf import fsio
from core.memory_shelf.shelf import Shelf, read_front_matter

INDEX_DIR = "memory-index"          # a sibling of the shelf root: the index never writes inside the shelf
LOCK_WAIT_S = 60.0
OUTSIDE_WINDOW = "outside_window"

# Result codes (fixed vocabulary).
OK, DISK_LOW, BUSY, EMBEDDER_UNAVAILABLE, PIN_MISMATCH, INCOMPATIBLE = (
    "ok", "disk_low", "index_busy", "embedder_unavailable", "pin_mismatch", "rebuild_needed")
CAREER_MISMATCH = "career_mismatch"        # filtered vs unfiltered, or another filter digest: rebuild, never patch
BUDGET_SPENT = "budget_spent"              # ``update(budget_s=)`` stopped between records: committed, the next run resumes (#660)
# Held reasons that a code change (not a new record edition) can lift: never skipped as "unchanged".
RECHECK_HELD = frozenset({policy.UNKNOWN_PROVIDER, policy.AWAITING_REPROCESS})


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


def career_meta(career) -> dict:
    """The ``meta`` keys that name a filtered index (empty for an unfiltered one)."""
    if career is None:
        return {}
    return {"profile": career_filter.PROFILE, "career_filter_digest": career.digest,
            "career_filter_version": career_filter.FILTER_VERSION}


def check_career(meta: dict, career) -> str | None:
    """``None`` when an existing index was built with exactly this filter (or none, for none), else ``career_mismatch``."""
    want = career_meta(career)
    have = {k: meta.get(k) for k in ("profile", "career_filter_digest", "career_filter_version")}
    if {k: (str(v) if v is not None else None) for k, v in want.items()} != {k: have[k] for k in want} or \
            (career is None and any(have.values())):
        return CAREER_MISMATCH
    return None


KEYWORD_ONLY_EMBEDDER = ("none", "0")      # (name, version) on chunk rows written without vectors


class _Run:
    def __init__(self, con, shelf_root: Path, embedder: Embedder | None, ident: Identity | None, now: Callable[[], datetime],
                 keyword_only: bool = False, donor=None, career=None):
        self.con, self.root, self.embedder, self.ident, self.now = con, shelf_root, embedder, ident, now
        self.career = career            # career_filter.CareerFilter, or None (the full index)
        self.keyword_only = keyword_only
        self.donor = donor              # a previous index whose vectors may be reused (``rebuild(reuse_vectors=True)``)
        self.counts: Counter = Counter()
        self.held: Counter = Counter()

    def _upsert(self, rid: str, rel: str, st: os.stat_result, sha: str | None, meta: dict | None, state: str,
                reason: str | None, n: int, activity: tuple[str | None, str | None, int] = (None, None, 0)) -> None:
        m = meta or {}
        self.con.execute(
            "INSERT OR REPLACE INTO records(record_id, rel, edition, edition_hash, file_size, file_mtime_ns, file_sha256,"
            " provider, chair, kind, tier, scope, canary, created, normalizer, state, reason, chunks, indexed_at,"
            " first_activity, last_activity, timed_turns)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, rel, m.get("edition"), m.get("edition_hash"), st.st_size, st.st_mtime_ns, sha,
             policy.provider_of(m) if m else None, m.get("chair"), m.get("kind"),
             policy.effective_tier(m) if m else None, m.get("scope"), int(policy.is_canary(m)) if m else 0,
             m.get("created"), (m.get("normalized") or {}).get("normalizer"), state, reason, n, _utc(self.now()),
             *activity))

    def _hold(self, rid, rel, st, sha, meta, reason) -> None:
        store.delete_record_chunks(self.con, rid, vectors=not self.keyword_only)
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
        recheck = bool(row and row[3] == "held" and row[4] in RECHECK_HELD)
        if row and row[0] == st.st_size and row[1] == st.st_mtime_ns and not recheck:
            self.counts["unchanged"] += 1
            if row[3] == "held":
                self.held[row[4]] += 1
            return
        try:
            head = read_front_matter(path)
        except (OSError, ValueError):
            head = None
        early = policy.record_gate(head) if isinstance(head, dict) else None
        if recheck and early == row[4] and row[0] == st.st_size and row[1] == st.st_mtime_ns:
            self.counts["unchanged"] += 1                   # re-checked, still held for the same reason: nothing to write
            self.held[early] += 1
            return
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
        if row and row[2] == sha and not recheck:
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
        if self.career is not None:
            reason, chunks, dropped = self.career.apply(meta, chunks)
            if reason:
                self.counts["career_turns_excluded"] += dropped
                self._hold(rid, rel, st, sha, meta, reason)
                self.con.commit()
                return
            self.counts["career_turns_excluded"] += dropped
        if self.keyword_only:
            reuse, vectors = {}, {}
            embedder_name, embedder_version = KEYWORD_ONLY_EMBEDDER
        else:
            # The donor lends what this index lacks; this index's own vectors win (#660: MC's index borrows the full index's).
            kept = {c.text_hash for c in chunks}
            reuse = {**(store.old_vectors(self.donor, rid, kept) if self.donor is not None else {}),
                     **store.old_vectors(self.con, rid)}
            todo = [c for c in chunks if c.text_hash not in reuse]
            fresh = self.embedder.embed_documents([c.text for c in todo], chunker.EMBED_MAX_CHARS) if todo else []
            vectors = {c.text_hash: sqlite_vec.serialize_float32(v) for c, v in zip(todo, fresh)}
            self.counts["embedded_chunks"] += len(todo)
            self.counts["reused_vectors"] += len(chunks) - len(todo)
            embedder_name, embedder_version = self.ident.name, self.ident.version
        store.delete_record_chunks(self.con, rid, vectors=not self.keyword_only)
        tier, canary, edition_hash = policy.effective_tier(meta), int(policy.is_canary(meta)), meta["edition_hash"]
        times = [c.at or meta.get("created") for c in chunks]
        known = [t for t in times if t]
        activity = (min(known) if known else meta.get("created"), max(known) if known else meta.get("created"),
                    len({c.turn for c in chunks if c.at}))
        for c, created in zip(chunks, times):
            cur = self.con.execute(
                "INSERT INTO chunks(chunk_id, record_id, edition_hash, ordinal, turn, speaker, rel, start_line, end_line,"
                " text_hash, embedder, embedder_version, tier, scope, canary, created, truncated)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (c.chunk_id(rid, edition_hash), rid, edition_hash, c.ordinal, c.turn, c.speaker, rel, c.start_line,
                 c.end_line, c.text_hash, embedder_name, embedder_version, tier, meta["scope"], canary,
                 created, int(len(c.text) > chunker.EMBED_MAX_CHARS)))
            self.con.execute("INSERT INTO fts(rowid, text) VALUES(?, ?)", (cur.lastrowid, c.text))
            if not self.keyword_only:
                self.con.execute("INSERT INTO vec(id, embedding, tier, canary) VALUES(?, ?, ?, ?)",
                                 (cur.lastrowid, reuse.get(c.text_hash) or vectors[c.text_hash], tier, canary))
        self._upsert(rid, rel, st, sha, meta, "indexed", None, len(chunks), activity)
        self.con.commit()
        self.counts["reindexed" if row else "indexed"] += 1

    def drop_missing(self, present: set[str]) -> None:
        for (rid,) in self.con.execute("SELECT record_id FROM records").fetchall():
            if rid not in present:
                store.delete_record_chunks(self.con, rid, vectors=not self.keyword_only)
                self.con.execute("DELETE FROM records WHERE record_id=?", (rid,))
                self.counts["removed"] += 1
        self.con.commit()

    def result(self, code: str, started: float) -> dict:
        total = self.con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"code": code, **dict(sorted(self.counts.items())), "held": dict(sorted(self.held.items())),
                "chunks_total": total, "seconds": round(time.monotonic() - started, 2)}


def _sync(con, shelf_root: Path, embedder: Embedder | None, ident: Identity | None, now, only: Iterable[str] | None,
          limit: int | None, keyword_only: bool = False, donor=None, career=None, budget_s: float | None = None) -> dict:
    started = time.monotonic()
    stop_at = None if budget_s is None else started + max(0.0, float(budget_s))
    run = _Run(con, shelf_root, embedder, ident, now, keyword_only, donor, career)
    entries = shelf_entries(shelf_root)
    if only is not None:
        wanted = set(only)
        entries = [e for e in entries if e[0] in wanted]
    if limit is not None:
        entries = entries[:limit]
    try:
        for rid, rel, path in entries:
            batch_window.remaining()
            if stop_at is not None and time.monotonic() >= stop_at:
                con.commit()
                return run.result(BUDGET_SPENT, started)
            run.record(rid, rel, path)
    except batch_window.BatchDeadline as exc:
        con.commit()
        return run.result(exc.code, started)
    except EmbedderUnavailable:
        con.commit()
        return run.result(EMBEDDER_UNAVAILABLE, started)
    finally:
        if embedder is not None:
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


def _keyword_meta(con, now, career=None) -> None:
    store.set_meta(con, embedder=KEYWORD_ONLY_EMBEDDER[0], policy_version=policy.POLICY_VERSION,
                   chunker_version=chunker.CHUNKER_VERSION, built_at=_utc(now()), **career_meta(career))


def update(shelf_root: str | Path, embedder: Embedder | None, *, index_path: str | Path | None = None,
           min_free_bytes: int | None = fsio.DEFAULT_MIN_FREE_BYTES, now: Callable[[], datetime] | None = None,
           only: Iterable[str] | None = None, limit: int | None = None, keyword_only: bool = False, career=None,
           budget_s: float | None = None, donor_path: str | Path | None = None) -> dict:
    """Bring the index up to date with the shelf (creating it if missing). Returns counts and a result code.
    ``keyword_only``: no embedder (``None``), no vectors; the index must be (or is created) keyword-only.
    ``career``: a ``career_filter.CareerFilter``; the index must be (or is created) filtered with exactly it.
    ``budget_s``: stop between records once this many seconds are spent (``budget_spent``; the next run resumes).
    ``donor_path``: another vector index, opened read-only, that lends the vectors of chunk text it already embedded for the same
    record, only when made with this embedder and chunker (MC's index borrows the full index's: #660)."""
    shelf_root = Path(shelf_root)
    index_path = Path(index_path) if index_path else default_index_path(shelf_root)
    now = now or (lambda: datetime.now(timezone.utc))
    if fsio.check_headroom(shelf_root, min_free_bytes, None):
        return {"code": DISK_LOW}
    ident = None
    if not keyword_only:
        ident, code = _pin(embedder)
        if code:
            return {"code": code}
    try:
        with index_lock(index_path):
            try:
                con = store.connect(index_path, create=True, keyword_only=keyword_only)
            except store.IndexIncompatible:
                return {"code": INCOMPATIBLE}
            try:
                code = check_compatible(con, ident)
                if code:
                    return {"code": code}
                meta = store.get_meta(con)
                fresh = "built_at" not in meta if keyword_only else "embedder_digest" not in meta
                if not fresh and check_career(meta, career):
                    return {"code": CAREER_MISMATCH}
                if keyword_only and fresh:
                    _keyword_meta(con, now, career)
                elif fresh:
                    store.set_meta(con, embedder=ident.name, embedder_digest=ident.digest, dims=ident.dims,
                                   policy_version=policy.POLICY_VERSION, chunker_version=chunker.CHUNKER_VERSION,
                                   built_at=_utc(now()), **career_meta(career))
                donor = _donor(Path(donor_path), ident) if donor_path is not None and not keyword_only else None
                try:
                    return _sync(con, shelf_root, embedder, ident, now, only, limit, keyword_only, donor, career, budget_s)
                finally:
                    if donor is not None:
                        donor.close()
            finally:
                con.close()
    except IndexBusy:
        return {"code": BUSY}


def _donor(index_path: Path, ident: Identity):
    """The old index, read-only, when its vectors may be reused by this embedder and chunker; else None."""
    if not index_path.is_file():
        return None
    try:
        con = store.connect(index_path, readonly=True)
    except Exception:                                   # noqa: BLE001 - an unreadable old index is simply not a donor
        return None
    try:
        meta = store.get_meta(con)
        if (meta.get("embedder_digest") == ident.digest and meta.get("dims") == str(ident.dims)
                and meta.get("chunker_version") == str(chunker.CHUNKER_VERSION)):
            return con
    except Exception:                                   # noqa: BLE001
        pass
    con.close()
    return None


def rebuild(shelf_root: str | Path, embedder: Embedder | None, *, index_path: str | Path | None = None,
            min_free_bytes: int | None = fsio.DEFAULT_MIN_FREE_BYTES, now: Callable[[], datetime] | None = None,
            keyword_only: bool = False, reuse_vectors: bool = False, career=None) -> dict:
    """Build a complete new index from the shelf alone and swap it in. The old file stays until the swap. With
    ``reuse_vectors`` (a vector index only) the old index lends the vectors of unchanged chunk text (see the module note)."""
    shelf_root = Path(shelf_root)
    index_path = Path(index_path) if index_path else default_index_path(shelf_root)
    now = now or (lambda: datetime.now(timezone.utc))
    current = index_path.stat().st_size if index_path.is_file() else 0
    floor = (min_free_bytes or 0) + current            # room for a second copy beside the old one
    if fsio.check_headroom(shelf_root, floor if min_free_bytes is not None else None, None):
        return {"code": DISK_LOW}
    ident = None
    if not keyword_only:
        ident, code = _pin(embedder)
        if code:
            return {"code": code}
    try:
        with index_lock(index_path):
            tmp = index_path.parent / f".{index_path.name}.rebuild-{os.getpid()}"
            tmp.unlink(missing_ok=True)
            con = store.connect(tmp, create=True, keyword_only=keyword_only)
            donor = None
            try:
                if keyword_only:
                    _keyword_meta(con, now, career)
                else:
                    donor = _donor(index_path, ident) if reuse_vectors else None
                    store.set_meta(con, embedder=ident.name, embedder_digest=ident.digest, dims=ident.dims,
                                   policy_version=policy.POLICY_VERSION, chunker_version=chunker.CHUNKER_VERSION,
                                   built_at=_utc(now()), **career_meta(career))
                result = _sync(con, shelf_root, embedder, ident, now, None, None, keyword_only, donor, career)
                if reuse_vectors and not keyword_only:
                    result["vectors_from_old_index"] = donor is not None
            except BaseException:
                con.close()
                tmp.unlink(missing_ok=True)
                raise
            finally:
                if donor is not None:
                    donor.close()
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


__all__ = ["update", "rebuild", "default_index_path", "index_lock", "check_compatible", "check_career", "career_meta",
           "shelf_entries", "OK", "DISK_LOW", "BUSY", "EMBEDDER_UNAVAILABLE", "PIN_MISMATCH", "INCOMPATIBLE", "CAREER_MISMATCH",
           "BUDGET_SPENT",
           "RECHECK_HELD"]
