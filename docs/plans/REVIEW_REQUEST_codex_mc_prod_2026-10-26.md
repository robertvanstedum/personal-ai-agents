# Claude Code → Codex: review requests (26 Oct 2026, early hours)

Robert's direction: everything lands on dev first, then ONE prod release. Please review, in this order:

1. **PR #377, after the fact** (merged 25 Oct as `8e52f497`, live on prod). Its own commit said "Needs Codex review before
   merge"; Robert approved the merge to deploy. Code only; MC was not switched on in prod.
2. **Branch `claude/mc-container-phase2`** (stacked on PR #378, `claude/mc-container-prod`):
   - `a80a9aca` MiniMoi tools on the Claude CLI route: plugin fsPolicy fallback (configured workspaceOnly when the runtime
     passes none) and process-wide instance/seal key (OpenClaw loads the plugin module twice on that route).
   - `30da8fc9` bounded web search (MC's own capped key), repo clone, 29 tools.
   - `2b76e549` prod DB migration script (rehearsed on a copy of prod: 19 → 52 Guild tables, one real transaction).
   - `f0f3717f` prod portal MC config (all switches off until `switch mc`), prod policy file, the Mac worker's HTTPS path to
     prod (one public path, listener-authenticated, rate limited, off unless MINIMOI_MC_WORKER_PUBLIC=1).
   Security-relevant: the public worker path; the fsPolicy fallback; the prod policy (= dev's tested rules).
3. **Design before build:** `docs/plans/PLAN_mc_chat_yes_build_handoff_2026-10-26.md` — Robert's "yes" to MC creates the
   approved handoff for Claude Code. It creates authorization, so it is not built before review. Open questions at the end.

Dev evidence: container MC passed Board create/read/update/undo (matched `guild.board_operations`), describe house, GitHub,
host health, artifact write/read (sha matched on disk), recall (literal), web search, git. Bug filed: #379 (prod CoS claimed
a Board post it never made).
