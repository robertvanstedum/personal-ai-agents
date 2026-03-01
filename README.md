# Personal AI Briefing System

**A local, privately controlled AI that maintains your memory and intent — and reaches out flexibly to LLMs.**

Reads 400+ articles/day. Curates the top 20. Learns from everything you've ever saved. Runs at 7 AM.

> Production system, daily use since Feb 2026. Built iteratively — see [roadmap](#roadmap) for the full sequence.

---

## Core Concept

Most AI tools put your memory in the cloud and make you dependent on a single provider. This system inverts that:

- **Local stack:** Preferences, learned signals, and history live on your machine (flat files today, Postgres/Neo4j ready)
- **Private by design:** Nothing about your reading habits, saved articles, or preferences leaves your machine unless you choose
- **LLM-flexible:** Claude Haiku, grok-3-mini, Claude Sonnet — swap models without breaking personalization. Local Ollama support planned ([#1](https://github.com/robertvanstedum/personal-ai-agents/issues/1))
- **Standalone:** Runs without OpenClaw. OpenClaw adds Telegram delivery and a conversational interface, but the curator pipeline runs independently

The LLMs are interchangeable workers. Your memory and preferences stay home.

---

## Build History

**January 2026 — Foundation**

Inspired by Foundation Capital's writing on context graphs, the goal from day one was a local, privately controlled system — not another cloud-dependent AI tool. Started with:
- **Context graph architecture:** Neo4j for relationship mapping, PostgreSQL for structured storage (DB integration ready; flat files used in practice)
- RSS ingestion from a curated source list → scored and ranked locally
- **Mechanical mode:** keyword/rule-based scoring, zero LLM dependency — still a supported mode today
- Local Ollama integration (Gemma 3) originally built; `--model=ollama` currently falls back to keyword scoring — restore tracked in [#1](https://github.com/robertvanstedum/personal-ai-agents/issues/1)
- Command-line reports: run `python curator_rss_v2.py` and get a ranked briefing
- Two use cases in scope: geopolitics/investing intelligence + career research

**OpenClaw integration (late January / early February)**

When OpenClaw launched (early adopter — spent a weekend installing and debugging it), added it as an optional delivery and interface layer. The personal-ai-agent pipeline was preserved standalone; OpenClaw adds Telegram delivery and a conversational interface but is not required.

**February 2026 — Intelligence Layer**

With the local foundation solid, built the AI layer on top:
- Replaced keyword scoring with two-stage AI scoring (Haiku pre-filter → grok-3-mini final ranking)
- Built the learning feedback loop: Like/Dislike/Save → updates local learned profile → influences tomorrow's run
- Bootstrapped 415 learning signals from 398 hand-saved X bookmarks (cold start solved in one session)
- Optimized cost from $100+/month → $35–45/month through model selection and batching
- Unified cost tracking across chat and curator runs

---

## What It Does

Every morning at 7 AM:

1. Fetches ~400 articles from 10+ RSS feeds (geopolitics, finance, institutional sources)
2. Pre-filters with Haiku (400 → ~50 candidates, cheap pass)
3. Scores candidates with grok-3-mini using your injected learned profile
4. Picks the top 20 most relevant articles for you specifically
5. Delivers a formatted briefing to Telegram with like/dislike/save buttons
6. Uses your reactions to score tomorrow's briefing better

Can also run in **mechanical mode** (no LLM, keyword scoring only) for zero-cost fallback or offline use.

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

---

## How the Learning Loop Works

```
Daily Briefing (7 AM)
      ↓
You react on Telegram (👍 Like · 👎 Dislike · 🔖 Save)
      ↓
curator_feedback.py records the signal locally
      ↓
Tomorrow's scorer gets: "prefer The Duran, institutional_debates, monetary_policy"
      ↓
Better briefing
```

**Model-agnostic by design:** The user profile is injected at the dispatcher level, not inside any model's prompt. When xAI goes down and Haiku takes over, it runs with the same learned profile. Swap models — preferences persist.

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
  ████████████████  +17  X/@[geopolitics_account]
  ██████████████░░  +16  X/@[geopolitics_account]
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
RSS Feeds (10+ sources, ~400 articles)
          ↓
  curator_rss_v2.py
          ↓
  [mechanical mode: keyword scoring, no LLM]
          ↓  OR
  Stage 1: Haiku pre-filter (400 → ~50, cheap pass)
          ↓
  Stage 2: grok-3-mini scorer + injected user profile
          ↓  [fallback: Haiku with same user profile]
  Top 20 ranked articles
          ↓
  Telegram delivery (7 AM via launchd)   OR   stdout/file
          ↓
  User reacts (Like / Dislike / Save)
          ↓
  curator_feedback.py → updates local curator_preferences.json
          ↓
  Tomorrow's run loads updated profile → repeat
```

**Key files:**

| File | Purpose |
|---|---|
| `curator_rss_v2.py` | Main pipeline: fetch, score, deliver |
| `curator_feedback.py` | Process reactions → update local learned profile |
| `show_profile.py` | Human-readable view of what the system has learned |
| `cost_report.py` | Unified cost tracking (chat + curator runs) |
| `x_bootstrap.py` | One-time ingestion of X bookmarks as learning signals |
| `x_auth.py` | X API OAuth credential management |

---

## Cost Story

**January:** Mechanical mode (keyword scoring) + Ollama local LLM — free. Ollama path has since regressed; tracked in [#1](https://github.com/robertvanstedum/personal-ai-agents/issues/1).

**Early February:** Switched to Claude Sonnet for all AI scoring: $100+/month.

**Current:**
- Haiku for bulk pre-filtering (cheap, fast)
- grok-3-mini for final ranking (good quality, low cost)
- Profile injection makes cheap models smarter — no need for expensive models

Result: **~$0.15–0.30/day** ($35–45/month) for 400+ articles daily.

Tracked in `curator_costs.json` (one record per API call, Postgres-ready schema):

```bash
python cost_report.py          # today's breakdown
python cost_report.py week     # last 7 days, day by day
python cost_report.py month    # this calendar month
python cost_report.py year     # this calendar year, month by month
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

# Keyword scoring mode — no LLM, no API key required (see issue #1 for Ollama restore)
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

**Scheduling:** launchd plist triggers at 2–3 AM (fetch/score), delivers at 7 AM.

---

## Roadmap

**v0.9 (current — Feb 2026):**
Full learning loop across all scoring paths, X bookmark bootstrap, cost tracking, Telegram feedback delivery, model-agnostic profile injection.

**v1.0 (active development):**
- Phase 3C: t.co URL enrichment — follow tweet links to surface trusted domains
- Phase 3D: Source discovery — auto-add new RSS feeds from discovered domains
- Phase 4: Wider sources — Substack, academic (BIS, Fed, arXiv), Reddit
- Phase 5: Synthesis — pattern detection, contradiction highlighting, proactive research
- Postgres migration — `curator_costs.json` already row-structured, `COPY` ready

Active development continues after v0.9 launch.

---

## Technical Notes

- **Anti-echo-chamber:** 20% serendipity reserve surfaces articles outside learned patterns
- **Decay gate:** Signals older than 30 days get half-weight — prevents preference lock-in
- **Signal normalization:** X bookmarks weighted to avoid volume bias vs. direct feedback
- **Model-agnostic design:** Profile injection at dispatcher level — model swaps don't break personalization
- **Local-first:** All learned state is flat files on your machine, structured for easy DB migration

---

**Status:** Production (daily use since Feb 9, 2026)
**Current milestone:** v0.9-beta — learning loop complete across all scoring paths
**Author:** Robert van Stedum
