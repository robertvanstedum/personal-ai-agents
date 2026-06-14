# Handoff — Colima Auto-Start + Portal Boot Restart
*mini-moi · Infrastructure*
*Created: 2026-06-14 — Claude Code*
*For: OpenClaw*

---

## What changed

After a Mac reboot (2026-06-14), Colima (the Docker runtime) did not
auto-start. This left Postgres unreachable, causing the Guild build log
and queue pages to appear empty. Two new launchd plists were added to fix
this permanently.

---

## New plists (both loaded and active)

### 1. `com.vanstedum.colima`
**File:** `~/Library/LaunchAgents/com.vanstedum.colima.plist`
**What it does:** Runs `colima start` at login. Colima starts the Docker
daemon (~15s), then exits — the VM stays running independently.

**Important:** Do NOT also run `brew services start colima`. The custom
plist is the managed path. `brew services` and this plist conflict.

### 2. `com.vanstedum.portal-boot-restart`
**File:** `~/Library/LaunchAgents/com.vanstedum.portal-boot-restart.plist`
**What it does:** Sleeps 45 seconds after login (allowing Colima +
Postgres to become ready), then runs `launchctl kickstart -k` on the
portal. This ensures the portal has a live DB connection on cold boot.

**Why needed:** The portal starts immediately at login (via its own
plist), but Colima + Postgres take ~20-30s. Without the restart, the
portal's first start fails to connect and shows empty build log/queue.
The 45s delay is conservative — gives Colima, Docker, and Postgres full
time to be ready.

---

## Startup order (full sequence)

See `docs/SERVICES.md` — Startup Order section — for the authoritative
sequence. Short version:

```
1. com.vanstedum.colima              → Docker daemon (~15s)
2. com.user.docker-compose           → postgres + neo4j (waits for socket)
3-7. Guild agents, Curator, Portal   → start immediately (resilient to DB delay)
8. com.vanstedum.portal-boot-restart → restarts portal at +45s with live DB
9. com.user.telegram-feedback-bot    → polling
```

---

## docs/SERVICES.md updated

Both new plists are now documented in `docs/SERVICES.md` (the canonical
services reference). The Colima section notes the `brew services`
conflict. The startup order table and sequence are updated.

---

## No further action needed

Both plists are loaded. Verify on next reboot:
1. `docker ps` — both containers running without manual intervention
2. Guild build log and queue populated without manual portal restart
3. `launchctl list | grep colima` — shows `com.vanstedum.colima`

---

*Handoff · Infrastructure · 2026-06-14*
