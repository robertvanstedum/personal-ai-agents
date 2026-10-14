"""The index file: one SQLite database with FTS5 + sqlite-vec (v0.5 C1/C7).

Tables:

* ``meta``: schema, policy and chunker versions; the embedder identity the vectors were made with; build times.
* ``records``: one row per shelf record seen: where it is, which edition and file state was indexed, its tier/scope
  facts, and ``state`` = ``indexed`` | ``held`` (with a fixed reason code).
* ``chunks``: one row per chunk: ``chunk_id`` ``{record_id}@{edition_hash12}#{ordinal}``, file (relative to the shelf
  root), ``start_line``/``end_line``, ``text_hash``, embedder name and version, and the filter facts.
* ``fts``: FTS5, **contentless** (``content=''``): the keyword index keeps no column text, so nothing can be read
  back from it as an answer. Ranked with ``bm25()``.
* ``vec``: sqlite-vec ``vec0``, cosine distance on normalized vectors, with ``tier`` and ``canary`` as filter columns.

``chunks.id`` is the rowid in ``fts`` and the key in ``vec``. Journal mode stays DELETE, so the index is one file.
The file and its folder are 0600/0700: it holds no text, but its vectors and keyword postings derive from private
records.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import sqlite_vec

from core.memory_index.embedder import DIMS

SCHEMA_VERSION = 1

SCHEMA = f"""
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE records(
  record_id TEXT PRIMARY KEY, rel TEXT NOT NULL, edition INTEGER, edition_hash TEXT,
  file_size INTEGER, file_mtime_ns INTEGER, file_sha256 TEXT,
  provider TEXT, chair TEXT, kind TEXT, tier TEXT, scope TEXT, canary INTEGER, created TEXT, normalizer INTEGER,
  state TEXT NOT NULL, reason TEXT, chunks INTEGER NOT NULL DEFAULT 0, indexed_at TEXT);
CREATE TABLE chunks(
  id INTEGER PRIMARY KEY, chunk_id TEXT UNIQUE NOT NULL, record_id TEXT NOT NULL, edition_hash TEXT NOT NULL,
  ordinal INTEGER NOT NULL, turn INTEGER NOT NULL, speaker TEXT NOT NULL, rel TEXT NOT NULL,
  start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, text_hash TEXT NOT NULL,
  embedder TEXT NOT NULL, embedder_version TEXT NOT NULL,
  tier TEXT NOT NULL, scope TEXT NOT NULL, canary INTEGER NOT NULL, created TEXT, truncated INTEGER NOT NULL);
CREATE INDEX chunks_record ON chunks(record_id);
CREATE VIRTUAL TABLE fts USING fts5(text, content='', contentless_delete=1, tokenize='unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE vec USING vec0(id INTEGER PRIMARY KEY, embedding float[{DIMS}] distance_metric=cosine,
  tier TEXT, canary INTEGER);
"""


class IndexMissing(RuntimeError):
    """No index file (run ``moi rebuild``)."""


class IndexIncompatible(RuntimeError):
    """The index was made by a different schema, policy or chunker version (run ``moi rebuild``)."""


def connect(path: str | Path, *, create: bool = False, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if not create and not path.is_file():
        raise IndexMissing("no index file")
    if create:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    uri = f"file:{path}?mode={'ro' if readonly else 'rwc'}"
    con = sqlite3.connect(uri, uri=True, timeout=30)
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    if create and not con.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone():
        con.executescript(SCHEMA)
        con.execute("INSERT INTO meta VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
        con.commit()
        os.chmod(path, 0o600)
    return con


def get_meta(con: sqlite3.Connection) -> dict[str, str]:
    return dict(con.execute("SELECT key, value FROM meta").fetchall())


def set_meta(con: sqlite3.Connection, **values) -> None:
    con.executemany("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    [(k, str(v)) for k, v in values.items()])


def delete_record_chunks(con: sqlite3.Connection, record_id: str) -> int:
    ids = [r[0] for r in con.execute("SELECT id FROM chunks WHERE record_id=?", (record_id,))]
    for cid in ids:
        con.execute("DELETE FROM fts WHERE rowid=?", (cid,))
        con.execute("DELETE FROM vec WHERE id=?", (cid,))
    con.execute("DELETE FROM chunks WHERE record_id=?", (record_id,))
    return len(ids)


def old_vectors(con: sqlite3.Connection, record_id: str) -> dict[str, bytes]:
    """text_hash -> stored vector for a record's current chunks (reused when only line numbers moved)."""
    rows = con.execute("SELECT c.text_hash, v.embedding FROM chunks c JOIN vec v ON v.id = c.id WHERE c.record_id=?",
                       (record_id,)).fetchall()
    return {h: e for h, e in rows}


__all__ = ["connect", "get_meta", "set_meta", "delete_record_chunks", "old_vectors", "IndexMissing",
           "IndexIncompatible", "SCHEMA_VERSION"]
