# Spec: Per-Domain / Per-Model AI Cost Tracking

**File:** `docs/specs/spec_ai_cost_tracking_2026-07-19.md`
**Status:** Approved — Robert, 2026-07-19, after OpenClaw + Claude.ai review
**Date:** 2026-07-19
**Author:** Claude Code (plan-mode session, approved by Robert)
**Related:** GitHub issue #93 (Curator cost log doesn't persist in production)

---

## Intent

This session found that Curator's cost log (`curator_costs.json`) doesn't actually
persist in production — it writes to a path inside the container that isn't
host-mounted, so every deploy wipes it (issue #93). Digging further turned up a
bigger gap: **German, Portuguese, and CoS make real LLM API calls with zero cost or
token logging anywhere** — confirmed by direct code search, not absence of evidence.

Robert wants this fixed properly, not patched narrowly: a real cross-domain
capability that answers "what does minimoi cost to run," broken out by **domain,
model, and day** — which means adding capture at every place an API call actually
happens, not just repairing Curator's one broken file path.

**This is also intended to become standing infrastructure, not a one-time fix.**
Once shipped, `cost_report.py` becomes the ongoing source of truth for the cost
figures that appear in ARCHITECTURE.md's AI Usage table (currently
`~$0.314/run`-style estimates, not measured) — a later architecture revision should
point at it directly rather than re-estimating those numbers by hand.

## What exists today (reuse, don't rebuild)

- **`curator_rss_v2.py:log_curator_cost()`** (~line 84) — the exact schema and
  append-to-JSON pattern to generalize: `{date, timestamp, model, use_type,
  input_tokens, output_tokens, cost_usd}`. This is the template for the new shared
  logger, not a from-scratch design.
- **`curator_rss_v2.py:_DATA_DIR`** (~line 77) — already does what `_COST_LOG` should
  have done: `Path(os.environ.get("CURATOR_DATA_DIR", .../data/curator))`. That exact
  directory (`/opt/minimoi/data/curator:/app/data/curator`) is already host-mounted in
  `docker-compose.prod.yml`. Curator's fix is moving `_COST_LOG` into this
  already-mounted directory — no compose change needed for Curator.
- **`cost_report.py`** — already aggregates two sources (Curator + OpenClaw chat) into
  a report. Extend it; don't build a second reporting tool.
- **German's and Portuguese's data directories are already whole-directory mounted**
  (`/opt/minimoi/data/german:/app/domains/german/data`, same pattern for Portuguese) —
  a new cost-log file dropped in either directory is automatically persisted, no
  compose change needed.
- **`curator_deepdive.py:analyze_with_sonnet()`** (~line 191) already reads
  `response.usage.input_tokens`/`output_tokens` and computes a cost — it just prints
  it instead of logging it. Easiest site to wire up.

## The gap — every call site, mapped

No shared LLM client wrapper exists anywhere in the codebase. German
(`domains/german/providers/review_router.py`), Portuguese
(`domains/portuguese/review_router.py`), and CoS
(`domains/cos/backends/grok_backend.py`) each independently reimplement the same
3-provider (xAI / OpenAI / Anthropic) try pattern, with no code sharing between them.

| Domain | Call sites | Provider(s) | Usage data available? |
|---|---|---|---|
| German | `german_domain.py:_call_llm()`, `:analyse_session()`; `providers/review_router.py` — `_review_grok/_review_openai/_review_claude`, `_chat_grok/_chat_openai/_chat_claude_chat` (6 total across the two files) | xAI, OpenAI, Anthropic, Ollama | Yes — response object in scope, `.usage` unread |
| Portuguese | `review_router.py` — same 6-site shape as German | xAI, OpenAI, Anthropic | Yes — same |
| CoS | `backends/grok_backend.py:call_backend()`, `:_maybe_update_memory()` | xAI | Yes — same |
| CoS (special case) | `chief_of_staff.py:ui_transcribe()` (Whisper) | OpenAI Whisper | **No token usage exists** — Whisper is priced per audio-minute, not tokens. Needs its own cost formula (duration × $/minute), not the token-based path. |
| Curator family | `deep_dive.py:analyze_with_sonnet()`, `curator_intelligence.py:_haiku()/_sonnet()` (share one client, `_haiku_client()`) | Anthropic | Yes — unread |
| Curator family | `curator_deepdive.py:analyze_with_sonnet()` | Anthropic | **Already captured**, print-only — just needs the log call added |

Fifteen call sites total. Every one except Whisper already has the API response
object in scope with `.usage` sitting on it, unread — this is additive
instrumentation (read a field that's already there), not a change to any call's
actual behavior or a new dependency.

**Explicitly out of scope:** Brave Search (`curator_priority_feed.py:brave_search()`,
research-intelligence's `fetch_brave_results()`) has no per-call dollar cost — it's a
flat-rate subscription, not metered per query. A call counter would be the only
meaningful signal there, and it's a different kind of metric than `cost_usd`. Not
building it in this spec; noted so it isn't assumed to already be covered.

## Design

**A new shared module, `utils/cost_log.py`.** One function every call site calls
right after its API response comes back:

```python
log_ai_cost(domain: str, model: str, use_type: str,
            input_tokens: int, output_tokens: int)
```

It owns a single `PRICING` dict (per-model $/token, covering xAI, Anthropic, OpenAI,
and Ollama at $0) so cost math lives in one place instead of being recomputed or
hardcoded at 15 sites — the function looks up the rate itself and computes
`cost_usd`, callers only pass token counts and the model name.

**Each domain gets its own JSON log file**, written into that domain's *already
host-mounted* data directory:

| Domain | Log file | Mount status |
|---|---|---|
| Curator | `data/curator/costs.json` (moved from the broken root-level path) | Already mounted — no compose change |
| German | `domains/german/data/costs.json` | Already mounted — no compose change |
| Portuguese | `domains/portuguese/data/costs.json` | Already mounted — no compose change |
| CoS | `domains/cos/data/costs.json` (new) | **Needs one new line in `docker-compose.prod.yml`** — CoS currently only mounts two individual files (`cos_memory.md`, `cos_context.json`), no directory |

Per-domain files (not one shared file) avoid cross-container write contention and
match the mount pattern each domain already has — no new shared-volume plumbing.

## Phases

### Phase 1 — Foundation + Curator fix (closes issue #93)

- Build `utils/cost_log.py` with `log_ai_cost()` and the `PRICING` table.
- Migrate `curator_rss_v2.py:log_curator_cost()` to call the shared function,
  writing into `_DATA_DIR` instead of the broken container-root path.
- Wire the three Curator-family scripts: `curator_deepdive.py` (add the log call to
  its already-computed cost), `deep_dive.py` and `curator_intelligence.py` (add
  `.usage` extraction, then the log call).

**Definition of Done — Phase 1:**
- [ ] `utils/cost_log.py` exists with `log_ai_cost()` and `PRICING`
- [ ] Curator's cost log writes to the host-mounted directory; survives a redeploy
      (push a trivial commit, redeploy, confirm prior entries still present — the
      literal regression test for #93)
- [ ] `curator_deepdive.py`, `deep_dive.py`, `curator_intelligence.py` each produce a
      log entry on a real run

### Phase 2 — German, Portuguese, CoS instrumentation

- Add the one-line `log_ai_cost()` call at each of the remaining ~12 sites, after
  extracting `.usage` from the already-in-scope response object.
- Whisper transcription gets its own cost path: duration × OpenAI's per-minute
  Whisper rate, logged via the same `log_ai_cost()` call but with a duration-derived
  cost rather than token-derived.
- Add CoS's missing directory mount to `docker-compose.prod.yml`.

**Definition of Done — Phase 2:**
- [ ] A German session (voice or text) produces a cost log entry
- [ ] A Portuguese session produces a cost log entry
- [ ] A CoS chat message produces a cost log entry
- [ ] A CoS voice-transcribe call produces a cost log entry with the audio-duration
      formula, not a token-based one

### Phase 3 — Reporting

- Extend `cost_report.py` to read and merge all four per-domain JSON cost files
  in memory (Curator, German, Portuguese, CoS — replacing its current two-source
  read of Curator + OpenClaw chat only) and produce a domain × model × day subtotal
  view. This is a small in-memory aggregation over four small files, not a live
  cross-domain data store — see "Considered and rejected" below for why that
  distinction matters.

**Definition of Done — Phase 3:**
- [ ] `cost_report.py` explicitly reads all four per-domain cost files
      (`data/curator/costs.json`, `domains/german/data/costs.json`,
      `domains/portuguese/data/costs.json`, `domains/cos/data/costs.json`) and
      merges them
- [ ] Its existing CLI views (day / week / month / year) show real, non-zero
      entries broken out by domain and model, across all four domains

## Decisions from review (OpenClaw + Claude.ai + Robert, 2026-07-19)

1. **JSON-first, not Postgres — confirmed.** Per-domain JSON files stay, as specced.
2. **Whisper cost formula — confirmed as specced.** Callers compute the dollar
   figure from audio duration themselves and call the same token-shaped
   `log_ai_cost()`; no second entry point.
3. **Phase 3's DoD tightened** to explicitly name reading all four per-domain files
   (done above) rather than leaving the aggregation implicit.

### Considered and rejected: a shared `costs.jsonl` across domains

OpenClaw proposed a single shared JSONL file (or shared mount) that all domains
write to, as a more "unified" alternative to four separate per-domain JSON files.
**Rejected.** Phase 3 already produces the domain × model × day view by having
`cost_report.py` read and merge four small JSON files in memory — that's a trivial
aggregation, not a workload that needs a shared write path or a new cross-container
mount to avoid. Adding a parallel shared-file mechanism now is exactly the kind of
pre-built abstraction the platform's own Design Principle #6 warns against ("extract
shared code only when duplication has actually happened twice and reconverged" —
not in anticipation of it). If reporting genuinely needs a live cross-domain tail
later (e.g., a real-time dashboard rather than a periodic report), that's a
legitimate, well-motivated **Phase 4** — not something to build speculatively inside
Phase 1.

### Still open

- The `PRICING` table's rates need a maintenance owner. Model pricing changes over
  time (already true once this session: `grok-3-mini` → `grok-4-1-fast-reasoning`),
  and a stale rate silently produces wrong totals rather than an error. Not resolved
  in this review round — carry forward.

## Not in this spec

- Brave Search call-count tracking (noted as a real gap, not the same kind of metric
  as `cost_usd` — separate future item if wanted).
- Any UI/dashboard for viewing the cost report inside the app — this spec covers
  capture and CLI reporting only. A Guild-surfaced cost view would be a follow-on
  spec once the data actually exists to show.
