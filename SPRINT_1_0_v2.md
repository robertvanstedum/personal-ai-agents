# Mini-moi Personal AI Curator
## Sprint to 1.0 — March 2026 (v2)

**Target:** Public GitHub launch, production-ready, two weeks
**Updated:** March 14, 2026
**For use with:** Claude.ai (strategy), OpenClaw (memory + validation), Claude Code (implementation)

---

## Agent Roles

Three agents collaborate on this project. Each has a distinct role. Respect the division.

| Agent | Role | Does not do |
|-------|------|-------------|
| **Claude.ai** | Strategy, architecture, design, formal specs, long documents, session summaries | Does not have local file access. Does not know current file state unless told. |
| **OpenClaw** | Memory, execution, local context, sanity check on designs, validation against actual files | Does not design from scratch. Primary check before any spec goes to Claude Code. |
| **Claude Code** | Implementation, file editing, script execution, step-by-step builds | Does not set strategy. Does not jump steps. Confirms with Robert between each step. |

**Key principle:** OpenClaw is the only agent that has seen everything — every file, every decision, every commit. Before any significant spec goes to Claude Code, OpenClaw validates it against local reality. Not to rewrite it — to confirm it doesn't contradict what's already on disk.

**Workflow:**
1. Claude.ai produces design or spec
2. OpenClaw validates against local files — flags conflicts, confirms schema, answers open questions
3. Robert confirms
4. Claude Code builds one step at a time, stops and confirms between steps
5. OpenClaw ingests updated docs from repo — repo is source of truth, OpenClaw reads from it

---

## Architecture — Three-Tier Model

This sprint established the curator's three-tier architecture. All design decisions flow from this:

| Tier | Name | Source | Delivery | Purpose |
|------|------|--------|----------|---------|
| 1 | Daily Briefing | RSS pool + X bookmarks | Telegram 7AM, top 20 | Morning pulse — what happened |
| 2 | Priority Feed | Brave web search on active priority keywords | Web UI only, on demand | Active topic monitoring — go deep when you want |
| 3 | Deep Dive | Scholarly repositories + current web | Web UI, on demand | Research launchpad — history, analysis, bibliography |

---

## Current State (March 14, 2026)

| | |
|---|---|
| Production since | February 9, 2026 |
| Daily briefing | Telegram, 7 AM, top 20 articles |
| Scoring pool | 722 candidates (390 RSS + 332 X bookmarks) |
| Signal store | 425 signals (398 historical + 27 incremental) |
| Daily cost | ~$0.30/day base + ~$0.06–0.21/day priority feed |
| RSS sources added Mar 13 | War on the Rocks, Foreign Affairs, arXiv q-fin, Just Security, CEPR VoxEU |
| Priority Feed | Built and running — Steps 1–6 complete, plist registered at 14:00 daily |
| Brave Search API | Live, freshness=pw, expanded whitelist (domestic + international) |
| GitHub | Public repo pushed, private repo synced |

---

## Workstream Status

### Workstream 1: Source Scoring — TO BUILD

**Problem:** Investing.com, The Duran, and similar sources occupy daily scoring slots that better content should win. No mechanism to suppress noise without removing a source entirely.

**Design:** Domain-level trust weights upstream of article scoring.

- `trusted` — score multiplier > 1, always competes strongly
- `neutral` — current behavior, no change
- `deprioritize` — score multiplier < 1, competes but handicapped
- `drop` — never enters scoring pool

**New file:** `curator_sources.json`
```json
{ "domain": "investing.com", "trust": "drop", "set_by": "explicit", "note": "aggregator, SEO noise" }
```

**Integration:** `curator_rss_v2.py` reads `curator_sources.json` before scoring. Drop sources never enter the candidate pool.

**Source discovery foundation (new — see Workstream 5):** `curator_sources.json` also tracks unknown domains surfaced by Brave but not yet whitelisted. Logged automatically, reviewed manually. Foundation for proactive discovery in 1.1.

**Status:** Not started. Build this week.

---

### Workstream 2: Broader Sources — LARGELY COMPLETE

**Institutional RSS feeds — DONE (March 13)**
Added to RSS pool:
- War on the Rocks — defense/security analysis
- Foreign Affairs — premier geopolitics journal
- arXiv q-fin — academic preprints (capped at 15)
- Just Security — national security / international law
- CEPR VoxEU — economic policy research columns

Additional institutional sources (BIS, Fed, ECB, NBER) available later — not in this sprint.

**Priority Feed (Tier 2 web search) — DONE (March 13–14)**
- Brave Search API live, credentials in keychain
- `curator_priority_feed.py` built and running
- `priorities.json` schema extended with `feed[]` and `feed_last_updated`
- Flask routes: `/api/priority/<id>/feed`, `/api/priority/<id>/refresh`
- UI: feed section on priority cards, score color coding, Refresh button
- launchd plist: `com.vanstedum.curator-priority-feed.plist`, fires 14:00 daily
- `freshness=pw` on all Brave calls
- Whitelist expanded — international policy + US domestic sources

**Whitelist (current):**
```
reuters.com, ft.com, economist.com, foreignpolicy.com, foreignaffairs.com,
project-syndicate.org, politico.com, theguardian.com, nytimes.com,
bloomberg.com, warontherocks.com, justsecurity.org, crisisgroup.org,
acleddata.com, hrw.org, cfr.org, chathamhouse.org, bbc.com, apnews.com,
aljazeera.com, iiss.org, sipri.org, worldbank.org, imf.org, un.org,
icij.org, wsj.com, washingtonpost.com, cbsnews.com, cnn.com,
chicagotribune.com, chicago.suntimes.com, chicagobusiness.com,
blockclubchicago.org, crimelab.uchicago.edu, counciloncj.org,
therealdeal.com, wirepoints.org
```

**Remaining in Workstream 2:**
- 🔖 Save button on priority feed articles (Haiku, small UI change)
- Web search in main briefing — topic-guided searches competing for daily 20 slots (distinct from Priority Feed)

**Priority feed behavior decisions (locked):**
- 50 article cap per priority, drop oldest when exceeded
- Expired priorities: feed read-only, Refresh hidden, "feed paused" label
- Inactive (manual deactivate): feed visible, Refresh stays
- Reactivation: resume from existing history, no reset
- Save only (no Like/Dislike) on priority feed articles — avoids distorting daily briefing signal

---

### Workstream 3: Investigation Workspace Infrastructure — TO BUILD

**What this is:** Data layer only. No UI in 1.0. Enables fast 1.1 build.

**Infrastructure:**
- `investigations/` directory in workspace — one JSON file per investigation
- Investigation schema: `id`, `topic`, `created_at`, `status` (active/closed), `entries[]`
- Entry schema: `timestamp`, `source_type`, `content`, `user_annotation`, `score`
- Topic index — maps topic keywords to relevant signals in `curator_signals.json`

**Deep Dive Tier 3 integration (new):** Investigation infrastructure also serves as the data foundation for Deep Dive scholarly sources. Repository connectors to build:
- Project Gutenberg — public domain books, API available
- Internet Archive / Open Library — broader catalog, controlled lending
- SSRN — economics, finance, law working papers
- arXiv — full archive (already in RSS for q-fin, broader scope here)
- JSTOR — partial free access, include as reference

Deep Dive bibliography output: structured short list grouped by tier (current / analysis / your library / deep repositories), 10–15 items, annotated, saveable locally.

**Status:** Not started. Build Week 2.

---

### Workstream 4: Mac Mini Migration — TO BUILD

**Why it matters:** Running production on a MacBook that sleeps is a credibility gap for a public portfolio piece.

**Migration scope:**
- Port Python environment, venv, all dependencies
- Migrate keychain credentials (xAI, Anthropic, X OAuth tokens, Brave Search)
- Transfer `curator_signals.json`, `curator_preferences.json`, `x_pull_state.json`, `priorities.json`
- Reconfigure all launchd plists for Mac Mini
- Verify Telegram delivery from new machine
- Decommission MacBook as primary host — keep as dev/test

**Sequencing:** Last in sprint. All feature development stays on MacBook. Migration is a cut-over, not a parallel run.

**Status:** Not started. Build end of Week 2.

---

### Workstream 5: Intelligence Layer — NEW, TO BUILD

**The problem:** The system is a well-engineered pipeline with a learned preference profile. The LLM is used reactively — score this, filter that. It is not proactively reasoning about what you should be seeing. This is the gap between a smart filter and an actual intelligence.

**Design philosophy:** Build observation infrastructure now. Actions come in 1.1. The system generates intelligence; you decide what to do with it.

**New component: `curator_intelligence.py`**

Runs daily after scoring. Produces a structured observation log. Four observation types:

**1. Topic velocity (daily)**
Which topics are gaining coverage momentum today vs. your 30-day baseline? Which interest profile topics have zero coverage today?

**2. Source anomalies (daily)**
Any trusted source behaving differently than its norm? Haiku reviews a source's last 10 articles against its historical profile and flags drift.

**3. Discovery candidates (daily)**
Unknown domains surfaced by Brave that passed Haiku quality check. Logged with the query that found them and a one-line Haiku quality assessment. You review periodically and add credible ones to the whitelist.

**4. Lateral connections (weekly, Sonnet)**
Looks across your recent reading history and signal profile and reasons: what related topics, adjacent angles, or second-order implications are you not covering? Produces 2–3 suggested topics with example sources and a one-line rationale.

Example output:
*"You've been tracking Iran sanctions heavily. You haven't touched India's oil purchase workarounds — India is now Iran's largest crude buyer. Suggested: The Hindu Business Line, S&P Global Commodity Insights."*

**5. US press blind spots (opportunistic)**
Stories with high coverage velocity in non-US sources (Al Jazeera, DW, Spiegel, O Globo — already in RSS) that have low or zero coverage in US outlets. The gap itself is the signal.

Example output:
*"Sahelian junta coordination meeting in Bamako this week — covered extensively in French and African press, essentially absent from US outlets. Potentially significant given your interest in geopolitical realignment."*

**Delivery:**
- Daily observation: separate Telegram message (brief — 5 lines max) + stored as `intelligence_YYYYMMDD.json`
- Weekly lateral connections: separate Telegram message
- All observations stored locally for Investigation Workspace to reference in 1.1

**Cost model:**
- Daily Haiku observations: ~$0.02–0.05/day
- Weekly Sonnet lateral connection: ~$0.05–0.10/week
- Total intelligence layer: ~$0.50–1.00/week — acceptable

**Source discovery integration:**
The discovery candidates observation feeds directly into Workstream 1's `curator_sources.json`. Unknown domains Haiku approves enter a `probationary` tier — they pass through to scoring at a slight penalty until enough signal accumulates to graduate them to `trusted` or `deprioritize`. This is the foundation of proactive source discovery without opening the gate to junk.

**Status:** Not started. Build alongside Workstream 3 in Week 2 — they share the observation storage layer.

---

## Revised Sprint Timeline

| Day | Focus | Deliverable |
|-----|-------|-------------|
| Mar 14 (today) | Workstream 2 remaining | 🔖 Save button on priority feed articles |
| Mar 15–16 | Workstream 1 | `curator_sources.json` + source scoring in daily pipeline |
| Mar 16–17 | Workstream 2 remaining | Web search in main briefing → daily 20 slots |
| Mar 18–19 | Workstream 5 foundation | `curator_intelligence.py` — daily observations, discovery candidate log |
| Mar 20–21 | Workstream 5 | Weekly lateral connections (Sonnet), US press blind spot detection |
| Mar 21–23 | Workstream 3 | Investigation workspace infrastructure + Deep Dive repository connectors |
| Mar 24–25 | Workstream 4 | Mac Mini migration |
| Mar 26 | GitHub cleanup | README, issues, roadmap, CLAUDE.md all current. Public launch. |

---

## 1.1 Plan — Investigation Workspace + Intelligence Actions

Infrastructure from Workstreams 3 and 5 enables fast 1.1 delivery.

| Feature | Description |
|---------|-------------|
| Investigation UI | Start, view, annotate investigations via web UI |
| Multi-source pull | Pull across news, academic, repository sources for a topic and date range |
| Synthesis layer | Haiku summarizes across sources, surfaces contradictions and key threads |
| Annotation store | User comments and ratings persist per investigation entry |
| Archive and restore | Close investigations; restore with full history after months |
| Proactive source graduation | Probationary sources auto-graduate based on accumulated signal |
| Intelligence actions | System acts on its own observations — not just generates them |
| Telegram integration | Optional investigation findings delivered on schedule |

---

## Deferred — Not in Sprint

- **Image analysis (Phase 3D)** — chart images from analyst tweets, vision model. Post-1.0.
- **Postgres migration** — `curator_costs.json` COPY-ready. Deferred until volume demands.
- **Deep Dive ratings UI** — 4-star rating, two-sided feedback loop. Post-1.0.
- **Language learning domain** — next major domain after 1.0 ships.
- **Haiku pre-filter evaluation** — one-week data window checkpoint in cron note.
- **Kindle/Goodreads integration** — no stable API available. Deferred indefinitely; open repositories (Gutenberg, Open Library) cover the book layer.

---

## GitHub Cleanup — After All Workstreams Complete

- **README** — reflects actual 1.0 system architecture (three-tier model, intelligence layer)
- **Issues** — close completed work, open 1.1 scope
- **Roadmap** — v1.0 complete, v1.1 scoped, future domains noted
- **docs/** — test reports current, portfolio summaries accurate
- **CLAUDE.md** — signal store state, agent division of labor, all implementation decisions current

---

*v2 prepared: March 14, 2026*
*Reflects actual build state as of March 14 and design decisions from March 13–14 sessions*
*Repo is source of truth — OpenClaw ingests from repo, not the other way around*
