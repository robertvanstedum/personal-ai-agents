# Defect: Container Data Persistence — Missing Volume Mounts

**Date:** 2026-07-14
**Status:** Open
**Priority:** Critical — data loss in production, backup system not functional, recurring
**Filed by:** Claude.ai (Fable 5) based on four instances surfaced 2026-07-13/14; scope expanded same night after backup investigation
**Build queue:** #136

---

## Summary

Two compounding failures, discovered the same night:

**Failure 1 — Missing volume mounts:** Multiple production containers write data to ephemeral storage instead of host-mounted paths. Every CI/CD deploy rebuilds the image and recreates the container, silently wiping that data. The failure mode is invisible until a user notices missing content — no error, no warning, no log entry.

**Failure 2 — Backup system not protecting data:** Spec #122 (three-tier backup) was marked closed and "live" but the Tier 1 local backup script was never written. Without Tier 1 creating `/opt/minimoi/backups/YYYY-MM-DD/`, the Tier 2 (S3) and Tier 3 (Dropbox) scripts silently log a warning and skip the file sync. S3 has received only Postgres dumps and agent logs — no curator briefings, no archive files. The safety net that was supposed to catch volume-mount gaps does not exist.

These two failures are independent but compound each other. Either one alone is serious. Together they mean: data written to containers has no host path, and even if it did, the backup would not have captured it.

---

## Impact

| Domain | Data Lost | Window | Recoverable? |
|---|---|---|---|
| Curator | Daily briefing archive (`curator_archive/`) | June 22 – July 12, 2026 | **No** — confirmed permanent gap |
| Curator | Sources history (`curator_history.json`) | June 22 – July 12, 2026 | **No** — same ephemeral path, same wipe pattern |
| Curator | Scans (`interests/2026/scans/`) | June 22 – July 12, 2026 | Partial — pre-migration dev copy rsynced |
| Curator | Deep dives (`_NewDomains/research-intelligence/data/dives/`) | June 22 – July 12, 2026 | Partial — pre-migration dev copy rsynced |
| All containers | Any data written to ephemeral paths | June 21 – July 14, 2026 | Unknown — full audit required |

The archive page currently shows pre-migration dev data (Feb–June 21), a permanent gap (June 22–July 12), then correct data from July 13 onward.

CoS (`cos_memory.md`) and `agent_logs/` were identified as at-risk and addressed separately tonight — same class of problem, caught before confirmed loss.

---

## Root Cause

When a container writes to a path that has no corresponding volume mount in `docker-compose.prod.yml`, the write lands in the container's writable (ephemeral) layer. This layer is wiped on every container recreate — which happens on every CI/CD deploy. The data appears to exist (it's readable within the same container session) but is silently lost on the next deploy.

This is not a bug in any one domain's code. It is a missing operational discipline: **no process currently ensures that every path a container writes to has a corresponding volume mount before that container goes to production.**

The `.dockerignore` file correctly excludes data directories from the image — this is right and should not change. The fix is ensuring `docker-compose.prod.yml` mounts every path that needs to survive a container recreate.

---

## Why It Keeps Recurring

Each time a new domain or feature writes data, it's built and tested locally (where bind mounts often aren't needed, since the process writes directly to the local filesystem). When containerized for production, the mount is either forgotten or assumed to already exist. There is no checklist, no automated test, and no CI/CD gate that catches a missing mount before deploy.

The pattern: **local dev works → container works within a session → deploy wipes data → user notices gap**.

---

## Backup System Gap (discovered same night — second root cause)

### What was built vs. what was designed

Spec #122 described a three-tier system:
- **Tier 1** — Local EC2 daily rsync to `/opt/minimoi/backups/YYYY-MM-DD/` (14-day retention)
- **Tier 2** — S3 daily sync from Tier 1 output + Postgres dump
- **Tier 3** — Dropbox weekly from Tier 1 output

`scripts/backup_s3.sh` and `scripts/backup_dropbox.sh` exist and are correctly written. **`backup_local.sh` (Tier 1) does not exist.** Both Tier 2 and Tier 3 check for `$LOCAL_BACKUP` and log a warning + skip if it's missing. The spec was marked closed with #122 noted as "Tier 2 S3 live" — this was inaccurate. The Postgres dump and agent logs reach S3. File-based data (curator_archive, curator_history, scans, dives) never did.

### Confirmed state after investigation

| Tier | Script | Status | What's in it |
|---|---|---|---|
| 1 — Local EC2 | Not written | **Missing** | — |
| 2 — S3 | `backup_s3.sh` ✓ | Running, partial | Postgres dumps + agent_logs only — no file data |
| 3 — Dropbox | `backup_dropbox.sh` ✓ | Running, empty | Nothing (Tier 1 output never exists) |
| Private repo | `sync_private_repo.sh` ✓ | Running, partial | `curator_signals.json`, `curator_latest.*`, memory files — no archive |

### Checked and ruled out

- **S3 bucket `minimoi-backups`**: has Postgres dumps and agent_logs; no `curator_archive` files. Verified by tracing what `backup_s3.sh` actually sends.
- **Dropbox `minimoi-backups/`**: nothing useful — Tier 1 never populated `$LOCAL_BACKUP`.
- **Private GitHub repo (`mini-moi-private`)**: `curator_archive/` not in `SYNC_PATHS`.
- **Mac local `curator_archive/`**: stops at June 21 — pre-migration dev runs only.

---

## Required Fix

### Immediate (already done tonight, confirm complete)

- [x] `curator_archive/` — volume mount added to `docker-compose.prod.yml`
- [x] `curator_history.json` — volume mount added to `docker-compose.prod.yml`
- [x] `interests/2026/scans/` — volume mount added, data rsynced
- [x] `_NewDomains/research-intelligence/data/dives/` — volume mount added, data rsynced
- [ ] `curator_history.json` — confirm Sources data gap matches or differs from archive gap (audit required)

### Backup Tier 1 (must be built before this defect can close)

Write `scripts/backup_local.sh`: daily rsync of all persistent data paths from `/opt/minimoi/data/` to `/opt/minimoi/backups/YYYY-MM-DD/`, with 14-day retention. Add to `setup_backup_cron.sh` at 2am UTC. Only after Tier 1 exists do Tier 2 and Tier 3 become real protection.

Paths to include (at minimum):
- `/opt/minimoi/data/curator_archive/`
- `/opt/minimoi/data/curator_history.json`
- `/opt/minimoi/data/curator/` (intelligence files)
- `/opt/minimoi/data/interests/` (scans)
- `/opt/minimoi/data/research-intelligence/` (dives)
- `/opt/minimoi/data/german/` (sessions, progress)
- `/opt/minimoi/data/portuguese/` (sessions)
- `/opt/minimoi/auth/` (users, guests)

### Systematic audit (this defect's actual scope)

**Task:** enumerate every path any container writes to at runtime, across all services in `docker-compose.prod.yml`. For each path, confirm either:
- A volume mount exists pointing to a persistent host path, **or**
- The path is intentionally ephemeral (e.g. a temp/cache directory) and confirmed safe to lose on recreate

Services to audit: `curator`, `cos` / `cos-scheduler`, `minimoi-portal`, `german`, `portuguese`, and any other container in the prod compose file.

Report format: a table of (service, write path, mount status, action needed).

### Process fix (prevents recurrence)

Add to the Ways of Working / build checklist: **before any new container feature that writes data goes to production, confirm the write path has a volume mount in `docker-compose.prod.yml`.** This check belongs in the spec's Definition of Done for any build that introduces persistent data in a container.

---

## What Is Not Recoverable

The June 22 – July 12 production daily briefings are permanently gone. Investigated thoroughly:
- Ephemeral container storage: wiped on each deploy ✗
- S3 `minimoi-backups`: Postgres + agent_logs only — no briefing files ✗
- Dropbox: empty (Tier 1 never ran) ✗
- Private GitHub repo: curator_archive not in SYNC_PATHS ✗
- Mac local: stops at June 21 ✗

The June 22–July 12 gap in the archive is a known, accepted permanent loss. The archive shows pre-migration dev data (Feb–June 21), this gap, then correct data from July 13 onward.

**Time-sensitive:** if the curator ran on July 14 before tonight's volume mount deployed, today's briefing may still exist in the running container. Rescue window closes when the c22ff28 pipeline finishes and recreates the container. Run from Mac:
```bash
ssh minimoi "docker exec minimoi-curator ls curator_archive/ 2>/dev/null | tail -5"
# If today's file is there:
ssh minimoi "docker cp minimoi-curator:/app/curator_archive/. /opt/minimoi/data/curator_archive/"
```

---

## Commit

| Item | Location | Actor |
|---|---|---|
| This defect | `docs/specs/defect_container_persistence_2026-07-14.md` | Claude Code |
| Build queue entry | New defect item, High priority | Claude Code |

Registration approval: Robert.

---

*Defect: Container Data Persistence + Backup System Gap · 2026-07-14 · Claude.ai (Fable 5)*
*Scope expanded same night: backup system confirmed non-functional for file data; Tier 1 never built*
