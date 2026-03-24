# Spec: Phase 1 Agent Runner — agent/run.py
**Author:** OpenClaw (Mini-moi) — Product Owner
**Date:** March 21, 2026
**For:** Claude Code (implementation)
**Status:** APPROVED BY ROBERT — ready to build
**Parent spec:** docs/specs/phase-zero-spec-2026-03-20.md

---

## Context

Phase Zero is complete. Directory scaffold exists, local git repo initialized,
`web/generate.js` written and working. This is Phase 1, Part 1: the agent runner.

Read WAY_OF_WORKING.md before doing anything. It is binding.

This script is infrastructure only. It does not contain research logic. It is the
gate OpenClaw passes through before starting any research session. All budget
enforcement is technical — exit codes, not behavior.

---

## What it is

`agent/run.py` — called by OpenClaw at session start and session end.

Two call signatures:

**Start:**
```
python agent/run.py start \
  --session-name kotkin-thread \
  [--estimated-cost 0.15]
```

**End:**
```
python agent/run.py end \
  --cost 0.12 \
  --duration "4min" \
  --notes "Kotkin citation pass — 3 sources validated"
```

OpenClaw checks exit code after `start`. If non-zero, it does not proceed.
`end` is called after research completes regardless of exit code — it closes the
RUNNING row.

---

## What it reads

On every `start` call:

1. `library/session-log.md` — parses the markdown table to extract:
   - **Cumulative total** — last numeric value in the Cumulative column
   - **Today's total** — sum of Cost column where Date = today (YYYY-MM-DD)
   - **Weekly total** — sum of Cost column where Date is within last 7 calendar days
   - **RUNNING rows** — any row with RUNNING in the Duration column (crash detection)

2. `agent/config.json` — reads:
   - `chat_id` — Telegram chat ID for Research Intel messages (not a secret, not in keyring)
   - Any future agent-level config goes here

3. macOS Keychain via `keyring`:
   - Service: `telegram`, Account: `polling_bot_token` — the OpenClaw gateway bot token
   - Research Intel messages are agent output, not Curator briefings — they belong on
     the OpenClaw channel. Curator's bot (`bot_token`) stays clean for briefings only.

4. Command-line args:
   - `--session-name` (required for `start`) — e.g. `burst`, `kotkin-thread`
   - `--estimated-cost` (optional for `start`) — OpenClaw's pre-session estimate

---

## Budget enforcement logic

Checked on every `start` call, in this order:

### Hard stops (exit code 1)

| Condition | Message |
|-----------|---------|
| Cumulative ≥ $20.00 | "Pilot budget exhausted. ($X.XX / $20.00). Stopping all activity." |
| Today's total ≥ $3.00 | "Daily limit reached. ($X.XX today). Resume tomorrow." |
| Weekly total ≥ $10.00 | "Weekly limit reached. ($X.XX this week). Resume next week." |

Hard stop sends a Telegram message AND exits non-zero. OpenClaw does not proceed.

### Warn-ahead abort (exit code 2)

If `--estimated-cost` is provided, check whether running this session would breach
any limit:

| Check | Condition |
|-------|-----------|
| Daily | today_total + estimated_cost > $3.00 |
| Weekly | weekly_total + estimated_cost > $10.00 |
| Total | cumulative + estimated_cost > $20.00 |

If any check fails: print the specific breach, exit code 2.
OpenClaw decides whether to proceed with a lower-cost approach or skip.
No Telegram message for warn-ahead — this is a planning check, not an alert.

### Warn (proceeds — exit code 0)

| Condition | Action |
|-----------|--------|
| Cumulative ≥ $18.00 | Send Telegram warning, print to stdout, proceed |
| Today's total ≥ $2.50 | Send Telegram warning, print to stdout, proceed |

Warning sends Telegram and continues. It is not a gate.

### Clear (exit code 0, no message)

All checks pass, no warnings triggered. Proceed silently.

---

## RUNNING state — crash detection

On `start`: appends a stub row to `session-log.md`:
```
| 2026-03-21 | kotkin-thread | RUNNING | TBD | $18.12 | Started 14:23 |
```
Cumulative column in the stub shows cumulative-before-this-session.

On `end`: finds the most recent RUNNING row for today, updates it:
```
| 2026-03-21 | kotkin-thread | 4min | $0.12 | $18.24 | Kotkin citation pass |
```

If `start` is called and a RUNNING row already exists (crash detection):
- Print a warning to stdout: "WARNING: previous session [name] shows RUNNING — may have crashed"
- Do NOT block — proceed. OpenClaw decides whether to close the orphan row first.
- Log the warning in the new stub row's Notes field.

---

## Telegram message formats

**Hard stop:**
```
[Research Intel] 🛑 Budget limit reached

Limit: [which limit]
Current: $X.XX [today/this week/total]
Limit: $X.XX

All research activity stopped. Reply to extend budget or adjust limits.
```

**Budget warning:**
```
[Research Intel] ⚠️ Budget warning

Today: $X.XX / $3.00 | This week: $X.XX / $10.00 | Total: $X.XX / $20.00
Headroom: $X.XX today · $X.XX this week · $X.XX total

Continuing this session.
```

Uses OpenClaw gateway bot (`polling_bot_token` from keyring).
Chat ID from `agent/config.json`.

---

## agent/config.json — seed content

```json
{
  "chat_id": "REPLACE_WITH_CHAT_ID",
  "telegram_bot": "polling_bot_token",
  "budget": {
    "daily_limit": 3.00,
    "weekly_limit": 10.00,
    "total_limit": 20.00,
    "daily_warn": 2.50,
    "total_warn": 18.00
  },
  "session_log": "library/session-log.md"
}
```

Budget values here are the source of truth for `run.py`.
Changing limits does not require code changes.

---

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Clear to proceed (or end logged successfully) |
| 1 | Hard stop — limit already reached |
| 2 | Warn-ahead abort — estimated cost would breach a limit |

---

## What it does NOT do

- No research logic
- No API calls to xAI or Anthropic
- No file writes outside `library/session-log.md`
- No scheduling — OpenClaw triggers it
- No locking — single agent, single session at a time assumed

---

## Files touched

| File | Action |
|------|--------|
| `agent/run.py` | Create (new) |
| `agent/config.json` | Create (new, with placeholder chat_id) |
| `library/session-log.md` | Append/update rows at runtime (not at build time) |

No other files modified.

---

## Done when

- [ ] `agent/run.py` written and shown to Robert before saving
- [ ] `agent/config.json` written with placeholder chat_id
- [ ] `python agent/run.py start --session-name test` exits 0 when under budget
- [ ] `python agent/run.py start --session-name test --estimated-cost 999` exits 2
- [ ] RUNNING row appears in session-log.md after start
- [ ] RUNNING row updates after end call
- [ ] Hard stop exits 1 and sends Telegram (manual test with seeded log)
- [ ] Commit on poc/agent-runner branch per WAY_OF_WORKING.md

---

*OpenClaw (Mini-moi) | Product Owner | March 21, 2026*
*Robert approved design with three answers + weekly headroom addition.*
*Claude Code: write run.py and config.json, show both before writing any files.*
