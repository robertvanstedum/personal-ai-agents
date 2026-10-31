# 02: Open PRs, reviews and merge order

All PRs are on `robertvanstedum/personal-ai-agents`, and none is merged. Every one had an independent agent review. Each review file is at `planning-studio/initiatives/INIT-2026-0007-interaction-vision/reviews/CLAUDE_INDEPENDENT_REVIEW_PR<n>_2026-09-29.md`, except #267, whose review is private in `_working/security/`. **No production merge happens without Robert's explicit OK after he has tested on dev.**

## Suggested merge order, when Robert says go
1. **#274:** a privacy bug in production voice (the mic stays open after a provider end).
2. **#267:** the CoS write guard (a production security gap; details private).
3. **#270, #281:** the voice button, then Confer on the standard voice pattern. #281 contains #270 and #274.
4. **The stack, strictly in order:** #261 → #263 → #265 → #266 → #275. Each is stacked on the previous one; merge in order and each rebases cleanly.
5. **#271.** When it lands after the stack, its #242 browser checks must add post-its on the **wall** (`/guild/build/bench`), not the floor. Copy the adaptation from the integration branch commit "Integration: #242 browser checks add the post-it on the wall".
6. **#276, #277, #280** (#280 includes #269).
7. **Documents-only:** #264, #278, #279, #282, #283.

## PR table

| PR | Head | Branch | What | Verdict | Open notes, not yet done | Production effect (classifier) |
|---|---|---|---|---|---|---|
| #261 | 516f6f4b | claude/shop-floor-markdown | Safe Markdown (markdown-it-py + nh3, 16k cap, linear time) | Approve w/ notes | The cache is keyed on the full text; the CSP blocks table-alignment style | portal |
| #263 | 88dc3c27 | claude/shop-floor-layout-slice1 | Slice 1: the layout, rail, wall, phone | Approve w/ notes | The status line in the rail strains "one urgent action" (a decision for Robert); a real-device keyboard check | portal (only /guild's alt text changes; /guild-next is not mounted in production) |
| #265 | 942d29bd | claude/shop-floor-conversations-slice2 | Conversations, and MC session per conversation, the cost probe | Approve w/ notes | Latent: a duplicate row from an old-format file (never ran on staging) | portal; MC config is dormant |
| #266 | 47c08607 | claude/workshop-observer-4a | Workshop 4a, the /guild-next redirect | Approve | The heartbeat is 9 min against a 15 min stale limit | portal |
| #275 | c8965477 | claude/mc-streaming-s1 | MC streaming S1 | Approve w/ notes (staging) | **Before production:** a provider-side spend limit (aborted streams are invisible to LiteLLM), then a stage_c proof; keep half-reported usage out of the 10% cross-check | portal, cos-bot, cos-scheduler, plus the MC image |
| #267 | 313ce4f5 | claude/cos-web-write-guard | The CoS web write guard | Approve (security) | A follow-up is tracked privately | portal, cos-bot, cos-scheduler |
| #269 | f4161cf6 | claude/operations-checkin-refresh | Operations check-in refresh (#240) | Approve w/ notes | Folded into #280 | portal, cos-bot, cos-scheduler |
| #270 | 7d3a7646 | claude/confer-voice-failed-start | Voice button, Stop during start | Approve | — | cos-bot, cos-scheduler |
| #271 | 67a05659 | claude/shop-floor-scrub-and-record-mode | Scrub and off the record (#242) | Approve w/ notes | Follow-up is #272; a mistyped card grouped with dots is kept; a removal normalises the rest of the note | portal |
| #274 | ec91bf05 | claude/voice-mic-closes-on-provider-end | Mic closes on a provider end (#273) | Approve | — | german, portuguese, system-bot, cos-bot, cos-scheduler |
| #276 | b6f92d3e | claude/usage-writer-subfolders | Usage writer folders | Approve w/ notes | — | portal, cos-bot, cos-scheduler |
| #277 | a61d9aa4 | claude/cos-proxy-timeout | Proxy 504/502, CoS turns 125 s (#262) | Approve w/ notes | **Production nginx `proxy_read_timeout` goes to 130 s**; dev's Cloudflare limit is about 100 s | portal |
| #280 | 99dba734 | claude/operations-staging-topology | The Operations agent on the Docker topology (includes #269) | Approve w/ notes (the DSN-leak fix is in) | An optional `not_watched` field in /status | portal, cos-bot, cos-scheduler (no functional change there) |
| #281 | a8e266f1 | claude/confer-voice-standard | Confer on the standard voice pattern, the Private switch, Write only | Approve (condition met) | The credential scrub over-scrubs speech ("the secret is to…"); Phase B not started | portal, curator, german, portuguese, system-bot, cos-bot, cos-scheduler (via `utils/`) |
| #282 | 168671a0 | claude/tour-capture-guild-pack | tour_capture: the hardened local sample server, Guild scenarios | Approve (security re-check) | Optional: widen the no-mention test to Dockerfiles, compose and workflows, and add the tool to .dockerignore | documents |
| #264 | 634ff1c6 | claude/verify-ignore-own-probes | verify skips its own CoS-key probes | (small, not separately reviewed) | — | documents |
| #278 | 8dbee5fc | claude/relay-test-readiness | Relay test readiness (EADDRINUSE retry) | Approve | — | documents |
| #279 | b5531101 | claude/verify-refused-helpers | verify advisory: refused helper calls | Approve w/ notes | — | documents |
| #283 | 471bec29 | docs/guild-1-1-handoff-2026-09-30 | The handoff docs (the Codex handoff, the new-session handoff, feedback, the screen pack index) | — | Add this folder to it too | documents |
| #248 | daa22be7 | claude/mc-stage-1a | The old MC stage 1a, as a partner agent inside CoS's OpenClaw | Superseded by separate containers | Close it, or leave it for Robert | — |

## Worktrees, one per branch (under `~/.worktrees/`)
- `integration`: the staging branch.
- `staging-release`: build.sh's pinned release worktree. **Don't edit it.**
- `ops-runtime`: the Operations launchd runtime. **Keep it.**
- `voiceA` (#281), `ops2` (#280), `ops240` (#269), `mic273` (#274), `layout` (#263), `md` (#261), `verify-probes` (#264), `ci-base` (#260, merged; can be removed), `handoff-docs` (#283).
- Other builder worktrees may exist; check with `git worktree list`.
