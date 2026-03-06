# Telegram Two-Bot Architecture

**Status:** ✅ Validated 2026-03-05
**Replaces:** `TELEGRAM_WEBHOOK_PLAN.md` (webhook/tunnel approach — abandoned)

---

## Design

Two bots, two jobs, zero update conflicts.

```
rvsopenbot  (existing token, keychain: telegram/bot_token)
  → telegram_bot.py owns this exclusively
  → Handles: Like/Dislike/Save button callbacks, /run, /status, /briefing
  → Sends: morning briefing, deep dive results
  → Runs via: com.user.telegram-feedback-bot (launchd, always-on)

minimoi_cmd_bot  (new token, set in OpenClaw config)
  → OpenClaw gateway owns this exclusively
  → Handles: text commands, voice notes, conversational queries
  → Reads: TELEGRAM_CONTEXT.md for session context
  → Runs via: com.openclaw.gateway (launchd)
```

**Why this works:** Telegram's `getUpdates` polling delivers each update to exactly one poller. Splitting by token means each bot gets its own update stream — no conflicts, no missed messages, no Conflict errors.

---

## What Broke (2026-03-05 Incident)

Root cause: `com.vanstedum.telegram-webhook` was loading `telegram_bot.py --webhook` at startup, competing with `com.user.telegram-feedback-bot` (polling mode) for the same token.

```
telegram.error.Conflict: terminated by other getUpdates request;
make sure that only one bot instance is running
```

Fix applied:
1. `launchctl unload ~/Library/LaunchAgents/com.vanstedum.telegram-webhook.plist`
2. Killed stray PIDs (47314, 47321)
3. `com.user.telegram-feedback-bot` (PID 46966) now sole owner of `rvsopenbot` token
4. Renamed plist to `com.vanstedum.telegram-webhook.plist.disabled` — `.disabled` extension prevents accidental reload via `launchctl load`

---

## Setup: rvsopenbot (curator bot)

Already running. No changes needed.

| Setting | Value |
|---|---|
| Token | `keyring.get_password("telegram", "bot_token")` |
| launchd label | `com.user.telegram-feedback-bot` |
| Script | `telegram_bot.py` (polling mode, no `--webhook`) |
| Responds to | Button callbacks + `/run` `/status` `/briefing` |

---

## Setup: minimoi_cmd_bot (OpenClaw gateway)

✅ Created 2026-03-05 via @BotFather.

**Token:** stored in macOS keychain:
- Service: `telegram`
- Account: `polling_bot_token`

```python
keyring.get_password("telegram", "polling_bot_token")
```

**OpenClaw config:** Point OpenClaw's Telegram provider at `keyring("telegram", "polling_bot_token")`. This key is separate from `rvsopenbot`'s `bot_token` — no collision.

**Note — keyring naming:** The `telegram` service name is consistent between both tokens (`bot_token` and `polling_bot_token`), but the broader keyring layout (across xAI, OpenAI, etc.) uses inconsistent service names. Worth a standardization pass at some point — one clear pattern across all credentials (e.g. `app_name/credential_type`).

---

## Testing Checklist (after any Telegram change)

```
□ ps aux | grep telegram_bot | grep -v grep  →  exactly ONE process
□ launchctl list | grep telegram  →  only com.user.telegram-feedback-bot loaded
□ Send /status to rvsopenbot  →  responds with last 10 log lines
□ Click Like on a briefing article  →  curator_preferences.json timestamp updates
□ Send text message to @minimoi_cmd_bot  →  OpenClaw responds
```

---

## Why Not Webhook Mode?

The webhook approach (`TELEGRAM_WEBHOOK_PLAN.md`) required:
- A running Cloudflare tunnel (URL changes on restart)
- Re-registration with Telegram on each start
- A stable named tunnel for Mac Mini production

Polling is simpler, always works, and the Mac is not a public server. Webhook mode is only worth the complexity if you need <1s response latency at high message volume — not the case here.

Webhook plan archived, not deleted, in case the Mac Mini migration ever resumes.
