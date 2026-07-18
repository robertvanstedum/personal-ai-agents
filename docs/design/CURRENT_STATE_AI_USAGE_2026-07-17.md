# Current State — AI Usage Across Mini-moi
**A working reference, not a polished document.** This preserves every finding, correction, and open question from the 2026-07-17 AI usage audit and the design discussion that followed, in full detail. The architecture document is deliberately more curated than this — this file is where the underlying evidence lives so nothing gets lost in service of a tighter narrative. Come back here when the architecture doc needs re-grounding, or when any of the open items below get resolved.

**Sources:** Three independent read-only code investigations by Claude Code (German, Portuguese, CoS/Guild — then Curator re-verification), each tracing every LLM/voice call site to file:line, cross-checked against each other. No code was changed producing any of this.

---

## Cross-cutting findings

**1. "Model-agnostic, swap via config" holds almost nowhere, including Curator.** Of roughly 30 LLM/voice call sites found across German, Portuguese, and CoS/Guild, exactly two are genuinely config-driven without a code change: German's CLI transcript reviewer (`reviewer.py`, reads `reviewer_model` from `domain.json`) and Guild's `ChallengerService` (reads model names from `challenger_config.json`). Every other call site hardcodes the model as a literal string. Curator — the domain everyone assumed was the clean counter-example — turned out not to be one either: its `--model=` flag genuinely works for the values it recognizes, but both production cron scripts pass `--model=grok-4.3`, which isn't one of those values, so production has been silently falling through to a default. The correct model has been landing by coincidence. Already independently flagged and backlogged as `spec_125_model_standardization_2026-07-05.md`, unresolved as of this writing.

**2. `docs/LLM_REGISTRY.md` is significantly out of date.** It scopes itself to "Curator, Mein Deutsch, Research Intelligence" — CoS and Guild aren't mentioned at all (9+ call sites missing), Portuguese has zero matches despite having 9+ of its own call sites, German's own entry is missing 6 call sites (the entire Gespräche review router, both OpenAI voice calls), TTS doesn't appear anywhere in the model table despite being live in both language domains, and `grok-4-1-fast-reasoning` — the single most-used model string across CoS/Guild (9 of 11 call sites there) — doesn't appear as its own registry entry. The registry also overstates Sonnet's role in German's transcript review: true for two of three review paths, false for the third, which defaults to Grok and lets the user pick a different provider entirely via dropdown — the registry doesn't disclose that this path is user-selectable at all. Curator's own registry entry is more accurate than Portuguese's or CoS's but still misses Priority Feed entirely, misses the new Challenger pass (below), and documents one script (`curator_deepdive.py`) as live when it's actually dead in production.

**3. No domain outside Curator has a true periodic, accumulate-then-synthesize review.** Curator's weekly Sonnet pass reads 30 days of accumulated observations and synthesizes across them. Nothing else in the platform does this yet:
- German's weekly `cos_german_watch.py` looks like a candidate but is actually a competitive-landscape scanner plus a practice-reminder nudge — it never reads the user's own session transcripts, errors, or vocabulary history.
- CoS/Guild's four scheduled loops (twice-daily career scout, weekly German-tool watch, weekly Curator-topic watch, biweekly novelty/competitor watch) all call a model on some cadence, but every one scores only that run's freshly-fetched external search results. None of them loads `cos_memory.md`, prior agenda entries, or any other accumulated internal state before calling the model.
- Portuguese has nothing at any cadence at all — no plist, no cron reference anywhere in the repo. Even the daily article-ingestion job claims in its own header comment to run via launchd/cron, but no actual scheduled invocation was found — only an on-demand HTTP route triggers it today.
- Guild's Operations agent makes zero LLM calls of any kind — its two loops (5-minute health check, hourly log audit) are pure infrastructure/SQL, correctly excluded from this count.

---

## Domain-by-domain detail

### Curator

**Model tiers, re-verified 2026-07-17 (supersedes March 2026 figures):**

| Stage | Model | Cost | Notes |
|---|---|---|---|
| Bulk pre-filter | Claude Haiku | ~$0.001/run | 10x cheaper than the March estimate |
| Daily ranking | `grok-4-1-fast-reasoning` | ~$0.314/run | Rotated from `grok-3-mini` (~$0.197/run); confirmed via `curator_costs.json` |
| Deep Dives | Claude Sonnet, on-demand | ~$0.25/session | |
| AI Observations (daily) | Claude Haiku | ~$0.005–0.01/run | |
| AI Observations (weekly) | Claude Sonnet, Sunday only | ~$0.07/week | |

The documented Ollama/Gemma local-mechanical fallback tier never existed in the codebase — planned, never built. Worth correcting rather than repeating.

**AI Observations** confirmed clean and unchanged since March: all five observation types active, same models, same Sunday-only gating for the weekly pass.

**New, undocumented capability:** a real multi-model challenge pattern (Opus drafts → Sonnet-4-6/Grok cross-check → Opus reconciles) went live in Curator's Deep Dive pipeline in June 2026. Possibly the same mechanism as Guild's `ChallengerService` (whose only confirmed production caller is Research Intelligence's `generate_dive.py`) and Research Intelligence's already-documented Synthesizer+Challenger framework from March 2026 — three names for what may be one capability. **Not yet confirmed — see Open Questions.**

**Multi-agent curation case study** (Jaccard similarity + three-gate structure) confirmed NOT implemented — design document only, zero code evidence.

**Deep Dive script fragmentation:** three coexisting scripts, only one actually live in production; the other two have nothing pointing to them. Robert's read: this may not be pure duplication — the frontend has grown to offer Scans, Deep Dive, and Deeper Dive as related-but-distinct options, and some of the apparent "dead code" may be legitimately separate features under different names rather than drift. **Needs re-verification of each script's actual current purpose before any regression/cleanup spec is written.**

**Priority Feed and X-bookmark-pull** confirmed unchanged and as-documented. X-bookmark-pull is intentionally AI-free by design — not a gap.

### German (Mein Deutsch) and Portuguese (Meu Português)

**Voice** — architecturally identical, and correctly so: browser-native `SpeechRecognition`/`speechSynthesis` handle free client-side cases (Lesen/Schreiben/Wörter/Üben and Escrita/Leitura/Palavras respectively); server-side OpenAI Whisper (`whisper-1`) and TTS (`tts-1`) handle live AI-persona conversation in Gespräche/Conversas, persona voice picked by gender. All voice model strings are hardcoded literals in both domains — no config-driven voice model swap exists anywhere. German additionally has an informal, non-API third path: assembling a text prompt for the user to paste into a separate consumer Grok Voice app for hands-free CarPlay use while driving — not a code dependency, a deliberate bridge to an existing tool rather than a fourth in-house voice pipeline.

**Translation:**

| | German | Portuguese |
|---|---|---|
| Route | `/api/translate` | `/api/pt/translate` |
| Fallback chain | Phrasebook JSON cache → DeepL → 3-provider LLM chain (`grok-4-1-fast` → `claude-haiku-4-5` → local Ollama `gemma3:1b`) | Postgres cache → DeepL → single-provider fallback (`claude-haiku-4-5` only) |
| Resilience | Still works if Claude's API is down (local Ollama fallback) | Fails entirely if Claude's API is down — no further fallback |
| Cache backend | Local JSON phrasebook file | Postgres `portuguese.translation_cache` table |
| Timing transparency | Frontend displays DeepL-vs-LLM timing breakdown | Backend returns the same data; frontend never displays it |

Both use near-identical `_get_deepl_client()` functions — clearly copied from one to the other, then diverged. Real, fixable, bidirectional gap: Portuguese's Postgres-backed cache is arguably the better long-term approach; German's fallback depth and frontend transparency are both ahead. DeepL API keys are config/env-driven on both sides already; the LLM fallback chains are hardcoded literals on both sides.

**Transcript analysis:**

German has three separate implementations of the same idea:

| Path | Trigger | Model | Config-driven? | Drives automation? |
|---|---|---|---|---|
| A — CLI pipeline (`reviewer.py`) | Dropbox-watched dictated/pasted transcripts | `claude-sonnet-4-6` (default) | Yes, via `domain.json` | Yes — Anki cards, next-lesson-plan, `progress.json` |
| B — Web app, human-tutor sessions | `/api/analyse-transcript` | `claude-sonnet-4-6` | No, hardcoded | No — UI feedback only |
| C — Web app, KI-Persona sessions | `/api/review`, user picks provider via dropdown | Grok by default; GPT-4o or Claude Sonnet if user selects | No — hardcoded per provider; selection is a UI dropdown, not a config setting | No — UI feedback only |

Only Path A drives real downstream automation. Portuguese's `review_router.py` is a genuine, structurally-parallel port of German's (its own docstring says so explicitly) — real parity, not a gap — but its Claude option is `claude-haiku-4-5`, a full tier below German's `claude-sonnet-4-6` for the equivalent feature, and it lacks German's stubbed (non-functional in either domain) Gemini provider branch.

**Correction needed in existing docs:** `LLM_REGISTRY.md` states German's transcript review goes to "Sonnet" — true for Paths A and B, false for Path C, which defaults to Grok and doesn't disclose that it's user-selectable at all.

**Content selection (Lesen/Leitura):** Neither domain does real scoring or ranking. Both use the identical, copy-pasted `_apply_source_cap()` function (same body, same docstring, same defaults: 10 max/category, 3 max/source, 3 min/category). German additionally runs one LLM call during ingestion (`categorize_article()`) tagging each article into one of 4 fixed categories — classification only, doesn't influence which articles make the pool. Portuguese has no LLM call anywhere in its ingestion pipeline — category comes from static per-source config. German's own `lesen_sources.json` already documents this as "a known Phase 2 issue. Requires source scoring" — an acknowledged, intentional gap, not a surprise. **Design decision (Robert, 2026-07-17): this lightness is correct and should stay — language practice is embodied/immersive, not a decision-support problem, and doesn't need Curator-equivalent scoring.**

**Per-user personalization in Portuguese — definitive finding:** no `proficiency_level` (or equivalent) field exists on `auth.users` or any `portuguese.*` table. The only "level" field in the schema (`portuguese.articles.level`) describes the article's source, not the user, and is confirmed never read by any query. Every AI-facing function signature was traced directly (`_translate_phrase`, `_run_correction`, `run_review`, `run_chat_turn`) — none accept a user ID or proficiency parameter. Every logged-in user hits an identical prompt and identical article pool regardless of skill level. One dead code path exists: `_request_user_tier()` reads an `X-Minimoi-User-Tier` header but is defined once and never called anywhere — evidence that proficiency-aware personalization was attempted once and abandoned, not simply never considered.

**Correction (Robert, 2026-07-17):** Portuguese's multi-user support is not proficiency-tiered by design — it's generic default personas plus fully user-controlled custom personas (any user can add their own, e.g. a persona for a friend). The gap above is real, but it's not a broken version of a proficiency-tiering feature that was supposed to exist — proficiency-awareness was never the intended design; per-persona control was.

**Priority reordering (Robert, 2026-07-17):** German/Portuguese convergence work now outranks the translation-config-swap fix, because a third language domain (French) is planned, timed to a family member's upcoming school year. German and Portuguese need to be a solid, consistent template before French is built on it — and the convergence is bidirectional (bring German's fallback resilience and Sonnet-tier review to Portuguese; bring Portuguese's Postgres-backed cache and frontend timing display to German), not simply Portuguese catching up to German.

### CoS and Guild

CoS was recently extracted out of Guild as its own domain. Both are in active redesign as a direct result — separating them clarified what each one is actually for in a way that wasn't visible while combined. Everything below is current direction, not settled architecture.

**Voice:** CoS has Whisper (`whisper-1`) input only, server-side, on the `/ui/transcribe` route — no TTS output anywhere in CoS or Guild. This is a second, separate Whisper integration beyond the already-documented Telegram voice pipeline, not previously noted in the registry.

**Agent layer:** OpenClaw is the Phase 1 implementation, intentionally meant to be backend-swappable — an infrastructure decision, not a user-facing one. Making that swap mechanism actually real is acknowledged, scoped integration work, not yet done.

**Common memory:** the three-tier taxonomy (episodic/semantic/procedural), with `cos_memory.md` as the currently active episodic tier, is meant to function as a repository independent of whichever agent is running — the point being continuity across a future agent swap, not agent-specific private state.

**Scheduled loops:** four call a model on some cadence (career scout, German-tool watch, Curator-topic watch, novelty/competitor watch) — see cross-cutting finding #3 above; none reads accumulated internal state first. Two other loops (build-discipline check, guest-nudge check) plus the EC2 health check make no LLM calls at all — pure cron/maintenance.

**`ChallengerService`:** real code, "Grok challenges, Claude synthesizes," inserted as a third review layer into Research Intelligence's Deeper Dive pipeline — but its only confirmed production caller anywhere in the repo is Research Intelligence's `generate_dive.py`, despite two Guild-specific configs (`guild_cos_analysis`, `guild_career_assessment`) existing and marked enabled. May be effectively dead code for its intended Guild use case, or may be the same mechanism as Curator's new Deep Dive Challenger pattern — see Open Questions.

**Design decision (Robert, 2026-07-17):** CoS and Guild are the correct first place to extend Curator's periodic-review pattern — design queues, build status, and decisions are exactly the kind of accumulating state that needs periodic reconciliation, the same job Curator's weekly pass does for a reading profile. Language domains explicitly do not need this by design (see above).

---

## Structural principle established during this discussion

Design intent and current implementation state are two different claims and should never be conflated in the architecture document. State the design as it was and still is; separately and honestly call out where real growth caused drift, each tied to its own tracked follow-up. The frontend across the whole platform has grown considerably faster than the backend has been consolidated behind it in places (Curator's Deep Dive fragmentation is the clearest example) — that's drift to name and fix, not evidence the original design was wrong.

The "verify production reality" principle (documented intent ≠ verified behavior) is the through-line connecting: the `venv`/`.gitignore` false alarm, the `docs/specs` volume-mount gap, the `backup_local.sh` overwrite-and-reconstruction, the #83 identity/data-scatter scare, and now Curator's own broken `--model=` flag. Every one of these looked correct on inspection of code or documentation. None were correct in production.

---

## Open questions, unresolved

1. **Is Curator's Deep Dive Challenger pattern, Guild's `ChallengerService`, and Research Intelligence's Synthesizer+Challenger framework one real capability or three separate things?** Needs direct code-level confirmation, not inference from naming/timing.
2. **Does the platform depend on its orchestration agent, or does the agent depend on the platform?** March 2026 platform doc states the latter explicitly; the current agent's own self-description leans the other way. Needs an actual decision.
3. **What is each of Curator's three Deep Dive-family scripts actually for?** Re-verify before writing any regression/cleanup spec — may be genuine feature distinctions (Scans/Deep Dive/Deeper Dive), not pure duplication.
4. **Does the broken `--model=grok-4.3` production flag get reprioritized** now that it's freshly reconfirmed, or stay where it sits in the existing backlog (`spec_125`)?

---

## Follow-up work items, not yet scheduled

- Full correction pass on `LLM_REGISTRY.md` (all gaps listed under cross-cutting finding #2)
- German/Portuguese bidirectional convergence (translation resilience, transcript-review model tier, cache backend, frontend timing display)
- Backend config-driven translation model choice, across both language domains (lower priority than convergence above)
- A separate, living set of per-domain flow/call diagrams, updated on its own schedule, outside the architecture document
- A public-facing GitHub version of the architecture document, once the internal version is settled
