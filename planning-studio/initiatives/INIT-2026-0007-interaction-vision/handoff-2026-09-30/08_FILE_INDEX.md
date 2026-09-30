# 08: File index

`INIT` means `planning-studio/initiatives/INIT-2026-0007-interaction-vision/` in the root checkout. That folder is untracked locally; the key handoff files are also on GitHub in PR #283.

## Handoffs and status
- `INIT/HANDOFF_TO_CODEX_GUILD_1_1_DEV_2026-09-30.md`: the Codex handoff (what's on dev, the PRs, the asks).
- `INIT/CLAUDE_CODE_STATUS_2026-09-27.md`: the running status log, newest first.
- `_working/HANDOFF_NEW_SESSION_2026-09-30.md`: the short version of this folder.
- `_working/handoff-2026-09-30/`: this folder.
- `INIT/handoff-guild-1-1-2026-09-29/bundle/START_HERE.md`: Codex's original Guild 1.1 handoff.

## Contract, specs and design
- `INIT/documents/CLAUDE_BUILD_NOW_GUILD_DEV_WORKSHOP_2026-09-29.md`: the build contract, with slices 0–5.
- `INIT/documents/SPEC_STREAMING_CHAT_COS_MC_v0.2_2026-09-29.md` plus `…v0.3_2026-09-29.md` (v0.3.1 notes are folded in): the streaming spec.
- `INIT/documents/WORKSHOP_DESIGN_NOTE_2026-09-29.md`: the Workshop design, awaiting Codex; it gates 4b.
- `INIT/documents/USAGE_RECORD_DESIGN_NOTE_2026-09-29.md`: usage record v1.
- `INIT/documents/COS_MULTI_CONVERSATION_CONTRACT_2026-09-29.md`: CoS multi-conversation, and Private inheritance.
- `docs/specs/spec_160_agent_memory_owned_by_minimoi_2026-09-28.md`: Spec 160 (agent memory, Private mode, the turn log).
- `docs/specs/spec_158_collaboration_rooms_cos_beta_v09_2026-09-21.md` §7: the local launcher and isolation.
- `INIT/documents/DECISIONS_MC_BACKEND_v0.2_ROBERT_2026-09-27.md`: all of Robert's decision addenda, including build-now.
- `INIT/documents/ROBERT_B1_WALKTHROUGH_FEEDBACK_2026-09-27.md`: F1–F12.
- `INIT/documents/ROBERT_DEV_FEEDBACK_2026-09-29.md`: tonight's feedback plus the screen-pack findings.

## Reviews
- `INIT/reviews/CLAUDE_INDEPENDENT_REVIEW_PR<n>_2026-09-29.md` for #261, #263, #265, #266, #269, #270, #271, #274, #275, #276, #277, #278, #279, #280, #281, #282.
- `INIT/reviews/CLAUDE_INDEPENDENT_REVIEW_STREAMING_SPEC_v0.1/v0.2_2026-09-29.md`.
- **Private:** `_working/security/review-pr267-cos-write-guard-2026-09-29.md` and `_working/security/cos-confer-csrf-2026-09-29.md`.

## Screen pack
- `INIT/review-package-2026-09-30/Guild_1_1_dev_screens_2026-09-30.pdf`: 36 pages of sample data, integration `5c57bdc`, made before #281.
- `INIT/review-package-2026-09-30/INDEX.md`.
- `_working/guild-1-1-capture/run-20260929-210608/`: the raw runs.
- `scripts/tools/tour_capture/scenarios/guild_1_1_*.json`: on the #282 branch.

## Probes and evidence
- `_working/mc-stream-s1-probe/`: the no-spend streaming probes. `run3.log` is before the retry cap; `run4.log` is with it.
- `_working/guild-1-1-slice*-screenshots/`, `_working/guild-1-1-streaming-s1-screenshots/`: the builder's evidence.

## Claude's memory (local)
- `~/.claude/projects/-Users-vanstedum-Projects-personal-ai-agents/memory/MEMORY.md`: the index. Its relevant entries are optionality, file-first, deploy-only-what-changed, keys, parity (plus voice), the review-pack tool, Telegram status, and the INIT-0007 project note.
