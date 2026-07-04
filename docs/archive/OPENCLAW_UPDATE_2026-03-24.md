# OpenClaw Update — 2026-03-24
**Session type:** UI build sprint
**Branch:** poc/research-source-quality
**Status:** Pushed to GitHub — 6 commits

---

## What Was Built Tonight

All work from DESIGN_UI_NAVIGATION_v1.2.md (already in docs/design/). Implementation is complete for Phase 1 and Phase 2 of that plan.

### Fix 1 — AI Observations reorder (`curator_intelligence.html`)
- Today's Observations now renders **above** Weekly Connections
- Date nav (← prev / date / today →) moved **inline** with the section header — no longer a standalone block below the page title
- Weekly staleness: if `weekly.date` is > 7 days old, section auto-collapses with a "last updated Mar XX" label in warn color and a show/hide toggle

### Fix 2 — Left Rail Navigation (all HTML files)
Multi-domain workspace switcher now live across the full product:
- **Left rail:** 210px, parchment background `#f9f5ed`, subtle paper-edge shadow, collapsible via chevron
- **Four domains:** Curator 🗞, Research 🔬, Language 💬, Jobs 💼
- **Language + Jobs:** now clickable links (not dead spans) — route to coming-soon pages
- **Coming-soon pages:** `language_coming.html` and `jobs_coming.html` — full parchment styled, rail active, feature list, back link. Routes added to `curator_server.py` (`/language`, `/jobs`)
- **Settings ⚙** anchored at rail bottom → `curator_priorities.html`
- **Files updated:** curator_latest, curator_library, curator_intelligence, curator_priorities, curator_briefing, interests/2026/deep-dives/index, web/dashboard, web/observe, web/candidates, web/save, web/index

### Fix 2 — Top Sub-Nav updated (all files)
- **Curator:** `Daily · Library · Deep Dives · Observations · 🎯`
- **Research:** `Dashboard · Queries · Observations · Save · 🎯`
- `candidates.html` tab renamed **Queries** (same page, same functionality)
- `🎯` right-aligned on all pages as `nav-focus`
- `interests/2026/deep-dives/index.html` fixed separately (relative URLs, missed in first pass)

### Fix 3 — Research Pages Palette Alignment
All 5 research web files (`web/*.html`):
- Google Fonts: Playfair Display + DM Mono + Source Sans 3
- Full `:root` CSS variable block matching Curator palette
- `body`: Georgia → Source Sans 3; hardcoded hex → CSS vars
- Headings: Georgia → Playfair Display
- `.research-nav` removed; replaced with new sticky `<header>` + `.nav-link` system
- Explicit `background: var(--bg)` on `.main-content` to restore parchment warmth

### Fix 4 — BACKLOG.md
Three new entries added:
- **B-008:** Source-level priority weights
- **B-009:** Reading Room page (Research) — new build
- **B-010:** Sessions page (Research) — new build

---

## Current State vs. Design Doc

### ✅ Complete (Phase 1 + Phase 2)
- Fix 1: AI Observations reorder, date nav inline, weekly staleness collapse
- Fix 2: Left rail across all pages
- Fix 3: Research palette alignment
- Fix 4: BACKLOG entries

### 🔲 Not Yet Built (Phase 3 — do not rush)
- SVG icon migration (Lucide/Feather — swap emoji in rail)
- Reading Room page build (Research)
- Sessions page build (Research)
- Priorities → per-domain 🎯 focus layer (Research/Language/Jobs stubs)
- Library tab rename (Curator) — placeholder name, defer until right word surfaces

### 🔲 Pre-Build Requirement Still Outstanding
- **Cross-domain data model note** — OpenClaw task. Must be written before left rail was meant to start per v1.2 doc, but rail is now built. Data model note still needed before PostgreSQL/Neo4j work begins. All four domains (Curator, Research, Language, Jobs). Base `Item` entity sketch is already in the design doc — needs formalizing as a spec file.

---

## Tomorrow's Direction (Robert's intent)

1. **Design review:** Confirm current UI state against v1.2 design doc in detail — identify remaining gaps
2. **Research page functional work first** — content before more UI
3. **UI updates to research page** after functional work is stable

Robert's priority order: functional content on Research → then Research UI polish.

---

## Known Remaining Issues (minor)

- Parchment gradient on outer borders slightly lighter than original — `background: var(--bg)` added to `.main-content` closes most of it; may need per-page review
- `curator_index.html` is gitignored — has the rail changes but cannot be committed without force-adding. Low priority.

---

*Written by Claude Code — 2026-03-24 end of session*
*Branch: poc/research-source-quality — pushed to GitHub*
