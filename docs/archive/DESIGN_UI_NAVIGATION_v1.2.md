# mini-moi — UI Navigation Architecture
**Document:** DESIGN_UI_NAVIGATION_v1.2.md
**Path:** docs/design/DESIGN_UI_NAVIGATION_v1.2.md
**Session:** 2026-03-24
**Status:** Design complete — approved for build
**Reviewed by:** Claude (claude.ai), Grok, Claude Code
**Handoff to:** Claude Code (build) + OpenClaw (schema + tracking)

---

## Origin and Context

mini-moi (*personal-ai-agents*) was always conceived as a personal intelligence system — a toolkit for learning, research, and professional growth with an AI agent at the center. Geopolitics and Finance was simply the first domain built, because it had the clearest daily cadence and the most immediate value as a proof of concept. The Curator briefing tool is domain one, not the whole product.

Two domains are now active (Curator + Research Intelligence) and two more are confirmed coming (Language Learning, Job Search). The horizontal top nav — designed for 5 items — does not scale to this. A left vertical rail solves it permanently and reflects what the project always was: a multi-domain personal toolkit, not a news briefing app that accumulated features.

**Robert's stated intent:** *"Personal learning and mutual growth and memory with an agent."*

The structure should feel like a personal toolkit — each domain is a room, the left rail is the hallway, the parchment aesthetic unifies them. The UI should always feel like one coherent personal notebook, not four separate apps glued together.

---

## Mental Model: Shared Infrastructure, Domain-Scoped Focus

The four domains are like four workspaces on the same machine. They are integrated in the sense that they share tools and infrastructure — but each domain has a different focus, intent, and interaction pattern, with its own domain-specific extensions built on top.

**Shared across all domains:**
- Database layer: JSON-first store, with PostgreSQL and Neo4j activated when functional completeness is reached. Schema design must accommodate all four domains from the start — designing Curator in isolation and bolting Job Search on later creates a migration problem.
- Core tools: search, fetch, scoring, summarization
- Chrome: parchment aesthetic, left rail, settings panel

**Domain-specific per domain:**
- Intent and interaction pattern (briefing vs. research vs. language practice vs. job hunting)
- Top sub-nav and content area layout
- Domain-specific extensions and add-ons built on shared tooling

The left rail is therefore not just navigation — it is a **workspace switcher**. Each domain is a focused room; the rail is the hallway connecting them.

---

## Proposed Architecture — Left Rail + Top Sub-Nav (Hybrid)

```
┌──────────────┬──────────────────────────────────────────────────────────┐
│              │  Daily    Library    Deep Dives    Observations    🎯    │
│  🗞 Curator  │────────────────────────────────────────────────────────  │
│  (active)    │                                                           │
│  🔬 Research │  [content area — changes per domain + page]              │
│              │                                                           │
│  💬 Language │                                                           │
│  (coming)    │                                                           │
│              │                                                           │
│  💼 Jobs     │                                                           │
│  (coming)    │                                                           │
│              │                                                           │
│              │                                                           │
│  ⚙           │                                                           │
└──────────────┴──────────────────────────────────────────────────────────┘
```

**How it works:**
- Left rail = workspace switcher only. One item per domain. Active domain highlighted.
- Top bar = within-domain sub-nav. Changes content depending on active domain.
- Clicking a domain is a full page load to that domain's default page — top nav replaces naturally. No client-side JS switching required. The JS concern only applies if client-side switching without reload is attempted, which is not the current approach.
- 🎯 (Focus/Priorities) = right-aligned in the top bar, per-domain. Not a global settings item.
- ⚙ (Settings) = bottom of left rail. Global system settings only.

---

## Per-Domain Tab Structure (Locked for Current Build)

| Domain | Top nav items |
|---|---|
| Curator | Daily · Library · Deep Dives · Observations · 🎯 |
| Research | Dashboard · Queries · Observations · Save · 🎯 |
| Language | TBD at build |
| Jobs | TBD at build |

**Tab notes:**

- **Library** (Curator): current working name, acknowledged placeholder — rename when the right word surfaces.
- **Deep Dives** (Curator): bridge concept toward Research. Stays in Curator for now — do not migrate.
- **Observations** (both domains): AI-generated pattern recognition. Means something slightly different in each domain — Curator Observations reads patterns in the daily briefing; Research Observations notices things across investigation sessions. Having it in both is intentional and correct.
- **Queries** (Research): the existing `candidates.html` page — manages search query promotion and retirement. Renamed from Candidates for clarity. Same page, same functionality, cleaner name.
- **Save** (Research): currently a quick-capture form (paste URL + note). Role is ambiguous now that Reading Room is on the roadmap — likely becomes a button/modal action rather than a top-level tab. Deferred. Do not change now.
- **🎯 Focus/Priorities** (per-domain, right-aligned): time-bound, directed intent layer — not a settings panel. Robert uses this to tell the system "for this period, weight toward these topics, this context, this urgency." Closer to a mission briefing than a configuration menu. Curator's is live and functional. Research, Language, and Jobs each get their own version when those domains build out — stubs for now, no current functionality broken.

---

## New Build Requirements (Flagged — Not Current Sprint)

These are confirmed future pages, not renames of existing pages:

**Reading Room** (Research domain)
Saved articles under active investigation. Distinct from Queries (which manages search queries). This is a new page build — maps to the reading room concept discussed in design. Add to Research sub-nav when built: `Dashboard · Queries · Reading Room · Observations · Save · 🎯`

**Sessions** (Research domain)
List of past research session files with their findings. No `/research/sessions` route exists yet. New page build required. Add to Research sub-nav when built.

Both flagged in BACKLOG.md.

---

## Design Principles

### Unified chrome, per-domain mood
- Left rail and top bar: same parchment palette everywhere
- Content area: each domain can have slight character (Research feels more scholarly, Language feels warmer) but CSS variables are shared
- No domain feels like a different product
- One coherent personal notebook, not four separate apps glued together

### Left rail — parchment softening (critical)
A hard vertical rail risks feeling "modern app" rather than "scholarly parchment journal." Make it intentionally soft and editorial:

- **Width:** 200–220px (not too narrow)
- **Background:** 5–10% lighter than content area, or a faint vertical ruling-line texture to reinforce the notebook feel
- **Shadow:** subtle paper-edge shadow on the right edge of the rail
- **Content margin:** slightly larger left margin on content area so it still feels like opening a notebook
- **Collapsible:** small chevron at top of rail for users who want maximum reading space
- **Active domain:** parchment accent background on the rail item
- **Coming soon domains:** dimmed with tooltip on hover — not hidden. Dimmed signals roadmap intent. Hidden obscures it.
- **Font:** DM Mono, small, uppercase label style

### Icons
Emoji works for prototyping but will feel too casual once Language and Jobs are live. Migrate to simple, consistent SVG icons (Lucide or Feather via CDN) as part of this build:

| Domain | Icon |
|---|---|
| Curator | Newspaper SVG |
| Research | Microscope or open-book SVG |
| Language | Book SVG (not a flag — flag reads as one specific language; this system may carry multiple language tracks) |
| Jobs | Briefcase SVG |
| Focus/Priorities | Target or compass SVG (🎯 as interim) |
| Settings | Gear SVG |

The specific language (German, Portuguese, Spanish) surfaces in the sub-nav, not the domain icon.

---

## Platform Strategy: HTML-First, Mobile Notification + Quick View

The full experience lives in the browser on desktop. mini-moi is desktop-primary.

**Mobile gets:**
- Push/Telegram notifications (already in place for Curator)
- A lightweight quick-view layer — a narrow responsive view or dedicated simple mobile page, not a full responsive redesign of the desktop UI

**Language Learning** is a potential standalone mobile app on the roadmap — language practice genuinely benefits from native mobile (flashcards, audio, streaks). That is a roadmap item, not a constraint today. No mobile architecture decisions should block the current build.

Do not build a bottom tab bar for mobile unless the tool is actively used on mobile.

---

## Cross-Domain Data Model (OpenClaw Task — Pre-Build Requirement)

**Owner: OpenClaw** — this task goes to OpenClaw, which maintains the schema and memory layer.

Before domain-specific schemas diverge, write a one-paragraph data model note per domain. This prevents the JSON store from becoming four incompatible silos requiring painful migration when PostgreSQL and Neo4j activate.

**Shared base entity sketch:**

All domains inherit from a base `Item` entity: `id`, `domain_tag`, `title`, `source`, `date`, `priority_weight`, `saved_state`, `tags[]`. Curator uses `Article` and `Observation` extensions. Research uses `Session` and `Candidate`. Language will use `VocabularyEntry` and `PracticeSession`. Jobs will use `Opportunity` and `Application`. Shared fields enable cross-domain search, memory, and future Neo4j graph relationships across domains.

This data model note must be written and reviewed **before** the left rail build starts (step 3 in sequencing below).

---

## "Coming Soon" Domain — First Load State

When a user clicks a dimmed domain for the first time, it must not feel broken. Define a landing state — a styled placeholder card that acknowledges intent and optionally surfaces configuration:

```
[Domain Name] — launching soon

[Short teaser: one sentence on what this domain will do]

[ Configure early access ]   ← opens settings stub, even if minimal
```

Example for Language:
> *"Language Learning — daily practice, spaced repetition, conversation simulation, and memory of your progress."*

This applies to Language and Jobs at launch. The card must be styled in the parchment aesthetic — not a generic error or empty state.

---

## What Changes vs. Previous Plan

**Stays the same:**
- Fix 1: AI Observations section reorder + date nav inline — independent of nav
- Fix 3: Research pages palette alignment — independent of nav
- Fix 4: BACKLOG source-level priority entry

**Changes:**
- Fix 2 (nav update) is now the left rail, not a horizontal nav tweak
- All Curator HTML files get the left rail + updated top sub-nav
- Research HTML files get the same left rail (Research domain active) + their sub-nav
- `candidates.html` tab label renamed to Queries — same page, same functionality
- Priorities tab moves to right-aligned 🎯 in top bar — same page, same functionality, new position. Zero Python changes.

---

## Sequencing — Build Order

**Phase 1 — Current sprint (safe, isolated, not nav-dependent):**
1. Fix 1: AI Observations reorder, date nav inline, weekly staleness collapse
2. Fix 3: Research pages palette alignment (Playfair + DM Mono, parchment palette)

**Phase 2 — After review:**
3. OpenClaw writes cross-domain data model note (all four domains) — required before rail build
4. Build left rail + top sub-nav across all files (Fix 2)
5. Commit and push

**Phase 3 — After rail is stable (do not rush):**
6. Migrate Priorities to per-domain 🎯 focus layer — Curator's existing functionality untouched, Research/Language/Jobs get stubs
7. SVG icon migration (swap emoji for Lucide/Feather)
8. Build Reading Room page (Research)
9. Build Sessions page (Research)

---

## Files Touched — Current Sprint

### Main Curator

| File | Change |
|---|---|
| curator_intelligence.html | Section reorder, date nav inline, weekly staleness collapse |
| curator_latest.html | Left rail + updated top sub-nav |
| curator_library.html | Left rail + updated top sub-nav |
| curator_priorities.html | Left rail + updated top sub-nav (becomes 🎯 target, right-aligned) |
| curator_briefing.html | Left rail + updated top sub-nav |
| curator_index.html | Left rail + updated top sub-nav |

### Research Pages

| File | Change |
|---|---|
| web/dashboard.html | Left rail (Research active) + palette alignment |
| web/observe.html | Left rail (Research active) + palette alignment |
| web/candidates.html | Left rail (Research active) + palette alignment + tab label → Queries |
| web/save.html | Left rail (Research active) + palette alignment |
| web/index.html | Left rail (Research active) + palette alignment |

### Other

| File | Change |
|---|---|
| BACKLOG.md | Source-level priority weights + Reading Room new build + Sessions new build |

**Not touched:** curator_server.py, research_routes.py, any Python files.

---

## Open Questions (Deferred — Do Not Block Build)

1. **Library rename** (Curator) — current name is a known placeholder. Rename when the right word surfaces naturally.
2. **Save tab** (Research) — likely becomes a button/modal action once Reading Room is built. Revisit after rail is stable; do not change now.
3. **Parchment compatibility at scale** — validate that the softened rail feels right once built in browser. Adjust texture/shadow as needed.
4. **SVG icon final set** — Lucide vs. Feather vs. custom. Decide before Phase 3.

---

## Commit Sequence

```
fix(ui): AI Observations — daily first, weekly stale-collapse, date nav inline
fix(ui): research pages — align to Curator parchment palette (Playfair + DM Mono)
feat(nav): left rail architecture — multi-domain workspace switcher, Research + Curator live
backlog: source-level priority weights, Reading Room new build, Sessions new build
```

---

*Document produced: 2026-03-24. Reviewed by claude.ai, Grok, and Claude Code prior to build.*
*Save to: `docs/design/DESIGN_UI_NAVIGATION_v1.2.md`*
