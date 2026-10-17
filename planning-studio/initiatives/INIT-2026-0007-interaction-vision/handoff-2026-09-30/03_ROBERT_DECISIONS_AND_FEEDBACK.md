# 03: Robert's principles, decisions and feedback

## Standing principles (Sep 28–29; also in Claude's memory)
- **Optionality:** every build adds or keeps optionality across model, agent runtime and provider, and never binds.
  - Agents ask for a route, not a model.
  - Runtimes sit behind adapters.
  - MiniMoi owns the memory, and runtime state is only a cache.
- **File-first:** data lives as files in data folders. Postgres is used only when a real need exists.
- **Deploy only what changed:** services are separate containers, and the release classifier redeploys only the affected services.
- **Keys:**
  - each agent has its own capped gateway key, scripted;
  - there is one provider key per environment (one Anthropic, one xAI);
  - staging and production never share keys;
  - production keys arrive with Hetzner.
- **Streaming is the default** for CoS and MC chat. Each backend declares `supports_streaming`, with a non-streaming fallback set in config.
- **Usage metrics:** one standard record now; alarms later. Cost levels: good, tight, act, stop-before-frozen.
- **Features first.** Operational features count as features.
- **No environment move** (Hetzner, Tailscale, failover) until Guild 1.1 code is complete and tested.
- **MC runs in separate containers,** with the same topology on dev and production. The personal OpenClaw stays separate and is excluded from MiniMoi memory.
- **Local Workshop:** two workshops structurally (the Mac now, plus a future one), plus Robert. Light and file-first; Rooms are used when needed.
- **The Mac:** stop Colima when staging isn't being tested; memory is 8 GB.
- **German/Portuguese parity,** now extended to CoS voice: one standard voice pattern, and any Confer-only difference is a defect.
- **Main is the baseline.** Branches are short-lived PR branches. Claude owns branch consolidation, Codex reviews, and Robert approves each merge.

## Decisions made in the Sep 29 session
- **"Build it all now, skip prototype"** (the contract is `documents/CLAUDE_BUILD_NOW_GUILD_DEV_WORKSHOP_2026-09-29.md`).
- **Layout.** Desktop: history, then a clean chat, then an upper-right rail with the Build card as the hero, followed by the focused Workshop item or a real Needs You item, and "Open wall". It stays quiet otherwise. On phone, a small header and expandable context, with chat first.
- **All six layout refinements approved:**
  1. collapse at 1200 and 900 px;
  2. a modest image (the Build card);
  3. the rail follows the conversation;
  4. Needs You in one place;
  5. a one-line quiet state;
  6. blockers shown above the composer.
- **#260 CI fix merged,** with a full production restart that Robert accepted.
- **Test spend:** up to $10 authorised; about $0.05 used.
- **Telegram status** from Claude to Robert at milestones and blockers, and periodically. "This would replicate with Mastercraftsman."
- **Operations crash loop:** fixed, as he asked, "tonight".
- **Voice reply mode:** Speak (the default), Write only (muted), or both. A standard control on all voice pages.
- **Production:** "no hurry, we need to do user testing in dev and refinements. The important part is Guild in workable shape there."
- **Review packs:** always use the shared `scripts/tools/tour_capture` tool (the Codex guidance).

## Pending decisions (ask Robert)
1. **Conversations:** new ones keep context until archived, and the legacy thread still starts fresh daily. OK?
2. **The status line** ("1 needs you · Queue Problem · Systems unknown…"): keep it in the rail, or move it to the bottom strip?
3. **CoS streaming (S2):** (a) is it the default only where its switch is on? (b) is voice non-streaming for now?
4. **A permanent Workshop observe/sync launchd job** (every 9 min)?
5. **Turning on the staging CoS voice transcript log:** `scripts/staging/cos.sh turns on`.
6. **M0:** the root checkout (it's on the old branch `codex/cos-agent-a-runtime`) and the approximately 40 old worktrees. This is where most of the disk space is.
7. **A provider-side spend limit** (Anthropic console) before production streaming.
8. **Close #248** (superseded)?

## Robert's dev feedback (also in `planning-studio/…/documents/ROBERT_DEV_FEEDBACK_2026-09-29.md`)
1. **The Shop floor, the wall and the Workshop are "all the same, essentially"**; they need differentiating.
2. **CoS voice must match the Mein Deutsch / Meu Português pattern.**
3. **Voice reply mode:** Gespräche talks back, and Schreiben writes. CoS gets both.
4. **Typed CoS worked.** Spoken CoS gave no reply, which led to #281.
5. **`/guild-next` gave "not found"** and now redirects.
6. **He wants the screen PDF and a big Codex handoff;** both are done. The PDF predates #281, so re-run it for the voice screens.

## Screen-pack findings (sample data; confirm on dev)
- **Contradictions:**
  - the header says "live" while the blocker says "unavailable · connected, no answer yet";
  - Stop shows "Last answer failed";
  - an MC failure appears twice.
- **Off the record barely changes the page:** it still says "kept as notes", and the ⓘ says MC "has not answered yet".
- **Phone:**
  - a large "Type" button sits under an open composer;
  - the portal bar is cut off ("Meu Portuguê", no CoS), and the subnav is clipped.
- **Queue and wall:**
  - there's no Blocked column, though the floor calls a blocked item most urgent;
  - times mix UTC and local;
  - one page is named both "Workbench" and "Wall";
  - the floating MC panel covers the Blocked panel.
- **Sameness:**
  - one skin everywhere;
  - three look-alike forms of Needs you;
  - the rail is a small copy of the wall;
  - the Workshop's "HOST TIGHT" is only a small pill.
  - Suggestion: one dominant element and one accent colour per room (the floor: chat; the wall: the board; the Workshop: the host gauge), plus a clear room header.

## Decisions, 2026-09-30 (Robert, after reviewing the Codex chat)
- **Guild 1.1 replaces production Guild completely:** "I don't want old and new mix. We need a new new and reuse all of the backend."
  - The new UI (Chat · Board · Build Log · Rooms) takes over `/guild` in production, and the legacy tabs and templates are retired.
  - Old URLs redirect to their new home.
  - All backend data and write paths are reused.
- **The new Build Log replaces all of the current production Guild tabs:** queue, log, roadmap, docs, spec detail, improve and experiment. They become its views and filters, and an item's detail when opened.
  - Legacy Career surfaces: Spec 156 says **remove, do not relocate or recreate in CoS**. An earlier Claude summary wrongly said "move to CoS"; Robert has not decided a relocation.
  - The admin surfaces (users, detailed operations, the Workshop) sit in secondary navigation, not the daily tabs.
- **MC gets full access to the Guild backend and tools:** read and write to the Build Log and queue, the Board, operations, and Workshop dispatch.
  - This is assumed to run within its capped key.
  - Merges and production changes stay behind Robert's one-word "approve", with the mechanics before and after in place. Confirm with Robert if "full access" should go further.
- **Docs, a fifth destination (Robert, 2026-09-30; a DESIGN IDEA to discuss, not decided):** a special section for the key official documents, clear to Robert, to others and to agents.
  - Layout: an introduction plus the top part of the roadmap on one side, and a library of the key artifacts on the other (ARCHITECTURE, AGENTS.md, and so on).
  - Purpose: "a place of reference for the intent and craft and history".
  - It replaces the Roadmap tab. The Build Log is the work, including future roadmap items that are still only thoughts.
  - **Proposed destinations:** Chat · Board · Build Log · Docs · Rooms, with Docs still open for discussion.
- **Docs page shape (Robert, 2026-09-30; a DESIGN IDEA to discuss, not decided; pointing at the current production Roadmap page):**
  - **Main side:** "a version of this" stays visible. That means the roadmap's intro, its Direction section, the loop diagrams (the Guild loop spec → build → operate → improve with "lessons into the next spec", the language loop, and so on) and the roadmap tiers (Committed, Planned, Aspired, Paths not taken).
  - **Library:** the key document links, each with a short description.
  - **Reuse:**
    - The current renderer is `minimoi_portal/app.py` (the route around line 2448), using `templates/guild/build_roadmap.html` to render the tracked root `ROADMAP.md`. Its label "edit in _working/ROADMAP.md" is stale; the source is root `ROADMAP.md`.
    - The current docs list is `_docs_group_files` and `_DOCS_CORE` in `app.py` (around line 2624), which already pull a title and subtitle from each file.
    - Turn that into a **curated library**: a small file listing each official document with a one-line description, a version and date, and current or superseded status. Codex's 1.1 alignment supplies it, and agents read the same file.
