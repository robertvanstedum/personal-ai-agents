"""Thinking Store T1: portable, headless tools for the memory shelf's on-disk record (v0.6 section 1A; v0.7 Unit 5).

Reads the shelf format directly (YAML front matter records, immutable ``editions/N--sha12.jsonl`` files). It imports nothing from
the rest of MiniMoi, needs no database, no network and no model, and depends only on the standard library and PyYAML. It lists,
searches literally, opens exact editions, exports a topic or the permitted corpus with schema documentation and an offline index,
verifies an export or a shelf, and restores an export into a clean folder. It never writes into a shelf it reads.
"""
