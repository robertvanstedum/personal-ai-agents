# Personal AI Briefing System
### *Mini-moi — not a general intelligence, but a specific one. Yours.*

A personal intelligence system. The first domain: the intersection of finance and geopolitics. Health, language learning, and others to follow.

Learns from your history and preferences, with deliberate friction built in. The goal isn't a curated feed — it's better thinking.

> Production system, daily use since Feb 2026. Designed to expand across domains — see [Roadmap](#roadmap).
> Three model tiers in production: Haiku for bulk filtering, a reasoning model for daily ranking, Sonnet for Deep Dives and development reasoning. All swappable by design.

---

## What This Is Really About

The cloud LLMs have the world's knowledge. That problem is largely solved.

The hard part — the part that actually matters for real decisions — is acting in your specific situation. Your history. Your goals. Your risk tolerance. Your team's context and motivation. General intelligence is widely available now. Specific intelligence, the kind that knows you and acts for you, isn't.

That's what this builds toward.

I started with myself: a daily briefing on geopolitics and finance, shaped by how I actually think, learning from what I actually read and save. But the vision is bigger. If I had a team at work — people and agents together — I'd want this same local context and motivation at the center of it. Not a generic assistant that knows everything about the world but nothing about us. A specific capability, grounded in our history, our goals, our way of making decisions under uncertainty.

That's what *Mini-moi* means. Not a mini version of a large language model. A system that carries your particular point of view and acts on your behalf — in your real world, for you, now.

The cloud LLMs are tools this system reaches out to when it needs them. Your memory, your preferences, your reasoning — those stay with you. The agents are your team members, not the cloud's.

---

## Why I Built This

The best way to understand something is to build it.

I've believed for a while that the future of AI isn't just larger models with more world knowledge — it's systems that carry specific context: your history, your goals, your way of reasoning through uncertainty. The cloud LLMs are remarkable, but they don't know you. They can't act for you in any meaningful sense without that layer.

I wanted to build that layer. Not as a prototype or a tutorial exercise, but as something I actually use and depend on every day. Geopolitics and finance were the natural first domain — areas I follow closely, where I have a real point of view that a generic feed can't capture.

The approach — local context, model-agnostic, flat files structured for future migration — was designed to scale beyond one person and one domain. Health, language learning, team environments at work. The architecture anticipates that. The first domain just had to be one I cared enough about to build it right.

---

## How It Was Built

Not designed up front, then implemented. Built iteratively — a working slice first, then production feedback, then refinement.

**January 2026 — Foundation decisions**

A few architectural commitments made before the first line of code, because they're hard to change later:

- **Local-first data layer:** Flat files (JSON), schema designed to be Postgres-ready — one `COPY` command when volume demands migration, not a rewrite. Context graph design (Neo4j) for relationship mapping. All learned state portable by design — move machines, switch providers, go offline — preferences travel with you.
- **Model-agnostic from day one:** User profile injection at the dispatcher level, not inside any model's prompt. The system ran on local Ollama first — no cloud dependency. Any model at any layer can be swapped without touching personalization logic.

**OpenClaw integration (late January / early February)**

Added OpenClaw as an optional delivery and interface layer after its launch. The personal-ai-agent pipeline was preserved standalone; OpenClaw adds Telegram delivery and a conversational interface but is not required.

**February 2026 — AI scoring layer**

With the local foundation solid, replaced keyword scoring with the two-stage AI pipeline:
- Haiku pre-filter (400 → ~50, cheap pass) followed by a final ranking model with injected user profile
- Built the learning feedback loop: Like/Dislike/Save → updates local learned profile → influences tomorrow's run
- Bootstrapped 415 learning signals from 398 hand-saved X bookmarks — cold start solved in one session
- Reduced cost from $100+/month → mid-$30s through model selection and profile injection (the insight: context makes cheaper models smarter)

**March 2026 — Source expansion and intelligence layer**

Extended the candidate pool from ~400 RSS articles to ~900 daily candidates across three source types: RSS feeds, enriched X bookmarks, and topic-guided web search via Brave. Added domain-level source trust scoring upstream of article ranking.

Then built the AI Observations layer — the system's first proactive capability. Instead of only reacting to articles, it now monitors its own output and tells you what it noticed:

- **Topic velocity:** What's surging vs. your 30-day baseline; what's absent from today's briefing that matches your interests
- **Source anomalies:** Trusted sources drifting in topic or quality
- **US press blind spots:** Stories with strong non-US coverage absent from US outlets
- **Lateral connections:** Weekly Sonnet reasoning across your reading history — adjacent topics and suggested sources you're not yet tracking

You can respond to any observation through the web UI, and those responses are stored in a structured format designed for the next layer: graph database, vector search, and automated action in 1.1.

---

## What It Does

This is not a smarter news feed. The daily briefing is the front door. Behind it:

**Daily Briefing**

Every morning, the system fetches hundreds of articles from RSS feeds across geopolitics, finance, and institutional sources — plus enriched X bookmarks and web search candidates. A two-stage scoring pipeline surfaces the top 20 most relevant articles for you specifically. Delivered to Telegram at 7 AM with like/dislike/save buttons. Your reactions feed tomorrow's scoring.

**AI Observations**

At 7:30 AM — 30 minutes after the briefing — a separate intelligence message arrives. Not more articles: observations about what the pipeline noticed. Topic momentum, source behavior, coverage gaps, lateral connections. You can respond, disagree, flag for follow-up. Those responses seed the personal memory system planned for 1.1.

**Deep Dives**

When an article or topic warrants more than a headline, Deep Dive produces a structured brief using a higher-capability model: analysis, counter-arguments, and a bibliography of references for further reading. The archive grows daily — a personal research library of topics you decided were worth understanding deeply.

**Signal Priorities**

You inject current focus areas directly — a conflict escalating, a policy shift, an earnings season — with keywords and a time-bounded expiry. The system boosts those signals for that window, then returns to baseline. The world changes. Your attention shifts. This is the mechanism for keeping the system aligned with where you actually are, not where you were three months ago.

**Reading Library**

Every saved article is stored, searchable, and categorized. A personal knowledge base that accumulates alongside the daily work.

**This is not YouTube.** YouTube optimizes for your attention. This system optimizes for your thinking. You inject direction. It surfaces material. You decide what goes deeper.

---

## Screenshots

**Morning Briefing** — ranked top 20, scored and categorized, with like/dislike/save actions

![Morning Briefing](docs/screenshots/morning-briefing.png)

**Reading Library** — everything you've ever liked or saved, searchable and filterable

![Reading Library](docs/screenshots/reading-library.png)

**Deep Dive Archive** — AI analysis on flagged articles, by date

![Deep Dives](docs/screenshots/deep-dives.png)

**Signal Priorities** — short-term focus injections that boost scoring for a set window

![Priorities](docs/screenshots/priorities.png)

**AI Observations** — daily proactive observations from the pipeline, with response capture

![AI Observations](docs/screenshots/ai-observations.png)

---

## Interface

The system has two production interfaces designed for different contexts:

**Web Portal** — full-featured local interface for browsing, research, and curation. Five views:
- **Daily** — ranked briefing with article scores and feedback controls
- **Reading Library** — searchable archive of all saved articles, filterable by category, type, and date
- **Deep Dives** — archive of structured research briefs with analysis, counter-arguments, and references
- **Signal Priorities** — inject and manage time-bounded focus areas with keyword boosting
- **AI Observations** — daily pipeline observations with response capture; weekly lateral connections

**Telegram** — mobile interface for daily delivery and on-the-go feedback. Briefing arrives at 7 AM, AI Observations at 7:30 AM, both with inline action buttons. Voice notes supported for quick capture.

Both interfaces write to the same local data layer. Feedback from either surface influences tomorrow's scoring.

---

## How the Learning Loop Works

```
Daily Briefing (7 AM)
      ↓
You react on Telegram (👍 Like · 👎 Dislike · 🔖 Save)
  or inject a Signal Priority ("Tigray Conflict +2.0x, expires in 3 days")
      ↓
curator_feedback.py records signals locally
      ↓
Tomorrow's scorer gets your updated profile:
  "prefer institutional_debates, monetary_policy / avoid ceremonial_reporting"
  + active priority boosts applied
      ↓
Better briefing — shaped by your reasoning, not an algorithm's engagement model
```

**Model-agnostic by design:** The user profile is injected at the dispatcher level, not inside any model's prompt. Swap any model at any layer — preferences persist. The system has run across multiple providers and will continue to evaluate as the landscape evolves.

**Bootstrapped cold start:** Rather than waiting months for enough feedback, 398 hand-saved X bookmarks were ingested as `Save` signals. The learning loop went from 17 signals to **415 scored signals in one session**.

---

## What It Has Learned

```bash
python show_profile.py
```

```
========================================================
  CURATOR LEARNED PROFILE
========================================================
  Interactions : 415 scored signals from 406 feedback events
  Last updated : 2026-02-28
  Feedback     : 8 liked  |  1 disliked  |  397 saved
========================================================

  SOURCES
  -------
  █████████████░░░  +14  X/@[macro_economist]
  ████████████░░░░  +12  X/@[macro_economist]
  ███████████░░░░░  +11  [independent_media]
  ███████████░░░░░  +11  X/@[economic_historian]

  THEMES
  ------
  ████████████████  +17  institutional_debates
  ████████████████  +17  market_analysis
  █████████░░░░░░░  +8   monetary_policy
  ███████░░░░░░░░░  +6   geopolitical_analysis

  AVOID PATTERNS
  --------------
  ▪▪▪  (3x)  ceremonial_reporting
  ▪    (1x)  event_coverage_not_analysis
```

Learned from actual reading behavior — nothing hard-coded. The system knows to up-rank institutional critique, monetary theory, and geopolitical analysis. It knows to skip ceremonial news coverage.

---

## Architecture

```
RSS Feeds + X Bookmarks + Brave Web Search (~900 daily candidates)
          ↓
  curator_rss_v2.py
          ↓
  [mechanical mode: keyword scoring, local Ollama/Gemma — no external dependency]
          ↓  OR
  Stage 1: Haiku pre-filter (~900 → ~50, cheap pass)
          ↓
  Stage 2: Reasoning model scorer + injected user profile + source trust weights
          ↓  [fallback: Haiku with same user profile]
  Top 20 ranked articles
          ↓
  Telegram delivery (7 AM via launchd)   OR   stdout/file
          ↓
  User reacts (Like / Dislike / Save)
  [or flags for Deep Dive → deep_dive.py → higher-capability model → brief + counter-arguments + bibliography]
          ↓
  curator_feedback.py → updates local curator_preferences.json
          ↓
  Tomorrow's run loads updated profile → repeat

          ↓  (7:30 AM, separate)
  curator_intelligence.py — AI Observations
          ↓
  Topic velocity · Source anomalies · Blind spots · Lateral connections
          ↓
  Telegram AI Observations message + intelligence_YYYYMMDD.json
          ↓
  You respond via web UI → intelligence_responses.json
  (seed of personal memory system — queryable in 1.1)
```

**Model tiers — all swappable:**

| Role | Current | Notes |
|---|---|---|
| Local / mechanical mode | Ollama + Gemma | Free, no external calls, always available |
| Bulk pre-filter | Claude Haiku | Low cost, high volume |
| Daily ranking | xAI Grok (reasoning variant) | Evaluated and tuned; will continue evaluating alternatives |
| Deep Dives + dev reasoning | Claude Sonnet | Higher capability, used selectively |
| AI Observations | Haiku (daily) + Sonnet (weekly) | Haiku for observation, Sonnet for lateral connections |

Profile injection at the dispatcher level means model swaps don't affect personalization. Costs are actively managed — the insight is that context makes cheaper models smarter, not that any specific model is the right one.

---

**Key files:**

| File | Purpose |
|---|---|
| `curator_rss_v2.py` | Main pipeline: fetch, score, deliver |
| `curator_intelligence.py` | AI Observations: daily proactive monitoring |
| `curator_feedback.py` | Process reactions → update local learned profile |
| `curator_sources.json` | Domain-level trust registry (trusted/neutral/drop) |
| `curator_priority_feed.py` | Priority-guided Brave web search candidates |
| `curator_server.py` | Flask web portal + API endpoints |
| `curator_utils.py` | Shared helpers (Telegram, utilities) |
| `show_profile.py` | Human-readable view of what the system has learned |
| `cost_report.py` | Unified cost tracking (chat + curator runs) |
| `x_bootstrap.py` | One-time ingestion of X bookmarks as learning signals |

---

## Cost

Two categories of cost, both tracked and actively managed:

**Operational costs** — daily curator runs:

| Period | Approach | Monthly Cost |
|---|---|---|
| January | Mechanical mode + local Ollama | Free |
| Early February | Single-stage Sonnet scoring | $100+/month |
| Current | Two-stage pipeline (Haiku pre-filter + reasoning model ranking) | Mid-$30s/month |

AI Observations (WS5) adds negligible cost — ~$0.01/day for Haiku observations, one Sonnet call per week for lateral connections.

**The insight:** Profile injection makes cheaper models smarter. Most of the cost reduction came not from switching models but from injecting context that let lower-cost models perform at the level previously requiring expensive ones.

**Build costs** — design and implementation sessions. Migrated to Claude Code (runs on Pro subscription) which eliminated per-token build costs for implementation work. Both categories tracked in `daily_usage.json`.

```bash
python cost_report.py          # today's breakdown by category
python cost_report.py week     # last 7 days, day by day
python cost_report.py month    # this calendar month
```

---

## How to Run

**Prerequisites:** Python 3.9+, API keys in macOS keyring

```bash
# Clone and install
git clone https://github.com/robertvanstedum/personal-ai-agents.git
cd personal-ai-agents
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Local mode — no LLM, no API key required
python curator_rss_v2.py --model=ollama

# AI mode (dry-run first)
python curator_rss_v2.py --mode=ai --dry-run

# See what the system has learned
python show_profile.py

# Check costs
python cost_report.py
```

**Credentials:** All API keys stored in macOS keyring, never in files. See `CREDENTIALS_SETUP.md`.

**OpenClaw (optional):** Adds Telegram delivery and conversational interface. The curator runs standalone without it.

**Scheduling:** launchd plists trigger briefing at 7 AM, AI Observations at 7:30 AM, priority feed at 2 PM.

---

## Development Process

Built through structured human-AI collaboration:

- **Multi-agent coordination:** Human architect bridges between specialized AI agents (Claude Code for implementation, OpenClaw for planning and memory)
- **Iterative build cadence:** Working slice → production run → observe → refine. No big design phases; each workstream is a complete loop
- **Zero regressions:** Multiple major feature phases shipped (Feb–Mar 2026) with no production outages

See [DEVELOPMENT.md](DEVELOPMENT.md) for detailed workflow and testing protocol.

---

## Roadmap

**v1.0 (March 2026 — current):**
- ✅ Two-stage AI scoring pipeline with learned preference profile
- ✅ X bookmark bootstrap (415 signals) + incremental pull
- ✅ Deep Dives, Signal Priorities, Reading Library
- ✅ Source expansion: institutional RSS + Brave web search (~900 daily candidates)
- ✅ Domain-level source trust scoring
- ✅ AI Observations layer (WS5): topic velocity, source anomalies, blind spots, lateral connections
- ✅ AI Observations response capture: web UI + `intelligence_responses.json` (seed of personal memory system)

**v1.1 (next):**
- Investigation workspace — persistent research threads with full timeline, annotation, and archive
- Telegram reply capture → AI Observations response classification via Haiku
- `intelligence_responses.json` read by Sonnet lateral connections prompt
- Automated action on `pending_action` items from AI Observations responses

**Post-1.1:**
- Full RAG layer: pgvector + Neo4j across responses, signals, and dialog history
- "What have I thought about this before?" becomes a real query
- Image analysis — chart images from analyst tweets via vision model
- Postgres migration when volume demands
- Language learning domain (German)
- Health domain

---

## Technical Notes

- **Anti-echo-chamber:** 20% serendipity reserve surfaces articles outside learned patterns
- **Decay gate:** Signals older than 30 days get half-weight — prevents preference lock-in
- **Signal normalization:** X bookmarks weighted to avoid volume bias vs. direct feedback
- **Model-agnostic design:** Profile injection at dispatcher level — model swaps don't break personalization
- **Local-first:** All learned state is flat files on your machine, structured for easy DB migration
- **Quiet paths:** AI Observations suppresses empty observations — if there's nothing meaningful, nothing is sent

---

**Status:** v1.0 feature complete (March 15, 2026). Documentation review and public launch in progress.
**Author:** Robert van Stedum
