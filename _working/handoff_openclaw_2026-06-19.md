# Handoff — OpenClaw
**Date:** 2026-06-19 early morning  
**From:** Claude Code session  
**To:** OpenClaw (planning, memory, documentation layer)

---

## 1. Infrastructure fix — Colima boot failure (recurring, now resolved)

**What happened:** After Mac reboot, Guild was empty. CoS showed `db_available: false`.
Root cause: Colima (the Docker runtime) was not starting at boot — both launchd plists
failed due to missing PATH entries.

**Fix applied:**
- Updated `~/Library/LaunchAgents/com.vanstedum.colima.plist` — added `EnvironmentVariables`
  block with PATH including both `/opt/homebrew/bin` (for `limactl`) and `/usr/local/bin`
  (for `docker` CLI, which lives at `/usr/local/bin/docker` not `/opt/homebrew/bin/`)
- Unloaded `~/Library/LaunchAgents/homebrew.mxcl.colima.plist` to prevent conflict with
  the custom plist. Custom plist is now authoritative for Colima startup.
- Restarted `com.user.cos` after Docker was confirmed running — CoS requires restart any
  time it starts before the DB is available (no automatic retry in the agent).

**Documentation written:** `data/guild/memory/ops_memory.md` — "Infrastructure standing state"
section now has full diagnosis steps, fix record, and recovery procedure for this issue.

---

## 2. What OpenClaw should do with this

**No new GitHub issue needed** — this is an infrastructure quirk, not a feature or bug in
the application code. The fix is deployed and documented.

**Check ops_memory.md** before any Guild-related planning session. The Colima section there
is the authoritative reference for this recurring pattern.

**Suggested: add to post-reboot checklist** (if you maintain one, or want to propose one
to Robert). After any Mac reboot, the correct boot verification order is:
1. `colima status` — must be running
2. `docker ps` — `postgres-ai-agents` + `neo4j-context-graph` must be up
3. `curl http://localhost:8769/status` — `db_available: true`
4. If any step fails — see ops_memory.md for recovery steps

---

## 3. Also noted — devagent memory over cap

`data/guild/memory/devagent_memory.md` is at ~18,788 chars vs 8,000 cap.
The devagent reports this at startup but does not block. Recommend OpenClaw distill
this file (trim/summarize older entries) before the next build session.
