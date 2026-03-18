# Architecture — Mini-moi

A production AI agent for daily geopolitical and financial intelligence. Single-user, local-first, model-agnostic. Running since February 2026.

---

## Pipeline Overview

Three scheduled jobs run daily via launchd:

| Time | Job | Entry Point |
|------|-----|-------------|
| 07:00 | Morning briefing | `run_curator_cron.sh` → `curator_rss_v2.py` |
| 07:30 | AI Observations | `run_intelligence_cron.sh` → `curator_intelligence.py` |
| 14:00 | Priority feed | `run_priority_feed_cron.sh` → `curator_priority_feed.py` |

**Morning briefing flow:**
1. Incremental X signal sync (`x_pull_incremental.py`) — fetches new bookmarks since `last_pull_at`; runs alongside briefing cron, failure is non-blocking
2. `curator_rss_v2.py` — ingest RSS + X signals, score, generate HTML
3. `telegram_bot.py --send` — deliver top 10 to mobile

---

## Model Stack

Each stage uses a swappable model selected at runtime via `--model=` flag.

| Stage | Model | Notes |
|-------|-------|-------|
| Article scoring (production) | `grok-4.1` (xAI) | Single-stage, ~700 candidates, temperature=1.0 |
| Article scoring (prior production) | `grok-3-mini` | Replaced March 2026 |
| Article scoring (fallback) | `ollama/gemma3` | Local, mechanical keyword scoring, zero cost |
| AI Observations (daily) | `claude-haiku-4-5` | Source anomalies, topic velocity |
| AI Observations (weekly) | `claude-sonnet-4-5` | Lateral connections, Sunday runs |
| Deep dives | `claude-sonnet-4-5` | On-demand, user-directed |
| Priority feed pre-filter | `claude-haiku-4-5` | Gates Grok scoring (min_score=3.0) |

User profile injection happens at the dispatcher level, not inside model prompts — any scorer (Grok, Haiku, local Ollama) inherits personalization automatically without changes to scorer logic.

**Cost baseline:** ~$0.30/day at current pool size. A bulk pre-filter tier (Haiku) is implemented and ready to activate as the candidate pool grows.

---

## Scoring & Serendipity Algorithm

The 20-article daily output is split into two pools with distinct selection logic. Pool sizes are configurable via `curation_settings.serendipity_reserve` in `curator_preferences.json` (default: 0.20).

**Phase 1 — Personalized pool (default 16 articles)**

Selection is iterative, not a simple sort. Each pass recalculates `final_score` for all remaining candidates:

```
final_score = base_score
            × source_trust_multiplier     # trusted=1.5, neutral=1.0, deprioritize=0.5, probationary=0.7
            - source_penalty              # count² × 30 × diversity_weight
            - category_penalty            # count² × 15 × diversity_weight
            + interest_boost              # from flagged articles in active interests
            + priorities_boost            # from active priority keywords (capped at +3.0)
```

Penalties are quadratic — the second article from a source costs 4× the first, the third costs 9×. Source penalty (×30) is more aggressive than category penalty (×15): depth per topic is fine, outlet monopoly is not.

**Phase 2 — Serendipity pool (default 4 articles)**

Drawn from candidates not selected in Phase 1. All personalization signals stripped — no interest boosts, no priority boosts, no profile injection. Selection by base score + diversity penalties only. Purpose: surfaces high-quality articles the model found interesting that the learned profile would have deprioritized. Tagged `serendipity_pick: true` in output.

---

## Data Layer

Flat JSON files, structured to match a future database schema. One migration command when the time comes — no rewrite required.

| File | Purpose |
|------|---------|
| `curator_latest.json` | Today's briefing (20 articles) |
| `curator_history.json` | 30-day rolling archive, keyed by `hash_id` |
| `curator_preferences.json` | User feedback history, learned patterns, serendipity config |
| `curator_sources.json` | RSS source trust scores, probationary tracking |
| `curator_signals.json` | X bookmark signals with enriched destination text |
| `x_pull_state.json` | X pull tracker (`last_pull_at`) |
| `priorities.json` | Active priority definitions + web search results |
| `curator_costs.json` | Per-run cost log (model, tokens, USD) |
| `curator_config.py` | Shared constants — domain names, file paths, active domain flag |

**Archive:** `curator_archive/curator_YYYY-MM-DD.html` — daily snapshots.

---

## Web Layer

Flask server (`curator_server.py`, port 8765) serves five views:

| Page | Route | Source |
|------|-------|--------|
| Daily Briefing | `/` | `curator_latest.html` |
| Reading Library | `/curator_library.html` | `curator_preferences.json` + history |
| Priorities | `/curator_priorities.html` | `priorities.json` |
| Deep Dives | `/interests/2026/deep-dives/` | Markdown files |
| AI Observations | `/curator_intelligence.html` | `intelligence_YYYYMMDD.json` |

Feedback (like/dislike/save, deep dive, notes) POSTs to `/feedback` and `/deepdive`.

---

## Telegram Layer

Two bots, two distinct responsibilities — a deliberate architectural separation.

**`rvsopenbot`** (`telegram_bot.py`) — the curator channel:
- Delivers top 10 articles each morning with inline Like/Dislike/Save buttons
- Callback handlers POST feedback to the Flask server
- Commands: `/run`, `/status`, `/briefing`, `/dry-run`
- Voice command support: voice messages are transcribed, pattern-matched against known commands, and executed

**`minimoi`** (OpenClaw gateway) — the planning/command channel:
- Natural language instructions, memory updates, cron scheduling
- Cross-session coordination and agent orchestration
- Separate bot token, separate chat ID, separate responsibilities

Execution feedback (did you like this article?) flows through one channel. Planning and instruction (run the curator, change the model, set a reminder) flows through the other.

---

## Key Design Decisions

**Model-agnostic dispatcher** — `curate(mode=...)` branches to the appropriate scorer. HTML and feedback systems never assume a specific model. All scorers return a normalized `{score, method}` result.

**Flat files as proto-database** — JSON files are keyed and structured to match a relational schema. Operational simplicity now, clean migration path later.

**Dry-run mode** — full pipeline execution against `curator_preview.html` without writing to history or archive. Automatically uses the local model — zero API cost.

**X bookmark integration** — `x_to_article.py` normalizes X signals to the RSS schema. Destination text fetched via BeautifulSoup (2000 char cap), tweet text as fallback. Merged into the main candidate pool after RSS ingestion.

**Credentials** — macOS Keychain via `keyring`. API keys for xAI, Anthropic, Telegram, and Brave Search.

---

## Codebase Scale

~15,000 lines of Python across 8 core modules. Primary: `curator_rss_v2.py` (3,019 lines, includes HTML generation), `telegram_bot.py` (~1,000 lines), `curator_server.py` (~900 lines), `curator_intelligence.py` (~600 lines).
