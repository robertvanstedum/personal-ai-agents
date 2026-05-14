# Operational Health Monitor — Spec v1.0
**Date:** 2026-05-14
**Status:** Approved — implementation deferred to Chicago (post-Istanbul)
**Reviewed by:** Claude.ai + Grok (independent reviews, fully aligned)
**Priority:** High — Mac Mini migration is the deployment target

---

## Why This Exists

The system currently runs 7 independent services with no observability layer.

On 2026-05-14, the OpenClaw Telegram gateway was silently broken for approximately
9 hours. A stuck agent task (a Budapest search that hit a missing API key, then
cascading LLM timeouts) starved the Node.js event loop. Telegram polling calls
could not complete, so no inbound messages were received — but no alert was sent,
no log was checked, and no one knew until the user noticed the bot wasn't responding.

Three compounding failures made the cascade worse:

1. **xAI at capacity** — `grok-4` hit peak demand; all xAI variants entered
   cooldown simultaneously.
2. **Anthropic billing issue** — OpenClaw's fallback chain skipped the entire
   Anthropic provider due to a billing flag, with no notification.
3. **Gemma can't handle tools** — `gemma3:1b` (local last-resort fallback) always
   rejects tool-use requests. It is non-functional as an OpenClaw agent fallback.

This is the failure mode that monitoring is designed to prevent. The system is now
being used daily in production. Silent failures are no longer acceptable.

---

## Goal

A lightweight, standalone health monitor that:

- Runs every 15 minutes via launchd
- Checks four critical subsystems in priority order
- Sends Telegram alerts when something is wrong
- Writes structured JSON logs for history and diagnosis
- Does **not** auto-remediate anything (Phase 1 scope)
- Is entirely self-contained — no dependency on `telegram_bot.py` or any
  running service

---

## Architecture

```
launchd (every 15 min)
    └── health_monitor.py
            ├── Check 1: LLM provider health (xAI + Anthropic)
            ├── Check 2: Service health (6 launchd services)
            ├── Check 3: OpenClaw gateway Telegram probe
            └── Check 4: Log error scan (3 log files)
                    │
                    ├── logs/health_monitor.log          ← structured JSON history
                    ├── logs/health_monitor_state.json   ← dedup + heartbeat state
                    └── Telegram alert → minimoi_cmd_bot
```

### New files

| File | Purpose |
|------|---------|
| `health_monitor.py` | The health check script |
| `~/Library/LaunchAgents/com.vanstedum.health-monitor.plist` | launchd schedule |
| `logs/health_monitor.log` | Append-only JSON log |
| `logs/health_monitor_state.json` | Alert dedup + heartbeat state |
| `logs/health_monitor_stdout.log` | launchd stdout |
| `logs/health_monitor_stderr.log` | launchd stderr |

### What is not touched
- `telegram_bot.py` — no changes
- Any existing plist — no changes
- OpenClaw config — separate action item (see Pre-requisites)
- Any curator, phrasebook, or drill files — not touched

---

## Check 1 — LLM Provider Health

**Highest priority.** When all LLM providers fail, the entire agent system silently
stops working. No indication reaches the user.

Minimal 1-token probe call to each provider on every run.

### Providers checked

| Provider | Model | Max tokens |
|----------|-------|------------|
| xAI | `grok-3-mini` | 1 |
| Anthropic | `claude-haiku-4-5-20251001` | 1 |

API keys read from macOS keychain at runtime (same pattern as `telegram_bot.py`).

### Failure classification

| Response | Severity | Action |
|----------|----------|--------|
| `401` / billing/unauthorized in body | 🚨 ALERT | Immediate — auth or billing issue |
| `429` / capacity/overloaded in body | ⚠️ LOG ONLY (see threshold rule) | Log, do not alert |
| Connection timeout >10s | 🚨 ALERT | Provider unreachable |
| `200 OK` | ✅ OK | Record latency |

### 429 Threshold Rule
Log every 429. Only send Telegram alert after **3 consecutive 429s** on the same
provider (45 minutes of sustained failure). Reset counter on any non-429 response.
This prevents noise from transient capacity blips while still catching real outages.

---

## Check 2 — Service Health

Parse `launchctl list` for each known service.

### Services monitored

| Service label | Type | Expected state |
|---------------|------|----------------|
| `com.user.telegram-feedback-bot` | KeepAlive | PID always present |
| `com.user.curator-server` | KeepAlive | PID always present |
| `ai.openclaw.gateway` | KeepAlive | PID always present |
| `com.vanstedum.curator` | Cron (hourly) | No PID, exit 0 |
| `com.vanstedum.curator-priority-feed` | Cron (hourly) | No PID, exit 0 |
| `com.vanstedum.curator-intelligence` | Cron (daily 18:00) | No PID, exit 0 |

### Detection logic

| State | Action |
|-------|--------|
| Label not in `launchctl list` | 🚨 ALERT — not loaded |
| No PID + non-zero exit code | 🚨 ALERT — crashed (include exit code) |
| No PID + exit 0 | ✅ OK — cron completed cleanly |
| PID present | ✅ OK — running |

---

## Check 3 — OpenClaw Gateway Telegram Probe

**This check directly catches the 9-hour silent failure from today.**

Calls `GET https://api.telegram.org/bot{TOKEN}/getMe` with 10-second timeout.
Token read from `~/.openclaw/openclaw.json` → `.channels.telegram.botToken`.

This is an external probe — bypasses the Node.js process entirely. If the gateway's
internal event loop is stalled (as happened today), this catches it because `getMe`
is a cheap call the gateway should be maintaining continuously.

- Timeout or HTTP error → 🚨 ALERT
- 200 OK → ✅ OK

---

## Check 4 — Log Error Scan

Tail last 200 lines of each active log on every run. Alert once per file per hour.

| Log file | Alert patterns |
|----------|----------------|
| `logs/telegram_bot_stdout.log` | `ERROR`, `CRITICAL`, `Traceback`, `⚠️` |
| `~/.openclaw/logs/gateway.err.log` | `All models failed`, `stale-socket`, `disconnected`, `missing_xai_api_key` |
| `logs/curator_launchd.log` | `Error`, `Exception`, `Traceback` |

Alert includes the triggering log line for context.

---

## Daily Cost Summary

A working cost summary script already exists on cron. Do not duplicate the logic.

At 8:00 AM daily, `health_monitor.py` calls the existing cost summary script and
forwards its output as a single non-alert Telegram message:

```
📊 Daily API spend — 2026-05-14
xAI: $0.18  Anthropic: $0.04  Total: $0.22
```

Integration point to confirm during implementation: exact script path and invocation.

---

## Alert Design

### Format — every alert must include:
1. Timestamp
2. Failing component
3. One clear next step

**Critical alert:**
```
🚨 ALERT — xAI API billing issue
Provider: xai
Error: 401 Unauthorized
Time: 2026-05-14 22:15
→ Check https://console.x.ai for billing status
```

**Recovery:**
```
✅ RECOVERED — xAI API responding
Provider: xai  Latency: 1.2s
Time: 2026-05-14 22:30
```

**Service crash:**
```
🚨 ALERT — Service crashed
Service: com.user.telegram-feedback-bot
Exit code: -1
Time: 2026-05-14 22:15
→ Check logs/telegram_bot_stdout.log
```

### Deduplication
State file `logs/health_monitor_state.json` tracks last-alerted time and
last-known status per check.

- Same issue: re-alert no more than once per hour
- Recovery: always alert once when previously-failing check returns to OK
- First run: no suppression — alert immediately if broken

### Heartbeat
On every run completion, write `last_run` timestamp to state file — regardless
of check results. This allows future detection of the monitor itself going silent.

```json
{
  "last_run": "2026-05-14T22:15:00+02:00",
  "last_alerted": {
    "llm_xai": "2026-05-14T22:15:00",
    "llm_anthropic": null,
    "service_telegram_bot": null,
    "gateway_telegram_probe": "2026-05-14T09:10:00",
    "log_telegram_bot": null
  },
  "last_status": {
    "llm_xai": "billing",
    "llm_anthropic": "ok",
    "service_telegram_bot": "running",
    "gateway_telegram_probe": "ok",
    "log_telegram_bot": "ok"
  },
  "consecutive_429": {
    "llm_xai": 0,
    "llm_anthropic": 0
  }
}
```

---

## Structured Log Format

Every run appends one JSON line to `logs/health_monitor.log`.
This file is the input for Level 2 OpenClaw diagnosis.

```json
{
  "ts": "2026-05-14T22:15:00+02:00",
  "run_id": "a3f9c2",
  "duration_ms": 4120,
  "checks": {
    "llm_xai":               {"status": "billing",  "error": "401 Unauthorized", "latency_ms": null},
    "llm_anthropic":         {"status": "ok",                                     "latency_ms": 820},
    "service_telegram_bot":  {"status": "running",  "pid": 45827},
    "service_openclaw":      {"status": "crashed",  "exit_code": -9},
    "gateway_telegram_probe":{"status": "timeout",  "error": "10s timeout"},
    "log_telegram_bot":      {"status": "ok"},
    "log_openclaw_gateway":  {"status": "error",    "match": "All models failed"}
  },
  "alerts_sent": ["llm_xai", "service_openclaw", "gateway_telegram_probe"]
}
```

---

## launchd Plist

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.vanstedum.health-monitor</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/vanstedum/Projects/personal-ai-agents/health_monitor.py</string>
  </array>
  <key>StartInterval</key>
  <integer>900</integer>
  <key>StandardOutPath</key>
  <string>/Users/vanstedum/Projects/personal-ai-agents/logs/health_monitor_stdout.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/vanstedum/Projects/personal-ai-agents/logs/health_monitor_stderr.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
```

---

## Pre-requisites (OpenClaw — before monitor goes live)

These are config changes, not code. Both must be done before the health monitor
is loaded via launchd.

### 1. Anthropic billing flag
The gateway error log shows `"Provider anthropic has billing issue"`. Investigate
and resolve, OR document as known/expected in MEMORY.md. If unresolved, the monitor
will alert on every run.

### 2. Gemma tool support fix
`gemma3:1b` always fails with `"does not support tools"`. It provides zero
resilience for any agent task that uses tools.

- Run `ollama list` to see what is installed
- Replace `gemma3:1b` in OpenClaw fallback chain with a tool-capable model:
  - `llama3.2:3b` (preferred)
  - `qwen2.5:3b` (second choice)
- Restart gateway: `launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway`

---

## Implementation Order (Claude Code — Chicago)

1. Write `health_monitor.py` with all 4 check modules + heartbeat + 429 threshold
2. Confirm cost summary script path with Robert — wire daily call at 8am
3. Manual dry run: `python3 health_monitor.py --dry-run` (prints, no sends)
4. Live test: `python3 health_monitor.py --once` (one real Telegram message)
5. Install plist to `~/Library/LaunchAgents/`
6. Load: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vanstedum.health-monitor.plist`
7. Verify first automated run fires within 15 minutes and summary arrives in Telegram

**Commit message:** `feat(ops): health monitor Phase 1 — 4 checks, 15min launchd, Telegram alerts`

---

## Not in Scope — Phase 1

| Item | Rationale |
|------|-----------|
| Auto-restart crashed services | Phase 2 — needs careful design to avoid restart loops |
| Web dashboard | Not worth overhead for Phase 1 |
| OpenClaw plugin integration | JSON log as input is sufficient for Level 2 diagnosis |
| Log rotation | Existing pattern already handles this |
| Monitoring the monitor | Heartbeat timestamp handles this at Phase 1 scope |

---

## Phase 2 — Future (Post Mac Mini Stable)

- Alert if heartbeat goes stale (monitor itself stopped)
- Auto-restart for crashed KeepAlive services after confirmation pattern
- Grafana / simple web dashboard if usage grows
- Alert threshold tuning based on real-world noise data

---

*mini-moi Operational Health Monitor — Spec v1.0 — 2026-05-14*
*Reviewed: Claude.ai + Grok — independent reviews, fully aligned*
*Implementation: deferred to Chicago post-Istanbul trip*
