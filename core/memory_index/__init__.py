"""The derived memory index (v0.5 Component C, amendment v0.5.1 M3).

One SQLite file holds a keyword index (FTS5, contentless: no record text is stored) and a vector index
(sqlite-vec) over the **current edition** of every retrievable shelf record. The index only points: every hit is
resolved by reading the cited file from disk and checking it (``resolver``). Deleting the file loses nothing;
``moi rebuild`` makes it again from the shelf.

Modules: ``policy`` (what may be indexed), ``chunker`` (records to line-bound chunks), ``embedder`` (the pinned
local embedder), ``store`` (the schema), ``indexer`` (update and rebuild), ``resolver`` (the pointing rule),
``search`` (the one search procedure M4 calls), ``health`` (freshness, orphans, model pin, F2 sweep), ``cli``.

This package never writes to the shelf. It reads records only through ``core.memory_shelf`` read functions.
"""
