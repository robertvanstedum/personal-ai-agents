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
| B-010 | **Sessions page** (Research) | ✅ Built 2026-03-25. `/research/sessions` live with topic selector, session list, per-finding notes and direction shifts. |
| B-015 | **Research sessions: resolve triage target labels** | Finding cards show raw pipeline text ("Target 1, Target 2, Target 3…"). For demo-ready/hosted stage, resolve these to actual target description strings from `config.json triage_targets`. Personal tool: acceptable as-is. Flag before demo recording. |
| B-016 | **Research sessions: source title truncation** | Source titles cut off mid-word in the session detail panel. Fix: `text-overflow: ellipsis` on `.source-title` with `white-space: nowrap; overflow: hidden`, or expand-on-hover tooltip. CSS-only, low effort. |
| B-017 | **Topic search bar** (Sessions + Observations) | Filter/search bar above the topic sidebar in `/research/sessions` and `/research/observe`. Needed when topic count grows past ~10. Deferred from 2026-03-25 session. |

## Curator

| # | Item | Notes |
|---|------|-------|
| B-006 | **UI bug fixes** | Spec in `_working/curator_ui_fix_plan.md`. Dedicated session, don't mix with other work. |
| B-012 | **BUG: curator UI changes wiped on every run** | Root cause: `curator_rss_v2.py` regenerates `curator_latest.html` and `curator_briefing.html` from a hard-coded template on every curator run, overwriting any manual edits to those files. This was the silent cause of UI regressions for ~1 week. Workaround in place: rail CSS/HTML now injected into the generator template. Long-term fix: decouple data from presentation — generator writes JSON only, Flask server renders from a Jinja template (see B-013). |
| B-013 | **ARCH: decouple curator HTML generation from curator_rss_v2.py** | `curator_rss_v2.py` should write `curator_latest.json` only. Flask server should render `curator_latest.html` dynamically from a Jinja2 template using that JSON. UI changes then live in the template and survive every curator run. Mirrors how `curator_intelligence.py` already works (writes JSON, server reads it via `/api/intelligence/latest`). Medium effort — requires new Jinja template + server route change + removing HTML generation from `curator_rss_v2.py`. OpenClaw to spec before build. |
| B-014 | **BUG: Telegram delivery not working** | ✅ Fixed 2026-03-25. Root cause: `telegram_bot.py` polling mode was using `bot_token` (same as OpenClaw), blocking inbound. Fixed by routing `run_bot_mode()` to `polling_bot_token` from keychain. |
| B-011 | **Card edge definition (refinement day)** | Card borders blend into parchment background, especially at the rail/content boundary. Fix: increase `--border2` contrast slightly, or add `box-shadow: 0 1px 4px rgba(42,36,24,0.10), 0 0 0 1px rgba(42,36,24,0.06)` to `.weekly-card`, `.topic-card`, and equivalent card classes across all domains. Warm light tan border or 10–15% opacity drop shadow. CSS-only, low effort. |

## Job Search

| # | Item | Notes |
|---|------|-------|
| B-007 | **LinkedIn CSV network map** | Drop export in repo when it arrives — network-map.js has data to work with. |

---

## Completed

| # | Item | Closed |
|---|------|--------|
| B-010 | **Sessions page** | 2026-03-25 |
| B-014 | **BUG: Telegram polling token conflict** | 2026-03-25 |

---

_Last updated: 2026-03-25 by Claude Code_
