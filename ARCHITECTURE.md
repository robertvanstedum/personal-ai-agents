# Architecture — Mini-moi

A personal AI agent platform. The first domain — geopolitical and financial intelligence — is live and generating daily data. Additional domains follow the same pipeline pattern.

Single-user, local-first, model-agnostic. Running since February 2026.

---

## Pipeline Overview

Three jobs run daily via launchd (hourly polling, 6AM–6PM, once-per-day idempotency):

| Job               | Entry Point                                                 |
|-------------------|-------------------------------------------------------------|
| Morning briefing  | `run_curator_cron.sh` → `curator_rss_v2.py`                |
| AI Observations   | `run_intelligence_cron.sh` → `curator_intelligence.py`     |
| Priority feed     | `run_priority_feed_cron.sh` → `curator_priority_feed.py`   |

**Morning briefing flow:**
1. Incremental signal sync (`x_pull_incremental.py`) — fetches new bookmarks since `last_pull_at`; failure is non-blocking
2. `curator_rss_v2.py` — ingest RSS + signals, score, generate HTML
3. `telegram_bot.py --send` — deliver top 10 to mobile

---

## Model Stack

Model selection is intentionally fluid. The table below reflects the current production configuration — models have rotated in and out during development and will continue to as the ecosystem evolves. OpenAI, Gemini, and others are candidates; the architecture doesn't prefer any provider.

Each stage uses a swappable model selected at runtime via `--model=` flag.

| Stage                          | Current Model      | Notes                                          |
|--------------------------------|--------------------|------------------------------------------------|
| Article scoring (production)   | `grok-4-1` (xAI)   | Single-stage, ~700 candidates, temperature=0.7 |
| Article scoring (fallback)     | `ollama/gemma3`    | Local, zero cost                               |
| AI Observations (daily)        | `claude-haiku-4-5` | Source anomalies, topic velocity               |
| AI Observations (weekly)       | `claude-sonnet-4-5`| Lateral connections, Sunday runs               |
| Deep dives                     | `claude-sonnet-4-5`| On-demand, user-directed                       |
| Priority feed pre-filter       | `claude-haiku-4-5` | Gates Grok scoring                             |

**Cost baseline:** ~$0.30/day at current pool size.

---

## Scoring & Serendipity Algorithm

The 20-article daily output is split into two pools with distinct selection logic. Pool sizes are configurable via `curation_settings.serendipity_reserve` in `curator_preferences.json` (default: 0.20).

**Phase 1 — Personalized pool (default 16 articles)**

Selection is iterative, not a simple sort. Each pass recalculates `final_score` for all remaining candidates. The trust multiplier is applied to `base_score` in-place first; the additive and subtractive terms then apply on top:

```
base_score × source_trust_multiplier     # trusted=1.5, neutral=1.0,
                                         # deprioritize=0.5, probationary=0.7
final_score = modified_base_score
            - source_penalty             # count² × 30 × diversity_weight
            - category_penalty           # count² × 15 × diversity_weight
            + interest_boost
            + priorities_boost           # capped at +3.0
```

Penalties are quadratic — the second article from a source costs 4× the first, the third costs 9×. Source penalty (×30) is more aggressive than category penalty (×15): depth per topic is acceptable, outlet monopoly is not.

**Phase 2 — Serendipity pool (default 4 articles)**

Drawn from candidates not selected in Phase 1. Interest boosts and priority boosts are stripped — no personalization signals. Selection by base score and diversity penalties only.

The design intent is explicit: Phase 1 lets your preferences dominate. Phase 2 asks what a smart, disinterested reader would surface that your learned profile suppressed. The serendipity picks aren't random — they're still model-scored, still quality-filtered, still diversity-penalized. They just ignore what you've trained the system to like.

This matters because 400 signals is not enough to know what you don't know you want. The personalization loop and the serendipity pool are intentionally in tension. One reinforces your profile. The other protects against it.

Tagged `serendipity_pick: true` in output.

---

## Data Layer

Flat JSON files for now — a deliberate deferral, not an oversight.

The decision was to get daily operations running first. A database schema designed before real usage would have been wrong. Six weeks of production has generated the context needed to design it correctly: article history, user feedback patterns, signal enrichment, source trust evolution, cost tracking. The data is now growing daily and the migration is the next infrastructure decision.

Files are keyed and structured to match a relational schema. The migration path is clean when the time comes.

| File                        | Purpose                                              |
|-----------------------------|------------------------------------------------------|
| `curator_latest.json`       | Today's briefing (20 articles)                       |
| `curator_history.json`      | 30-day rolling archive, keyed by `hash_id`           |
| `curator_preferences.json`  | User feedback, learned patterns, serendipity config  |
| `curator_sources.json`      | RSS source trust scores, probationary tracking       |
| `curator_signals.json`      | X bookmark signals with enriched destination text    |
| `x_pull_state.json`         | X pull tracker (`last_pull_at`)                      |
| `priorities.json`           | Active priority definitions + web search results     |
| `curator_costs.json`        | Per-run cost log (model, tokens, USD)                |
| `curator_config.py`         | Shared constants — domain names, file paths          |

**Archive:** `curator_archive/` — daily HTML snapshots.

---

## Web Layer

Flask server (`curator_server.py`, port 8765) serves five views:

| Page            | Route                         | Source                               |
|-----------------|-------------------------------|--------------------------------------|
| Daily Briefing  | `/`                           | `curator_latest.html`                |
| Reading Library | `/curator_library.html`       | `curator_preferences.json` + history |
| Priorities      | `/curator_priorities.html`    | `priorities.json`                    |
| Deep Dives      | `/interests/2026/deep-dives/` | Generated HTML files                 |
| AI Observations | `/curator_intelligence.html`  | `intelligence_YYYYMMDD.json`         |

Feedback (like/dislike/save, deep dive, notes) POSTs to `/feedback` and `/deepdive`.

**Note:** Deep dive paths are year-scoped (`/interests/2026/`). Intentional — annual folders are natural boundaries. A new path is created each January.

---

## Telegram Layer

Two bots, two distinct responsibilities — a deliberate architectural separation locked in April 2026 during the language-german domain build.

**`@rvsopenbot`** (`telegram_bot.py`) — the execution channel:
- Delivers top 10 articles each morning with inline Like/Dislike/Save buttons
- Callback handlers POST feedback to the Flask server
- Commands: `/run`, `/status`, `/briefing`, `/dry-run`
- Voice messages are transcribed, pattern-matched against known commands, and executed
- Handles all domain pipeline commands: `!german`, future `!french`, `!research` etc.
- Receives `GERMAN_SESSION_TRANSCRIPT` and other structured data submissions
- **Owns polling exclusively** — no other process polls this token

**`minimoi_cmd_bot`** — the command and agent channel:
- Plain text commands and open-ended agent requests
- Routes to OpenClaw or future agent for reasoning, tool use, multi-step tasks
- Natural language instructions, memory updates, operational queries
- Separate bot token, separate chat ID, separate responsibilities

**Binding rule — one poller per token:** `telegram_bot.py` is the sole poller for `@rvsopenbot`. OpenClaw or any future agent is the sole poller for `minimoi_cmd_bot`. No two processes ever poll the same token simultaneously. Violations cause 409 conflicts and silent message loss.

**Agent portability:** The Telegram interface (`telegram_bot.py`) is independent of any specific agent. If OpenClaw is replaced, the interface does not change — only the routing target changes.

**Messaging platform:** Telegram is the current choice — secure, bot API is clean, and free. There is no architectural preference for Telegram over WhatsApp, Signal, or any other platform. The integration is isolated to `telegram_bot.py` and swappable.

---

## Agent Layer

Mini-moi was conceived as an agent platform from the start — the name is intentional. The current build layer uses OpenClaw for planning, memory, and conversational design, and Claude Code for implementation. Robert sits between them as the decision point.

OpenClaw is a capable partner for back-and-forth design sessions and persistent memory across sessions. It is one option, not a dependency. The architecture is agent-agnostic — a different agent framework could be swapped in as the ecosystem evolves.

As the platform matures, the agent layer shifts from build support toward runtime use: mobile command interface, session memory, operational monitoring.

---

## Key Design Decisions

**Model-agnostic dispatcher** — `curate(mode=...)` branches to the appropriate scorer. HTML and feedback systems never assume a specific model. All scorers return a normalized `{score, method}` result. Provider rotation is expected — the architecture doesn't lock to any one API.

**Platform over product** — geopolitics and finance is the first domain. The pipeline pattern (ingest → score → deliver → feedback) is designed to host additional domains without structural changes.

**Run first, design later** — operational decisions (flat files, single user, local infra) were deliberate deferrals. Six weeks of daily production has generated the context to make the next infrastructure decisions correctly: database schema, multi-domain data separation, always-on hosting.

**Infrastructure migration path** — currently runs on a MacBook laptop. Hourly polling with idempotency (rather than fixed-time cron) was a conscious design choice: it works correctly on a laptop that sleeps and is equally correct on always-on infrastructure. Migration to Mac Mini or equivalent is a configuration change, not a rewrite. See Deployment Constraints section below for the full migration strategy.

**One domain per language** — the language learning domain pattern establishes that each language gets its own independent domain (`language-german`, `language-french`, etc.). Shared utility code is factored out only after two domains prove the pattern. Progress tracking, personas, and error taxonomies are language-specific and never shared across domains.

**Content breadth as a direction** — a key evolution ahead is consuming broader and deeper sources. The ingestion layer is designed to absorb new RSS feeds, APIs, and signal types without changes to the scoring or delivery pipeline.

**Signal sources** — RSS feeds are the primary input. Additional signal types are normalized to the same schema before scoring — the scorer sees one unified candidate pool regardless of source. New sources plug in at the ingestion layer.

**Dry-run mode** — full pipeline execution without writing to history or archive. Output goes to `curator_preview.html`. Used for model changes, config tweaks, and testing.

**Credentials** — macOS Keychain via `keyring`. No credentials in files or environment variables.

---

## Deployment Constraints & Migration Path

### Current State: MacBook-Dependent

The entire stack runs on a MacBook. This works for daily home use. It breaks for mobile use: when the MacBook is closed, sleeping, or not on the network, polling stops, pipelines cannot fire, and Telegram commands go unanswered.

This constraint became real during the language-german domain build (April 2026). The domain works. The pipeline works. But using it from Vienna — submitting a transcript from an iPhone while traveling — requires the pipeline to be running somewhere always-on.

### Migration Stages

**Stage 1 — Mac Mini (v1.2, target Summer 2026)**

Move the entire stack to a Mac Mini that stays home, always on. This is a configuration change, not a rewrite — the idempotency design means everything runs correctly on always-on infrastructure without modification.

Required additions:
- `systemd`-equivalent launchd services with auto-restart on reboot
- Tailscale for secure remote SSH access from anywhere (free, non-negotiable)
- Validation: full pipeline fires from iPhone while MacBook is off

Tailscale is essential — it provides SSH access to the Mac Mini from Vienna or anywhere, enables remote service restarts from an iPhone, and does not expose the Mac Mini to the public internet.

**Stage 2 — Cloud Relay (v1.3, only if Mac Mini proves insufficient)**

A lightweight stateless relay (Cloudflare Workers, Railway, or Fly.io — $0-5/month) sits in front of the Mac Mini. It receives Telegram messages and queues them when the Mac Mini is unreachable, forwarding when it comes back.

The relay is a dumb pipe — no compute, no data storage, no API calls. All processing stays on the Mac Mini. This adds resilience against home network outages and power failures without moving data to the cloud.

Build trigger: only justified if Mac Mini + Tailscale proves unreliable across multiple trips. Do not build speculatively.

**What never moves to the cloud:**
- Session data, Anki cards, progress tracking
- Personal content of any kind
- Anthropic API calls (direct from Mac Mini)
- Domain data files

### Principle

Functionality before portability. The Mac Mini migration unlocks mobile use. The cloud relay adds resilience. Neither is needed to learn German before Vienna.

---

## Codebase Scale

~15,000 lines of Python across 8 core modules. Primary: `curator_rss_v2.py` (~3,000 lines, includes HTML generation), `telegram_bot.py` (~1,000 lines), `curator_server.py` (~900 lines), `curator_intelligence.py` (~600 lines).
