# Operations Manual

**Project:** Personal AI Agents (Curator RSS)  
**Purpose:** Day-to-day operations, health checks, troubleshooting  
**Last Updated:** Feb 21, 2026

---

## Daily Health Check

Run these three commands each morning to confirm everything is healthy:

```bash
# 1. Is the server running?
launchctl list | grep curator

# 2. Did cron run this morning?
ls -la ~/Projects/personal-ai-agents/curator_latest.html

# 3. Any errors overnight?
tail -20 ~/Projects/personal-ai-agents/logs/curator_server_stderr.log
```

**What you should see:**
1. `[PID]  0  com.user.curator-server` (non-zero PID = running)
2. File timestamp from 7:00-7:15 AM today
3. Empty or minor warnings only (no Python tracebacks)

---

## Daily Operations

### Running the Curator Manually

```bash
cd ~/Projects/personal-ai-agents
./run_curator_cron.sh
```

This runs the full pipeline:
- Fetches RSS feeds (15 sources, ~390 articles)
- Analyzes with xAI Grok (~$0.18)
- Generates HTML briefing
- Sends to Telegram automatically

**Files generated:**
- `curator_latest.html` - Current briefing (opens in browser)
- `curator_archive/curator_YYYY-MM-DD.html` - Dated archive
- `curator_output.txt` - Text summary
- `telegram_message.txt` - Message sent to Telegram

### Testing Changes (Dry Run)

```bash
cd ~/Projects/personal-ai-agents
python curator_rss_v2.py --dry-run --mode=xai --open
```

Use `--dry-run` when testing:
- Model changes
- Configuration tweaks
- Scoring logic

**Dry run behavior:**
- Runs full pipeline normally
- Saves to `curator_preview.html` (not `curator_latest.html`)
- Does NOT update archive or history
- Safe for testing without polluting data

### Checking What's Running

```bash
# Server status
launchctl list | grep curator
ps aux | grep curator_server.py | grep -v grep

# Cron jobs (OpenClaw gateway cron)
# (This runs through OpenClaw, not system cron)
openclaw cron list
```

### Viewing Recent Briefings

```bash
# Open latest in browser
open ~/Projects/personal-ai-agents/curator_latest.html

# List recent archives
ls -lt ~/Projects/personal-ai-agents/curator_archive/ | head -10
```

---

## Known Behaviors

### Multiple Runs Same Day

**Archive behavior:** Running the curator multiple times on the same day **overwrites** the archive HTML file.

- First run (9:00 AM): Creates `curator_2026-02-21.html`
- Second run (11:00 AM): **Overwrites** `curator_2026-02-21.html` (first version is lost)

**History behavior:** `curator_history.json` handles multiple runs gracefully:
- Updates existing entries with new rank/score
- No duplicate appearances for same day

**Recommendation:** Use `--dry-run` for testing model or configuration changes.

### Testing Without Archive Pollution

```bash
# Test run (no archive/history updates)
cd ~/Projects/personal-ai-agents
python curator_rss_v2.py --dry-run --mode=xai --open

# Output saved to curator_preview.html
# Archive and history remain unchanged
```

**When to use dry run:**
- Testing model changes (e.g., trying grok-3-mini vs grok-2)
- Testing config tweaks
- Debugging scoring logic
- Preview before committing to archive

**Future enhancement:** Timestamp-based archiving planned if multiple real runs per day become needed.

---

## Background Services

### curator_server.py (launchd)

**What it does:** Web server for interactive features (deep dive buttons, feedback)  
**Port:** 8765  
**Auto-starts:** On login  
**Auto-restarts:** If it crashes

#### Management Commands

```bash
# Check status
launchctl list | grep curator

# Stop & disable
launchctl unload ~/Library/LaunchAgents/com.user.curator-server.plist

# Start & enable
launchctl load ~/Library/LaunchAgents/com.user.curator-server.plist

# Restart (after code changes)
launchctl kickstart -k gui/$(id -u)/com.user.curator-server

# View live logs
tail -f ~/Projects/personal-ai-agents/logs/curator_server_stderr.log
```

#### Log Locations
- Stdout: `~/Projects/personal-ai-agents/logs/curator_server_stdout.log`
- Stderr: `~/Projects/personal-ai-agents/logs/curator_server_stderr.log`

#### Configuration File
- `~/Library/LaunchAgents/com.user.curator-server.plist`

---

## Scheduled Jobs (OpenClaw Cron)

### Daily Curator (7:00 AM CST)

**Command:** Runs through OpenClaw gateway cron  
**What it does:** 
- Fetches RSS feeds
- Generates briefing
- Sends to Telegram

**Check if it ran:**
```bash
ls -la ~/Projects/personal-ai-agents/curator_latest.html
# Should show today's date at 7:00-7:15 AM
```

**Manual trigger:**
```bash
cd ~/Projects/personal-ai-agents
./run_curator_cron.sh
```

### Viewing Cron Jobs

```bash
# List all cron jobs
openclaw cron list

# View specific job runs
openclaw cron runs --jobId <job-id>
```

---

## Credentials & Configuration

### API Keys (macOS Keychain)

All credentials stored in macOS Keychain via `keyring` library:

| Service | Keychain Entry | Used By |
|---------|---------------|---------|
| Anthropic | `anthropic` / `api_key` | Deep dives, fallback |
| xAI | `xai` / `api_key` | Daily curator |
| Telegram | `telegram` / `bot_token` | Message delivery |

#### Viewing Keys
```bash
# Check if keys are stored
python3 -c "import keyring; print(keyring.get_password('anthropic', 'api_key')[:20] + '...')"
python3 -c "import keyring; print(keyring.get_password('xai', 'api_key')[:20] + '...')"
python3 -c "import keyring; print(keyring.get_password('telegram', 'bot_token')[:20] + '...')"
```

#### Adding/Updating Keys
```bash
cd ~/Projects/personal-ai-agents
source venv/bin/activate
python3 setup_keys.py
```

### Environment Variables

Optional fallback if Keychain fails:
- `ANTHROPIC_API_KEY`
- `XAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`

Not recommended for daily use (Keychain is more secure).

---

## Troubleshooting

### Deep Dive Buttons Not Working

**Symptom:** Clicking buttons in HTML does nothing

**Fix:**
```bash
# Check if server is running
launchctl list | grep curator

# If not running, start it
launchctl load ~/Library/LaunchAgents/com.user.curator-server.plist

# Test server
curl http://localhost:8765/
```

### Curator Didn't Run This Morning

**Symptom:** `curator_latest.html` is old

**Check:**
```bash
# 1. Check OpenClaw cron status
openclaw cron list

# 2. Check for errors
tail -50 ~/Projects/personal-ai-agents/curator_errors.log

# 3. Run manually to diagnose
cd ~/Projects/personal-ai-agents
./run_curator_cron.sh
```

### API Key Errors

**Symptom:** "Authentication failed" or "Invalid API key"

**Fix:**
```bash
cd ~/Projects/personal-ai-agents
source venv/bin/activate
python3 setup_keys.py
# Re-enter the failing key when prompted
```

### Server Won't Start

**Symptom:** launchctl shows exit code != 0

**Check logs:**
```bash
tail -50 ~/Projects/personal-ai-agents/logs/curator_server_stderr.log
```

**Common causes:**
- Port 8765 already in use
- Missing dependencies (reinstall venv)
- Python version mismatch

**Nuclear option (rebuild venv):**
```bash
cd ~/Projects/personal-ai-agents
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
launchctl kickstart -k gui/$(id -u)/com.user.curator-server
```

### Telegram Not Receiving Briefing

**Check:**
```bash
# 1. Did curator run?
ls -la ~/Projects/personal-ai-agents/curator_latest.html

# 2. Was Telegram message generated?
cat ~/Projects/personal-ai-agents/telegram_message.txt

# 3. Check OpenClaw Telegram config
openclaw status
```

---

## File Locations Reference

### Generated Files
```
~/Projects/personal-ai-agents/
├── curator_latest.html          # Current briefing
├── curator_output.txt           # Text summary
├── telegram_message.txt         # Telegram content
├── curator_history.json         # Article tracking
├── curator_preferences.json     # User feedback
├── curator_archive/             # Historical briefings
│   └── curator_briefing_YYYYMMDD_HHMM.html
├── curator_cache/               # Cached article content
│   └── {hash_id}.json
└── interests/2026/deep-dives/   # Deep dive research
    └── {topic}-{hash_id}.html
```

### Log Files
```
~/Projects/personal-ai-agents/logs/
├── curator_server_stdout.log    # Server normal output
├── curator_server_stderr.log    # Server errors
└── curator_errors.log           # Cron run errors
```

### Configuration
```
~/Library/LaunchAgents/
└── com.user.curator-server.plist  # launchd service config
```

---

## Quick Command Reference

```bash
# HEALTH CHECK (run daily)
launchctl list | grep curator
ls -la ~/Projects/personal-ai-agents/curator_latest.html
tail -20 ~/Projects/personal-ai-agents/logs/curator_server_stderr.log

# RUN CURATOR MANUALLY
cd ~/Projects/personal-ai-agents && ./run_curator_cron.sh

# TEST CHANGES (DRY RUN)
cd ~/Projects/personal-ai-agents && python curator_rss_v2.py --dry-run --mode=xai --open

# CHECK WHAT'S RUNNING
launchctl list | grep curator
ps aux | grep curator_server

# VIEW LOGS
tail -f ~/Projects/personal-ai-agents/logs/curator_server_stderr.log
tail -50 ~/Projects/personal-ai-agents/curator_errors.log

# RESTART SERVER
launchctl kickstart -k gui/$(id -u)/com.user.curator-server

# MANAGE CREDENTIALS
cd ~/Projects/personal-ai-agents && source venv/bin/activate && python3 setup_keys.py

# VIEW LATEST BRIEFING
open ~/Projects/personal-ai-agents/curator_latest.html
```

---

## Mac Mini Migration Checklist

When moving to Mac Mini as always-on server:

- [ ] Install Python 3.14 on Mini
- [ ] Clone repo to Mini
- [ ] Create venv and install requirements
- [ ] Run `setup_keys.py` to migrate credentials
- [ ] Copy launchd plist to Mini's `~/Library/LaunchAgents/`
- [ ] Load launchd service on Mini
- [ ] Move OpenClaw cron jobs from MacBook to Mini
- [ ] Update curator HTML to use Mini's IP instead of localhost
- [ ] Configure Mini firewall (allow port 8765)
- [ ] Test from MacBook browser → Mini server
- [ ] Verify deep dive generation works end-to-end

---

## Support Resources

**Documentation:**
- `PROJECT_BRIEF.md` - System architecture and features
- `CHANGELOG.md` - Version history
- `CREDENTIALS_SETUP.md` - Initial setup guide
- This file (`OPERATIONS.md`) - Day-to-day operations

**Repositories:**
- Public: https://github.com/robertvanstedum/personal-ai-agents
- Private: https://github.com/robertvanstedum/rvs-openclaw-agent

**Key Scripts:**
- `curator_rss_v2.py` - Main curator
- `curator_server.py` - Web server
- `curator_feedback.py` - Deep dive generation
- `setup_keys.py` - Credential management
- `run_curator_cron.sh` - Cron wrapper

---

**Remember:** If something breaks at 2 AM, start with the Daily Health Check. Most issues show up in those three commands.
