# Mini-moi Personal AI Curator
## Sprint to 1.0 — March 2026

**Target:** Public GitHub launch, production-ready, two weeks

---

## Context and Goal

Mini-moi has been in production since February 9, 2026. The system delivers a daily intelligence briefing via Telegram — geopolitics and finance, scored and ranked from a pool of RSS feeds and X bookmarks. Phases 3A through 3C.7 are complete. The system works.

The goal of this sprint is to reach a coherent, presentable 1.0: broader source coverage, source-level quality filtering, Mac Mini migration for always-on operation, and the technical infrastructure for the investigation workspace feature planned for 1.1. GitHub cleanup and public launch follow the build — not before.

---

## Current State (March 13, 2026)

| | |
|---|---|
| Production since | February 9, 2026 |
| Daily briefing | Telegram, 7 AM, top 20 articles |
| Scoring pool | 722 candidates (390 RSS + 332 X bookmarks) |
| Signal store | 425 signals (398 historical + 27 incremental) |
| Daily cost | ~$0.30/day (xAI grok, single-stage) |
| Last commit | Phase 3C.7 complete — incremental X bookmark pull |
| GitHub | public repo pushed, private repo synced |

---

## 1.0 Build Plan

Four workstreams. Build in this order — Mac Mini migration last so development happens on the familiar MacBook environment.

| Feature | Description | Status |
|---|---|---|
| Source scoring | Score at domain level, not just article. Explicit trust/drop/deprioritize per source. Suppresses Investing.com, Duran-type noise before scoring. | Build |
| Broader sources | Add 5-8 institutional RSS feeds (BIS, Fed, ECB, NBER, arXiv). Web search as additional candidates — topic-guided, domain-whitelisted, competes for same 20 slots. | Build |
| Investigation workspace infra | Technical plumbing for 1.1 feature — topic store, timeline index, annotation schema. No UI. Enables fast 1.1 build. | Build |
| Mac Mini migration | Port to always-on server. Resolves sleep/wake scheduling workarounds. Prerequisite for calling this production-ready. | Build |
| GitHub cleanup | Issues, roadmap, README all current. Reflects actual system state. Happens after build, not before. | After build |

---

## Workstream 1: Source Scoring

### The problem

Investing.com, The Duran, and similar sources occupy scoring slots that better content should win. The current pipeline scores every article equally regardless of source quality. There is no mechanism to suppress a low-signal source without removing it entirely from the feed list.

### The design

Source scoring operates at the domain level, upstream of article scoring. Every domain in the candidate pool carries a trust weight that multiplies its articles' scores before ranking.

- **trusted** — weight up, always competes strongly
- **neutral** — current behavior, no change
- **deprioritize** — score multiplier < 1, competes but handicapped
- **drop** — never enters scoring pool

Source ratings are set explicitly via a new command or UI action. Over time, article-level feedback (like/dislike) accumulates into source-level reputation automatically — if you consistently dislike ZeroHedge sensationalism, ZeroHedge deprioritizes without manual action.

### New file: `curator_sources.json`

Source registry lives in a standalone file — add/remove/rate sources without touching code. Schema:

```json
{ "domain": "investing.com", "trust": "drop", "set_by": "explicit", "note": "aggregator, SEO noise" }
```

### Integration point

`curator_rss_v2.py` reads `curator_sources.json` before scoring. Drop sources never enter the candidate pool. Deprioritized sources enter with a score penalty. Trusted sources enter with a bonus.

---

## Workstream 2: Broader Sources

### The problem

The daily brief is limited to sources defined at RSS setup. Relevant content from institutional sources and current events outside the subscription bubble never surfaces. The fix is not to change the output — still 20 articles — but to improve what competes for those 20 slots.

### Two additions

**Institutional RSS feeds (immediate)**

Low effort, high quality. These sources publish original research and data — not aggregated news. Added March 13, 2026:

- War on the Rocks — defense/security analysis
- Foreign Affairs — premier geopolitics journal
- arXiv q-fin — academic preprints (capped at 15)
- Just Security — national security / international law
- CEPR VoxEU — economic policy research columns

Additional institutional sources (BIS, Fed, ECB, NBER) available to add later as needed — not in this sprint.

**Web search candidates (topic-guided)**

Web search runs daily, constrained to trusted domains and known topic areas. Results enter the same scoring pool and compete for the same 20 slots — no new output format, no separate section.

- Searches defined by topic list (geopolitics, monetary policy, EM markets, etc.)
- Domain whitelist — only trusted outlets, no SEO aggregators
- Haiku pre-filters web results before they reach Grok scoring
- Dedup against today's RSS articles — no duplicates surface
- Cost-controlled: bounded query list, Haiku handles bulk filtering

---

## Workstream 3: Investigation Workspace Infrastructure

### What this is

The investigation workspace is a 1.1 feature — not released in 1.0. But the technical infrastructure goes in during 1.0 so the 1.1 build is fast. Plumbing only. No UI in this sprint.

### The 1.1 feature vision

An investigation is a persistent, evolving research thread on a topic. Example: the Ethiopia-Eritrea conflict. The user opens an investigation, the system pulls relevant content across a wide timeline — news, books, scholarly articles, primary sources. The user reads, annotates, and iterates. The investigation can be closed and reopened months later with full history intact.

- **Start investigation** — name, topic, scope (date range, source types)
- **System pulls and synthesizes** — wide timeline, multiple source types
- **User annotates** — comments, ratings, follow-up questions
- **Iterate** — each session deepens the research, history preserved
- **Close** — archived but retrievable; reopen 6 months later with full context

### Infrastructure to build in 1.0

Minimal schema and storage that enables the 1.1 build without constraining it:

- `investigations/` directory in workspace — one JSON file per investigation
- Investigation schema: `id`, `topic`, `created_at`, `status` (active/closed), `entries[]`
- Entry schema: `timestamp`, `source_type`, `content`, `user_annotation`, `score`
- Topic index — maps topic keywords to relevant signals already in `curator_signals.json`
- No API calls, no UI, no Telegram integration — data layer only

---

## Workstream 4: Mac Mini Migration

### Why it matters for 1.0

Running a 'production' system on a MacBook that sleeps is a credibility gap for a public portfolio piece. The Mac Mini migration makes the system genuinely always-on — no launchd sleep/wake workarounds, no missed 7 AM runs if the laptop is closed.

### Migration scope

- Port Python environment, venv, all dependencies
- Migrate keychain credentials (xAI, Anthropic, X OAuth tokens)
- Transfer `curator_signals.json`, `curator_preferences.json`, `x_pull_state.json`
- Reconfigure launchd plists for Mac Mini
- Verify Telegram delivery from new machine
- Decommission MacBook as primary host — keep as dev/test environment

### Sequencing

Mac Mini migration happens last in the sprint. All feature development stays on the MacBook. Migration is a cut-over, not a parallel run.

---

## 1.1 Plan — Investigation Workspace

Scoped here, built after 1.0 launches. Infrastructure from Workstream 3 enables fast delivery.

| Feature | Description | Status |
|---|---|---|
| Investigation UI | Start, view, and annotate investigations via web UI (existing Flask server). | 1.1 |
| Multi-source pull | Pull across news, academic, and book sources for a given topic and date range. | 1.1 |
| Synthesis layer | Haiku summarizes across sources, surfaces contradictions and key threads. | 1.1 |
| Annotation store | User comments and ratings persist per investigation entry. | 1.1 |
| Archive and restore | Close investigations cleanly; restore with full history after months. | 1.1 |
| Telegram integration | Optional: new investigation findings delivered to Telegram on schedule. | 1.1 |

---

## Deferred — Not in Sprint

- **Image analysis (Phase 3D)** — chart images from analyst tweets, vision model. Infrastructure gate in place. Post-1.0.
- **Postgres migration** — `curator_costs.json` already row-structured, COPY-ready. Deferred until volume demands.
- **Deep Dive ratings UI** — 4-star rating, two-sided feedback loop. Post-1.0.
- **Language learning domain** — conversation with Grok, vocabulary extraction, phrase practice. Next major domain after 1.0 ships.
- **Haiku pre-filter evaluation** — one-week data window on X article performance before deciding. Checkpoint built into cron note.

---

## GitHub Cleanup — After the Build

GitHub cleanup happens after all four workstreams are complete. Cleaning before building means cleaning twice.

- **README** — reflects actual 1.0 system, not the build history narrative
- **Issues** — close completed work, open new issues for 1.1 scope
- **Roadmap** — v1.0 complete, v1.1 scoped, future domains noted
- **docs/** — test reports current, portfolio/phase summaries accurate
- **CLAUDE.md** — signal store state, agent division of labor, implementation decisions all current

---

## Sprint Timeline

| Week | Focus | Deliverable |
|---|---|---|
| Week 1 | Mar 13–19 | Source scoring + `curator_sources.json`. Institutional RSS feeds added. Web search candidates wired in. |
| Week 2 | Mar 20–26 | Investigation workspace infrastructure. Mac Mini migration. GitHub cleanup and public launch. |

---

*Prepared: March 13, 2026*  
*For use with OpenClaw (planning) and Claude Code (implementation)*
