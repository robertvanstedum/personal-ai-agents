# ARCHITECTURE.md
## Mini-moi Personal AI Curator — System Design

_Last updated: March 2026 — reflects 1.0 production system_

---

## What Was Actually Built

This document replaces the February 2026 planning doc (Docker, docker-compose, FastAPI architecture — all superseded). The system that emerged is simpler and more direct: flat Python scripts, Flask web portal, macOS launchd scheduling, macOS Keychain for credentials, and JSON files designed for future DB migration.

No Docker. No FastAPI. No containers. Production-ready without them.

---

## Design Principles

These were set before the first line of code and held throughout:

**1. Local-first data layer**
All learned state lives in flat JSON files on the local machine. Schema is designed to be Postgres-ready — one `COPY` command when volume demands migration, not a rewrite. Context graph design (Neo4j) is planned for relationship mapping but not yet activated. Everything is portable: move machines, switch providers, go offline — preferences travel with you.

**2. Model-agnostic by design**
User profile injection happens at the dispatcher level, not inside any model's prompt. The system ran on local Ollama/Gemma first — no cloud dependency. Swap any model at any layer without touching personalization logic. The system has run across Ollama, Haiku, Sonnet, and multiple xAI Grok variants. It will continue to evaluate as the landscape evolves.

**3. Swappable architecture, not swappable configs**
Model-agnosticism is enforced structurally. The scoring function receives a normalized article batch and a user profile. It does not know or care which model is behind it.

**4. Quiet paths over noise**
If there is nothing meaningful to surface, nothing is sent. Applies to code (empty observations suppressed), documentation (no padding), and Telegram delivery (no "nothing to report" messages).

**5. Operator stays in control**
No autonomous agent-to-agent calls. The build workflow is: design → human reviews → build → human confirms → ship. Agents do not push to remote without explicit confirmation.

---

## Pipeline Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     DAILY CANDIDATE POOL                        │
│   RSS Feeds (~400)  +  X Bookmarks (~332)  +  Brave Web (~50)  │
│                        ~900 total candidates                     │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   curator_rss_v2.py                             │
│                                                                 │
│   1. Source trust filter (curator_sources.json)                 │
│      drop tier → excluded before scoring                        │
│      trusted tier → 1.5× score multiplier                      │
│                                                                 │
│   2. Mechanical mode (--model=ollama)                           │
│      Local Ollama/Gemma — keyword scoring, no external calls    │
│                                OR                               │
│   3. AI mode                                                    │
│      Stage 1: Haiku pre-filter (~900 → ~50)                     │
│      Stage 2: Reasoning model + injected user profile           │
│               [fallback: Haiku with same profile]               │
│                                                                 │
│   4. Top 20 ranked, scored, categorized                         │
└────────────────────────┬────────────────────────────────────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
   Telegram (7 AM)          Web portal (Flask)
   like/dislike/save        Daily · Library · Dives · Priorities
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│              curator_feedback.py                                │
│   Reactions → curator_preferences.json (local)                 │
│   Tomorrow's scorer loads updated profile                       │
└─────────────────────────────────────────────────────────────────┘

   (7:30 AM, separate launchd job)
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│              curator_intelligence.py — AI Observations          │
│                                                                 │
│   Obs 1: Topic velocity — today vs 30-day baseline             │
│   Obs 2: Discovery candidates — new Brave domains, Haiku-rated  │
│   Obs 3: Source anomalies — trusted source drift (Haiku)        │
│   Obs 4: US press blind spots — non-US/US coverage gap (Haiku)  │
│   Obs 5: Lateral connections — adjacent topics (Sonnet, Sunday) │
│                                                                 │
│   → Telegram message (5 lines max, quiet paths enforced)        │
│   → intelligence_YYYYMMDD.json (OpenClaw workspace)             │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
              curator_intelligence.html
              (web UI — response capture)
                         │
                         ▼
              intelligence_responses.json
              (seed of personal memory system → 1.1 RAG layer)
```

---

## Source Pool

Three source types, all competing for the same 20 daily slots:

| Source | Volume | Notes |
|---|---|---|
| RSS feeds | ~400/day | Institutional sources, news outlets, analysis |
| X bookmarks | ~332 enriched signals | Enriched with destination URL text, Haiku-classified topics |
| Brave web search | ≤20/day | Topic-guided by active priorities + baseline queries |

**Source trust scoring** (`curator_sources.json`): Domain-level trust weights applied upstream of article scoring. Trusted sources get a 1.5× multiplier; drop-tier sources are excluded before the scoring stage (saves tokens). New Brave domains are auto-logged as probationary, Haiku-evaluated daily.

---

## Model Tiers

All tiers are swappable. Profile injection at the dispatcher level means model changes don't affect personalization.

| Role | Current | Swappable to |
|---|---|---|
| Local / mechanical | Ollama + Gemma | Any Ollama-compatible model |
| Bulk pre-filter | Claude Haiku | Any low-cost model |
| Daily ranking | xAI Grok (reasoning variant, temp=0.7) | Any scoring model; A/B test protocol documented |
| Deep Dives + dev | Claude Sonnet | Any high-capability model |
| AI Observations (daily) | Claude Haiku | Any low-cost model |
| AI Observations (weekly) | Claude Sonnet | Any reasoning model |

**Model upgrade protocol:** Documented in `docs/CASE_STUDY_GROK41_MODEL_TUNING.md`. Key steps: A/B on same batch, read flagged articles before tuning, separate model validation from temperature testing, CLI flags not code edits.

---

## Scheduling (launchd)

All jobs run via macOS launchd — reliable across sleep/wake cycles, unlike cron.

| Job | Time | File |
|---|---|---|
| X bookmark pull | Before briefing | `run_curator_cron.sh` (calls `x_pull_incremental.py` first) |
| Daily briefing | 7:00 AM | `com.vanstedum.curator-rss.plist` |
| AI Observations | 7:30 AM | `com.vanstedum.curator-intelligence.plist` |
| Priority feed | 2:00 PM | `com.vanstedum.curator-priority-feed.plist` |

Shell wrapper pattern: each plist calls a `.sh` wrapper that activates the venv, runs the script, and logs to `logs/`.

---

## Data Files

**Committed to repo (source code + configs):**

| File | Purpose |
|---|---|
| `curator_sources.json` | Domain trust registry |
| `curator_rss_v2.py` | Main pipeline |
| `curator_intelligence.py` | AI Observations runner |
| `curator_feedback.py` | Reaction processing |
| `curator_server.py` | Flask web portal + API |
| `curator_utils.py` | Shared helpers (Telegram, utilities) |
| `curator_priority_feed.py` | Brave web search + priority management |

**Not committed (operational data — private):**

| File | Location | Purpose |
|---|---|---|
| `curator_preferences.json` | repo root | Learned user profile (personal) |
| `curator_signals.json` | repo root | 425 enriched X bookmark signals |
| `curator_history.json` | repo root | Rolling article history |
| `curator_latest.json` | repo root | Today's full scored pool (for AI Observations) |
| `intelligence_YYYYMMDD.json` | `~/.openclaw/workspace/` | Daily AI Observations output |
| `intelligence_state.json` | `~/.openclaw/workspace/` | 30-day rolling baseline |
| `intelligence_responses.json` | `~/.openclaw/workspace/` | User responses to observations |

---

## Web Portal

Flask server (`curator_server.py`) serves a local web UI. Five views:

| View | Route | Purpose |
|---|---|---|
| Daily briefing | `/` | Top 20 articles with scores, feedback controls, deep dive trigger |
| Reading library | `/library` | Searchable archive of all saved articles |
| Deep dives | `/deep-dives` | Archive of structured research briefs |
| Signal priorities | `/priorities` | Time-bounded focus injections |
| AI Observations | `/curator_intelligence.html` | Daily observations + response forms |

API endpoints: `POST /api/intelligence/respond`, `GET /api/intelligence/latest`, feedback/deep-dive endpoints.

---

## AI Observations Layer (WS5)

Added in the final sprint to 1.0. The system's first proactive capability — it now monitors its own output and reports what it noticed, rather than only reacting to article fetch results.

**Five observation types:**

| # | Observation | Model | Cadence | Cost |
|---|---|---|---|---|
| 1 | Topic velocity | Haiku | Daily | ~$0.005 |
| 2 | Discovery candidates | Haiku | Daily (≤5) | ~$0.01 |
| 3 | Source anomalies | Haiku (on flag) | Daily | ~$0.005 avg |
| 4 | US press blind spots | Haiku (on flag) | Daily | ~$0.005 avg |
| 5 | Lateral connections | Sonnet | Sunday only | ~$0.07/week |

**Quiet paths are mandatory.** All observations suppress output when nothing meaningful is detected. The daily message never pads with "nothing to report."

**Response capture (Phase C):** `curator_intelligence.html` displays observations with response forms. Responses written to `intelligence_responses.json` — append-only, structured for future RAG queries. In 1.0, responses are stored but not yet acted on. In 1.1, the Sonnet lateral connections prompt reads them, and `pending_action` items activate automatically.

---

## Learning Loop

```
User reaction (Like / Dislike / Save)
      ↓
curator_feedback.py
      ↓
curator_preferences.json (local)
  learned_patterns:
    preferred_themes, preferred_sources
    preferred_content_types, avoid_patterns
      ↓
Injected at dispatcher level into next scoring run
      ↓
Cheaper models perform better with context than expensive models without it
```

**Cold start solution:** 398 hand-saved X bookmarks ingested as Save signals in one session — 415 scored signals before the first production run.

**Anti-filter-bubble mechanisms:**
- 20% serendipity reserve — surfaces articles outside learned patterns
- 30-day decay gate — signals older than 30 days get half-weight
- Signal normalization — X bookmarks weighted against direct feedback volume

---

## Planned Infrastructure (Not Yet Activated)

| Component | Status | Notes |
|---|---|---|
| Postgres | Installed, not active | `curator_costs.json` already row-structured, `COPY`-ready |
| pgvector | Installed, not active | Ready for embedding-based search in 1.1+ |
| Neo4j | Installed, not active | Context graph for relationship mapping, post-1.1 |
| Investigation workspace | Scoped for 1.1 | Persistent research threads, annotation, archive |

Decision: activate databases when functional completeness demands migration, not before. JSON is the right storage layer until volume or query complexity requires more.

---

## Agent Division of Labor

| Role | Tool | Responsibility |
|---|---|---|
| Strategy Agent | Claude.ai | Architecture, design, documentation drafts |
| Memory Agent | OpenClaw | Context, validation, CHANGELOG, commit commands |
| Implementation Agent | Claude Code | Code only — one step at a time, confirms with Robert |

Human (Robert) is the decision point between all agents. No autonomous agent-to-agent calls. See `WAYS_OF_WORKING.md` for full protocol.

---

## 1.1 Roadmap

- **Investigation workspace** — persistent research threads (topic, timeline, annotation, archive)
- **Telegram reply capture** — Haiku classifies replies to AI Observations messages → `intelligence_responses.json`
- **RAG activation** — pgvector indexes responses, signals, dialog history; every LLM call retrieves relevant personal context
- **Neo4j activation** — relationship mapping across topics, positions, reading history
- **"What have I thought about this before?"** — becomes a real query

---

_This document reflects the 1.0 production system as of March 2026._
_Update when architecture changes — not when plans change._
