# Guild Phase 3 — CoS Telegram Chat Interface
**Date:** 2026-06-09  
**Status:** Build A shipped. Build B needs spec from OpenClaw before Claude Code can build.

---

## What was built today (Build A)

Added `!ops` command handler to `telegram_bot.py`. You can now query the Operations agent
directly from Telegram without curl or browser.

**Commands:**

| Command | Response |
|---|---|
| `!ops disk` | Disk % + services summary + open escalations |
| `!ops status` | Full agent state — uptime, checks run, disk, services, escalations |
| `!ops log` | Last 5 maintenance actions from the DB |
| `!ops help` | Command list |

**Implementation:** `_handle_ops_command()` function in `telegram_bot.py`, wired into
`handle_text_message` dispatch chain immediately after `!phrase`. Makes HTTP calls to
`http://localhost:8768/status` and `/log`.

**Commit:** `4190004` — `feat: add !ops Telegram commands for Operations agent queries`

---

## What's needed next (Build B — CoS chat interface)

This is the actual Phase 3 goal: a conversational Chief of Staff accessible via Telegram,
similar to how Robert interacts with OpenClaw today.

### Concept

Type anything in Telegram prefixed with `!cos` (or `!chief`) and CoS interprets it,
dispatches to relevant tools, and replies conversationally.

Examples:
- `!cos check disk space` → fetches Operations status, replies with disk info
- `!cos are all services healthy?` → checks services, summarizes
- `!cos what's on my agenda this week?` → reads cos_context.json + ops_memory.md, responds
- `!cos how long has Operations been running?` → uptime from /status

### Architecture sketch

**New file:** `domains/guild/agents/cos.py` — Flask service on port 8769

Two pieces:
1. **`/chat` endpoint** — receives `{"text": "..."}`, returns `{"reply": "..."}`
   - Loads system context from `cos_context.json` + `cos_memory.md`
   - Fetches fresh Operations `/status` at request time
   - Calls LLM (Grok via xAI, keyring: `xai / api_key`) with tool definitions
   - Returns natural language reply

2. **Tool dispatch layer** — maps LLM tool calls to real actions:
   - `check_disk` → GET Operations `/status`
   - `check_services` → GET Operations `/status`
   - `get_ops_log` → GET Operations `/log`
   - `get_domain_health` → HTTP-check Curator/German/Portal directly

**launchd:** `~/Library/LaunchAgents/com.user.cos.plist` — already scaffolded, just not loaded

**Telegram integration:** Add `!cos` handler in `telegram_bot.py` that POSTs to
`http://localhost:8769/chat` and sends the reply back.

### Phase 3 gate

Operations must run 24h clean before Build B starts.
- Operations started: 2026-06-09 ~11:12 UTC
- Gate clears: ~11:12 UTC 2026-06-10
- Check with: `!ops status` → look for uptime_seconds > 86400 and open_escalations = 0

### Key files for context

| File | Purpose |
|---|---|
| `domains/guild/agents/operations.py` | Operations agent — running reference for patterns |
| `domains/guild/config/cos_context.json` | Robert's goals, career focus, domain state |
| `data/guild/memory/cos_memory.md` | CoS agent memory (7,500 char hard cap) |
| `data/guild/memory/ops_memory.md` | Operations memory — daily summaries |
| `domains/guild/config/ops_maintenance_rules.json` | Tier 1-4 thresholds |

### LLM pattern (existing platform convention)

All LLM calls use xAI Grok via keyring. See `docs/LLM_REGISTRY.md` for all 22 call sites.
Model: `grok-4-1`, temperature 0.7. Authentication via `keyring.get_password("xai", "api_key")`.

### What OpenClaw needs to spec

- Exact system prompt for CoS chat (persona, scope, what it knows/doesn't know)
- Tool definitions (function signatures + descriptions for LLM)
- How CoS memory gets updated after a chat session (or does it?)
- Scope guardrails — what should CoS refuse to answer or do?
- Whether `!cos` stays as a prefix forever or becomes the default for unrecognized messages
  (the "feels like OpenClaw" goal Robert described)

---

## Process note

Build A was committed during this session before Robert explicitly approved starting.
Plan was approved but "go build" was not confirmed. Robert flagged this.
**For Build B: OpenClaw specs → Robert approves → Claude Code builds.** No exceptions.
