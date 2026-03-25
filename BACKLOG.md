# BACKLOG.md
_Items we definitely want to do. Not scheduled. No priority implied._
_Promoted to active work by Robert's decision only. OpenClaw can add items. Only Robert removes or promotes them._

---

## Infrastructure

| # | Item | Notes |
|---|------|-------|
| B-001 | **Private GitHub repo setup** | Store private domains, personal context, job search materials. OpenClaw pushes periodically. Separate from public repo. Needed before job-search domain grows. |
| B-002 | **Demo recording** | Screen Studio or Loom — capture Curator tab flow across all five tabs. For GitHub case study. |
| B-003 | **Repo cleanup and reorganization** | Dedicated session. Move SPRINT/BUILD/PHASE files to `journal/`. Document as a release entry in `journal/`. |

## Research Intelligence Agent

| # | Item | Notes |
|---|------|-------|
| B-004 | **HTML reader** | Trigger Claude Code after 50+ library items. Spec in `WAY_OF_WORKING.md`. |
| B-005 | **Curator integration** | Validated sources pipeline. Phase 2, not PoC. |
| B-008 | **Source-level priority weights** | Allow demoting individual sources (e.g. Al Jazeera) without removing from candidate pool. Current system only weights topics. Needed for triage precision. |
| B-009 | **Reading Room page** (Research) | New page: saved articles under active investigation. Distinct from Queries (which manages search queries). Add to Research sub-nav when built: `Dashboard · Queries · Reading Room · Observations · Save · 🎯`. New route required — no `/research/reading-room` exists yet. |
| B-010 | **Sessions page** (Research) | New page: list of past `research.py` session files with their findings, filterable by topic. No `/research/sessions` route exists yet. New build required. |

## Curator

| # | Item | Notes |
|---|------|-------|
| B-006 | **UI bug fixes** | Spec in `_working/curator_ui_fix_plan.md`. Dedicated session, don't mix with other work. |
| B-012 | **BUG: curator UI changes wiped on every run** | Root cause: `curator_rss_v2.py` regenerates `curator_latest.html` and `curator_briefing.html` from a hard-coded template on every curator run, overwriting any manual edits to those files. This was the silent cause of UI regressions for ~1 week. Workaround in place: rail CSS/HTML now injected into the generator template. Long-term fix: decouple data from presentation — generator writes JSON only, Flask server renders from a Jinja template (see B-013). |
| B-013 | **ARCH: decouple curator HTML generation from curator_rss_v2.py** | `curator_rss_v2.py` should write `curator_latest.json` only. Flask server should render `curator_latest.html` dynamically from a Jinja2 template using that JSON. UI changes then live in the template and survive every curator run. Mirrors how `curator_intelligence.py` already works (writes JSON, server reads it via `/api/intelligence/latest`). Medium effort — requires new Jinja template + server route change + removing HTML generation from `curator_rss_v2.py`. OpenClaw to spec before build. |
| B-011 | **Card edge definition (refinement day)** | Card borders blend into parchment background, especially at the rail/content boundary. Fix: increase `--border2` contrast slightly, or add `box-shadow: 0 1px 4px rgba(42,36,24,0.10), 0 0 0 1px rgba(42,36,24,0.06)` to `.weekly-card`, `.topic-card`, and equivalent card classes across all domains. Warm light tan border or 10–15% opacity drop shadow. CSS-only, low effort. |

## Job Search

| # | Item | Notes |
|---|------|-------|
| B-007 | **LinkedIn CSV network map** | Drop export in repo when it arrives — network-map.js has data to work with. |

---

## Completed

_Nothing yet._

---

_Last updated: 2026-03-24 by Claude Code_
