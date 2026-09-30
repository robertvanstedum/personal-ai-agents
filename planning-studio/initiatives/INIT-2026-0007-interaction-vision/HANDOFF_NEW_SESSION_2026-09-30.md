# New session handoff: Guild 1.1 on dev (written 2026-09-29, late)

**Read first in the new Claude Code session.** It pairs with the Codex handoff: `planning-studio/initiatives/INIT-2026-0007-interaction-vision/HANDOFF_TO_CODEX_GUILD_1_1_DEV_2026-09-30.md`. That one has the full PR table, the reviews, the risks and the next steps.

## State at the break

- **dev.minimoi.ai** runs the staging branch `staging/integration-2026-09-29` (release `0d2e7df`), in the worktree `~/.worktrees/integration`. The branch merges only reviewed PR heads: #261, #263, #265, #266, #267, #270, #271, #274, #275, #276, #277, #280 and #281.
  - The portal and cos-scheduler run `0d2e7df`, MC runs `f26b7f0`, and CoS Agent A and the gateway run `c7869a3`.
  - The entry point is https://dev.minimoi.ai/guild-next/guild/build, and CoS is at https://dev.minimoi.ai/app/cos.
- **Production is unchanged,** apart from #260 (the CI fix, `954546a`, all 9 services). Robert: no production merges until he has tested and refined on dev.
- **Open PRs #261–#282** are all independently reviewed (approved, or approved with notes); the reviews are in `reviews/`. **Nothing is merged.**
- **The Mac Operations launchd job** (`com.user.operations`) runs from `~/.worktrees/ops-runtime`, pinned at #280's head `99dba734`. Keep that worktree. Its DSN comes from the Keychain item `minimoi-dev-db/database_url`, and nothing secret is in the plist. The old 40 MB log `logs/operations_stderr.log` in the root checkout can be deleted.
- **The Workshop host reading** was refreshed by a loop inside this session, and it stops when the session ends. After that, the Workshop shows "unknown", which is honest. A permanent observe/sync launchd job needs Robert's OK.
- **The staging CoS turn log is OFF.** Robert turns it on with `scripts/staging/cos.sh turns on`.
- **Colima is up** (staging), with 17 GB of disk free. Each build leaves 5–8 GB of cache, so prune after every rollout: remove unused `minimoi-staging/*` tags, run `docker builder prune -af`, then `colima ssh -- sudo fstrim /mnt/lima-colima`.

## Waiting on Robert
- The three short CoS voice tests on dev: OpenAI Speak and write, OpenAI Write only, Grok Write only.
- **Conversations:** new ones keep their context until archived, and the old thread still starts fresh daily. Is that OK?
- **The status line:** keep it in the rail, or move it to the bottom strip?
- **CoS streaming:** (a) is it the default only where its switch is on? (b) is voice non-streaming for now?
- The permanent Workshop launchd job.
- M0 (the root checkout and the old worktrees, which is where most of the disk space is).
- A provider-side spend limit before production streaming.

## Waiting on Codex
- The diff reviews.
- The Workshop design note (it gates 4b).
- The streaming spec v0.3.1.
- The room-differentiation design, i.e. Robert's "Shop floor, Wall and Workshop are all the same".

## Key docs
- Robert's feedback: `planning-studio/…/documents/ROBERT_DEV_FEEDBACK_2026-09-29.md`.
- The screen pack: `planning-studio/…/review-package-2026-09-30/Guild_1_1_dev_screens_2026-09-30.pdf`. It predates #281; re-run it via `scripts/tools/tour_capture` (PR #282) for the voice screens.
- The status log: `planning-studio/…/CLAUDE_CODE_STATUS_2026-09-27.md`.
- Private security notes: `_working/security/cos-confer-csrf-2026-09-29.md`, and `review-pr267…`.

## Working conventions from this session
- **Roles:** a builder agent writes code; a different agent reviews every PR; I coordinate, roll out to staging and report.
- **Rollout:** stage from the integration branch with `scripts/staging/build.sh <branch> --reviewed-branch`, then `up.sh <services>`. For MC, use `mc.sh build` and `mc.sh up`.
- **Telegram status:** use the `tg.py` helper in the session scratchpad. It reads the Keychain `telegram/bot_token` and `telegram/chat_id` and never prints them. Recreate it if the scratchpad is gone.
- **Review packs:** always use the shared `scripts/tools/tour_capture` tool.
