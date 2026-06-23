# Handoff → OpenClaw: Operations monitoring — Mac cleanup + EC2 spec
*From: Claude Code · Date: 2026-06-22*
*Trigger: Colima disk incident (Mac hit 90% / 1.9GB free; `~/.colima` ballooned to 62GB, reclaimed 38GB via fstrim). Surfaced gaps in how disk/health monitoring is wired.*

## Robert's direction (decisions made — not open questions)
- **OpenClaw may keep its own independent checks.** It does more than minimoi; **no hard dependency** on CoS/minimoi for OpenClaw's own monitoring. **Some overlap with CoS is acceptable** and expected.
- **CoS should also have a monitoring task** of its own.
- **Mac monitoring needs a coordinated cleanup** — not necessarily a single owner, but resolve the confusing split (below).
- **EC2 operations/monitoring update: spec it now, build soon-but-later** (not urgent, but planned).

## Current state — Mac disk/health monitoring (the "wrong bot" confusion)
| Function | Runs | Alerts on bot |
|---|---|---|
| Proactive disk alerts (85%/95%, every 5 min) | Operations agent `com.user.operations` (Mac launchd), `_check_disk()` in `domains/guild/agents/operations.py:332` | **minimoi_system_bot** (`get_system_token`) |
| `!ops disk` interactive query | CoS `domains/guild/agents/chief_of_staff.py` | **minimoi_cos_bot** |

Issues:
- Disk info is **split across two bots** — proactive alerts on system_bot, interactive `!ops disk` on cos_bot. Robert reads the proactive "TIER 4 — Operations" alert as "CoS." Pick one clear channel for ops alerts.
- **Stale comment** at `chief_of_staff.py:636` says "via rvsopenbot" but the function takes `token` as a param (actually cos_bot). Fix/remove.
- The Operations agent monitors only the Mac's `/`.

## Current state — EC2
- **No disk/health monitor at all.** The Operations agent is Mac-only. Nothing watches EC2's disk.
- EC2 accumulates dangling docker images per deploy (each `pull latest` orphans the previous). Reclaimed with `docker system prune -af` (native Linux — frees EBS directly; **no Colima/fstrim** on EC2, that's Mac-only).

## Work items (for OpenClaw to spec → issues)
1. **[Mac, soon] Coordinated cleanup of disk alerting.** One visible Operations monitoring script; one clear bot for ops alerts; remove stale `rvsopenbot` comment. OpenClaw's own independent checks + a CoS monitoring task may coexist (overlap OK).
2. **[EC2, soon-but-later] Operations/monitoring update — needs a spec.** Stand up disk + service-health monitoring on EC2; route notifications through **CoS → Robert**. Include `docker system prune -af` as routine EC2 maintenance + a disk threshold alert. (EC2 is production minimoi.ai; currently unmonitored.)

## Reference — Colima maintenance for OPERATIONS.md (protected; OpenClaw to add)
> **Colima disk maintenance (Mac node only):** sparse VM disk grows with Docker writes and does NOT auto-return freed space (seen: 62GB, host 90%). `docker system prune` frees inside the VM; `fstrim` returns it to macOS.
> - After heavy build sessions: `docker system prune -af && colima ssh -- sudo fstrim -av`
> - Recurring: weekly scheduled `fstrim`. Cap is `disk: 60 GiB` in `~/.colima/_lima/colima/colima.yaml`.
> - EC2 differs: native Docker, no Colima — `docker system prune -af` periodically; **EC2 disk not currently monitored.**
