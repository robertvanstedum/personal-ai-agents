"""The index file: one SQLite database with FTS5 + sqlite-vec (v0.5 C1/C7).

Tables:

* ``meta``: schema, policy and chunker versions; the embedder identity the vectors were made with; build times.
* ``records``: one row per shelf record seen: where it is, which edition and file state was indexed, its tier/scope
  facts, and ``state`` = ``indexed`` | ``held`` (with a fixed reason code). For an indexed record, ``first_activity`` /
  ``last_activity`` are the earliest and latest times of its indexed turns and ``timed_turns`` how many of those turns
  carry their own time (0: every time is the record's start date).
* ``chunks``: one row per chunk: ``chunk_id`` ``{record_id}@{edition_hash12}#{ordinal}``, file (relative to the shelf
  root), ``start_line``/``end_line``, ``text_hash``, embedder name and version, and the filter facts. ``created`` is the
  chunk's turn time when the turn has one, else the record's created time.
* ``fts``: FTS5, **contentless** (``content=''``): the keyword index keeps no column text, so nothing can be read
  back from it as an answer. Ranked with ``bm25()``.
* ``vec``: sqlite-vec ``vec0``, cosine distance on normalized vectors, with ``tier`` and ``canary`` as filter columns.
* ``pending_vectors`` (vector indexes, #660): vectors embedded for a record that is not written yet, one row per (record, chunk
  text), saved batch by batch. A run stopped inside a big record (its time budget, the batch window, the embedder) resumes from
  them instead of embedding the record again; the record's write clears its rows. Created on first write, so an index made
  before it needs no rebuild; readers never look at it.

**Keyword-only variant** (production, 17 Oct 2026): the same tables without ``vec``, and ``meta.keyword_only = 1``. Built and
updated by ``indexer.update/rebuild(..., keyword_only=True)`` with the same chunker and no embedder, so it needs no sqlite-vec.
A keyword-only index is never opened for vector writes, and a vector index is never written as keyword-only.

``chunks.id`` is the rowid in ``fts`` and the key in ``vec``. Journal mode stays DELETE, so the index is one file.
The file and its folder are 0600/0700: it holds no text, but its vectors and keyword postings derive from private
records.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

try:                        # the vector extension; absent in production images, where only keyword search is served
    import sqlite_vec
except ImportError:
    sqlite_vec = None

from core.memory_index.embedder import DIMS

SCHEMA_VERSION = 2                  # 2 (17 Oct 2026): chunk created = turn time; records first/last_activity, timed_turns

KEYWORD_SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE records(
  record_id TEXT PRIMARY KEY, rel TEXT NOT NULL, edition INTEGER, edition_hash TEXT,
  file_size INTEGER, file_mtime_ns INTEGER, file_sha256 TEXT,
  provider TEXT, chair TEXT, kind TEXT, tier TEXT, scope TEXT, canary INTEGER, created TEXT, normalizer INTEGER,
  state TEXT NOT NULL, reason TEXT, chunks INTEGER NOT NULL DEFAULT 0, indexed_at TEXT,
  first_activity TEXT, last_activity TEXT, timed_turns INTEGER NOT NULL DEFAULT 0);
CREATE TABLE chunks(
  id INTEGER PRIMARY KEY, chunk_id TEXT UNIQUE NOT NULL, record_id TEXT NOT NULL, edition_hash TEXT NOT NULL,
  ordinal INTEGER NOT NULL, turn INTEGER NOT NULL, speaker TEXT NOT NULL, rel TEXT NOT NULL,
  start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, text_hash TEXT NOT NULL,
  embedder TEXT NOT NULL, embedder_version TEXT NOT NULL,
  tier TEXT NOT NULL, scope TEXT NOT NULL, canary INTEGER NOT NULL, created TEXT, truncated INTEGER NOT NULL);
CREATE INDEX chunks_record ON chunks(record_id);
CREATE VIRTUAL TABLE fts USING fts5(text, content='', contentless_delete=1, tokenize='unicode61 remove_diacritics 2');
"""
VEC_SCHEMA = f"""
CREATE VIRTUAL TABLE vec USING vec0(id INTEGER PRIMARY KEY, embedding float[{DIMS}] distance_metric=cosine,
  tier TEXT, canary INTEGER);
"""
PENDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_vectors(record_id TEXT NOT NULL, text_hash TEXT NOT NULL, embedding BLOB NOT NULL,
  PRIMARY KEY(record_id, text_hash));
"""
SCHEMA = KEYWORD_SCHEMA + VEC_SCHEMA + PENDING_SCHEMA


class IndexMissing(RuntimeError):
    """No index file (run ``moi rebuild``)."""


class IndexIncompatible(RuntimeError):
    """The index was made by a different schema, policy or chunker version (run ``moi rebuild``)."""


def connect(path: str | Path, *, create: bool = False, readonly: bool = False, keyword_only: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if not create and not path.is_file():
        raise IndexMissing("no index file")
    if create:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if sqlite_vec is None and not readonly and not keyword_only:
        raise IndexIncompatible("vector extension unavailable: an index can only be opened read-only here")
    # Encode path punctuation as a filename, not SQLite URI query/fragment syntax.
    # Only an explicit create may recreate a file that vanished after the check.
    mode = "ro" if readonly else "rwc" if create else "rw"
    uri = f"{path.absolute().as_uri()}?mode={mode}"
    con = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        if sqlite_vec is not None and not keyword_only:
            con.enable_load_extension(True)
            sqlite_vec.load(con)
            con.enable_load_extension(False)
        if create and not con.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone():
            con.executescript(KEYWORD_SCHEMA if keyword_only else SCHEMA)
            con.execute("INSERT INTO meta VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
            if keyword_only:
                con.execute("INSERT INTO meta VALUES('keyword_only', '1')")
            con.commit()
            os.chmod(path, 0o600)
        if not readonly and is_keyword_only(con) != keyword_only:
            raise IndexIncompatible("keyword-only and vector indexes are never written as each other")
        if not readonly and not keyword_only:
            con.executescript(PENDING_SCHEMA)               # an index made before #660's resume gains the table on its next write
    except BaseException:
        con.close()
        raise
    return con


def is_keyword_only(con: sqlite3.Connection) -> bool:
    try:
        row = con.execute("SELECT value FROM meta WHERE key='keyword_only'").fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(row and row[0] == "1")


def get_meta(con: sqlite3.Connection) -> dict[str, str]:
    return dict(con.execute("SELECT key, value FROM meta").fetchall())


def set_meta(con: sqlite3.Connection, **values) -> None:
    con.executemany("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    [(k, str(v)) for k, v in values.items()])


def delete_record_chunks(con: sqlite3.Connection, record_id: str, *, vectors: bool = True) -> int:
    ids = [r[0] for r in con.execute("SELECT id FROM chunks WHERE record_id=?", (record_id,))]
    for cid in ids:
        con.execute("DELETE FROM fts WHERE rowid=?", (cid,))
        if vectors:
            con.execute("DELETE FROM vec WHERE id=?", (cid,))
    con.execute("DELETE FROM chunks WHERE record_id=?", (record_id,))
    return len(ids)


def old_vectors(con: sqlite3.Connection, record_id: str, hashes=None) -> dict[str, bytes]:
    """text_hash -> stored vector for a record's current chunks (reused when only line numbers moved). ``hashes``: only these
    texts (a donor lends MC's index the vectors of the chunks the career filter kept, never the others: #660)."""
    rows = con.execute("SELECT c.text_hash, v.embedding FROM chunks c JOIN vec v ON v.id = c.id WHERE c.record_id=?",
                       (record_id,)).fetchall()
    return {h: e for h, e in rows if hashes is None or h in hashes}


def pending_vectors(con: sqlite3.Connection, record_id: str) -> dict[str, bytes]:
    """text_hash -> vector already embedded for this record by an earlier, interrupted run (empty when none)."""
    return dict(con.execute("SELECT text_hash, embedding FROM pending_vectors WHERE record_id=?", (record_id,)).fetchall())


def save_pending(con: sqlite3.Connection, record_id: str, vectors: dict[str, bytes]) -> None:
    """Keep one embedded batch of a record that is not written yet (the caller commits)."""
    con.executemany("INSERT OR REPLACE INTO pending_vectors(record_id, text_hash, embedding) VALUES(?, ?, ?)",
                    [(record_id, h, v) for h, v in vectors.items()])


def clear_pending(con: sqlite3.Connection, record_id: str | None = None) -> None:
    """Drop a record's pending vectors (it was written or held), or all of them (``None``: a run that covered the whole shelf)."""
    if record_id is None:
        con.execute("DELETE FROM pending_vectors")
    else:
        con.execute("DELETE FROM pending_vectors WHERE record_id=?", (record_id,))


__all__ = ["connect", "pending_vectors", "save_pending", "clear_pending", "is_keyword_only", "get_meta", "set_meta", "delete_record_chunks", "old_vectors", "IndexMissing",
           "IndexIncompatible", "SCHEMA_VERSION"]
