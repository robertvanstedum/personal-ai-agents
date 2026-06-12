# Roadmap & Backlog
*mini-moi · living document — append freely*

This is the working list of future build ideas, roadmap items, and loose
ends. **Not spec-track** — no Definition of Done or Commit sections, so
Design/Dev's filter ignores this file entirely (per
`docs/HANDOFF_PROCESS.md`). Add a line whenever something comes to mind.

When an item is ready to build: write it up as a proper spec in `_working/`
(with DoD + Commit), then remove or check it off here.

---

## Verify first

- **Career — what's actually live?** `spec_career_two_page_2026-06-11.md`
  (separate Positions table + filtered Active Pipeline board, collapsed
  cards) appears unbuilt, but the close_reason/priority/closed_at schema
  works on the existing single-board Kanban (NVIDIA test confirmed). Open
  `/guild/career` and `/guild/career/active` — if the single board is good
  enough for daily use, mark the two-page spec `deferred`. If it's cramped
  at real volume, it's a real build item.

- **Curator — Archive nav + UI consistency.** `spec_archive_navigation_2026-06-10.md`
  and `spec_curator_ui_consistency_2026-06-10.md` were written last session
  and may have been archived as "done" without being built (the Archive
  page issue was re-raised with a new screenshot this session). Check
  `/curator/archive` and Daily for the "show more" / "↑ top · show less"
  patterns — if missing, pull specs back from `_working/archive/`.

---

## Next — high value, ready to spec

- **Loop B/C/D first-run validation.** B/C/D (German watch, Curator scout,
  novelty watch) fire for the first time **Sunday 6/15**. No spec exists
  for "Robert reviews the first batch, judges quality, confirms go-live or
  recalibrates." Write before Sunday. Reference: `handoff_guild_phase4`
  (archive).

- **Guild Daily Briefing** (`/guild` homepage). Fully designed in
  `docs/GUILD.md` — four sections (Systems/Career/Build/Ahead), mockup
  built, Telegram timing resolved (7:00/7:30/8:30). Build discipline's data
  layer (`design_log`, `pipeline.items`) now exists specifically to feed
  this. Biggest "designed, data's ready, no page" gap.

---

## Batch — small items

- `NAMING_CONVENTIONS.md` audit (read-only, Claude Code)
- Career "what I'm targeting" panel — read-only `cos_context.json` display
- Private repo sync expansion — `sync_private_repo.sh` +
  `devagent_config.json` private paths (curator signals/sources/radar,
  German session/anki/progress data)
- `/start-build` + `/complete-build` discipline — dev_agent has `/start-build`
  but no `/complete-build` endpoint; Claude Code never calls either in practice.
  Add `/complete-build` to dev_agent, update `docs/HANDOFF_PROCESS.md` to
  require both calls as bookends of every build session. Closes the gap where
  specs stay `incomplete` or `in_build` forever after a build ships.
- Manual spec entry from `/guild/build` — hold until auto-archive test shows
  whether classification misses are common enough to need it
- Build Clarity round 2 — edit `spec_title`/`summary`/`github_issue` from
  UI, transition-history view on cards

---

## Later — correctly sequenced, don't pull forward

- Challenger Phase 3 (German), Phase 4 (Career) — after current sprint, per
  `docs/GUILD.md` roadmap. A file existing in archive doesn't mean overdue.
- Mein Deutsch v1.1 — Gespräche toggle (KI-Personas/Konversation pill),
  Lesen writing drill (GitHub #41) — post Aug 1
- Neo4j Phase 5 — trigger: 20+ tagged sources
- DB password hardening (GitHub #42) — post-Guild build window
- Design/Dev Level 2 — routing, doc lifecycle management
- CoS principal profile + composite character — bookshelved per
  `docs/design/GUILD_CoS_Identity_Founding_2026-06-11.md`

---

## Platform / Portfolio

- Full README update — reflect German v1.0, Curator v1.2, Guild all live
- Portfolio translation doc — mini-moi domains → org pattern mapping
  (table drafted in six-week-focus session, ready to write up)
- minimoi.ai landing — remaining "What's running" entries (Curator, Guild
  blurbs still need the em-dash/tightening pass "What this is" got)

---

*Roadmap & Backlog · Guild · 2026-06-12*
*`docs/GUILD.md`'s "Roadmap" section should point here rather than
duplicate — single source of truth.*
