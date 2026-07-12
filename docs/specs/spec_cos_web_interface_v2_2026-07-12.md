# Spec: CoS Web Interface v2 — Built to Show, Not Just to Test

**Status:** Spec Ready — supersedes the unversioned original spec from earlier today
**Build queue #:** Same as original (TBD — assign at registration), version-bumped
**Date:** 2026-07-12
**Author:** Claude.ai design session (Fable 5)
**Sequencing:** Claude Code finishes its current in-flight work against the original spec first — nothing gets abandoned mid-build. This version is picked up next, extending rather than replacing what's already done.

---

## Change Log — v1 → v2

**Why this version exists:** the original spec optimized for speed alone — a deliberately bare skeleton, styled later. That's changed. This is now a key artifact Robert will show to other people, not just a private testing tool. The standard is genuinely attractive and usable, built today, not shortcut and fixed later.

**What changed:**
1. **All four tabs are now in scope today** — Talk, Feed, To-do, Archive, all functional. Not Talk-plus-three-stubs.
2. **Real visual quality from day one**, not deferred — enabled by the reuse strategy already established (Curator's feed pattern, German/Portuguese's visual language), which was already the efficient path, not an added cost.
3. **Chicago photography** treated as core to the design now, not a placeholder afterthought — see Design Direction below.
4. **What's genuinely still deferred** is narrower and more specific: full portal-auth integration (the actual mechanism for safely showing this beyond Robert's own laptop) and mobile-specific optimization. Both real, both near-term, neither blocking today's build.
5. Time budget explicitly reset: Robert is prepared to spend more time on this today than on the backend work — build accordingly, don't rush to a placeholder result.

---

## Intent

CoS's web interface is being built today as a genuine, attractive, usable piece of mini-moi — not a temporary testing scaffold. It needs to work well for Robert's daily use *and* hold up when shown to someone else. The path to both speed and quality is the same: build from Curator's and German/Portuguese's existing, proven, already-attractive components rather than designing from a blank page.

---

## What's Not Changing

- Telegram bots (`minimoi_cos_bot`, `minimoi_cos_test_bot`) — untouched
- `chief_of_staff.py`'s core chat/memory/tool logic — untouched; the interface calls the same `_chat()` path Telegram already calls
- Doesn't require #133 Phase 1's containerization to be complete — builds against the currently-running dev instance
- Still no full portal-auth integration today — see Deferred, below. This is the one piece of the original scoping that holds.

---

## Backend Independence (confirmed 2026-07-12)

The UI talks only to `chief_of_staff.py`'s coordination layer (`_chat()`), never to `call_backend()` directly — this was the point of the coordination-layer/backend-call boundary established for #133 Phase 1. **The UI requires zero changes when OpenClaw comes online as a second backend.** Today it's built and tested against `grok_backend.py`, the real current backend — not a placeholder. No "fake it out" needed.

**Backend/model display — confirmed in scope, display only (updated 2026-07-12).** Show both the agent/backend type (Grok direct API today, OpenClaw once online) and the specific underlying model each is running (e.g. "Grok · grok-4-1-fast-reasoning"). **No toggle, no switcher control** — this is deliberately display-only. Robert's own framing: the backend choice is too important to be a casual UI toggle the way German/Portuguese's voice persona switch is; showing who's doing the work matters, choosing it from the UI doesn't (that's a coordination-layer/config decision, not a chat-session one).

Mechanism: the coordination layer already knows which backend module is active — this is a config-level fact, not something that flows through `call_backend()`'s existing contract. Expose it as a small, separate status data point (e.g. a lightweight endpoint or embedded in the page load) — no changes to `cos_interface.md` needed.

---

## Design Direction

**Reuse is the strategy, not a shortcut.** Curator's feed/list pattern (dated, scored items) is the closest existing structural match for Feed and Archive — adapt it directly. German/Portuguese's design system (parchment/dark-nav, Georgia serif, book-cover-to-pages transition) is the shared visual language — apply it, don't reinvent it. The result should feel like it belongs next to Curator and German/Portuguese, not like a separate, rougher thing bolted on.

**Chicago photography** is CoS's visual identity — personal, not borrowed from elsewhere the way German→Germany or Portuguese→Brazil work. Robert is providing a curated base list directly. A handful of strong wide/landscape shots (skyline, streets) cover the header banner; a few square or vertical options give Claude Code room for secondary placements at its design judgment, informed by how Curator/German use imagery, if at all. Full-resolution originals are fine to hand off as-is — no need to pre-resize; Claude Code optimizes for web. Delivery goes directly to Claude Code / the repo, not through this chat — binary files aren't something to route through Claude.ai.

**Quality bar:** Robert should be comfortable pulling this up and showing another person, without caveats about it being unfinished.

---

## Scope — Build Today

### The four tabs, all functional

- **Talk** — the chat interface. Text input, submit, scrolling conversation log, calling the same `_chat()` function Telegram already calls. Voice input included if the Pre-Flight check below finds it's genuinely reusable; otherwise noted as the immediate next fast-follow.
- **Feed** — `cos_memory.md`'s episodic entries (decisions, actions, notes, questions), styled from Curator's feed pattern.
- **To-do** — open action-type entries from `cos_memory.md`, merged with pending `guild.cos_agenda` recommendations. Same conceptual category to Robert ("things CoS flagged for me"), different backend stores.
- **Archive** — full/older history, searchable, across `cos_memory.md` + `agent_logs`, same reused feed pattern.

### Dashboard Card (added 2026-07-12, per Robert's screenshot of `dev.minimoi.ai/dashboard`)

CoS gets a fifth card on the dev dashboard, matching the existing four (Curator, Mein Deutsch, Meu Português, Guild) exactly — same card styling, icon + title + short feature list + arrow.

- **Title:** spell out "Chief of Staff" (at least partially) rather than a bare "CoS" — this dashboard may be shown to people unfamiliar with the abbreviation.
- **Feature list:** `Talk · Feed · To-do · Archive` — same pattern as German's `Lesen · Gespräche · Schreiben · Wörter`, directly matching CoS's own tab names.
- **Icon:** open choice, not blocking — something suggesting advisor/coordination rather than a literal domain marker (the other four use content-type or language flags, which don't map onto what CoS is).
- **Scope: dev dashboard only, today.** Prod dashboard card follows the same gate as CoS's own prod placement — tests pass, Robert's approval, portal proxy in place. Don't add it to prod until CoS itself is safely reachable from prod.

### Pre-Flight (Claude Code, before building)

1. Confirm which running `chief_of_staff.py` instance Robert's Mac testing actually hits, and that its Flask service is reachable locally in that mode.
2. **Voice check:** does a live browser-mic-capture component already exist anywhere in the codebase, or is voice handling limited to Telegram voice notes and uploaded Google Meet transcripts (neither of which is live browser capture)? Findings decide whether voice-in ships today or immediately after.
3. **UI-reuse inventory:** what's directly adaptable from Curator's feed template and German/Portuguese's design system — components, CSS, layout patterns. This is the actual build plan, not a nice-to-have — confirm it before writing new HTML.
4. **Photo status: real files, confirmed.** Robert is curating a base list of Chicago photos now — no placeholder needed for this build. Check whatever static-asset convention Curator/German/Portuguese already use for their own images and follow it, rather than inventing a new location.
5. **Dashboard link target:** confirm how `dev.minimoi.ai` (the dashboard's host) and CoS's Flask service (port 8769, currently Mac-local) actually relate on the network — same host, different port reachable directly, or something else. This determines what URL the new dashboard card's arrow actually points to. Don't guess — report the finding before wiring the link.

### Build Tasks

1. Complete Pre-Flight above
2. Build the four-tab structure using reused components per the inventory — not new design work
3. Wire Talk to `_chat()`; wire Feed/Archive to `cos_memory.md` (+ `agent_logs` for Archive); wire To-do to `cos_memory.md` actions + `guild.cos_agenda`
4. Apply the shared design system (parchment/dark-nav/Georgia serif) throughout
5. Chicago photo treatment — real files from Robert's curated list, per Design Direction above
6. Backend/model display — visible, always-on, showing agent type + specific model, no toggle (see Backend Independence above)
7. Add the fifth dashboard card to `dev.minimoi.ai/dashboard`, matching the existing four exactly, per Dashboard Card above
8. Voice-in, if Pre-Flight confirms it's genuinely reusable
9. No auth, no full portal integration yet — see Deferred

### Definition of Done

- [ ] All four tabs present and functional, not stubbed
- [ ] Visual design consistent with Curator/German's established system — Robert would show this to someone without caveats
- [ ] Talk, Feed, To-do, Archive all correctly wired to their real data sources
- [ ] Photo treatment in place using Robert's curated Chicago photos
- [ ] Backend + model visibly displayed, no toggle/switcher present
- [ ] Fifth dashboard card live on `dev.minimoi.ai/dashboard`, visually matching the existing four, arrow correctly linking to CoS's UI
- [ ] Zero regression to Telegram-based interaction
- [ ] Robert has actually tested it and confirms it unblocks his CoS build testing

---

## Deferred (genuinely, not "later" as a euphemism)

- **Prod placement — gated on tests passing, not a separate deployment.** Since this UI's routes live directly on `chief_of_staff.py`'s own Flask service, prod placement happens as part of spec #133 v1.3's already-speced Phase 1 containerization cutover (Rollback/Production Safety section), not as a second deployment event. Gate: basic dev tests pass, then Robert's explicit approval — same discipline as everything else tonight.
- **Full portal-auth integration** (`app.minimoi.ai/cos/`, reverse-proxied through the existing three-tier auth) — the genuinely separate remaining piece. This is the real mechanism for safely showing the interface beyond Robert's own laptop: proxy setup, auth wiring, moving CoS's Flask service to internal-only binding on EC2 once the proxy exists. Not today, but not indefinitely deferred either — the next real step after prod placement.
- **Mobile-specific optimization** — today's build targets the laptop use case Robert actually has right now. Mobile-first polish follows once the laptop version is solid.
- **Voice output (text-to-speech)** — no urgency signal on this; stays a clearly separate future decision, not assumed.

---

## Commit

| Item | Location | Actor |
|---|---|---|
| This spec | `docs/specs/spec_cos_web_interface_v2_2026-07-12.md` | Claude Code |
| Original spec | Retained, marked superseded, in-flight work against it completes first | Claude Code |

Registration and build approval: Robert.

---

*Spec: CoS Web Interface v2 · 2026-07-12 · Claude.ai (Fable 5)*
*Built to show, not just to test.*
