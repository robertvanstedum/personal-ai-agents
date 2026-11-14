# START HERE: Guild 1.1 new Claude Code session (2026-09-30)

This folder is the complete handoff from the Sep 29 build session, which was long. It built "Guild 1.1 on dev", following Robert's instruction: "Build it all now. Let's skip prototype. I want to see in Dev a working new version that I can live with for now."

**Run the new session locally** (the Code tab on Robert's Mac, working directory `~/Projects/personal-ai-agents`), not in the cloud. Staging (Colima), dev.minimoi.ai, the worktrees and the Operations job exist only on this Mac.

## Paste this to start the new session

> New thread for Guild 1.1, 2026-09-30. Read the handoff folder `_working/handoff-2026-09-30/` in order, starting with `00_START_HERE.md`. Then check the live state against `01_WHAT_IS_ON_DEV.md`:
> - `git fetch`;
> - the PR heads;
> - `docker ps`;
> - `~/minimoi-staging/RELEASE`;
> - `launchctl print gui/$(id -u)/com.user.operations`.
>
> Report any drift.
> Codex is reviewing the diffs of #261–#282. Read and plan only; do not push to those PRs until I pass on Codex's findings.
> "Complete 1.1" means three things, in this order:
> 1. act on Codex's findings;
> 2. the refinement list in `06_NEXT_STEPS.md`;
> 3. the room-differentiation design pass once Codex's design is in.
>
> Give me a plan to approve before building.

## Reading order

| File | What |
|---|---|
| `01_WHAT_IS_ON_DEV.md` | Exactly what runs on dev.minimoi.ai, and how it's assembled |
| `02_OPEN_PRS_AND_REVIEWS.md` | Every open PR, with head, review verdict, open notes, production effect and suggested merge order |
| `03_ROBERT_DECISIONS_AND_FEEDBACK.md` | Standing principles, decisions made, decisions pending, his dev feedback |
| `04_STAGING_RUNBOOK.md` | How to build, roll out, verify, prune disk, and run MC, CoS turns, Workshop and Operations |
| `05_RULES_AND_WAYS_OF_WORKING.md` | Security constraints, roles, review cadence, spend, Telegram status |
| `06_NEXT_STEPS.md` | The ordered backlog, and what is blocked on whom |
| `07_KNOWN_ISSUES_AND_RISKS.md` | Things that are true, and matter before production |
| `08_FILE_INDEX.md` | Where every spec, review, pack and note lives |

## The situation in 10 lines

1. dev.minimoi.ai runs the staging branch **`staging/integration-2026-09-29`** (release **`0d2e7df`**), a merge of reviewed PR heads only.
2. **Production is untouched,** except #260 (the CI fix, all 9 services on `954546a`). Robert: **no production merges until he has tested and refined on dev.**
3. **PRs #261–#282 are all independently reviewed,** each approved or approved with notes. **None is merged.** #283 is the docs-only handoff PR.
4. **Codex has the Codex handoff** and is reviewing the diffs, the Workshop design note (it gates 4b), the streaming spec v0.3.1, and the room-differentiation design.
5. **Robert's biggest feedback:** the Shop floor, the wall and the Workshop are "all the same, essentially". The screen pack confirms it.
6. **CoS voice was just rebuilt** to follow the standard German/Portuguese voice pattern (#281). Robert has **not yet done the 3 short voice tests** on dev.
7. **The Mac Operations launchd job** runs from `~/.worktrees/ops-runtime` (#280). Keep that worktree.
8. **The disk is tight,** at about 13 GB free. Prune after every staging build (see the runbook).
9. The builder and reviewer agents from the old session are gone. Spawn new ones as needed; see `05`.
10. **Telegram status** to Robert uses a helper script. Recreate it from `04` if it's missing.
