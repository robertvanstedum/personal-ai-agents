# Defect: Container Data Persistence — Missing Volume Mounts

**Date:** 2026-07-14
**Status:** Open
**Priority:** High — data loss in production, recurring
**Filed by:** Claude.ai (Fable 5) based on four instances surfaced 2026-07-13/14
**Build queue:** Register as defect, assign number at registration

---

## Summary

Multiple production containers write data to ephemeral storage instead of host-mounted paths. Every CI/CD deploy rebuilds the image and recreates the container, silently wiping that data. The failure mode is invisible until a user notices missing content — no error, no warning, no log entry.

This has surfaced four times in one session. Each instance was treated as a one-off fix. This defect tracks the root cause and requires a systematic audit, not another one-off patch.

---

## Impact

| Domain | Data Lost | Window | Recoverable? |
|---|---|---|---|
| Curator | Daily briefing archive (`curator_archive/`) | June 22 – July 12, 2026 | **No** — permanent gap |
| Curator | Sources history (`curator_history.json`) | June 22 – July 12, 2026 | Unknown — audit required |
| Curator | Scans (`interests/2026/scans/`) | June 22 – July 12, 2026 | Partial — dev copy rsynced up |
| Curator | Deep dives (`_NewDomains/research-intelligence/data/dives/`) | June 22 – July 12, 2026 | Partial — dev copy rsynced up |

CoS (`cos_memory.md`) and `agent_logs/` were identified as at-risk and addressed separately tonight. They are not in the table above because they were caught before data loss occurred — but they are the same class of problem.

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

## Required Fix

### Immediate (already done tonight, confirm complete)

- [x] `curator_archive/` — volume mount added to `docker-compose.prod.yml`
- [x] `curator_history.json` — volume mount added to `docker-compose.prod.yml`
- [x] `interests/2026/scans/` — volume mount added, data rsynced
- [x] `_NewDomains/research-intelligence/data/dives/` — volume mount added, data rsynced
- [ ] `curator_history.json` — confirm Sources data gap matches or differs from archive gap (audit required)

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

The June 22 – July 12 production daily briefings are permanently gone. They were written to ephemeral container storage and wiped on each deploy during that window. This is a known, accepted permanent gap. The archive page in production currently shows pre-migration dev data (Feb – June 21) followed by this gap, then correct persistent data from July 13 onward.

---

## Commit

| Item | Location | Actor |
|---|---|---|
| This defect | `docs/specs/defect_container_persistence_2026-07-14.md` | Claude Code |
| Build queue entry | New defect item, High priority | Claude Code |

Registration approval: Robert.

---

*Defect: Container Data Persistence · 2026-07-14 · Claude.ai (Fable 5)*
*Four instances in one session — root cause documented, systematic audit required*
