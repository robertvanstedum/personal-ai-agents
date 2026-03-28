# BUILD — Research Intelligence Web UI
*Date: March 22, 2026*
*Spec: docs/specs/build-plan-2026-03-22.md*
*Branch: poc/research-web-ui*

---

## What Was Planned

Close the visual loop for the research intelligence pipeline — candidates review and article saving accessible from browser without CLI. Four-way nav connecting research pages.

---

## Pre-conditions Completed

- `cmd_promote()` and `cmd_retire()` in `agent/candidates.py` refactored to return status dicts (were print-only, returned None). Required before Flask routes could serialize responses.
- `sys.path` insert pattern confirmed for research module imports from `curator_server.py` root. `RESEARCH_ROOT` already defined in server.
- Duplicate detection behavior in `record_article_signal()` confirmed: returns None on duplicate save signal for same URL — no changes to `feedback.py` needed.
- `observe.html` had no existing nav bar — four-way nav addition was additive only.

---

## What Was Built

| File | Change |
|------|--------|
| `agent/candidates.py` | `cmd_promote()` / `cmd_retire()` now return status dicts |
| `research_routes.py` | **New** — Flask Blueprint owning all 8 research routes. All future research backend routes go here. |
| `curator_server.py` | Research section replaced with 5 lines: `try: import + register_blueprint / except: print warning`. Permanently closed to research changes. |
| `web/candidates.html` | New — parchment palette, filter bar, table with promote/retire, DOM removal on action |
| `web/save.html` | New — URL + title + session + note form, inline status (saved/duplicate/error) |
| `web/observe.html` | Four-way nav bar added |
| `curator_intelligence.html` | Research strip nav added (Observe · Candidates · Save) |

**Commits:**
- `_NewDomains/research-intelligence/`: `0f36e6c`
- `personal-ai-agents/`: `7516a6f`

**Architectural note:** Research routes extracted to Blueprint after initial build. curator_server.py is now closed to research changes — all future research routes go into `research_routes.py` only.

---

## Confirmed Working Output

Verification per spec:
1. `localhost:8765/research/candidates` — pending candidates visible
2. Promote → row removed from DOM, query added to `agent/config.json`
3. `localhost:8765/research/save` — form renders, saves article with note to `data/feedback/article_signals.json`

---

## Design Decisions Made During Build

- **Four-way inline nav only** (not left rail) — rail migration scoped out as separate task. Inline nav closes the research loop now; full rail migration happens after Robert approves visual design on new pages.
- **Left rail CSS spec locked in** (from claude.ai design review) — dark rail (`#1e1e2e`), per-domain content tokens (parchment for research, white for curator). Ready for migration task.
- **DOM removal on promote/retire** (not hide) — spec requirement; prevents stale row counts.

---

## Open Items → Next

- Left rail migration: all Curator + Research pages to unified chrome. Separate task.
- Verification of note field in `article_signals.json` after live save
- Bug #8 (Telegram save silent on NetworkError) — still open
- Robert to tune research tool over next week before expanding scope

---

## Cost

Not tracked for this build session.
