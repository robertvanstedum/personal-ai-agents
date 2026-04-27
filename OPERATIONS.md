# Operations Manual

**Project:** Mini-moi — Personal AI Briefing Agent
**Purpose:** Day-to-day operations, health checks, troubleshooting
**Last Updated:** 2026-03-17

---

## Daily Health Check

Three commands to confirm everything is healthy:

```bash
# 1. Are all services running?
launchctl list | grep com.vanstedum

# 2. Did today's briefing generate?
ls -la ~/Projects/personal-ai-agents/curator_latest.html

# 3. Any errors overnight?
tail -20 ~/Projects/personal-ai-agents/logs/curator_launchd_error.log
```

**What you should see:**
1. Three entries with non-zero PIDs: `com.vanstedum.curator`, `com.vanstedum.curator-intelligence`, `com.vanstedum.curator-priority-feed`
2. File timestamp from 6–9 AM today
3. Empty or minor warnings — no Python tracebacks

---

## Services Overview

Mini-moi runs four background services via macOS launchd. All auto-start on login and auto-restart on crash.

| Service | Label | What It Does |
|---------|-------|-------------|
| Briefing | `com.vanstedum.curator` | Daily briefing + Telegram |
| AI Observations | `com.vanstedum.curator-intelligence` | Geopolitical intelligence layer |
| Priority Feed | `com.vanstedum.curator-priority-feed` | Tracked topic search |
| Web Server | `com.vanstedum.curator-server` *(if active)* | Deep dive buttons, feedback API |

All three pipeline jobs run **hourly** via `StartInterval=3600` with:
- A **time gate** (6 AM–6 PM only) so they don't fire at night
- An **idempotency check** so they run once per day even if triggered multiple times
- **Auto-retry on wake** — if the Mac slept through the morning, the job runs as soon as the machine is back online

---

## Scheduled Jobs

### 1. Daily Briefing (`com.vanstedum.curator`)

**Script:** `run_curator_cron.sh`
**Runs:** Hourly, 6 AM–6 PM, once per day
**Model:** xAI grok-4-1, temperature 0.7, ~$0.30/day

**What it does:**
1. Pulls new X bookmarks (`x_pull_incremental.py`) — failure is isolated, never blocks the briefing
2. Fetches RSS feeds (~390 articles) + X bookmark signals (~332 articles)
3. Scores and ranks with grok-4-1
4. Generates `curator_latest.html` and `curator_latest.json`
5. Sends morning briefing to Telegram via `telegram_bot.py --send`

**Idempotency marker:** `curator_latest.json` — checks first article's date field

**Manual trigger:**
```bash
cd ~/Projects/personal-ai-agents
./run_curator_cron.sh
```

**Check if it ran:**
```bash
ls -la ~/Projects/personal-ai-agents/curator_latest.html
# Should show today's date
```

---

### 2. AI Observations (`com.vanstedum.curator-intelligence`)

**Script:** `run_intelligence_cron.sh`
**Runs:** Hourly, 6 AM–6 PM, once per day
**Depends on:** Briefing must have run first (checks `curator_latest.json`)

**What it does:**
1. Waits for today's briefing to exist before running
2. Runs `curator_intelligence.py --telegram`
3. Generates geopolitical/finance intelligence analysis
4. Sends via Telegram

**Idempotency marker:** `intelligence_YYYYMMDD.json` — file existence check

**Manual trigger:**
```bash
cd ~/Projects/personal-ai-agents
./run_intelligence_cron.sh
```

---

### 3. Priority Feed (`com.vanstedum.curator-priority-feed`)

**Script:** `run_priority_feed_cron.sh`
**Runs:** Hourly, 6 AM–6 PM, once per day

**What it does:**
1. Runs web search pipeline for all active tracked topics
2. Accumulates results in `priorities.json`
3. Results appear at `/curator_priorities.html`

**Idempotency marker:** `priorities.json` → `last_run_date` field

**Manual trigger:**
```bash
cd ~/Projects/personal-ai-agents
./run_priority_feed_cron.sh
```

---

## Launchd Management

### Check Status of All Services
```bash
launchctl list | grep com.vanstedum
```

A healthy entry looks like: `[PID]  0  com.vanstedum.curator` (non-zero PID, exit code 0)

### Start / Stop Individual Services
```bash
# Load (start and enable auto-start)
launchctl load ~/Library/LaunchAgents/com.vanstedum.curator.plist
launchctl load ~/Library/LaunchAgents/com.vanstedum.curator-intelligence.plist
launchctl load ~/Library/LaunchAgents/com.vanstedum.curator-priority-feed.plist

# Unload (stop and disable auto-start)
launchctl unload ~/Library/LaunchAgents/com.vanstedum.curator.plist

# Restart (e.g. after code changes)
launchctl kickstart -k gui/$(id -u)/com.vanstedum.curator

# ⚠️  Known issue: kickstart -k does not always kill a long-running process.
# If the old PID is still alive after kickstart, use this instead:
kill $(lsof -ti :8765) && sleep 3
# launchd will auto-restart the process. Verify with: lsof -i :8765
```

### Plist Locations
```
~/Library/LaunchAgents/
├── com.vanstedum.curator.plist
├── com.vanstedum.curator-intelligence.plist
└── com.vanstedum.curator-priority-feed.plist
```

---

## Manual Operations

### Run Briefing Manually (Full Pipeline)
```bash
cd ~/Projects/personal-ai-agents
./run_curator_cron.sh
```

### Test Changes Without Affecting Production
```bash
cd ~/Projects/personal-ai-agents
source venv/bin/activate
python curator_rss_v2.py --dry-run --model=grok-4-1 --temperature=0.7
```

Dry run behavior:
- Runs the full pipeline (fetch → score → rank)
- Saves to `curator_preview.html` and `curator_preview.txt`
- Does **not** update `curator_latest.json`, archive, or history
- Does **not** send to Telegram
- Safe for testing model changes, scoring logic, configuration tweaks

### View Latest Briefing
```bash
open ~/Projects/personal-ai-agents/curator_latest.html
```

### List Recent Archive
```bash
ls -lt ~/Projects/personal-ai-agents/curator_archive/ | head -10
```

---

## Log Files

All logs live in `~/Projects/personal-ai-agents/logs/`. Rotation is managed by newsyslog (config at `/etc/newsyslog.d/com.vanstedum.curator-logs.conf`).

| Log File | Contents | Max Size |
|----------|----------|----------|
| `curator_launchd.log` | Briefing job stdout | 2 MB |
| `curator_launchd_error.log` | Briefing job stderr / errors | 1 MB |
| `intelligence_launchd.log` | AI Observations job output | 1 MB |
| `intelligence_launchd_error.log` | AI Observations errors | 1 MB |
| `priority_feed_launchd.log` | Priority feed output | 1 MB |
| `curator_server_stdout.log` | Web server normal output | 2 MB |
| `curator_server_stderr.log` | Web server errors | 2 MB |
| `telegram_bot.log` | Bot activity | 2 MB |
| `telegram_bot_stderr.log` | Bot errors (NetworkError suppressed) | 5 MB |

Up to 5 rotated archives per log file (`.0`, `.1`, etc.).

### Viewing Logs
```bash
# Live tail — briefing errors
tail -f ~/Projects/personal-ai-agents/logs/curator_launchd_error.log

# Quick error scan — all pipeline jobs
tail -20 ~/Projects/personal-ai-agents/logs/curator_launchd_error.log
tail -20 ~/Projects/personal-ai-agents/logs/intelligence_launchd_error.log
tail -20 ~/Projects/personal-ai-agents/logs/priority_feed_launchd.log
```

---

## Credentials

All API keys stored in macOS Keychain via the `keyring` library. Never stored in files or environment variables.

| Service | Keychain Service | Keychain Account | Used By |
|---------|-----------------|-----------------|---------|
| xAI | `xai` | `api_key` | Briefing (grok-4-1) |
| Anthropic | `anthropic` | `api_key` | Deep dives, fallback |
| Telegram briefing bot | `telegram` | `bot_token` | `telegram_bot.py` |
| Telegram gateway bot | `telegram` | `polling_bot_token` | OpenClaw commands |
| X OAuth2 client ID | `x_oauth2` | `client_id` | X bookmark API |
| X OAuth2 client secret | `x_oauth2` | `client_secret` | X bookmark API |
| X access token | `x_oauth2` | `access_token` | X bookmark API |
| X refresh token | `x_oauth2` | `refresh_token` | X bookmark API |

### Check if a Key Is Stored
```bash
cd ~/Projects/personal-ai-agents && source venv/bin/activate
python3 -c "import keyring; v=keyring.get_password('xai','api_key'); print(v[:12]+'...' if v else '❌ missing')"
python3 -c "import keyring; v=keyring.get_password('anthropic','api_key'); print(v[:12]+'...' if v else '❌ missing')"
python3 -c "import keyring; v=keyring.get_password('telegram','bot_token'); print(v[:12]+'...' if v else '❌ missing')"
```

### Add or Update a Key
```bash
cd ~/Projects/personal-ai-agents
source venv/bin/activate
python3 setup_keys.py
```

### Re-authorize X OAuth (if bookmarks stop syncing)
```bash
cd ~/Projects/personal-ai-agents
source venv/bin/activate
python x_oauth2_authorize.py          # Full browser flow (first time or re-auth)
python x_oauth2_authorize.py --status # Check token expiry
python x_oauth2_authorize.py --refresh # Refresh without browser (if refresh_token valid)
```

---

## Troubleshooting

### Briefing Didn't Arrive This Morning

**Check:**
```bash
# 1. Did the briefing file generate today?
ls -la ~/Projects/personal-ai-agents/curator_latest.html

# 2. What happened?
tail -40 ~/Projects/personal-ai-agents/logs/curator_launchd_error.log

# 3. Is the service running?
launchctl list | grep com.vanstedum.curator

# 4. Run manually to diagnose
cd ~/Projects/personal-ai-agents && ./run_curator_cron.sh
```

**Common causes:**
- Mac was asleep all morning → will run automatically within the hour after wake
- API key error → check logs for "Authentication failed", re-run `setup_keys.py`
- Service not loaded → `launchctl load ~/Library/LaunchAgents/com.vanstedum.curator.plist`

---

### AI Observations Not Appearing

**Check:**
```bash
# Did intelligence run today?
ls ~/Projects/personal-ai-agents/intelligence_$(date +%Y-%m-%d).json

# Did the briefing run first? (required dependency)
ls -la ~/Projects/personal-ai-agents/curator_latest.html

# Errors?
tail -30 ~/Projects/personal-ai-agents/logs/intelligence_launchd_error.log
```

**Most common cause:** Briefing ran late and intelligence hadn't fired yet. Will self-resolve within the hour.

---

### Telegram Not Receiving Messages

**Check:**
```bash
# 1. Was Telegram message generated?
cat ~/Projects/personal-ai-agents/telegram_message.txt

# 2. Bot errors?
tail -30 ~/Projects/personal-ai-agents/logs/telegram_bot_stderr.log

# 3. Is the bot token valid?
cd ~/Projects/personal-ai-agents && source venv/bin/activate
python3 -c "import keyring; v=keyring.get_password('telegram','bot_token'); print(v[:12]+'...' if v else '❌ missing')"
```

---

### API Key Error ("Authentication failed" / "Invalid API key")

```bash
cd ~/Projects/personal-ai-agents
source venv/bin/activate
python3 setup_keys.py
# Re-enter the failing key when prompted
```

---

### Web Server Not Responding (Deep Dive Buttons Broken)

```bash
# Check status
launchctl list | grep curator-server

# Check logs
tail -30 ~/Projects/personal-ai-agents/logs/curator_server_stderr.log

# Restart
launchctl kickstart -k gui/$(id -u)/com.vanstedum.curator-server

# Test
curl http://localhost:8765/
```

**Common causes:** Port 8765 already in use, Python version mismatch, missing dependencies.

**Rebuild venv (last resort):**
```bash
cd ~/Projects/personal-ai-agents
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
launchctl kickstart -k gui/$(id -u)/com.vanstedum.curator-server
```

---

## Key Files Reference

### Generated Daily
```
~/Projects/personal-ai-agents/
├── curator_latest.html          # Today's briefing (open in browser)
├── curator_latest.json          # Scored articles (idempotency marker)
├── curator_output.txt           # Plain text summary
├── telegram_message.txt         # Message sent to Telegram
├── intelligence_YYYYMMDD.json   # AI Observations output (idempotency marker)
└── priorities.json              # Priority feed results + last_run_date
```

### Archives
```
~/Projects/personal-ai-agents/
├── curator_archive/             # Daily briefings (HTML)
│   └── curator_briefing_YYYYMMDD_HHMM.html
├── curator_cache/               # Cached article content
│   └── {hash_id}.json
├── curator_history.json         # Article appearance tracking
└── interests/2026/deep-dives/  # Deep dive research output
    └── {topic}-{hash_id}.html
```

### Signal Store (X Bookmarks)
```
~/Projects/personal-ai-agents/
├── curator_signals.json         # 425 X bookmark signals (398 historical + growing)
└── x_pull_state.json            # last_pull_at timestamp (authoritative pull tracker)
```

### Scripts
| Script | Purpose |
|--------|---------|
| `run_curator_cron.sh` | Hourly briefing job (launchd entry point) |
| `run_intelligence_cron.sh` | Hourly AI Observations job |
| `run_priority_feed_cron.sh` | Hourly Priority Feed job |
| `curator_rss_v2.py` | Main curator pipeline |
| `curator_intelligence.py` | Intelligence layer |
| `curator_priority_feed.py` | Priority feed pipeline |
| `curator_server.py` | Web server (port 8765) |
| `telegram_bot.py` | Telegram bot (polling + send) |
| `x_pull_incremental.py` | Pulls new X bookmarks since last pull |
| `x_oauth2_authorize.py` | X OAuth2 PKCE authorization flow |
| `setup_keys.py` | Add/update API keys in Keychain |

---

## Quick Command Reference

```bash
# HEALTH CHECK
launchctl list | grep com.vanstedum
ls -la ~/Projects/personal-ai-agents/curator_latest.html
tail -20 ~/Projects/personal-ai-agents/logs/curator_launchd_error.log

# RUN BRIEFING MANUALLY
cd ~/Projects/personal-ai-agents && ./run_curator_cron.sh

# DRY RUN (test changes — no Telegram, no archive)
cd ~/Projects/personal-ai-agents && source venv/bin/activate
python curator_rss_v2.py --dry-run --model=grok-4-1 --temperature=0.7

# VIEW LATEST BRIEFING
open ~/Projects/personal-ai-agents/curator_latest.html

# VIEW LOGS (live)
tail -f ~/Projects/personal-ai-agents/logs/curator_launchd_error.log

# RESTART A SERVICE
launchctl kickstart -k gui/$(id -u)/com.vanstedum.curator
launchctl kickstart -k gui/$(id -u)/com.vanstedum.curator-intelligence
launchctl kickstart -k gui/$(id -u)/com.vanstedum.curator-priority-feed

# MANAGE CREDENTIALS
cd ~/Projects/personal-ai-agents && source venv/bin/activate && python3 setup_keys.py

# X OAUTH STATUS
cd ~/Projects/personal-ai-agents && source venv/bin/activate
python x_oauth2_authorize.py --status

# EXPORT DOCS TO PDF (saved to ~/Downloads/)
cd ~/Projects/personal-ai-agents && source venv/bin/activate
python tools/export_pdf.py --bundle curator   # README, ARCHITECTURE, OPERATIONS, ROADMAP, VISION
python tools/export_pdf.py --bundle german    # GERMAN_USER_GUIDE, GERMAN_SPEC
python tools/export_pdf.py README.md          # single file
python tools/export_pdf.py --list-bundles     # see all bundles
python tools/export_pdf.py --test             # verify tool is working
```

---

## German Domain

### Get a session

```bash
# Via Telegram (primary path)
# Send to @minimoi_cmd_bot:  !german session

# Or locally (dry-run to verify output without sending):
cd ~/Projects/personal-ai-agents && source venv/bin/activate
python _NewDomains/language-german/get_german_session.py \
  --base-dir _NewDomains/language-german/language/german/ --dry-run
```

### Start / stop the Dropbox watcher

```bash
# Via Telegram:  !german watcher start  /  !german watcher stop

# Or locally:
cd ~/Projects/personal-ai-agents/_NewDomains/language-german
source ../../venv/bin/activate
python watch_transcripts.py &         # start in background
pkill -f watch_transcripts.py         # stop
```

### Run the acceptance test suite

```bash
cd ~/Projects/personal-ai-agents/_NewDomains/language-german
source ../../venv/bin/activate
python run_tests.py                   # all 9 tests
python run_tests.py --test 9          # single test
```

### Check pipeline status

```bash
# Via Telegram (recommended):  !german status

# Or locally:
cd ~/Projects/personal-ai-agents && source venv/bin/activate
python _NewDomains/language-german/status.py \
  --base-dir _NewDomains/language-german/language/german/
```

### Key file locations

| Path | Purpose |
|---|---|
| `_NewDomains/language-german/ORCHESTRATOR.md` | Command routing reference for any orchestrating agent |
| `_NewDomains/language-german/GERMAN_USER_GUIDE.md` | End-to-end practice workflow guide |
| `_NewDomains/language-german/language/german/config/sync_config.json` | Dropbox paths, watcher settings, `agent_mode` |
| `_NewDomains/language-german/language/german/config/prompts/` | Persona `.txt` files (one per persona) |
| `_NewDomains/language-german/language/german/lessons/` | Daily lesson plan JSONs (gitignored) |
| `_NewDomains/language-german/run_tests.py` | 9-test acceptance suite |
| `~/Dropbox/German_Sessions/prompts/` | Generated session prompt files |
| `~/Dropbox/German_Sessions/transcripts/inbox/` | Drop transcripts here after practice |

---

## Documentation

- `README.md` — Project overview and setup
- `ARCHITECTURE.md` — System design and technical decisions
- `ROADMAP.md` — Build priorities and future work
- `CHANGELOG.md` — Version history
- `CREDENTIALS_SETUP.md` — Initial credential setup guide
- `tools/export_pdf.py` — Export any markdown doc to PDF (`--bundle curator`, `--bundle german`, or single file; `--test` to verify)
- This file (`OPERATIONS.md`) — Day-to-day operations

**Repository:** https://github.com/robertvanstedum/personal-ai-agents

---

**Remember:** If something breaks, start with the Daily Health Check. Most issues surface in those three commands.
