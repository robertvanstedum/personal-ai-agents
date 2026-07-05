# Investigation Brief: German/Portuguese Domain Parity Analysis
**File:** `docs/design/investigation_german_portuguese_parity_2026-07-04.md`
**Status:** Active investigation — report only, no code changes
**Date:** 2026-07-04
**For:** Claude Code
**Output feeds:** `spec_german_portuguese_parity_2026-07-04.md` (to be written by Claude.ai after findings)

---

## Intent

German and Portuguese were built at different times with different patterns. Unexpected behavior over the past week (guests.json risk, stale build queue, plaintext credentials, curator not under launchd) traces partly to this inconsistency. Before building the parity spec, we need a structured comparison of exactly where the two domains differ.

**This is a report-only task. No code changes. No commits.**

---

## Investigation Areas

### 1. Storage layer
For each domain, document:
- What data is stored in JSON files (path, what it contains, how it's written/read)
- What data is stored in Postgres (table name, schema, what it contains)
- What data is stored nowhere (in-memory only, lost on restart)

Expected finding: German uses JSON for everything; Portuguese uses Postgres for conversations and writing sessions.

### 2. HTTP API endpoints
For each domain server, list every Flask route:
- Method (GET/POST)
- Path
- Auth required? (owner / any logged-in user / guest / public)
- What it does
- Whether an equivalent exists in the other domain

### 3. Telegram bot integration
For each domain:
- What logic lives in `telegram_system_bot.py` vs the domain server
- What data does the bot read/write directly vs via HTTP call
- Whether the bot shares a data volume with the domain container

### 4. Process management
For each domain on both Mac (dev) and EC2 (prod):
- What process manager runs the domain server (launchd / Docker / manual)
- What happens if the process dies (auto-restart? manual restart needed?)
- What the launchd plist or Docker service name is

### 5. Wörter vs Palavras feature parity
Compare side by side:
- Vocabulary list display
- Filters (Origem, Status)
- Manual add
- Anki export
- Treino mode
- Any features present in one but not the other

### 6. Per-user data isolation
For each domain:
- How is the current user identified in requests (session, header, other)
- How is data scoped to the user (filename convention, DB query, other)
- Whether guest users are properly isolated from owner data

### 7. Tips system implementation
For each domain server:
- Is `tips.get('slot.key')` implemented?
- Which slot keys are wired up vs missing?
- Is the tips.json path correct and consistent?

### 8. Archiv vs Arquivo
Compare side by side:
- What sessions are recorded (conversations, writing, reading)
- Data source (JSON files, Postgres, other)
- Display structure (tabs, filters, expandable rows)
- Any features present in one but not the other

---

## Output Format

Produce a structured markdown report: `docs/design/parity_findings_2026-07-04.md`

For each area above:
- One comparison table: German column vs Portuguese column
- Gap summary: what German has that Portuguese lacks, and vice versa
- Severity: High (data risk or broken feature) / Medium (inconsistency) / Low (cosmetic)

Do not propose fixes in this report. Just document what exists.

---

## After Investigation

Claude.ai will use this report to write `spec_german_portuguese_parity_2026-07-04.md` covering:
- Storage migration (German JSON → Postgres, matching Portuguese schema)
- Bot/API refactor (system-bot → HTTP endpoints, closing GitHub #54)
- Process management standardization
- Feature parity gaps
- Tips system completion

That spec will be status: `spec_ready` — targeted for build this week.

---

*Investigation brief · 2026-07-04 · Claude.ai → Claude Code*
*Report only — no code changes until spec is written and approved*
