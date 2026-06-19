# Operations Manual

**Project:** Mini-moi — Personal AI Briefing Agent
**Purpose:** Day-to-day operations, health checks, troubleshooting
**Last Updated:** 2026-06-19

---

## Daily Health Check

### After any reboot — check this first

Docker/Colima must be running before the Guild platform has data. The guild agents start at login before Colima finishes booting; if the DB is down at that moment, Guild stays empty for the whole session.

```bash
# 1. Is Colima (Docker runtime) running?
colima status

# 2. Are the database containers up?
docker ps

# 3. Does the CoS agent see the DB?
curl -s http://localhost:8769/status | python3 -m json.tool
```

**What you should see:**
1. `colima is running` (or `running` state)
2. Both `postgres-ai-agents` and `neo4j-context-graph` listed with `Up` status
3. `"db_available": true` in the CoS status JSON

**If Colima is down:**
```bash
colima start
cd ~/Projects/personal-ai-agents && docker compose up -d
# Then restart CoS (it doesn't retry DB on its own):
launchctl kickstart -k gui/$(id -u)/com.user.cos
```

---

### Routine health check (Curator)

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

## System Applications Inventory

Complete map of every process running in production. Update this table whenever a service is added, removed, or changed.

### Flask web services

| Service | Port | launchd Label | Script | AWS Migration |
|---------|------|---------------|--------|---------------|
| Portal | 5001 | `com.vanstedum.minimoi-portal` | `minimoi_portal/app.py` | Containerize → EC2 |
| Curator server | 8766 | `com.user.curator-server` | `curator_server.py` | Containerize → EC2 |
| German domain | 8767 | `com.vanstedum.german-html-server` | `domains/german/german_server.py` | Containerize → EC2 |
| Operations agent | 8768 | `com.user.operations` | `domains/guild/agents/operations.py` | Containerize → EC2 |
| Chief of Staff | 8769 | `com.user.cos` | `domains/guild/agents/chief_of_staff.py` | Containerize → EC2 |
| Dev agent | 8770 | `com.user.devagent` | `domains/guild/agents/dev_agent.py` | Containerize → EC2 |

### Infrastructure services

| Service | Port | launchd Label | Notes | AWS Migration |
|---------|------|---------------|-------|---------------|
| PostgreSQL | 5432 | (Docker) `postgres-ai-agents` | `personal_agents` DB; guild/jobs/research schemas | Replace with RDS (Phase 4) |
| Neo4j | 7474 / 7687 | (Docker) `neo4j-context-graph` | Context graph; not yet active | EC2-hosted (no managed service needed) |
| Ollama | 11434 | `com.ollama.ollama` (`homebrew.mxcl.ollama`) | Local LLM server; gemma3:1b running | Stay local / g4dn.xlarge spot (Phase 5) |
| Open WebUI | 8080 | (via SSH tunnel) | Ollama browser UI | Stay local |
| Cloudflare tunnel | 20241 | `com.vanstedum.cloudflared` | `minimoi.ai` → portal (5001) | Retire in Phase 2 (direct EC2 DNS) |
| OpenClaw gateway | 18789 | `ai.openclaw.gateway` | Node.js; OpenClaw planning agent | Stay local (manages local files) |
| Colima | — | `com.vanstedum.colima` | Docker VM runtime; must be running for DB | Replace with native Docker on EC2/Linux |

### Scheduled jobs (cron-style)

| Job | Schedule | launchd Label | Script | AWS Migration |
|-----|----------|---------------|--------|---------------|
| Daily briefing | Hourly 6 AM–6 PM (idempotent) | `com.vanstedum.curator` | `run_curator_cron.sh` | EC2 cron (Phase 2) |
| AI observations | Hourly 6 AM–6 PM (idempotent) | `com.vanstedum.curator-intelligence` | `run_intelligence_cron.sh` | EC2 cron (Phase 2) |
| Priority feed | Hourly 6 AM–6 PM (idempotent) | `com.vanstedum.curator-priority-feed` | `run_priority_feed_cron.sh` | EC2 cron (Phase 2) |
| Lesen refresh | Hourly | `com.vanstedum.lesen-refresh` | `run_lesen_refresh.sh` | EC2 cron (Phase 2) |
| Private repo sync | Nightly 02:00 | `com.user.private-sync` | `scripts/sync_private_repo.sh` | Stay local — see [Private Repository](#private-repository-mini-moi-private) |

### Telegram bots

| Bot | Token Keyring Key | Mode | launchd Label | Role |
|-----|------------------|------|---------------|------|
| minimoi_cmd_bot | `telegram` / `polling_bot_token` | Polling | `com.user.telegram-feedback-bot` | German drills, Curator feedback (Like/Dislike/Save) |
| rvsopenbot | `telegram` / `bot_token` | Polling | `com.user.cos` (handled inside CoS) | Outbound briefings + `!ops` / `!cos` commands |
| minimoi_agent_bot | separate keyring entry | — | `ai.openclaw.gateway` | OpenClaw gateway only |

### Boot-time helpers (load-and-exit)

| Label | What it does |
|-------|-------------|
| `com.vanstedum.colima` | Starts Colima at login (exits after start) |
| `com.user.docker-compose` | Runs `docker compose up -d` after login (exits after start) |
| `com.vanstedum.portal-boot-restart` | Waits 45s then kicks the portal (workaround for Cloudflare tunnel race) |

### Disabled / legacy

| Label / File | Status | Reason |
|-------------|--------|--------|
| `com.vanstedum.telegram-webhook.plist.disabled` | Disabled | Pre-dates polling mode; retained as reference |
| `homebrew.mxcl.colima.plist` | Unloaded | Conflicts with custom plist; PATH issue. Do not re-enable. |

### Port reference (all ports in use)

| Port | Service | Accessible from |
|------|---------|-----------------|
| 5001 | Portal | localhost → Cloudflare tunnel → minimoi.ai |
| 5432 | PostgreSQL (Docker) | localhost only |
| 7474 | Neo4j HTTP (Docker) | localhost only |
| 7687 | Neo4j Bolt (Docker) | localhost only |
| 8080 | Open WebUI | localhost (SSH tunnel) |
| 8766 | Curator server | localhost (proxied by portal) |
| 8767 | German domain | localhost (proxied by portal) |
| 8768 | Operations agent | localhost (proxied by portal) |
| 8769 | Chief of Staff | localhost (proxied by portal) |
| 8770 | Dev agent | localhost (proxied by portal) |
| 11434 | Ollama API | localhost |
| 18789 | OpenClaw gateway | localhost |
| 20241 | Cloudflare tunnel daemon | localhost (internal) |

---

## Docker / Colima

The Guild platform (Operations agent, Chief of Staff, Dev agent) depends on PostgreSQL and Neo4j running in Docker. Docker runs via Colima (a lightweight VM runtime for macOS).

### Containers

| Container | Port | Purpose |
|-----------|------|---------|
| `postgres-ai-agents` | 5432 | Guild DB (`personal_agents`) — `guild.*`, `jobs.*`, `research.*` schemas |
| `neo4j-context-graph` | 7474 / 7687 | Context graph (currently seeding; activates at 20+ tagged sources) |

### Launchd boot (known quirk — fixed 2026-06-19)

Colima is configured to start at login via `~/Library/LaunchAgents/com.vanstedum.colima.plist`. The Homebrew-managed `homebrew.mxcl.colima.plist` is **disabled** (unloaded) to prevent conflict.

The custom plist requires the PATH fix applied on 2026-06-19 — without it, `limactl` (`/opt/homebrew/bin/`) and the Docker CLI (`/usr/local/bin/docker`) are not found at boot and Colima silently fails. If Colima ever stops auto-starting after a macOS or Homebrew update, check:

```bash
launchctl list | grep colima
# Should show: [PID or -]  0  com.vanstedum.colima  (exit code 0)
# If exit code is 1:
tail -20 ~/Projects/personal-ai-agents/logs/colima_stderr.log
```

### Manual start / stop

```bash
# Start everything
colima start
cd ~/Projects/personal-ai-agents && docker compose up -d

# Stop (rarely needed — containers use restart: unless-stopped)
docker compose down
colima stop

# Check container logs
docker logs postgres-ai-agents --tail 20
docker logs neo4j-context-graph --tail 20
```

### Database access (read-only, for inspection)

```bash
docker exec postgres-ai-agents psql -U robert_ro -d personal_agents \
  -c "SELECT id, spec_title, status FROM guild.design_log ORDER BY last_transition_at DESC LIMIT 10;"
```

| User | Password | Level |
|------|----------|-------|
| `postgres` | `simple123` | Superuser |
| `minimoi` | `simple123` | App user (owns guild.* and jobs.*) |
| `robert_ro` | `simple123` | Read-only SELECT on guild.* and jobs.* |

TablePlus: `localhost:5432 / personal_agents / robert_ro / simple123`

---

## Services Overview

Mini-moi runs as a collection of Flask services, background agents, and scheduled jobs — all via macOS launchd. See the System Applications Inventory above for the complete table.

### Service dependency order (matters after reboot)

```
1. Colima                 → Docker runtime must be up first
2. docker compose up      → postgres-ai-agents, neo4j-context-graph
3. Guild agents           → Operations, CoS, DevAgent (need DB at startup)
4. Portal, Curator, German → Flask services (independent of DB)
5. Telegram bot           → Polling loop (independent)
6. Cloudflare tunnel      → minimoi.ai → portal:5001
```

Launchd starts everything concurrently at login. Guild agents that need DB but start before Colima finishes will have `db_available: false` and require a manual `launchctl kickstart` after Docker is confirmed up.

### Curator pipeline jobs

All three Curator jobs run hourly via `StartInterval=3600` with:
- A **time gate** (6 AM–6 PM only) — no night runs
- An **idempotency check** — run once per day even if triggered multiple times
- **Auto-retry on wake** — if the Mac was asleep, the job runs on next wake

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

### Guild Pages Empty / "db_available: false" After Reboot

**Symptom:** Guild portal shows no data. `curl http://localhost:8769/status` returns `"db_available": false`.

**Cause:** Colima and/or Docker containers were not running when the CoS agent started. The agent checks DB availability once at startup — it does not retry automatically.

**Fix:**
```bash
# Step 1 — confirm Colima is down
colima status

# Step 2 — start Colima and containers
colima start
cd ~/Projects/personal-ai-agents && docker compose up -d

# Step 3 — restart CoS so it re-checks the DB
launchctl kickstart -k gui/$(id -u)/com.user.cos

# Step 4 — verify
curl -s http://localhost:8769/status | python3 -m json.tool
# db_available should now be true
```

**If Colima fails to start** (exits with code 1):
```bash
tail -20 ~/Projects/personal-ai-agents/logs/colima_stderr.log
# Common cause: limactl or docker CLI not in PATH at boot
# The plist at ~/Library/LaunchAgents/com.vanstedum.colima.plist must have EnvironmentVariables
# with PATH including /opt/homebrew/bin and /usr/local/bin — see that file to verify
```

---

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

## Portal

**Port:** 5001 | **Label:** `com.vanstedum.minimoi-portal` | **Script:** `minimoi_portal/app.py`

Flask reverse proxy and auth layer. All traffic from `minimoi.ai` enters here. Routes to Curator (8766), German (8767), and Guild (8768–8770). Requires login for all app routes; static preview pages are public.

```bash
# Status
launchctl list | grep minimoi-portal
curl -s http://localhost:5001/ | head -5  # should return HTML (login redirect is expected for /health)

# Restart
launchctl kickstart -k gui/$(id -u)/com.vanstedum.minimoi-portal

# Logs
tail -30 ~/Projects/personal-ai-agents/logs/portal_stderr.log
```

**Known quirk:** A `com.vanstedum.portal-boot-restart` plist fires 45s after login and kicks the portal. This works around a race condition where the Cloudflare tunnel connects before the portal is ready.

---

## Guild Agents

Three autonomous agents running as persistent Flask services. All depend on PostgreSQL (Docker). See the Docker / Colima section for the boot dependency.

### Operations agent (port 8768)

**Label:** `com.user.operations` | **Script:** `domains/guild/agents/operations.py`

Health monitor. Checks disk, service status, and escalations every 5 minutes. Sends Tier 4 Telegram alerts. Reads rules from `domains/guild/config/ops_maintenance_rules.json`.

```bash
curl -s http://localhost:8768/status | python3 -m json.tool
launchctl kickstart -k gui/$(id -u)/com.user.operations
tail -20 ~/Projects/personal-ai-agents/logs/operations_stderr.log
```

### Chief of Staff (port 8769)

**Label:** `com.user.cos` | **Script:** `domains/guild/agents/chief_of_staff.py`

Conversational agent for `!cos` / `!chief` Telegram commands and the CoS portal page. Manages `cos_agenda.json`. Requires DB — check `db_available` in status response.

```bash
curl -s http://localhost:8769/status | python3 -m json.tool
# db_available must be true — if false, restart after confirming Docker is up
launchctl kickstart -k gui/$(id -u)/com.user.cos
tail -20 ~/Projects/personal-ai-agents/logs/cos_stderr.log
```

### Dev agent (port 8770)

**Label:** `com.user.devagent` | **Script:** `domains/guild/agents/dev_agent.py`

Watches `_working/` and `docs/` directories. Files design events to `guild.design_log`. Memory cap: 8,000 chars — check `memory_chars` in status. If over cap, trim `data/guild/memory/devagent_memory.md`.

```bash
curl -s http://localhost:8770/status | python3 -m json.tool
launchctl kickstart -k gui/$(id -u)/com.user.devagent
```

### Guild launchd management

```bash
# Restart all three guild agents
launchctl kickstart -k gui/$(id -u)/com.user.operations
launchctl kickstart -k gui/$(id -u)/com.user.cos
launchctl kickstart -k gui/$(id -u)/com.user.devagent

# Check all guild status at once
for svc in 8768 8769 8770; do
  echo "--- port $svc ---"
  curl -s http://localhost:$svc/status | python3 -m json.tool
done
```

---

## Telegram Bots

Three bots, each with a distinct role. Do not swap tokens or chat IDs.

| Bot | Token key | Mode | Role |
|-----|-----------|------|------|
| minimoi_cmd_bot | `polling_bot_token` | Polling (`telegram_bot.py`) | Curator feedback (Like/Dislike/Save), German drills |
| rvsopenbot | `bot_token` | Polling (inside `chief_of_staff.py`) | `!ops` + `!cos`/`!chief` commands, outbound briefings |
| minimoi_agent_bot | separate | — | OpenClaw gateway only |

**Chat ID:** `8379221702` (Robert's Telegram; set as `TELEGRAM_CHAT_ID` env var in relevant plists)

```bash
# Check feedback bot
launchctl list | grep telegram-feedback
tail -20 ~/Projects/personal-ai-agents/logs/telegram_bot_stderr.log

# Restart feedback bot
launchctl kickstart -k gui/$(id -u)/com.user.telegram-feedback-bot

# Verify token exists
cd ~/Projects/personal-ai-agents && source venv/bin/activate
python3 -c "import keyring; v=keyring.get_password('telegram','polling_bot_token'); print(v[:12]+'...' if v else '❌ missing')"
python3 -c "import keyring; v=keyring.get_password('telegram','bot_token'); print(v[:12]+'...' if v else '❌ missing')"
```

---

## Ollama (Local LLM)

**Port:** 11434 | **Label:** `homebrew.mxcl.ollama` | **Binary:** `/opt/homebrew/opt/ollama/bin/ollama serve`

Local LLM inference server. Currently running `gemma3:1b`. Used for design sessions and as a junior partner in the Learning System (Phase 0). Open WebUI available at `localhost:8080` (via SSH tunnel).

```bash
# Status
curl -s http://localhost:11434/api/tags | python3 -m json.tool

# List models
ollama list

# Run a model interactively
ollama run gemma3:1b

# Restart
brew services restart ollama
```

**AWS migration:** Stays local until Phase 5. When a `g4dn.xlarge` spot instance is provisioned, Ollama moves there and accesses larger models (13B–30B quantized). The local instance remains for low-latency dev work.

---

## Cloudflare Tunnel

**Label:** `com.vanstedum.cloudflared` | **Script:** `scripts/start_cloudflared.sh`

Exposes portal (5001) at `minimoi.ai` / `app.minimoi.ai` without a static IP. Will be retired in AWS Phase 2 when EC2 gets an Elastic IP and DNS is updated directly.

```bash
# Status
launchctl list | grep cloudflared
curl -s https://minimoi.ai/  # should respond

# Restart
launchctl kickstart -k gui/$(id -u)/com.vanstedum.cloudflared
```

---

## OpenClaw

**Port:** 18789 | **Label:** `ai.openclaw.gateway` | **Runtime:** Node.js via Homebrew

OpenClaw is the planning and documentation agent. It runs as a persistent gateway process and connects to `minimoi_agent_bot` for Telegram commands. It manages files in this repo (memory files, specs, issue drafts) but does not write implementation code.

Logs: `~/Library/Logs/openclaw/gateway.log`

---

## Private Repository (`mini-moi-private`)

**Repo:** `github.com/robertvanstedum/mini-moi-private` (private)
**Local clone:** `~/Projects/mini-moi-private`
**Purpose:** Sensitive and personal data layer for mini-moi. Keeps private data out of the public `personal-ai-agents` repo.

### Data privacy policy

**Public repo (`personal-ai-agents`)** — code only. No personal data, no memory files, no learning history, no agent context that contains private information. Safe to be public.

**Private repo (`mini-moi-private`)** — everything that should not be on public GitHub:
- Agent memory files (`cos_memory.md`, `ops_memory.md`, `devagent_memory.md`)
- Guild config with personal context (`cos_context.json`, `cos_agenda.json`)
- Curator daily output and personal signal data (`curator_signals.json`, `curator_latest.*`, `curator_radar.json`)
- German learning sessions, Anki cards, lessons, progress
- `_working/archive/` — archived planning docs

### Sync mechanism

A nightly launchd job copies changed files from `personal-ai-agents` to the local `mini-moi-private` clone and pushes to GitHub.

| Item | Detail |
|------|--------|
| Script | `scripts/sync_private_repo.sh` |
| Plist | `~/Library/LaunchAgents/com.user.private-sync.plist` |
| Schedule | Nightly at 02:00 local (≈ 07:00 UTC) |
| Mechanism | rsync for directories, diff-then-copy for files; commits only if something changed |
| Log | `logs/private_sync_stdout.log`, `logs/private_sync_stderr.log` |

The script pulls from `mini-moi-private` before writing (safe for manual runs). It does **not** sync back the other way — `personal-ai-agents` is always the source of truth for code.

### Manual sync

```bash
# Dry run — see what would change without committing
./scripts/sync_private_repo.sh --dry-run

# Live sync
./scripts/sync_private_repo.sh
```

### If the private repo clone is missing

```bash
cd ~/Projects && git clone git@github.com:robertvanstedum/mini-moi-private.git
```

The launchd job will fail silently (exit 1) until the clone exists.

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
