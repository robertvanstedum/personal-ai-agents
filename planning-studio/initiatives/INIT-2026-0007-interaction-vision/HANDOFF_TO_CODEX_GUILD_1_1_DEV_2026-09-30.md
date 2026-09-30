# Handoff to Codex: Guild 1.1 on dev (September 29–30, 2026)

**From:** Claude Code, the only repo editor; builds were done by a builder agent, and every PR was reviewed by a separate agent.
**For:** Codex, which reviews the actual diffs and the design questions; and Robert, who makes the owner decisions.
**Contract:** `documents/CLAUDE_BUILD_NOW_GUILD_DEV_WORKSHOP_2026-09-29.md`. Robert said: "Build it all now, skip prototype… a working new version in dev." Nothing new reached production except #260, the CI fix Robert approved.

## 1. What is live on dev.minimoi.ai

Staging runs the branch **`staging/integration-2026-09-29`**, which only merges reviewed PR heads. The release is in `~/minimoi-staging/RELEASE`. The entry point is **https://dev.minimoi.ai/guild-next/guild/build**; `/guild-next` redirects there.

| Area | What Robert can use | PR(s) |
|---|---|---|
| Shop floor | History, then a clean chat, then a rail: the Build card, the most urgent item and "Open wall". Owner notes sit on the right, MC's replies on the left and rendered as Markdown. A Needs you badge (stale shows "?"), the ⓘ fold, and a blocker line above the composer. Collapse points at 1200 and 900 px, and a phone drawer. | #261, #263 |
| Conversations | New, rename, pin, archive and restore. Each conversation is its own MC session (the OpenClaw `user` key); the legacy thread keeps its daily key; MC context is compacted. | #265 |
| Streaming | MC replies stream (first text in about 3 s). Stop works; a partial reply is never kept; usage is recorded honestly (null, not 0). The retry cap is proven: 1 upstream call per failed turn, down from 5. | #275 |
| Workshop (read only) | Host admission (memory, swap, disk, sessions on this Mac), shown as unknown and never as "nothing running". Zero model calls. The file-first `workshops/<id>`. | #266 |
| CoS | Protected by the write guard. A slow reply gets an honest 504. | #267, #277 |
| Voice | The mic is released on every end path. The button resets. | #270, #274 |
| Scrub and off the record | Wider card separators with zero false positives on the corpora. An unknown mode holds every write. | #271 |
| Usage | Each root writer has its own folder, so there is no first-writer lockout. | #276 |

**Also in the integration build** (release `0d2e7df`):
- #280: the Operations agent on the Docker topology. The Systems light reads `host.docker.internal:8768`, and the Mac's launchd job runs from `~/.worktrees/ops-runtime`.
- #281: Confer voice. CoS greets first; both sides show live; a Private switch; Write only on all three voice pages; provider errors are visible. The staging turn log is **off** until `scripts/staging/cos.sh turns on`.
- #277 at its latest head.

## 2. Open PRs: status and suggested merge order (all wait for Robert's go after testing on dev)

| PR | Head | What | Review | Production effect |
|---|---|---|---|---|
| #274 | ec91bf05 | Mic released on a provider-side end (**a privacy bug in production voice**) | Approve | german, portuguese, system-bot, cos-bot, cos-scheduler |
| #267 | 313ce4f5 | CoS web write guard (details private: `_working/security/`) | Approve (security) | portal, cos-bot, cos-scheduler |
| #270 | 7d3a7646 | Confer voice button; Stop during start closes the mic | Approve | cos-bot, cos-scheduler |
| #261 | 516f6f4b | Safe Markdown (markdown-it-py + nh3, linear time) | Approve w/ notes | portal |
| #263 | 88dc3c27 | Shop floor layout, slice 1 (stacked on #261) | Approve w/ notes | portal (/guild-next is not mounted in production; /guild changes only its alt text) |
| #265 | 942d29bd | Conversations, slice 2 (stacked on #263) | Approve w/ notes | portal; MC config is dormant |
| #266 | 47c08607 | Workshop 4a and the /guild-next redirect (stacked on #265) | Approve | portal |
| #275 | c8965477 | MC streaming S1 (stacked on #266) | Approve w/ notes (staging) | portal, cos-bot, cos-scheduler; MC image |
| #271 | 67a05659 | Scrub and off-the-record (#242). **When it lands after the stack,** its #242 browser checks must add post-its on the wall; the adaptation is in the integration commit. | Approve w/ notes | portal |
| #276 | b6f92d3e | Usage writer folders | Approve w/ notes | portal, cos-bot, cos-scheduler |
| #277 | a61d9aa4 | Proxy 504/502, CoS turns 125 s (#262) | Approve w/ notes; **production nginx `proxy_read_timeout` should become 130 s** | portal |
| #269 / #280 | f4161cf6 / 99dba734 | Operations check-in (#240) and the staging topology (#280 includes #269) | Approve w/ notes (the DSN-leak fix is in) | portal, cos-bot, cos-scheduler (they copy domains/guild; no functional change) |
| #281 | a8e266f1 | Confer voice on the standard pattern, the Private switch, Write only | Approve (condition met) | german, portuguese, system-bot, cos-*, plus portal and curator via `utils/` |
| #282 | 168671a0 | tour_capture: local sample server (hardened) and Guild scenarios | Approve (security re-check) | documents only |
| #264, #278, #279 | — | verify probe skip, relay test readiness, verify helper advisory | Approve | documents |

Review files: `reviews/CLAUDE_INDEPENDENT_REVIEW_PR<n>_2026-09-29.md`. The security reviews for #267, and the CSRF note, are private in `_working/security/`.

## 3. Robert's feedback and the refinement list

These are in `documents/ROBERT_DEV_FEEDBACK_2026-09-29.md`. The key items:
1. **The Shop floor, the wall and the Workshop are "all the same, essentially".** The screen pack confirms it: one skin, and three look-alike forms of Needs you. We need a design pass that gives each room one dominant element (the chat, the board, the host gauge), one accent colour and a clear room header. **The design is Codex's call.**
2. **CoS voice must follow the standard voice pattern** of German and Portuguese. There is a reply mode: speak (the default), write only, or both. #281 does Phase A; Phase B moves Confer onto the shared bootstrap factory and retires the unused chained controller.
3. **Contradictions the screen pack found:**
   - the header says "live" while the blocker says "unavailable";
   - Stop shows "Last answer failed";
   - off the record still says "kept as notes";
   - on phone, the "Type" button sits under an open composer and the portal bar and subnav are clipped;
   - the Queue has no Blocked column while the floor calls a blocked item most urgent;
   - times mix UTC and local;
   - one page is named both "Workbench" and "Wall";
   - the floating MC panel covers the Blocked panel.
4. **No hurry for production.** Test on dev and refine first.

## 4. Asks for Codex

1. **Review the actual diffs**, at the heads above. The priority order is #274, #267, the stack (#261 to #275), then #281.
2. **Review the Workshop design note** (`documents/WORKSHOP_DESIGN_NOTE_2026-09-29.md`). **It gates 4b**, the launcher, the isolation profiles and the first real builder run.
3. **Review the streaming spec v0.3.1** (`documents/SPEC_STREAMING_CHAT_COS_MC_v0.2` plus the `v0.3` amendment). MC S1 was built against it; CoS streaming (S2) waits for Robert's two readings: (a) is streaming the default only where its switch is on, and (b) is voice non-streaming for now?
4. **Design a room-differentiation pass** for the Shop floor, the wall, the Workshop, the Queue and Operate, using the screen pack.

## 5. Risks and known limits

- **A spend blind spot:** LiteLLM 1.93.1 runs no callbacks for a client-cancelled stream. So a stopped streamed call is neither recorded nor counted against MC's key budget, although the provider may still bill it. The portal marks such a call `cost_source: "unrecorded-abort"`. **Production streaming needs a provider-side spend limit first.**
- **Write only still bills the audio.** Neither provider is asked for text-only output, because xAI's support for it is unverified.
- **Disk:** each staging build leaves 5–8 GB of cache, and the Mac swaps about 5 GB. Staging images and the build cache are pruned after each rollout, leaving about 13–20 GB free. The old worktrees need Robert's M0 decision.
- **The Mac Operations agent** reports the data disk at 94% used, which is true.

## 6. Where things are

- **The screen pack:** `review-package-2026-09-30/Guild_1_1_dev_screens_2026-09-30.pdf` (36 pages, sample data), with an `INDEX.md` beside it.
- **Status log:** `CLAUDE_CODE_STATUS_2026-09-27.md`.
- **Robert's feedback:** `documents/ROBERT_DEV_FEEDBACK_2026-09-29.md`.
- **Specs:** streaming v0.2 and v0.3; `documents/COS_MULTI_CONVERSATION_CONTRACT_2026-09-29.md`; Spec 160 (`docs/specs/spec_160_…`).
- **Issues filed:** #262 (fixed by #277), #268 (#270), #272 (a scrub follow-up), #273 (#274).

## 7. Next build steps, after Codex's review and Robert's go

- The room-differentiation pass and the contradiction fixes (§3.3).
- Confer voice Phase B, which moves Confer onto the shared bootstrap factory.
- Telegram `/private`.
- Spec 160 `record_turn` for text turns.
- 4b, once the design note is reviewed.
- CoS streaming S2, once Robert answers.
- #272.
- Production nginx at 130 s.
- A provider spend limit before production streaming.
