# Integration patch: the standing worker's scheduled trigger

**From:** Claude Code (package P).
**For:** Codex, who owns compose, deploy and the live rollout.

**Nothing here is wired into a deploy script.** It is a proposal, to adapt at approved activation (runbook: `docs/MC_STANDING_RUNBOOK.md`).

## What it adds

**One container, `mc-standing`,** runs `scripts/mc_standing/schedule_loop.py`. That is the worker's single scheduler, on America/Chicago time:
- **Every minute:** it acknowledges the owner's control document, but only when no cycle holds the lock (`f3bb8020`).
- **From 05:40 to 09:00:** it runs the day's brief, retrying no sooner than every 5 minutes until the brief is delivered or ends in another final state.
- **Outside that window:** nothing runs, so a restart never floods.

**What it uses:**
- **Writable:** its spool (`/state`) and the jobs status folder.
- **Read-only:** sanitized sources and the control folder.
- **Credentials:** the ingress token, and nothing else.
- **Hardening:** a read-only root filesystem, every Linux capability dropped, and CPU, memory and process limits.

## Decisions for Codex

1. **Image:** which image carries the repo code and its dependencies (including `tzdata`)?
2. **Ingress:** the URL and path of the standing-event ingress (`f348e40b`), and how the token is minted.
3. **Sources:** who writes the sanitized files under `/sources` (job status, health and host snapshots, release status, guest summary), and how often.
4. **Control:** the location of the control folder that the owner control endpoint (`993daea3`) writes to. This container mounts it read-only.
5. **Learning:** consolidation (04:40) isn't in this patch. It needs harness S3's eligibility, claims and admission adapters first.
6. **Registry:** at activation, merge `config/mc_standing/scheduled_jobs.candidate.json` into `config/scheduled_jobs.json`, with `active_from` set.
