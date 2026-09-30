# 06: Next steps (ordered), and what each waits on

## A. Now (no one blocked)
1. **Verify the handoff against the live state** (see 00). Report any drift.
2. **Robert's three voice tests on dev** (https://dev.minimoi.ai/app/cos), each a few paid seconds:
   - OpenAI, Speak and write: one question;
   - OpenAI, Write only: one question;
   - Grok, Write only: one question.
   Expect CoS to greet first, both sides to show live, and "Transcript saved" only if `cos.sh turns on`.
3. **Re-run the screen pack** with the #281 voice screens, using `scripts/tools/tour_capture` (see 04), and update `review-package-2026-09-30/`.
4. **Add this handoff folder to PR #283,** so cloud sessions and Codex can read it too.

## B. Waiting on Codex's review findings
5. **Apply Codex's diff findings** to #261–#282. That means commits on each PR, a re-check by an independent reviewer, then the integration branch, then staging.
6. **The Workshop design note review:** it gates **4b**, which covers the launcher, isolation profiles proven with hostile fixtures, one managed local run per host, a queue, a real builder run in a scoped checkout, an independent reviewer, and Robert's decision. See the build contract §4 and slice 4, and Spec 158 §7.
7. **Streaming spec v0.3.1:** CoS streaming S2 waits for this and for Robert's two readings.
8. **The room-differentiation design:** the Shop floor, the wall, the Workshop, the Queue and Operate. Codex's call, then build.

## C. The refinement list (can start once Robert approves the plan; each is small)
9. **Shop floor status honesty:**
   - one source for the header and the blocker line, so there's no "live" plus "unavailable" at once;
   - a Stop shown as "Stopped", not "failed";
   - no duplicate failure line.
10. **Off the record:** the header and the ⓘ text change with the mode.
11. **Phone:**
    - the "Type" button placement (Robert earlier said not to change its behaviour, so position only);
    - the portal bar and subnav clipping.
12. **Queue and wall:**
    - a Blocked column or grouping on the Queue;
    - one time zone convention everywhere (local, with UTC in a tooltip);
    - one name for the wall;
    - the floating MC panel must not cover the Blocked panel.
13. **#272:** a scrub follow-up (the group-size rule for plain separators).
14. **Confer voice Phase B:** move Confer onto the shared bootstrap factory; a contract test for all three voice pages; retire or label `realtime-confer-controller.js`.
15. **Telegram `/private`**, sharing CoS's Private mode.
16. **Spec 160 `record_turn`** for text turns, which reads the same Private state.

## D. Before any production release of streaming or voice
17. **A provider-side spend limit.** Aborted streams bypass LiteLLM's accounting and the MC key budget.
18. **Production nginx** `proxy_read_timeout` at 130 s (#277).
19. **Robert's merge go-ahead,** in the order given in 02.

## E. Later (explicitly parked)
- Hetzner, Tailscale and failover: not until 1.1 is complete and tested.
- The MC Grok backend (stage D), mc-evidence (stage E), and the Spec 160 build beyond `record_turn`.
- #240 is closed by #269/#280; #242 by #271.
- A broad provider bake-off.

## ⚠ Direction update, 2026-09-30 morning (Robert with Codex). This supersedes section C item 8 and the "room differentiation" idea
- **Guild's daily UI shrinks to four destinations: Chat · Board · Build Log · Rooms.** Keep all of the backend.
  - **Chat:** the Shop floor chat, personalised with Robert's artwork or photos.
  - **Board:** the post-it wall, as light operational status.
  - **Build Log:** one filtered list (spec, ready, in progress, done, roadmap, …), with Robert's **ranked 1, 2, 3** as its default view. Kanban and graphs come later.
  - **Rooms:** group discussion and design with Robert, Claude and Codex, with assignments and reviews.
  - The Workshop, Operate, Labs and Planning Studio move to secondary navigation, or onto the task itself.
- **CoS and MC are both kept.** CoS is the daily personal partner (Career, RVS Associates, German). MC keeps MiniMoi running and carries requested enhancements through staging, review and a result. MC needs the whole Guild backend and tools, which today are limited to session status.
- **The next big bucket after this one** is an infrastructure-hardening package, delivered in sequence:
  - A: Hetzner staging;
  - B: the vault, extending the Central Personal Repository design;
  - C: models on subscription plans plus the Grok API and a cheap open model, with no silent subscription-to-API fallback.
- **Codex is aligning every Guild, CoS, Rooms and Workshop doc for 1.1** (a 128-entry source inventory, a versioned 1.1 roadmap and crosswalk). Claude reviews it here, in the existing chat. Claude Code's CLI login was revoked, so Codex does not run Claude Code itself.
