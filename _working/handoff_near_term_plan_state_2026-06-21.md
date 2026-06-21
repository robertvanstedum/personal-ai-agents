# Handoff — Current State Investigation for NEAR_TERM_PLAN Finalization
*Created: 2026-06-21 — Claude Code*
*For: Claude.ai — use to finalize and validate NEAR_TERM_PLAN.md*

---

## Purpose

Robert asked Claude Code to investigate 7 open questions about current
system state before Claude.ai finalizes the revised NEAR_TERM_PLAN.
Answers below are based on git history, local DB queries, and file
inspection — not EC2 live queries (those require SSH).

---

## Findings

### 1. PostgreSQL on EC2 — pg_dump → S3 → pg_restore status

**Status: DONE.**

The migration was completed during the June 20–21 session. Evidence:
- Commit `97abc54` — Fix guild DB connection for Docker (DATABASE_URL env var)
- Commit `82ba2d5` — Add psycopg2-binary to portal Docker requirements
- User confirmed "queue and build log empty" was resolved after both fixes deployed
- User confirmed Guild Queue and Build Log showing data on EC2 later in the session

Mac local postgres (source of truth) has:
- `guild.design_log`: 84 rows
- `guild.cos_agenda`: 27 rows

EC2 postgres should match (restored from pg_dump taken after Mac was confirmed good).

**No action needed.** Note: the DR (`dr_aws_migration_day1_2026-06-20.md`) says
"PostgreSQL migration not yet run" under "Not yet implemented" — this is stale.
The DR was written mid-session before the migration completed. Claude.ai should
note this when referencing the DR.

---

### 2. Nginx + env fix — committed and deployed?

**Status: DONE and committed. Deployed to EC2 (user confirmed German/Curator load).**

Commit `e8241f1` — "AWS Phase 2: fix Docker networking for curator/german proxy"
Fixed two bugs:
1. Removed nginx location blocks for `/app/curator/` and `/app/german/` that
   bypassed portal auth and sent wrong paths to backends
2. Added Docker service-name URLs to `.env.ec2` so portal proxy reaches
   curator/german containers via bridge network (not localhost)

Current nginx config (`nginx/minimoi.conf`):
- `server_name app.minimoi.ai` (both HTTP and HTTPS blocks)
- Single `location /` → `proxy_pass http://127.0.0.1:5001` (portal handles all routing)
- Comment explicitly warns against adding separate location blocks

Current `.env.ec2`:
- `CURATOR_BACKEND=http://curator:8766` ✓
- `GERMAN_BACKEND=http://german:8767` ✓
- `MINIMOI_ROLE` — **NOT YET ADDED** (pending A1 build)

**No action needed for the bugs. MINIMOI_ROLE addition is A1 work.**

---

### 3. SSM credentials — which are confirmed working on EC2?

**Status: Partial. Confirmed via feature testing, no exhaustive audit done.**

Confirmed working (feature tests passed on EC2 during session):
- `anthropic_api_key` — Deep dive ran successfully after SSM fallback added to `curator_feedback.py`
- `xai_api_key` — Curator scoring assumed working (briefing data loaded)
- `database_url` — Guild DB connects (queue/build log showing data)

Unknown / not yet tested on EC2:
- `telegram_bot_token` — Telegram NOT running on EC2 yet (no polling container)
- `telegram_polling_bot_token` — Same
- `deepl_api_key` — German translation not tested on EC2
- `tavily_api_key` — Deep dive web search (Tavily) not explicitly verified
- `x_oauth2` credentials — X bookmark pull not running on EC2

**For Claude.ai:** The full SSM credential audit is a prerequisite for A3
(Telegram build). Robert will need to add `/minimoi/production/telegram_*`
and `/minimoi/test/telegram_*` params before that build starts. Recommend
adding SSM audit as a checklist item in the plan.

---

### 4. Telegram spec v2 — design log status

**Status: Registered, spec_ready.**

```
id: 94
title: Telegram Architecture: Swappable Environments + Two-Bot Model
status: spec_ready
spec_file: docs/specs/spec_telegram_architecture_v2_2026-06-20.md
```

Superseded entries:
- id 91 — old v1 spec → `deferred`
- id 93 — duplicate v2 → `deferred`

Spec file is committed to repo at `docs/specs/spec_telegram_architecture_v2_2026-06-20.md`.

**Build is gated on Robert creating test bots in BotFather and adding SSM params.**

---

### 5. cos_context.json — committed or Mac-local?

**Status: COMMITTED to repo.**

```
git ls-files | grep cos_context
→ domains/guild/config/cos_context.json
```

This file is tracked in git and will be present in any clone, including EC2
Docker images (if built after the file was added). It is not sensitive — it
contains career context for the CoS agent, not credentials.

**No action needed.**

---

### 6. DR from yesterday — committed?

**Status: COMMITTED to `docs/decision-records/drafts/`.**

```
git ls-files | grep dr_aws_migration
→ docs/decision-records/drafts/dr_aws_migration_day1_2026-06-20.md
```

Committed in commit `e3cb431` — "Commit DR and Telegram spec; fix scans sort order by date"

**Stale item in DR**: The "Not yet implemented" section lists "PostgreSQL migration
not yet run" — this was completed later in the same session. Claude.ai should
treat the DR as mostly accurate but with this one stale item.

---

### 7. docker-compose.prod.yml — telegram-bot container?

**Status: NOT included. Telegram-bot container is A3 work.**

Current `docker-compose.prod.yml` services:
- `postgres`
- `curator`
- `german`
- `portal`

No `telegram-bot` service. This is expected — it's part of the A3 Telegram
build, gated on Robert creating test bots and adding SSM params.

**No action needed until A3 gate is cleared.**

---

## Summary for Claude.ai

| Question | Status | Action needed |
|----------|--------|---------------|
| Postgres migration | ✅ Done | None — DR has stale note, flag it |
| Nginx + env fix | ✅ Done and deployed | Add MINIMOI_ROLE to .env.ec2 in A1 |
| SSM credentials | ⚠️ Partial | Full audit needed; Telegram params not yet in SSM |
| Telegram spec v2 | ✅ spec_ready (id 94) | Gated on Robert creating test bots |
| cos_context.json | ✅ Committed | None |
| DR committed | ✅ Yes (docs/decision-records/drafts/) | Note stale Postgres item |
| telegram-bot in compose | ❌ Not yet | A3 work — add when spec builds |

---

## Open items not in the original 7 questions

These came up during investigation and are worth Claude.ai knowing:

**MINIMOI_ROLE not in .env.ec2 yet.** The env var is the foundation of A1
but hasn't been added. First thing Claude Code does when A1 starts.

**Mac cron still active.** Mac launchd plists run curator RSS pull, x_pull_incremental,
and x_to_article on their normal schedules. EC2 has NO cron running. This means
EC2's briefing data is whatever was last synced — it does not update automatically.
This is the most operationally significant gap. A2 (cron on EC2) directly addresses it.

**Current cron times on Mac** (for reference when writing EC2 crontab):
- Curator RSS pull + scoring: check `~/Library/LaunchAgents/com.user.curator-cron.plist`
- X pull: check `com.user.x-pull-cron.plist`
These times should be confirmed before A2 deploys.

---

*Handoff · 2026-06-21 · Claude Code*
*File: `_working/handoff_near_term_plan_state_2026-06-21.md`*
