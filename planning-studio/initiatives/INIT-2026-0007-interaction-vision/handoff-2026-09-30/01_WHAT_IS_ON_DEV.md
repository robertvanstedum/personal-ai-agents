# 01: What is on dev.minimoi.ai (as of 2026-09-30 morning)

## How dev is reached
- dev.minimoi.ai reaches Robert's Mac localhost:5001 through a Cloudflare tunnel (`~/.cloudflared/config.yml`).
- On the Mac, staging is Docker on Colima (4 GiB, 3 CPUs), with data in `~/minimoi-staging`.
- **Agents never sign in** to dev.minimoi.ai or production, and never use Robert's owner login.

## Entry points
- Shop floor: https://dev.minimoi.ai/guild-next/guild/build. `/guild-next` and `/guild-next/guild` redirect here.
- The wall: `/guild-next/guild/build/bench`.
- Queue: `/guild-next/guild/build/queue`; item pages at `/guild-next/guild/build/items/<id>`.
- Workshop: `/guild-next/guild/workshop`, or from a queue item.
- Operate: `/guild-next/guild/operate`.
- Labs: `/guild-next/guild/labs`.
- CoS (Confer): https://dev.minimoi.ai/app/cos.

## What's assembled
- **Branch:** `staging/integration-2026-09-29`, worktree `~/.worktrees/integration`, pushed to origin.
- **What it merges:** the reviewed heads of #261 → #263 → #265 → #266 → #275 (the stack), plus #267, #270, #271, #274, #276, #277, #280 and #281.
- **Two integration-only test adaptations** (browser_checks_b1.py):
  - #271's #242 checks add post-its on the wall, because slice 1 moved post-its off the floor;
  - both sets of appended browser checks were kept when sections conflicted.
- **Pinned release:** `~/minimoi-staging/RELEASE` holds sha `0d2e7df3`; the release worktree is `~/.worktrees/staging-release`.

## Running containers

| Container | Image | Notes |
|---|---|---|
| minimoi-portal | portal:0d2e7df | `MINIMOI_GUILD_NEXT=1`, `MINIMOI_GUILD_MC=openclaw`, `MINIMOI_GUILD_MC_TURNS=on`, `MINIMOI_GUILD_MC_STREAM=on`, `GUILD_OPERATIONS_STATUS_URL=http://host.docker.internal:8768/status`. `data/usage` is read-only, and only `data/usage/portal` is read-write. `data/workshops` is read-only. |
| minimoi-cos-scheduler | cos-scheduler:0d2e7df | Has the #267 write guard, the #281 voice fix and the Private switch. The turn log (`state/cos.turns`) is **off**. |
| minimoi-mc-agent, minimoi-mc-relay | mc-agent:f26b7f0 | Master Craftsman: OpenClaw 2026.9.6 pinned by digest, its own Compose project (`minimoi-staging-mc`). It has a retry cap (`agent-settings.json`, maxRetries 0), streaming relay limits of 120 s, 30 s idle and 256 KB, and compaction on. |
| minimoi-cos-agent-a | cos-scheduler:agent-a-c7869a3 | CoS Agent A (OpenClaw), on its **own capped key** (`cos-agent-308c59`, $30/30d). |
| minimoi-model-gateway | cos-scheduler:model-gateway-c7869a3 | LiteLLM 1.93.1 with the key DB `litellm_keys`. MC's key is `mc-agent-e78796` ($15/30d, route `minimoi-mc-agent`). |
| postgres-ai-agents | postgres:latest | Bound to 127.0.0.1:5432. |

- **Focus:** curator, german, portuguese, cos-bot and system-bot are stopped on purpose on staging, to save memory (`state/focus.stopped`).
- **Verify:** `verify.sh` shows the "image not the pinned release" failures for services left on older tags. That's expected with partial rollouts.

## What Robert can use there
- **Shop floor (#261, #263):** a history column, a clean chat and a Build-card rail.
  - Owner notes sit on the right, tinted; MC's replies are on the left, rendered as safe Markdown.
  - One Needs you badge, which shows "?" dashed when stale.
  - The rail shows the most urgent item or a quiet line, plus "Open wall".
  - An ⓘ fold, and a blocker line above the composer.
  - Collapse at 1200 and 900 px; a phone drawer with ✕ and a scrim.
- **Conversations (#265):** new, rename, pin, archive and restore (files under `data/guild/conversations/`, 0600).
  - Each conversation is its own OpenClaw session (the key `guild:<principal>:<conv id>` hashed into `user`).
  - The legacy thread keeps its daily key.
- **Streaming (#275):** MC streams, first text in about 3 s and done in about 6 s.
  - Stop works, and a partial reply is never kept.
  - `runtime-stream` usage is recorded, with tokens null when OpenClaw reports zeros, and `cost_source: unrecorded-abort` on a Stop.
- **Workshop 4a (#266):** read-only host admission (ok / tight / blocked, with named limits), sessions on this Mac (outside Docker), and a file-first record.
  - It needs `scripts/workshop/workshop.py observe` then `sync` about every 9 minutes, otherwise it shows unknown.
  - **No launchd job exists for this yet;** that needs Robert's OK.
- **CoS:**
  - write guard (#267);
  - an honest 504 after 125 s on turn routes (#277);
  - voice (#281): CoS greets first, both sides live, the transcript saved on stop (only if `cos.sh turns on`, and never when Private);
  - a sticky **Private switch**;
  - **Speak and write / Write only** on all three voice pages, where Write only still bills the audio;
  - visible provider errors, and the mic released on every end path (#270, #274).
- **Shop floor safety (#271):** the wider payment scrub (0% false positives on the corpora), and an off-the-record mode that is fail-safe (an unknown mode holds writes).
- **Usage (#276):** each root writer has its own `data/usage/<writer>/`; only the gateway writes the top-level monthly file.
- **Systems light (#269, #280):** reads the Mac Operations agent.

## Mac-native (outside Docker)
- **`com.user.operations`** (launchd) runs `~/.worktrees/ops-runtime/domains/guild/agents/operations.py` (pinned at #280's head `99dba734`).
  - The DSN comes from the Keychain item `minimoi-dev-db/database_url`, via `core/get_secret.py` with environment_scoped and MINIMOI_ROLE=standby. There are no secrets in the plist.
  - Logs are in `~/.worktrees/ops-runtime/logs/`.
  - It watches only the services that aren't focus-stopped. In standby it restarts nothing and sends no Telegram.
  - The old 40 MB `logs/operations_stderr.log` in the root checkout can be deleted.
  - The previous plist is kept as `~/Library/LaunchAgents/com.user.operations.plist.disabled-2026-09-29`.
- **The Workshop observe/sync loop** ran inside the old session only, so it's gone now.
