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

### DEC-002 — X bootstrap signals poisoning Library date range

**Date:** 2026-03-21
**Area:** Library UI / curator_server.py
**Logged by:** OpenClaw (from Robert's session summary)
**Commits:** `919c9f0`, `6986fbb`, `f5fcc96`

**Context:**
The Library date range stat ("447 articles saved · Feb 18, 2026 – Mar 21, 2026")
was rendering as "Invalid Date" or showing stray characters. The bug was subtle
and survived multiple fix attempts.

**Root cause:**
X cold-start bootstrap stored signals in `feedback_history` under a non-ISO key
format (`x_bootstrap_2026-02-28`). When `curator_server.py` built a date string
from this key, it prepended the bootstrap prefix to what should have been a
clean ISO date, producing an unparseable string like `x_bootstrap_2026-02-28T12:00:00`.
JavaScript's `new Date()` returned `Invalid Date`, breaking the stat bar.

**Decision:**
Strip non-numeric leading characters from `date_str` before fallback timestamp
construction: `re.sub(r'^[^0-9]+', '', date_str)` — ensures only the ISO date
portion is used regardless of key prefix format.

**Pattern:** Defensive date parsing at the server boundary.
Any string entering the date field should be sanitized to a clean ISO format
before being passed to the client. Do not assume feedback_history keys are
always clean ISO dates.

**Why not fix the key format instead?**
Existing bootstrap data already written with the prefixed key format. Renaming
keys would require a migration and risk breaking other logic that references
the same keys. The strip-and-sanitize approach is backward-compatible.

**Lesson:**
Bootstrap/seed data keys should always use the same format as runtime data keys.
If they differ, date parsing and sorting will silently produce wrong output.

---

### DEC-003 — Two-bot Telegram routing: content vs command channel
**Date:** 2026-03-22
**Component:** Research Intelligence Agent — Telegram delivery

**Context:**
Two Telegram bots configured:
- `rvsopenbot` (`bot_token` in Keychain) — content delivery channel
- `minimoi_cmd_bot` (`polling_bot_token` in Keychain) — OpenClaw command/conversation channel

**Decision:**
Research session summaries route via `rvsopenbot` (same channel as daily briefing).
`minimoi_cmd_bot` is reserved exclusively for OpenClaw ↔ Robert conversation.

**Config:** `agent/config.json` → `research_telegram_bot: "bot_token"`, `research_chat_id: "8379221702"`

**Why not separate research from briefing?**
Both are content — daily events and research findings are complementary, not competing.
Mixing them on one channel keeps the content flow unified. The command channel stays clean for operations.

**Pattern:** Content goes to rvsopenbot. Commands/operations go to minimoi_cmd_bot. Never cross them.
