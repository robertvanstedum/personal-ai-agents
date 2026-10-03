"""The memory shelf's record contract (Memory and retrieval v0.5 §B, amendment v0.5.1 R2/R3).

Pure library, no network and no model. The shelf's files are the record; this
package only defines how a record is named, written, annotated (append-only
events), weighed at read time, and kept in immutable editions.

M2 additions: ``shelf`` (writer, editions, dedup), ``watchers`` (Claude Code / Codex), ``inbox`` + ``canary``,
``backfill`` (dry-run listing only), ``fidelity`` (F2 sample), ``ledger`` (F1), ``review``, ``approvals`` (the only
code that builds approvals) and ``cli`` (``scripts/moi``). Nothing here installs or schedules anything.
"""
