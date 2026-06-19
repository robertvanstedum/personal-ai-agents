# Operations Memory — distilled
Last distilled: 2026-06-08 (initialized) | Size: ~200 chars

## Infrastructure standing state

### Colima / Docker boot dependency (critical — recurring issue)
**Symptom after reboot:** Guild DB unavailable, CoS shows `db_available: false`, Guild portal pages empty.
**Root cause:** Colima (Docker runtime) does not auto-start reliably. Two plists existed but both failed:
- `homebrew.mxcl.colima.plist` — PATH missing `/usr/local/bin` where `docker` CLI lives
- `com.vanstedum.colima.plist` — no PATH at all, `limactl` not found

**Fix applied 2026-06-19:** Added correct PATH to `com.vanstedum.colima.plist`:
`/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`
Unloaded `homebrew.mxcl.colima.plist` (conflicts with custom plist). Custom plist is now authoritative.

**How to check after any reboot:**
1. `colima status` — must say "running"
2. `docker ps` — must show `postgres-ai-agents` and `neo4j-context-graph`
3. `curl -s http://localhost:8769/status | python3 -m json.tool` — `db_available` must be `true`
4. If Colima is down: `colima start && docker compose up -d` from repo root, then restart CoS: `launchctl kickstart -k gui/$(id -u)/com.user.cos`

**launchd boot order dependency:** All guild agents (operations, cos, devagent) start at login before Colima finishes. If DB is down at agent start, CoS stays `db_available: false` for the entire session — a restart of `com.user.cos` is required after Colima/Docker are confirmed up.

**Plist status (as of 2026-06-19):**
- `com.vanstedum.colima` — loaded ✅ (exit 0 at last run)
- `homebrew.mxcl.colima` — unloaded (disabled to prevent conflict)

## Recent actions taken
[What Operations has done autonomously — tier, action, outcome — pruned to 30 days]

## Open escalations
[Items currently in CoS queue or awaiting resolution]

## Maintenance calendar notes
[Anything unusual about scheduled jobs, crons, or launchd services]

## Thresholds in effect
[Any custom thresholds or rules added since baseline ops_maintenance_rules.json]

### 2026-06-09 01:39 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 96.5% used, 8.0 GB free
- Checks run today: 1
- Open escalations: 1

### 2026-06-09 01:40 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 96.5% used, 8.0 GB free
- Checks run today: 1
- Open escalations: 2

### 2026-06-09 01:41 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 95.2% used, 11.0 GB free
- Checks run today: 1
- Open escalations: 3

### 2026-06-09 03:04 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 94.8% used, 12.0 GB free
- Checks run today: 15
- Open escalations: 17

### 2026-06-09 11:11 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 87.0% used, 29.8 GB free
- Checks run today: 1
- Open escalations: 0

### 2026-06-10 06:04 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 88.6% used, 25.9 GB free
- Checks run today: 151
- Open escalations: 0

### 2026-06-11 03:51 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 88.5% used, 26.2 GB free
- Checks run today: 348
- Open escalations: 0

### 2026-06-12 03:41 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 89.8% used, 23.3 GB free
- Checks run today: 492
- Open escalations: 0

### 2026-06-13 03:04 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 90.4% used, 22.0 GB free
- Checks run today: 675
- Open escalations: 0

### 2026-06-13 13:43 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 85.5% used, 33.0 GB free
- Checks run today: 1
- Open escalations: 0

### 2026-06-14 03:03 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 88.1% used, 27.3 GB free
- Checks run today: 161
- Open escalations: 0

### 2026-06-15 10:25 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 88.5% used, 26.1 GB free
- Checks run today: 285
- Open escalations: 0

### 2026-06-16 05:07 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 89.2% used, 24.6 GB free
- Checks run today: 456
- Open escalations: 0

### 2026-06-17 05:01 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 90.7% used, 21.1 GB free
- Checks run today: 725
- Open escalations: 0

### 2026-06-17 16:26 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 91.3% used, 19.9 GB free
- Checks run today: 1
- Open escalations: 0

### 2026-06-18 05:07 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 94.4% used, 12.7 GB free
- Checks run today: 111
- Open escalations: 0

### 2026-06-19 05:03 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 95.2% used, 10.9 GB free
- Checks run today: 326
- Open escalations: 0

### 2026-06-19 05:39 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 92.0% used, 18.2 GB free
- Checks run today: 1
- Open escalations: 0

### 2026-06-19 05:57 UTC
- Services: curator-server ✅, german-html-server ✅, minimoi-portal ✅
- Disk: 82.1% used, 40.9 GB free
- Checks run today: 1
- Open escalations: 0
