# Personal AI Briefing System

### *Mini-moi — not a general intelligence, but a specific one. Yours.*

A personal intelligence system. The first domain: the intersection of finance and geopolitics. Health, language learning, and others to follow.

Learns from your history and preferences, with deliberate friction built in. The goal isn't a curated feed — it's better thinking.

> Production system, daily use since Feb 2026. Designed to expand across domains — see [Roadmap](#roadmap).

---

## What This Is Really About

The cloud LLMs have the world's knowledge. That problem is largely solved.

The hard part — the part that actually matters for real decisions — is acting in your specific situation. Your history. Your goals. Your risk tolerance. Your team's context and motivation.

General intelligence is widely available now. Specific intelligence, the kind that knows you and acts for you, isn't.

That's what this builds toward.

I started with myself: a daily briefing on geopolitics and finance, shaped by how I actually think, learning from what I actually read and save.

But the vision is bigger. If I had a team at work — people and agents together — I'd want this same local context and motivation at the center of it. Not a generic assistant that knows everything about the world but nothing about us. A specific capability, grounded in our history, our goals, our way of making decisions under uncertainty.

That's what *Mini-moi* means. Not a mini version of a large language model. A system that carries your particular point of view and acts on your behalf — in your real world, for you, now.

The cloud LLMs are tools this system reaches out to when it needs them. Your memory, your preferences, your reasoning — those stay with you. The agents are your team members, not the cloud's.

---

## Why I Built This

The best way to understand something is to build it.

I've believed for a while that the future of AI isn't just larger models with more world knowledge — it's systems that carry specific context: your history, your goals, your way of reasoning through uncertainty.

The cloud LLMs are remarkable, but they don't know you. They can't act for you in any meaningful sense without that layer.

I wanted to build that layer. Not as a prototype or a tutorial exercise, but as something I actually use and depend on every day.

Geopolitics and finance were the natural first domain — areas I follow closely, where I have a real point of view that a generic feed can't capture.

The approach — local context, model-agnostic, flat files structured for future migration — was designed to scale beyond one person and one domain. Health, language learning, team environments at work. The architecture anticipates that.

The first domain just had to be one I cared enough about to build it right.

---

## Build History

### January 2026 — Architecture Before Code

The system was designed before it was built. The decisions that matter most were made here:

**Local-first data layer:**
- Flat files first (JSON), schema designed to be Postgres-ready — one `COPY` command when volume demands migration, not a rewrite
- Context graph design (Neo4j) for relationship mapping: *why did I save this? what connects these ideas?*
- All learned state portable by design — move machines, switch cloud providers, go offline — preferences travel with you

**Model-agnostic from day one:**
- User profile injection happens at the dispatcher level, not inside any model's prompt
- Swap xAI for Haiku for Ollama — personalization persists
- Mechanical mode (keyword scoring, zero LLM) built first — the system works without any AI at all

**Scoring pipeline designed to expand across domains:**
- Geopolitics/investing intelligence (daily briefing — first domain in production)
- Health, language learning, and team/professional contexts planned

The system ran standalone from the start. No cloud dependencies required.

---

### Late January / Early February 2026 — OpenClaw Integration

When OpenClaw launched I was an early adopter — spent a weekend getting it running. It became clear it could handle Telegram delivery and conversational interface cleanly.

But the integration was designed as an **optional layer**, not a dependency.

**What OpenClaw adds:**
- Telegram delivery at 7 AM
- Conversational interface: "Explain this article" or "Why did you rank this #1?"
- Feedback buttons (👍 Like · 👎 Dislike · 🔖 Save) in the briefing message
- Voice note capture for quick thoughts while away from the desk

**What stays standalone:**
- The entire curator pipeline (`curator_rss_v2.py`, `curator_feedback.py`, `show_profile.py`)
- All learned preferences and history (local files)
- Scheduling (launchd — not dependent on OpenClaw uptime)

You can run the curator without OpenClaw. You can run OpenClaw without the curator. Integrated, but independent.

---

### February 2026 — Intelligence Layer

With the local foundation solid, the AI layer was built on top:

**Two-stage scoring:**
- Replaced keyword scoring with Haiku pre-filter (400 → ~50 candidates, cheap fast pass) → grok-3-mini final ranking with injected user profile
- When xAI goes down, Haiku fallback runs with the **same learned profile** — no degradation in personalization

**Learning loop closed:**
- Like/Dislike/Save → `curator_feedback.py` → updates local `learned_patterns` → influences tomorrow's scoring
- Signal weighting: Like = +2, Save = +1, Dislike = -1
- Decay factor: signals older than 30 days get half-weight — prevents preference lock-in
- Serendipity reserve: 20% of briefing comes from outside learned patterns — prevents filter bubble

**Cold start solved with X bookmark ingestion:**
- Built X OAuth 2.0 PKCE flow from scratch
- Ingested 398 hand-saved X bookmarks as learning signals in one session
- Profile jumped from 17 signals → **415 scored signals**
- System went from "barely knows you" to "knows your macro/geopolitics ecosystem" overnight

**Cost optimization:**
- Started: Claude Sonnet for all scoring → $100+/month
- Current: Haiku pre-filter + grok-3-mini final ranking → **$35–45/month**
- Profile injection makes cheap models smarter — expensive models not required at scale

---

## What It Does

Every morning at 7 AM:

1. Fetches ~400 articles from 10+ RSS feeds (geopolitics, finance, institutional sources)
2. Pre-filters with Haiku (400 → ~50 candidates, cheap pass)
3. Scores candidates with grok-3-mini using your injected learned profile
4. Picks the top 20 most relevant articles *for you specifically*
5. Delivers a formatted briefing to Telegram with like/dislike/save buttons
6. Uses your reactions to score tomorrow's briefing better

Can also run in **mechanical mode** (no LLM, keyword scoring only) for zero-cost fallback or offline use.

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

**Model-agnostic by design:** The user profile is injected at the dispatcher level, not inside any model's prompt. Swap models — preferences persist.

**Bootstrapped cold start:** Rather than waiting months for enough feedback, 398 hand-saved X bookmarks were ingested as `Save` signals in one session. The learning loop went from 17 signals to **415 scored signals** overnight.

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

Learned from actual reading behavior — nothing hard-coded.

---

## Architecture

```
RSS Feeds (10+ sources, ~400 articles)
          ↓
  curator_rss_v2.py
          ↓
  [mechanical mode: keyword scoring, zero LLM dependency]
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

| Period | Approach | Monthly Cost |
|---|---|---|
| January | Mechanical mode + Ollama | Free |
| Early February | Claude Sonnet for all scoring | $100+/month |
| Current | Haiku pre-filter + grok-3-mini final | $35–45/month |

Profile injection makes cheap models smarter — no need for expensive models at scale.

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

# Mechanical mode — no LLM required
python curator_rss_v2.py --mode=mechanical

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

## Development Process

Built through structured human-AI collaboration:

- **Multi-agent coordination:** Human architect bridges between specialized AI agents (Claude Code for implementation, OpenClaw assistant for planning/memory)
- **"Build little, test little":** Formalized testing checklist catches bugs before production
- **Process discipline:** Roadmap + memory + testing checklist ensures zero regressions across 7 feature phases

**Example:** Phase 3C test sequence caught a string mismatch bug before it reached the preferences file — preventing a silent scoring system failure.

See [DEVELOPMENT.md](DEVELOPMENT.md) for detailed workflow and testing protocol.

---

## Roadmap

**v0.9 — current (Feb 2026)**

Full learning loop across all scoring paths. X bookmark bootstrap (415 signals). Cost tracking. Telegram feedback delivery. Model-agnostic profile injection.

**v1.0 — active development**

- Phase 3C: Domain-scoped content signals — X bookmark folders map to knowledge domains (Finance/Geo, Health, Tech, etc.)
- Phase 3D: User-driven domain tagging — tag articles across domains from web UI and Telegram
- Phase 4: Wider sources — Substack, academic (BIS, Fed, arXiv), Reddit
- Phase 5: Synthesis — pattern detection, contradiction highlighting, proactive research
- Postgres migration — `curator_costs.json` already row-structured, one `COPY` command away

Active development continues after v0.9 launch.

---

## Technical Notes

- **Anti-echo-chamber:** 20% serendipity reserve surfaces articles outside learned patterns
- **Decay gate:** Signals older than 30 days get half-weight — prevents preference lock-in
- **Signal normalization:** X bookmarks weighted to avoid volume bias vs. direct feedback
- **Model-agnostic design:** Profile injection at dispatcher level — model swaps don't break personalization
- **Local-first:** All learned state is flat files on your machine, structured for easy DB migration
- **Postgres-ready schema:** `curator_costs.json` is one `COPY` command away from production DB

---

**Status:** Production — daily use since Feb 9, 2026  
**Current milestone:** v0.9-beta — learning loop complete across all scoring paths  
**Author:** Robert van Stedum
