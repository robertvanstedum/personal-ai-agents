# Telegram Bot Architecture — Design Spec
**Date:** 2026-06-21  
**Status:** Draft — for Claude.ai review before implementation  
**Author:** Claude Code (implementation spec)

---

## Problem

The current bot setup evolved organically and is now unclear:

- `minimoi_cmd_bot` handles German drills, curator commands, and `!ops` — all on Mac via launchd
- `rvsopenbot` was outbound-only (briefings, alerts) — partially replaced by system_bot in A3
- `minimoi_agent_bot` is OpenClaw's gateway, polling on Mac
- No bot polls on EC2 — production commands go nowhere

With EC2 as production, we need inbound command handling on EC2. We also need a clean separation between "talking to the system" and "talking to the Chief of Staff."

---

## Desired State — Three Production Bots

### 1. minimoi_system_bot (formerly minimoi_cmd_bot concept)
**Purpose:** Everything operational — German practice, curator, system health, cron triggers  
**Polls on:** EC2 (Docker container, `minimoi-telegram-bot`)  
**Handles:**
- German session commands (`session`, `/drill`, `/writing`, phrasebook)
- Curator (`/briefing`, `/run`, feedback buttons)
- System health (`/status`, `!ops`)
- Outbound delivery: curator briefings, Ops alerts (already wired via `get_system_token()`)

**Token:** SSM `/minimoi/production/telegram_system_bot_token`  
**Test token:** SSM `/minimoi/test/telegram_system_bot_token` → `minimoi_system_test_bot`  

> **Open question for Claude.ai:** German commands currently depend on `german_domain.py` and local file state on Mac. If the system bot polls on EC2, German command handling needs to either (a) call the German container's HTTP API, or (b) stay on Mac with a separate polling process. Recommend (a) — German container exposes endpoints — but this needs design work.

---

### 2. minimoi_cos_bot (new — Chief of Staff gateway)
**Purpose:** Conversational interface to the Chief of Staff agent  
**Polls on:** EC2 (Docker container, separate from system bot)  
**Handles:**
- `!cos` / `!chief` — CoS commands and conversation
- Morning brief delivery (CoS pushes daily summary)
- Job search status, domain health queries
- Future: approval requests, prioritization prompts

**Token:** Needs new BotFather bot — `minimoi_cos_bot`  
**SSM path:** `/minimoi/production/telegram_cos_bot_token`  
**Test token:** `/minimoi/test/telegram_cos_bot_token` → `minimoi_cos_test_bot`  

> **Note:** CoS agent (`chief_of_staff.py`) currently polls via `get_system_token()` in its `_telegram_poll_loop()`. This needs to be updated to use a dedicated CoS bot token once this bot exists.

---

### 3. minimoi_agent_bot (unchanged)
**Purpose:** OpenClaw gateway — planning, memory, GitHub issues  
**Polls on:** Mac (OpenClaw process, not containerized)  
**Handles:** OpenClaw conversational commands  
**Token:** Separate keyring key — NOT in SSM  
**No changes needed.**

---

## Current vs Desired Mapping

| Bot | Current state | Desired state |
|-----|--------------|---------------|
| `minimoi_cmd_bot` | Polls on Mac — German, curator, !ops | Retire or repurpose as Mac-only test/dev |
| `rvsopenbot` | Outbound only | Retire — replaced by system_bot outbound |
| `minimoi_agent_bot` | Mac, OpenClaw | Unchanged |
| `minimoi_system_bot` | Token in SSM, no polling process | EC2 container polling, full system commands |
| `minimoi_cos_bot` | Does not exist | New bot — EC2 container, CoS gateway |

---

## Token Inventory (complete picture)

### macOS Keychain (Mac only)
| Keyring key | Current use | After this change |
|------------|-------------|-------------------|
| `telegram / bot_token` | rvsopenbot outbound | Retire — replace with system_bot |
| `telegram / polling_bot_token` | minimoi_cmd_bot polling | Keep for Mac dev testing or retire |
| `telegram / chat_id` | Chat ID | Keep |

### AWS SSM (EC2 + Mac SSM-capable)
| SSM path | Bot | Status |
|----------|-----|--------|
| `/minimoi/production/telegram_system_bot_token` | minimoi_system_bot | ✅ Exists |
| `/minimoi/production/telegram_agent_bot_token` | minimoi_agent_bot | ✅ Exists |
| `/minimoi/production/telegram_cos_bot_token` | minimoi_cos_bot | ❌ Needs creating |
| `/minimoi/test/telegram_system_bot_token` | minimoi_system_test_bot | ✅ Exists |
| `/minimoi/test/telegram_agent_bot_token` | minimoi_agent_test_bot | ✅ Exists |
| `/minimoi/test/telegram_cos_bot_token` | minimoi_cos_test_bot | ❌ Needs creating |

---

## Infrastructure Changes Required

### New (Robert action before build)
1. Create `minimoi_cos_bot` in BotFather — get token
2. Create `minimoi_cos_test_bot` in BotFather — get token
3. Add both tokens to SSM (production + test paths above)
4. Create ECR repository `minimoi/telegram-bot` (already requested)
5. Optional: Create `minimoi_system_test_bot` if not already done

### Code changes (Claude Code builds after spec approved)
1. `telegram_system_bot.py` — already drafted; extend with German + curator commands via HTTP to containers
2. `telegram_cos_bot.py` — new; thin polling wrapper around CoS agent HTTP API
3. `docker/Dockerfile.telegram` — already drafted
4. `docker/Dockerfile.cos-bot` — new (or shared with system bot Dockerfile)
5. `docker-compose.prod.yml` — add `telegram-bot` and `cos-bot` services
6. `chief_of_staff.py` — update `_telegram_poll_loop()` to use `get_cos_token()` instead of `get_system_token()`
7. `utils/telegram.py` — add `get_cos_token()` function
8. `telegram_bot.py` (Mac) — scope down to Mac-only dev/test, or retire

### Mac cleanup
- Remove `!ops` from `telegram_bot.py` (moves to system_bot on EC2)
- Set `MINIMOI_ROLE=standby` on Mac so scheduled jobs suppress
- `minimoi_cmd_bot` polling on Mac: keep for dev/local testing only

---

## Open Questions for Claude.ai

1. **German commands on EC2:** The German domain has complex local state (drill pools, session state, phrasebook). Does it make sense to move German command handling to EC2 system bot (requires German container HTTP API), or keep it as a Mac-only concern and have two parallel polling bots (system_bot on EC2 for ops/curator, cmd_bot on Mac for German)?

2. **CoS bot vs agent bot:** Long-term, does CoS replace agent_bot as the primary conversational interface, or do they serve different audiences (CoS = Robert daily use, agent_bot = OpenClaw technical interface)?

3. **Outbound delivery consolidation:** Currently `rvsopenbot` (`bot_token` keyring) is used for some outbound sends. Should we fully retire it now and confirm all outbound goes through `minimoi_system_bot` (`get_system_token()`)?

4. **minimoi_cmd_bot retirement timeline:** Can it be retired immediately once system_bot is live on EC2, or does the Mac polling need to run in parallel during a transition?

---

## Success Criteria

- Sending `!ops` to `minimoi_system_bot` → response confirms EC2 identity
- Sending `!cos` to `minimoi_cos_bot` → CoS agent responds from EC2
- Sending a message to `minimoi_agent_bot` → OpenClaw responds from Mac
- No duplicate message delivery (each bot token has exactly one polling process)
- `MINIMOI_ROLE=standby` on Mac suppresses all production bot polling
