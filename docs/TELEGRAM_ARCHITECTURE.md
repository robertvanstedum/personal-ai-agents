# Telegram Architecture

## Two Bots

| Bot | Purpose | Direction |
|-----|---------|-----------|
| `minimoi_system_bot` | Curator briefings, German drills, deploy alerts, `!ops` commands | Mixed |
| `minimoi_agent_bot` | CoS + OpenClaw conversational | Bidirectional |

Clean separation: the system talks to you via the system bot; you talk to the agent via the agent bot.

## Production / Standby

```
MINIMOI_ROLE=production  →  production bots, all scheduled jobs run
MINIMOI_ROLE=standby     →  test bots, scheduled jobs suppressed
```

Only one instance is `production` at any time. Standby still runs the web app and bots for full testing capability — nothing shuts down, sends are just suppressed.

## Test Bots (standby instance)

| Test bot | Mirrors |
|---------|---------|
| `minimoi_system_test_bot` | minimoi_system_bot |
| `minimoi_agent_test_bot` | minimoi_agent_bot |

Same developer receives messages from both. Bot name in the Telegram header distinguishes which instance sent the message.

## Token Lookup (utils/telegram.py)

Lookup order: env var → macOS Keychain (keyring) → AWS SSM

```python
get_system_token()   # TELEGRAM_SYSTEM_BOT_TOKEN → keyring(telegram/bot_token) → SSM
get_agent_token()    # TELEGRAM_AGENT_BOT_TOKEN  → keyring(telegram/agent_bot_token) → SSM
get_chat_id()        # TELEGRAM_CHAT_ID          → keyring(telegram/chat_id)
```

On EC2, the instance role grants SSM read — no IAM credentials needed.
On Mac (standby), SSM is inaccessible but all sends are suppressed by `is_production()` guards.

## SSM Parameter Paths

```
Production:  /minimoi/production/telegram_system_bot_token
             /minimoi/production/telegram_agent_bot_token

Test:        /minimoi/test/telegram_system_bot_token
             /minimoi/test/telegram_agent_bot_token
```

Chat ID (8379221762) is not in SSM — same value on both envs, stored in `.env` as `TELEGRAM_CHAT_ID`.

## Callers

| File | Bot | Method |
|------|-----|--------|
| `curator_utils.py` → `send_telegram_alert()` | system | `get_system_token()` |
| `domains/guild/agents/operations.py` → `_send_telegram()` | system | `get_system_token()` |
| `domains/guild/agents/chief_of_staff.py` alerts | system | `get_system_token()` |
| `domains/guild/agents/chief_of_staff.py` polling | system (→ agent_bot later) | `get_system_token()` |
| `telegram_bot.py` (Mac launchd) | system + polling | keyring (Mac only) |

## Environment Promotion

Promoting Mac to production:
1. Sync data: `pg_dump` → S3 → `pg_restore` on Mac
2. Swap roles: EC2 `.env` → `MINIMOI_ROLE=standby`, Mac `.env` → `MINIMOI_ROLE=production`
3. Restart services on both instances
4. DNS: update Cloudflare A record for `minimoi.ai`
5. Validate full feature pass on new production instance

Estimated switchover: 2–4 hours. Rollback: reverse steps 2–4, ~1–2 hours.

See `docs/PRODUCTION_CUTOVER_PLAN.md` for the full checklist.
