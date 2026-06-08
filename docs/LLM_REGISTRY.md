# LLM Registry — mini-moi / personal-ai-agents

**Last updated:** 2026-06-07  
**Maintainer:** Robert (update whenever a model or provider changes — see Part 5)

---

## Part 1 — Call site registry

22 call sites across 10 Python files, 3 cloud providers, 1 local runtime.

| # | Program | Domain | Feature | Model | Provider |
|---|---------|--------|---------|-------|----------|
| 1 | `curator_rss_v2.py` | Curator | Article pre-filter (150 → 50 candidates) | `claude-haiku-4-5` | Anthropic |
| 2 | `curator_rss_v2.py` | Curator | Article scoring — category + 0-10 relevance (Anthropic path) | `claude-haiku-4-5` | Anthropic |
| 3 | `curator_rss_v2.py` | Curator | Article ranking — quality / originality / depth of top 50 | `claude-sonnet-4-5` | Anthropic |
| 4 | `curator_rss_v2.py` | Curator | Article scoring — full score + category (xAI path, production) | `grok-4-1` | xAI |
| 5 | `curator_feedback.py` | Curator | Scan — deep analysis of one article (contrarian take, connections) | `claude-sonnet-4-5` | Anthropic |
| 6 | `curator_feedback.py` | Curator | Feedback metadata extraction — parses user reaction to structured JSON | `claude-sonnet-4-5` | Anthropic |
| 7 | `curator_deepdive.py` | Curator | Deep article analysis — implications, contrarian angles, what to watch | `claude-sonnet-4-5` | Anthropic |
| 8 | `curator_intelligence.py` | Curator | Daily observations — topic velocity, blind spots, source anomalies | `claude-haiku-4-5` | Anthropic |
| 9 | `curator_intelligence.py` | Curator | Weekly lateral connections — synthesis across all tracked topics | `claude-sonnet-4-5` | Anthropic |
| 10 | `curator_utils.py` | Curator | X/bookmark entity extraction — topics + entities from tweet text | `claude-haiku-4-5` | Anthropic |
| 11 | `research_routes.py` | Curator | Leanings teammate read — AI brief on a research leaning, on demand | `claude-sonnet-4-5` | Anthropic |
| 12 | `german_domain.py` | German | Translation, writing correction, verb conjugation (primary) | `grok-4-1-fast` | xAI |
| 13 | `german_domain.py` | German | Translation, writing correction, verb conjugation (fallback 1) | `claude-haiku-4-5` | Anthropic |
| 14 | `german_domain.py` | German | Translation, writing correction, verb conjugation (fallback 2 — local) | `gemma3:1b` | Ollama |
| 15 | `get_german_session.py` | German | Prompt compression — condenses session prompts for Telegram delivery | `claude-haiku-4-5` | Anthropic |
| 16 | `reviewer.py` | German | Session review — structured feedback on a German practice transcript | `claude-sonnet-4-6` | Anthropic |
| 17 | `research.py` | Research Intelligence | Research triage — classify search results (local first) | `gemma3:1b` | Ollama |
| 18 | `research.py` | Research Intelligence | Research triage — classify search results (cloud fallback) | `claude-haiku-4-5` | Anthropic |
| 19 | `observe.py` | Research Intelligence | Session synthesis — narrative summary of research findings | `claude-sonnet-4-5` | Anthropic |
| 20 | `observe.py` | Research Intelligence | Query extraction — next research questions from synthesis output | `claude-haiku-4-5` | Anthropic |
| 21 | `generate_dive.py` | Research Intelligence | Deeper Dive essays — synthesizer + challenger agents for thread wrap-ups | `claude-opus-4-5` | Anthropic |
| 22 | `telegram_bot.py` | Platform | Voice message transcription — Telegram audio to text | `whisper-1` | OpenAI |

> **Rows 12–14** (`german_domain.py`) are one function (`_call_llm()`) that tries providers in order: xAI → Anthropic Haiku → Ollama local. One call site, three possible executors.  
> **Rows 17–18** (`research.py`) follow the same pattern: Ollama first (free, local, private), Haiku as cloud fallback.

---

## Part 2 — Model tiers and rationale

### Three tiers

| Tier | Models | When used | Rationale |
|------|--------|-----------|-----------|
| **Fast / cheap** | `claude-haiku-4-5`, `grok-4-1-fast`, `gemma3:1b` | Volume jobs, mechanical extraction, pre-filtering | High call frequency; quality bar is "good enough to triage" |
| **Quality / reasoning** | `claude-sonnet-4-5`, `claude-sonnet-4-6`, `grok-4-1` | Final scoring, deep analysis, user-facing feedback | Lower call frequency; output is what the user actually reads |
| **Premium** | `claude-opus-4-5` | Research Deeper Dive essays (rare) | Runs once per research thread close-out; cost justified by research value |

### Provider choices

**xAI (Grok)** — primary for German translation and Curator production scoring.  
Strong German language capability; competitive cost; OpenAI-compatible API makes it easy to swap.

**Anthropic (Claude)** — primary for all analytical and synthesis work.  
Haiku for volume, Sonnet for quality, Opus for the rare premium task.  
Fallback for German when xAI is unavailable.

**Ollama (local)** — free inference, no data leaves the machine.  
Used in German as the last fallback, and in Research Intelligence triage as the *first* attempt (cost $0, private, fast for small models).  
Current model: `gemma3:1b`.

**OpenAI (Whisper)** — audio transcription only. Purpose-built for speech-to-text; no alternative evaluated.

### Fallback chains

```
German translation:      xAI grok-4-1-fast  →  Anthropic Haiku  →  Ollama gemma3:1b (local)
Curator scoring:         xAI grok-4-1 (production)
                    OR   Haiku prefilter  →  Sonnet ranking (two-stage Anthropic path)
Research triage:         Ollama gemma3:1b  →  Anthropic Haiku
```

---

## Part 3 — Authentication

All API keys stored in macOS Keychain via `keyring`. Nothing in `.env` files or committed to git.

| Provider | Keyring service | Keyring account |
|----------|----------------|-----------------|
| Anthropic | `anthropic` | `api_key` |
| xAI | `xai` | `api_key` |
| OpenAI (Whisper) | `openai` | `api_key` |
| Ollama | — | No auth (local HTTP `localhost:11434`) |

---

## Part 4 — Cost baseline

| Path | Approx daily cost | Notes |
|------|------------------|-------|
| Curator scoring (xAI, production) | ~$0.30/day | ~700 articles, single-stage grok-4-1 |
| Curator scoring (Anthropic two-stage) | ~$0.20–0.40/run | Haiku prefilter + Sonnet ranking |
| Daily intelligence observations | ~$0.05/day | 4 Haiku calls + 1 Sonnet (Sundays) |
| German translation calls | ~$0.01–0.05/session | Mostly xAI; Ollama = $0 |
| Research triage | ~$0.01–0.05/session | Mostly Ollama; Haiku fallback |
| Scans (on demand) | ~$0.10–0.20 each | User-triggered Sonnet calls |
| Deeper Dives (rare) | ~$0.50–1.50 each | Opus; once per research thread |

---

## Part 5 — Change log

Add one entry here every time a model or provider changes.

### 2026-06-07 — Initial registry
- Baseline audit of all 22 call sites
- Production scoring: `grok-4-1` via xAI (`curator_rss_v2.py`)
- German primary: `grok-4-1-fast` via xAI (`german_domain.py`)
- German fallback chain: Haiku → `gemma3:1b` local Ollama

---

*To update: edit the table in Part 1, update dates in Part 4 if costs shift, and add a changelog entry in Part 5. Commit with message `llm: [what changed] — [one-line reason]`.*
