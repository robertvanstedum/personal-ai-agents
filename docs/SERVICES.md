# mini-moi Services Reference
**Last updated:** 2026-06-14
**Purpose:** Canonical list of all always-on services, how they start, and what manages them.

---

## Docker Infrastructure

### Runtime: Colima (not Docker Desktop)
Docker Desktop is GUI-only and not required at runtime. Colima provides a
headless Docker daemon managed by Homebrew's launchd integration.

| Item | Detail |
|---|---|
| Install | `brew install colima` |
| Service | `com.vanstedum.colima` launchd plist (runs `colima start` at login) |
| Socket | `unix:///Users/vanstedum/.colima/default/docker.sock` |
| Context | `colima` (active — set by `docker context use colima`) |
| Config | Default: VZ driver, aarch64, 20GB disk |

**Docker Desktop** can remain installed for occasional GUI inspection but must NOT
be relied on for daemon availability. Colima owns the daemon.

**Note:** Do NOT use `brew services start colima` — the custom launchd plist
(`com.vanstedum.colima`) is the managed path. `brew services` and launchd
conflict and cause double-start issues.

### Container startup
After Colima is ready, containers are brought up by:
- `~/Library/LaunchAgents/com.user.docker-compose.plist`
- Which calls `scripts/start_docker_services.sh`
- Script waits up to 60s for Docker socket before running `docker compose up -d`

### Containers (docker-compose.yml)
| Container | Image | Ports | Restart |
|---|---|---|---|
| `postgres-ai-agents` | postgres:latest | 5432 | unless-stopped |
| `neo4j-context-graph` | neo4j:latest | 7474, 7687 | unless-stopped |

**Volume note (Postgres 18+):** Mount is `/var/lib/postgresql` (not `/data`).
Postgres 18 places data in a subdirectory of this mount. Do not change to `/data`.

### Database bootstrap (fresh container)
```bash
docker exec -i postgres-ai-agents psql -U postgres < domains/guild/db/init_db.sql
docker exec -i postgres-ai-agents psql -U minimoi -d personal_agents < domains/guild/db/schema.sql
docker exec -i postgres-ai-agents psql -U minimoi -d personal_agents < domains/guild/db/schema_phase4.sql
docker exec -i postgres-ai-agents psql -U minimoi -d personal_agents < domains/guild/db/schema_phase5.sql
```

---

## Application Services (launchd)

All services run under `~/Library/LaunchAgents/`. Log files in `logs/`.

| Label | File | Port | Scope |
|---|---|---|---|
| `com.vanstedum.colima` | runs `colima start` | — | Docker runtime — starts first, everything depends on it |
| `com.user.docker-compose` | `scripts/start_docker_services.sh` | — | Waits for Colima socket, then `docker compose up -d` |
| `com.vanstedum.portal-boot-restart` | `launchctl kickstart` after 45s sleep | — | Restarts portal after Colima+Postgres are ready |
| `com.user.operations` | `domains/guild/agents/operations.py` | 8768 | Guild Operations agent |
| `com.user.cos` | `domains/guild/agents/chief_of_staff.py` | 8769 | Guild Chief of Staff |
| `com.user.devagent` | `domains/guild/agents/dev_agent.py` | 8770 | Guild Design/Dev agent |
| `com.user.curator-server` | `curator_server.py` | 8766 | Curator Flask service |
| `com.vanstedum.minimoi-portal` | `minimoi_portal/` | 5001 | Portal (Cloudflare tunnel target) |
| `com.user.telegram-feedback-bot` | `telegram_bot.py` | — | minimoi_cmd_bot polling (German + Curator) |
| `com.user.private-sync` | `scripts/sync_private_repo.sh` | — | Nightly 02:00 — syncs memory/config to mini-moi-private |

### Telegram bots
| Bot | Token keyring key | Role |
|---|---|---|
| rvsopenbot | `telegram / bot_token` | CoS chat + `!ops` `!cos` `!chief` `!dev` — polling via CoS |
| minimoi_cmd_bot | `telegram / polling_bot_token` | German drills, Curator delivery |
| minimoi_agent_bot | separate key | OpenClaw gateway — do not use for delivery |

### Cron jobs (launchd)
| Label | Schedule | Script |
|---|---|---|
| `com.vanstedum.curator` | 07:00 daily | Curator run |
| `com.vanstedum.curator-intelligence` | 07:30 daily | Intelligence layer |
| `com.vanstedum.curator-priority-feed` | varies | Priority feed update |

---

## Intelligence Loops (APScheduler inside CoS)

| Loop | Name | Schedule | Source |
|---|---|---|---|
| A | career_focus_scout | 06:00 + 18:00 daily | Tavily + Indeed RSS |
| B | german_watch | Sunday 09:00 | sessions dir + Tavily |
| C | curator_scout | Sunday 10:00 | Tavily per scout_for terms |
| D | novelty_watch | 1st + 15th 08:00 | Tavily per watch_terms |

Status: `curl http://localhost:8769/loops`

---

## Startup Order at Login

```
1. com.vanstedum.colima              — runs `colima start` (Docker daemon, ~15s)
2. com.user.docker-compose           — waits for socket, then: postgres + neo4j
3. com.user.operations               — Guild Operations (8768)
4. com.user.cos                      — Chief of Staff (8769) — APScheduler loops inside
5. com.user.devagent                 — Design/Dev agent (8770)
6. com.user.curator-server           — Curator Flask (8766)
7. com.vanstedum.minimoi-portal      — Portal (5001) — starts immediately but DB not ready yet
8. com.vanstedum.portal-boot-restart — waits 45s, then restarts portal with live DB connection
9. com.user.telegram-feedback-bot    — minimoi_cmd_bot polling
```
launchd does not guarantee order — services must be resilient to dependencies being
temporarily unavailable. All Guild agents use file fallback when DB is down.
The portal-boot-restart (step 8) handles the specific case where the portal starts
before Postgres is ready and shows empty build log/queue pages.

---

## Private Repository (mini-moi-private)

Repo: `git@github.com:robertvanstedum/mini-moi-private.git`
Local clone: `~/Projects/mini-moi-private/`
**Rule: never contains code — only data and memory files.**

### What's synced
| Path | Description |
|---|---|
| `data/guild/memory/*.md` | cos_memory, ops_memory, devagent_memory |
| `data/guild/cos_agenda.json` | CoS recommendations queue |
| `domains/guild/config/cos_context.json` | Robert's goals + CoS context |
| `_working/archive/` | Superseded handoff/spec docs |

### Sync schedule
- **Nightly at 02:00** via `com.user.private-sync` launchd service
- **Manual run:** `bash scripts/sync_private_repo.sh`
- **Dry-run:** `bash scripts/sync_private_repo.sh --dry-run`

The Design/Dev agent (`dev_agent.py`) detects when archive files are uncommitted
and notifies CoS — but the actual push is always done by this script.

---

## Health Check

```bash
# Containers
docker ps

# Guild agents
curl http://localhost:8768/health   # Operations
curl http://localhost:8769/health   # CoS
curl http://localhost:8770/health   # Design/Dev

# Loops
curl http://localhost:8769/loops

# Telegram (send from rvsopenbot channel)
!ops status
!dev
```
