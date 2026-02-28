# Telegram Integration Architecture
**Status:** Design spec — ready for implementation  
**Date:** 2026-02-27  
**Priority:** High  

---

## Problem Statement

The current Telegram integration mixes three distinct input types (button callbacks, text commands, voice) through a single handler, causing:
- Polling conflicts between `telegram_feedback_bot.py` and OpenClaw
- Session context bloat ($0.52/msg vs $0.003/msg fresh)
- Unreliable button handling
- No clean separation of concerns

---

## Design Principles

1. **Buttons never touch OpenClaw** — lightweight dedicated handler only
2. **OpenClaw handles intelligence** — text and voice commands only
3. **Briefings sent directly** — no OpenClaw involvement in outbound notifications
4. **Context stays small** — TELEGRAM_CONTEXT.md replaces full session history
5. **Voice via Telegram transcription** — no custom voice code needed

---

## Architecture Overview

```
TELEGRAM
   │
   ├── Button callbacks (like/dislike/save)
   │       └── telegram_feedback_bot.py (always running via launchd)
   │               └── calls curator_feedback.py directly
   │               └── NO OpenClaw involved
   │
   ├── Text commands ("daily report", "status", "reset")
   │       └── OpenClaw Telegram channel
   │               └── reads TELEGRAM_CONTEXT.md (50 lines max)
   │               └── executes task
   │               └── updates TELEGRAM_CONTEXT.md
   │               └── resets session after task completes
   │
   ├── Voice messages
   │       └── Telegram transcribes → text
   │               └── same OpenClaw text command handler
   │               └── no special voice code needed
   │
   └── Outbound (briefings, notifications)
           └── curator_rss_v2.py sends directly
           └── NO OpenClaw involved
```

---

## Component 1: telegram_feedback_bot.py (Buttons Only)

### Responsibility
Handle ONLY `callback_query` updates: `like:N`, `dislike:N`, `save:N`

### Behavior
1. Receive callback_query
2. Answer callback immediately (clears Telegram spinner)
3. Run `curator_feedback.py <action> <rank> --channel telegram`
4. Reply with article title + confirmation

### What it must NOT do
- Handle text messages
- Handle voice messages  
- Involve OpenClaw in any way
- Maintain session state

### Key fix needed
Remove any text/voice message handlers from this file. If a non-button update arrives, ignore it silently.

### Runs via
`launchd` — always on, auto-restart on crash

### Path fix required
`CURATOR_CALLBACKS.md` lookup should search `~/Projects/rvs-openclaw-agent/` first, not `~/.openclaw/workspace/`

---

## Component 2: TELEGRAM_CONTEXT.md (OpenClaw Memory)

### Purpose
Replaces full session history for Telegram interactions. Gives OpenClaw the context it needs without bloating tokens.

### Location
`~/Projects/personal-ai-agents/TELEGRAM_CONTEXT.md`

### Format
```markdown
# Telegram Context
Last updated: 2026-02-27 14:23 CST

## User
Name: Robert
Timezone: America/Chicago
Preferences: Top 20 articles, grok-3-mini scoring

## Recent Commands (last 5)
- 2026-02-27 14:23: "daily report" → completed OK
- 2026-02-26 08:15: "status" → session 12k tokens
- 2026-02-25 09:02: "daily report" → completed OK

## Recent Feedback Patterns
- Likes contrarian/analytical articles
- Dislikes: summary-only, no depth
- Saved: geopolitics, monetary policy

## Active Tasks
None

## System State
- curator_server.py: running (launchd)
- telegram_feedback_bot.py: running (launchd)
- Last briefing: 2026-02-27 08:00
```

### Rules
- Max 50 lines
- Updated after every OpenClaw Telegram interaction
- OpenClaw reads this INSTEAD of full session history
- Never grows — oldest entries drop off

---

## Component 3: OpenClaw Telegram Command Handler

### Responsibility
Handle text commands and voice messages that require intelligence or script execution.

### Voice handling
Telegram automatically transcribes voice messages to text. OpenClaw receives plain text — no special voice code required. Handle identically to text commands.

### Supported commands
| Command | Action |
|---------|--------|
| `daily report` | Run `curator_rss_v2.py --model=xai`, send confirmation |
| `status` | Show session tokens, last run time, service health |
| `reset` | Compact/reset OpenClaw session context |
| `balance` | Show Anthropic API spend estimate |
| `archive` | List last 5 briefings with links |
| `deep dive [topic]` | Trigger `curator_feedback.py` for specific topic |

### Session behavior (critical)
1. OpenClaw receives Telegram message
2. Read `TELEGRAM_CONTEXT.md` (not full session history)
3. Execute command
4. Update `TELEGRAM_CONTEXT.md` with outcome
5. **Reset session context** automatically after task completes
6. Result: every Telegram command starts near-fresh, stays cheap

### Cost target
- Per command: $0.003 - $0.01
- Never exceed $0.05 per Telegram interaction

---

## Component 4: Outbound Notifications

### Responsibility
Send briefings and alerts to Telegram. No OpenClaw involvement.

### Implementation
Direct Telegram API calls from `curator_rss_v2.py` after briefing generation:

```python
def send_telegram_notification(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": message})
```

### Triggers
- Daily briefing generated → send summary + link
- Error in cron job → send alert
- (Future) Budget threshold exceeded → send warning

---

## Conflict Resolution

The root cause of today's polling conflict:

Both `telegram_feedback_bot.py` and OpenClaw's Telegram channel were calling `getUpdates` on the same bot token simultaneously. Telegram only allows one active polling connection per bot.

### Fix: Webhook for feedback bot, polling for OpenClaw

Option A (recommended): Run `telegram_feedback_bot.py` as a **webhook** (not polling). OpenClaw uses polling. Webhooks and polling don't conflict.

Option B: Use **two separate bot tokens** — one for button callbacks, one for OpenClaw commands. Clean but requires managing two bots.

Option C: **Single dispatcher** — one polling loop that routes by update type. More complex but single token. Not recommended.

**Recommendation: Option A (webhook for feedback bot)**
- `telegram_feedback_bot.py` registers webhook on startup
- OpenClaw polls its own bot token
- No conflict possible

---

## Implementation Order

### Phase 1 (this session)
1. Fix `telegram_feedback_bot.py` — buttons only, webhook mode, remove text/voice handlers
2. Fix `CURATOR_CALLBACKS.md` path lookup
3. Verify buttons work cleanly with no conflicts

### Phase 2 (next session)
1. Create `TELEGRAM_CONTEXT.md` template
2. Configure OpenClaw to read context file instead of full session
3. Add session auto-reset after Telegram commands
4. Implement text command handlers

### Phase 3 (future)
1. Voice command testing (should work automatically via Telegram transcription)
2. Outbound notification improvements
3. Budget alert notifications

---

## Files Affected

| File | Change |
|------|--------|
| `telegram_feedback_bot.py` | Buttons only, webhook mode, remove other handlers |
| `TELEGRAM_CONTEXT.md` | New file — create from template above |
| `CURATOR_CALLBACKS.md` | Fix path lookup order |
| `curator_rss_v2.py` | Add direct Telegram notification on completion |
| OpenClaw config | Point to TELEGRAM_CONTEXT.md for Telegram sessions |
| `OPERATIONS.md` | Document new architecture |

---

## Lessons Learned (Feb 21-27, 2026)

- **Two pollers = conflict.** Never run two `getUpdates` loops on the same token
- **Session bloat is expensive.** 141k tokens = $0.52/msg, fresh = $0.003/msg (170x)
- **Separation of concerns first.** Debug loops happen when one component tries to do too many things
- **OpenClaw is an intelligence layer, not a router.** Don't route button clicks through it
- **Design before building.** An hour of debugging = 15 minutes of proper design upfront

---

## Notes for OpenClaw

When implementing Phase 1, Claude Code should:
1. Read this file completely before writing any code
2. Make one change at a time and test
3. Do NOT modify OpenClaw's own Telegram polling configuration
4. Commit after each phase with descriptive message
5. Update `OPERATIONS.md` when Phase 1 complete

Save this file to: `~/Projects/personal-ai-agents/docs/FEATURE_TELEGRAM_ARCHITECTURE.md`
