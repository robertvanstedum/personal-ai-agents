# Production Cron Setup Guide

**Goal:** Replace unreliable OpenClaw cron (agent-mediated) with standard system cron + OpenClaw messaging wrapper.

## Architecture

```
System Cron → Wrapper Script → Your Script → OpenClaw Message API → Telegram
              (captures output)  (runs logic)  (delivers result)
```

**No LLM involved** = Reliable, fast, predictable.

---

## Files Created

### 1. Wrapper Script
**Location:** `~/.openclaw/workspace/scripts/cron_wrapper.sh`

**What it does:**
- Runs your script
- Captures stdout/stderr
- Sends output to Telegram via `openclaw message send`
- Handles errors
- Logs to system logger

**Usage:**
```bash
cron_wrapper.sh <script_path> [telegram_chat_id]
```

### 2. Example Crontab
**Location:** `~/.openclaw/workspace/scripts/example-crontab.txt`

Contains ready-to-use cron entries for:
- Morning Curator (7 AM)
- Daily Usage Report (8 AM)
- Balance Monitor (every 4 hours)

---

## Installation Steps

### Step 1: Test the wrapper manually

```bash
# Test curator
~/.openclaw/workspace/scripts/cron_wrapper.sh \
  ~/Projects/personal-ai-agents/run_curator_cron.sh \
  8379221702

# Test usage report
~/.openclaw/workspace/scripts/cron_wrapper.sh \
  ~/Projects/personal-ai-agents/track_usage_wrapper.sh \
  8379221702

# Test balance monitor
~/.openclaw/workspace/scripts/cron_wrapper.sh \
  ~/.openclaw/workspace/scripts/check_balance_alert.sh \
  8379221702
```

✅ **Expected:** You should receive Telegram messages for each.

### Step 2: Install crontab

```bash
# Edit your crontab
crontab -e

# Paste the contents from example-crontab.txt
# Save and exit (Ctrl+X in nano, :wq in vim)

# Verify installation
crontab -l
```

### Step 3: Disable OpenClaw cron jobs

```bash
# List current jobs
openclaw cron list

# Disable each (keep for backup)
openclaw cron edit 3ffe5aff-e21b-4d4b-8715-eeca9f701cc9 --disable
openclaw cron edit 1948415b-5e16-4670-8488-25b7fb48e6f1 --disable
openclaw cron edit 86927931-85a1-4016-8e20-b6949c4d5099 --disable
```

---

## Monitoring

### Check cron logs (macOS)
```bash
# Last hour
log show --predicate 'process == "openclaw-cron"' --last 1h

# Live tail
log stream --predicate 'process == "openclaw-cron"'
```

### Check system cron status
```bash
# View current crontab
crontab -l

# Check mail for cron errors (if configured)
mail
```

---

## Advantages Over OpenClaw Cron

| Feature | System Cron | OpenClaw Cron |
|---------|-------------|---------------|
| **Reliability** | ✅ No LLM dependency | ❌ LLM timeouts |
| **Speed** | ✅ Immediate | ❌ Agent overhead |
| **Cost** | ✅ Free | ❌ API calls |
| **Predictability** | ✅ Deterministic | ❌ Agent variability |
| **Production-grade** | ✅ Battle-tested | ⚠️ Beta quality |

---

## Troubleshooting

### Job doesn't run
```bash
# Check cron is running
ps aux | grep cron

# Check crontab syntax
crontab -l

# Test script manually
bash /path/to/script.sh
```

### No Telegram message
```bash
# Test OpenClaw messaging
openclaw message send --channel telegram --to 8379221702 --message "Test"

# Check OpenClaw gateway is running
openclaw gateway status
```

### Permission denied
```bash
# Ensure scripts are executable
chmod +x ~/.openclaw/workspace/scripts/cron_wrapper.sh
chmod +x ~/Projects/personal-ai-agents/*.sh
```

---

## Next Steps

Once stable for 1 week:
- Delete disabled OpenClaw cron jobs
- Document any edge cases
- Consider migrating other automation to system cron

---

**Created:** 2026-02-16  
**Status:** Ready for testing
