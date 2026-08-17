# How mini-moi uses AI — LLM registry and change log

> STALE 2026-07-21. Current summary: [ARCHITECTURE.md § AI Usage Across the
> Platform](../ARCHITECTURE.md#ai-usage-across-the-platform). A full
> correction of this registry remains a tracked roadmap item, not done here.

**Author:** Robert van Stedum  
**Updated:** 2026-06-07  
**Repo:** github.com/robertvanstedum/personal-ai-agents

---

## Part 1 — Historical narrative (June 2026)

This section records the model mix when the registry was created. It is retained
as change history, not as current configuration. Use Part 2's reviewed subset and
the root Architecture document for current operational claims.

### What mini-moi is

mini-moi is a personal, local-first, model-agnostic AI agent platform built from scratch. Three
domains are in production: Curator (daily geopolitics and finance intelligence), Mein Deutsch
(German language coaching), and Research Intelligence (threaded deep research). The platform is
deliberately model-agnostic — any LLM call can be routed to a different model or provider by
changing one configuration value. Nothing is hardwired to a single vendor.

The design philosophy behind every model choice is **spend follows attention**: expensive inference
runs only when a human is actively engaged with the output. No background inference, no speculative
calls.

---

### Curator — the daily intelligence pipeline

The problem Curator solves is volume. Roughly 700 RSS and X items arrive daily across geopolitics,
finance, and technology. A human can read perhaps 20 of them with real attention. The system's job
is to cut, score, and rank so that the 20 delivered are the 20 worth reading.

LLMs are used at three points in that pipeline:

**Pre-filter and scoring.** The system currently runs a single-stage configuration using xAI Grok-4-1, which scores each
candidate article for category and a 0–10 relevance score in one call. Grok-4-1 was chosen over
Haiku for this role because it is a newer, more capable model at a comparable low price point —
more capability for the same cost tier, with no meaningful increase in daily spend. An alternative
two-stage Anthropic configuration also exists in the codebase: Haiku does a fast
cheap cut (150 articles → 50 candidates), then Sonnet re-scores the shortlist on quality,
originality, and depth. Both configurations are maintained so the production path can be switched.
The two-stage pattern is an explicit cost-optimization: it separates "is this relevant?" (a cheap
classification question Haiku handles well) from "how good is this?" (a harder quality judgment
worth Sonnet's cost, run on far fewer items).

**On-demand article analysis.** When a user saves an article and triggers a Scan, a single Sonnet
call produces a structured deep analysis: why it matters, a contrarian take, and connections to
other topics the user is tracking. This is the highest-value per-call LLM use in the system — one
article, one engaged user, detailed output. Sonnet quality is justified.

**Weekly synthesis.** Once a week, Sonnet synthesizes lateral connections across all tracked
topics — patterns that might not be visible within any single reading stream. This runs on a
schedule but only when research data has accumulated; the output is one of the few LLM calls that
runs without a human directly in the loop, though it produces something the user reads the next
morning.

---

### Mein Deutsch — German language coaching

The coaching system handles translation, writing correction, and verb conjugation — available via
browser, Telegram text, and CarPlay voice sessions. The core LLM call goes through a three-provider
fallback chain implemented in a single `_call_llm()` function:

1. **xAI Grok-4-1-fast** (primary) — chosen for strong German language capability and low latency.
2. **Anthropic Haiku** (fallback) — if xAI is unavailable.
3. **Ollama gemma3:1b, local** (final fallback) — free, runs entirely on the local machine, no
   network required. This ensures translation and correction work even during commutes, offline
   sessions, and CarPlay voice use, where network reliability varies.

The local fallback is not just a safety net — it's a design choice for dry runs and testing, where
running real API calls against a cloud provider adds cost and latency for no benefit.

A second LLM use in this domain is prompt compression: session briefs that are too long for
Telegram's character limits are condensed by Haiku before delivery. This is a purely mechanical
task — correct the length, preserve the content — and Haiku is more than sufficient.

The third use is session review. After a real German conversation (recorded via Google Meet
transcript), the transcript goes to Sonnet for structured feedback: what patterns appeared, what
needs attention, which moments are worth turning into Anki cards. This is user-facing, qualitative,
and worth Sonnet's quality.

---

### Research Intelligence — threaded deep research

A research thread can span multiple sessions over days or weeks. LLMs are used at each stage of
the thread lifecycle:

**Triage.** New search results are classified and sorted by the local Ollama model first. Triage
runs inside an agent loop where call volume is unpredictable, so local/free is the right choice.
Haiku is the cloud fallback when Ollama is unavailable.

**Session synthesis.** After each research session, Sonnet writes a narrative summary of what was
found and extracts the next set of research questions. This output directly drives the next session,
so quality matters. Haiku also runs a targeted query-extraction pass on Sonnet's output — parsing
structured information from known-format text, a task where Haiku is sufficient.

**Thread close-out — Deeper Dive.** When a research thread concludes, Opus runs a two-agent
pattern. A Synthesizer agent builds the essay; a Challenger agent argues against each claim. This
"confirm and complicate" design is intentional: the system surfaces doubt rather than producing
false confidence. Opus runs rarely — once per completed research thread — and at that frequency
its cost is negligible against the research value it produces.

---

### How models are called

All API calls go through provider-specific Python SDK clients. Keys are stored in the macOS
Keychain via the `keyring` library — nothing in `.env` files, nothing in git, nothing on disk in
plaintext. Ollama runs as a local HTTP service on `localhost:11434` with no authentication.

Prompting follows a consistent pattern across the codebase: structured task prompts with explicit
output format instructions. Calls that feed downstream processing (pre-filter, scoring, entity
extraction, query extraction) request JSON-structured output so the results can be parsed
programmatically without post-processing. User-facing calls (session review, Leanings teammate
read, Deeper Dive essays) use narrative output prompts that prioritize readability and completeness
over structure.

---

## Part 2 — Model registry

*Curator, language, and Research Intelligence subset reviewed 2026-08-17. CoS,
Guild, Portuguese, and voice coverage still require the full registry audit tracked
elsewhere. Update this table and add a changelog entry whenever a listed model or
provider changes.*

| # | Program | Domain | Feature | Model | Provider | Fallback chain |
|---|---|---|---|---|---|---|
| 1 | `curator_rss_v2.py` | Curator | Article pre-filter — 150 → 50 candidates | claude-haiku-4-5 | Anthropic | (two-stage) Haiku → Sonnet |
| 2 | `curator_rss_v2.py` | Curator | Article scoring — category + relevance (Anthropic path) | claude-haiku-4-5 | Anthropic | (two-stage) Haiku → Sonnet |
| 3 | `curator_rss_v2.py` | Curator | Article ranking — quality/originality/depth re-score | claude-sonnet-4-5 | Anthropic | (two-stage) Haiku → Sonnet |
| 4 | `curator_rss_v2.py` | Curator | Article scoring — full score + category (xAI path, production) | grok-4.3 | xAI | mechanical scoring on API failure |
| 5 | `curator_feedback.py` | Curator | Scan — deep analysis: implications, contrarian take, connections | claude-sonnet-4-5 | Anthropic | — |
| 6 | `curator_feedback.py` | Curator | Feedback metadata extraction — user feedback → structured JSON | claude-sonnet-4-5 | Anthropic | — |
| 7 | `curator_deepdive.py` | Curator | Deep article analysis — implications, angles, what to watch | claude-sonnet-4-5 | Anthropic | — |
| 8 | `curator_intelligence.py` | Curator | Daily observations — topic velocity, blind spots, source anomalies | claude-haiku-4-5 | Anthropic | — |
| 9 | `curator_intelligence.py` | Curator | Weekly lateral connections — synthesis across tracked topics | claude-sonnet-4-5 | Anthropic | — |
| 10 | `curator_utils.py` | Curator | X/bookmark entity extraction — topics and entities from tweet text | claude-haiku-4-5 | Anthropic | — |
| 11 | `research_routes.py` | Curator | Leanings teammate read — AI brief on a research leaning, on demand | claude-sonnet-4-5 | Anthropic | — |
| 12 | `german_domain.py` | German | Translation, correction, conjugation — primary | grok-4.3 | xAI | xAI → Haiku → Ollama |
| 13 | `german_domain.py` | German | Translation, correction, conjugation — fallback 1 | claude-haiku-4-5 | Anthropic | xAI → (this) → Ollama |
| 14 | `german_domain.py` | German | Translation, correction, conjugation — fallback 2 (local) | gemma3:1b | Ollama | xAI → Haiku → (this) |
| 15 | `get_german_session.py` | German | Prompt compression — condenses prompts for Telegram limits | claude-haiku-4-5 | Anthropic | — |
| 16 | `reviewer.py` | German | Session review — structured feedback on practice transcript | claude-sonnet-4-6 | Anthropic | — |
| 17 | `research.py` | Research Intelligence | Triage — classify search results (local first) | gemma3:1b | Ollama | Ollama → Haiku |
| 18 | `research.py` | Research Intelligence | Triage — classify search results (cloud fallback) | claude-haiku-4-5 | Anthropic | Ollama → (this) |
| 19 | `observe.py` | Research Intelligence | Session synthesis — narrative summary of findings | claude-sonnet-4-5 | Anthropic | — |
| 20 | `observe.py` | Research Intelligence | Query extraction — next research questions from synthesis | claude-haiku-4-5 | Anthropic | — |
| 21 | `generate_dive.py` + `ChallengerService` | Research Intelligence | Deeper Dive closing synthesis and challenge | claude-opus-4-5 → grok-4.3 → claude-sonnet-4-6 → claude-opus-4-5 | Anthropic + xAI | Graceful return to first synthesis if shared challenge fails |
| 22 | `telegram_bot.py` | Platform | Voice transcription — Telegram audio to text | whisper-1 | OpenAI | — |
This table is not a complete call-site inventory. Its reviewed subset is current;
the omitted CoS, Guild, Portuguese, and voice entries remain a tracked audit item.

---

## Part 3 — Model selection rationale

### Three tiers

| Tier | Models | When used | Why |
|---|---|---|---|
| Fast / cheap | Haiku, Grok 4.3 with no reasoning, gemma3:1b | Volume jobs, mechanical extraction, pre-filtering, agent loops | High call frequency; quality bar is "good enough to triage"; cost matters |
| Quality | Sonnet (4.5, 4.6), Grok 4.3 | Final scoring, deep analysis, user-facing feedback, synthesis | Lower frequency; output is what the user reads; quality is felt directly |
| Premium | Opus 4-5 | Research Deeper Dive essays only | Runs once per thread; two-agent reasoning; cost negligible at this frequency |

### Fallback chains

```
German translation:   xAI grok-4.3 → Anthropic haiku-4-5 → Ollama gemma3:1b (local)
Curator scoring:      xAI grok-4.3 (single-stage, production) → mechanical on API failure
                   OR Anthropic haiku-4-5 pre-filter → Anthropic sonnet-4-5 ranking (two-stage)
Research triage:      Ollama gemma3:1b (local first) → Anthropic haiku-4-5
```

---

## Part 4 — Authentication

All API keys in macOS Keychain via `keyring`. Nothing in `.env`, nothing in git, nothing on disk
in plaintext. Ollama: local HTTP on `localhost:11434`, no authentication.

---

## Part 5 — Changelog

*One entry per change. Date · what changed · which file · why.*

### 2026-08-17 — Retired Grok 4.1 Fast aliases removed from active paths
- **Files:** Curator scoring, German provider chain, CoS/Guild direct calls,
  Challenger configuration, and LiteLLM gateway routes.
- **Change:** Active xAI calls now request `grok-4.3` explicitly. Curator rejects
  unknown CLI model values instead of silently selecting a default.
- **Why:** xAI retired Grok 4.1 Fast on 2026-05-15 and redirected old aliases to
  Grok 4.3. Explicit selection removes hidden provider behavior and permits correct
  cost reporting.

### [Prior to 2026-06-07] — Curator scoring switched to xAI Grok-4-1 (single-stage)
- **File:** `curator_rss_v2.py`
- **Change:** Production scoring path moved to single-stage xAI Grok-4-1, replacing the
  Anthropic Haiku-based configuration.
- **Why:** Grok-4-1 is a newer, more capable model than Haiku at a comparable low price point.
  Better capability for the same cost tier — a more recent release without a significant cost
  increase. The Anthropic two-stage configuration (Haiku pre-filter → Sonnet ranking) is
  preserved in the codebase as a switchable alternative.
- *Documented retroactively at registry creation. Add exact date if known.*

### 2026-06-07 — Initial registry
Full codebase audit. 22 LLM call sites across 16 Python files. Three providers active: Anthropic,
xAI, Ollama local. Curator production path: single-stage xAI grok-4-1. German primary: xAI
grok-4-1-fast with two-level fallback. Document committed to `docs/LLM_REGISTRY.md`.

---
*Commit this file every time the model mix changes. The git diff is the full experiment record;
this changelog is the human-readable why.*
