# DECISIONS.md
**Mini-moi Personal AI Curator**
**Maintained by:** OpenClaw (Memory Agent) + Claude Code (Implementation Agent)
**Robert confirms decisions before they are logged here.**

---

## Purpose

This file records architectural and design decisions that are non-obvious,
deliberate, and worth explaining to a future agent or collaborator who asks
*"why was it done this way?"*

Not every implementation choice belongs here — only decisions where the
rationale would not be obvious from reading the code alone, or where a
reasonable alternative was considered and rejected.

---

## When to Log a Decision

Log here when:
- A non-obvious architectural pattern was chosen (and there was a real alternative)
- A tradeoff was made explicitly (cost, complexity, correctness, speed)
- A "fix" is intentional behavior — to prevent a future agent from "correcting" it
- A known limitation is accepted and the reason matters

Do not log here:
- Routine implementation details
- Decisions already explained fully in a BUILD doc
- Things obvious from reading the code

---

## Decision Log

---

### DEC-001 — Filesystem reconciliation for deep dive URL resolution

**Date:** 2026-03-20
**Area:** Library UI / curator_server.py
**Logged by:** Claude Code + Robert

**Context:**
The Library view needed to show a "View Dive" button for articles that already
had a deep dive on disk. The original implementation checked only
`curator_history.json` to determine whether a dive existed. This caused a
hidden gap: dives written to `interests/2026/deep-dives/` were not always
recorded in history, so the button showed "Start Dive" even when the file
existed on disk.

**Decision:**
`/api/library` now performs a secondary scan of the `deep-dives/` directory
on every request. It builds a `hash_id → URL` map from filenames and uses
this to resolve `deep_dive_url` for any article whose dive was missed by
history. History is checked first; the filesystem scan fills the gaps.

**Pattern:** Filesystem reconciliation / read-repair.
The filesystem is the authoritative source. The history JSON is a derived
index. When they diverge, the filesystem wins.

**Why not fix history instead?**
Writing back to history on every library load would have side effects and
is harder to make idempotent. Read-repair on the API response is stateless,
safe to run repeatedly, and requires no migration.

**Accepted tradeoff:**
Directory scan on every `/api/library` request adds minor I/O. Acceptable
at current scale (tens of files). Revisit if the deep-dives directory grows
into hundreds of files.

---
