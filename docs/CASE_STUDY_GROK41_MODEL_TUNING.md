# Case Study: grok-4-1-fast-reasoning Model Tuning
**Date**: 2026-03-06
**Author**: vanstedum + Claude Code + OpenClaw + Grok
**Status**: ✅ Complete — shipped to production

---

## Context

The personal AI curator uses a multi-stage LLM pipeline to score and rank RSS/feed articles daily:
- **Stage 1**: Claude Haiku pre-filter (400 → ~50 articles, cheap cut)
- **Stage 2**: xAI Grok scoring/ranking (final personalised ranking)
- **Delivery**: Telegram morning briefing via `rvsopenbot`

The system maintains a learned user profile (`curator_preferences.json`) from Like/Dislike/Save feedback. The profile is injected into Stage 2 as a dispatcher concern — model-agnostic, passed to whatever LLM is scoring.

**Baseline model**: `grok-3-mini` at `temperature=0`

---

## Goal

Upgrade Stage 2 from `grok-3-mini` → `grok-4-1-fast-reasoning` for:
- Better reasoning depth and pattern recognition
- Larger context window (2M tokens)
- Native tool-use readiness for future wider-sources work
- More nuanced, non-obvious signal surfacing ("surprise" without noise)

---

## Test Methodology

Full structured A/B comparison — same article batch, same injected profile, different model. Results compared on:
- Average score across all articles
- Rank flips (δ ≥ 3.0 = significant)
- Profile deference (does the model respect learned preferences?)
- Rationale quality (literal vs. signal-rich)

---

## Round 1: Baseline A/B

**Result**: ❌ HOLD

| Metric | grok-3-mini | grok-4-1 (default temp) |
|---|---|---|
| Average score | 6.33 | 5.07 |
| Significant rank flips | — | 4 (all downward, δ 3–4) |

**Problem articles:**
1. "Brazil's soy industry" (Deutsche Welle) — Grok-3: +5, Grok-4-1: +1 ("off-topic")
2. "10 Thursday AM Reads" (The Big Picture) — Grok-3: +6, Grok-4-1: +3 ("generic")
3. UN genocide allegations (Die Welt) — Grok-3: +7, Grok-4-1: +4 ("tangential")
4. "10 Tuesday AM Reads" (The Big Picture) — same as #2

**Root cause**: Grok-4-1's stronger reasoning was overriding the injected user profile with independent categorical judgments ("this is generic content"). It was being *smarter* but less *deferential* — applying its own content quality model instead of respecting the user's learned preferences.

---

## Round 2: Prompt Tuning (Profile-Weight Fix)

Added explicit profile deference instruction to the system prompt:

```
The USER PROFILE below is ground truth.
Source preferences OVERRIDE your content judgment.
If a source appears in the user's boost list, score it UP
regardless of whether the content seems generic or off-topic.
The user has chosen these sources deliberately.
```

**Result**: ⚠️ YELLOW LIGHT — Getting Close

- Fixed 2/4 problem articles ✅
- 2 remaining edge cases: Brazil/soy and one UK article

---

## Manual Article Review (Key Step)

Before further prompt tuning, the user read both remaining articles directly. Finding:

**Brazil soy article** — *Extremely relevant*. Brazil exports 70% of its soybeans to China; Europe is importing less due to stricter standards; rainforest clearance is accelerating. This is a commodities × geopolitics × supply-chain signal — exactly the kind of non-obvious intersection the curator is designed to surface. **Grok-4-1 was wrong.**

**UK Ad article** — *Borderline*. More politics than finance, but relevant as a signal of European ideological shift (away from "victim-only" framing). Genuinely ambiguous — not a clear model failure, more a judgment call.

**Decision**:
- Brazil/soy → add domain clarification to prompt (model was wrong)
- UK article → handle via Like feedback loop, not prompt engineering (too subtle to encode as a rule; encoding ideological-shift rules ages badly)

---

## Round 3: Domain Clarification

Added explicit domain guidance:

```
- ZeroHedge sources = trusted risk/contrarian angles
- Commodity/supply-chain disruptions = geopolitical risk signals
- Treat source trust from user profile as categorical, not content-dependent
```

**Result**: ✅ GREEN LIGHT

Brazil/soy article restored to appropriate ranking. Profile deference solid. Rationale noticeably richer than grok-3-mini — less literal, more signal-aware.

---

## Temperature Testing

With the model and prompt validated, tested temperature settings:

| Temperature | Result |
|---|---|
| Default (0) | Deterministic, reliable, slightly flat |
| 1.1 | ❌ Too "newsy" — gravitates toward headline volume, not signal depth |
| 0.7 | ✅ Signal-not-noise sweet spot — sharper reasoning without noise |

**Key insight**: For this use case, temperature is a *signal-to-noise dial*, not just a creativity dial. At 1.1, the model gravitates toward what's broadly trending (the same headlines everyone sees). At 0.7, it maintains analytical sharpness while finding non-obvious angles.

**Temperature 0.7 initially failed** — diagnosed as airport WiFi timeouts, not temperature-related. Confirmed on stable connection (phone hotspot): ran cleanly. ✅

---

## Infrastructure Improvement

During temperature testing, discovered `--temperature` was hardcoded (not a CLI flag). Added proper CLI support:

```bash
# Now works:
python3 curator_rss_v2.py --dry-run --model=grok-4-1 --temperature=0.7
```

Changes: `--temperature=<float>` flag wired through `main()` → `curate()` → `score_entries_xai()` → xAI API call. Default remains `0.0` for production stability.

Commit: `70e1571`

---

## Final Configuration (Production)

```
Model:       grok-4-1-fast-reasoning
Temperature: 0.7
Pre-filter:  Claude Haiku (unchanged)
Prompt:      Profile-weight framing + domain clarifications
             (ZeroHedge = contrarian signal; commodities = geopolitical risk)
```

---

## Outcome

| | Before | After |
|---|---|---|
| Model | grok-3-mini | grok-4-1-fast-reasoning |
| Temperature | 0 | 0.7 |
| Profile deference | Implicit | Explicit (ground truth rule) |
| Domain awareness | None | ZeroHedge + commodity/geopolitical |
| Rationale quality | Literal | Signal-rich, non-obvious angles |

---

## Lessons Learned

1. **Stronger models aren't automatically better** — grok-4-1 was *smarter* but less deferential. Intelligence without profile respect is the wrong trade-off for a personalisation system.

2. **Read the articles** — the most important diagnostic step was reading the two flagged articles manually. A/B tests tell you *that* something changed; human review tells you *who was right*.

3. **Prompt bloat is a risk** — each tuning round adds rules. Encoding too many specific cases (e.g. "European ideological shift = relevant") creates brittle prompts. The feedback loop (Like/Dislike) is the right mechanism for subtle preference signals.

4. **Temperature is a signal-to-noise dial** — for curation, not just creativity. The right question is "does this surface signal or noise?" not "is this more or less creative?"

5. **Environment matters for testing** — the airport WiFi timeouts were a confounding variable. Always confirm on stable connection before attributing failures to model/config changes.

6. **CLI flags over code edits** — temperature was hardcoded, forcing code patches during testing. A `--temperature` flag makes future experiments clean and repeatable.

---

## Reusable Testing Protocol (for future model upgrades)

```
1. Run A/B on same article batch — note rank flips (δ ≥ 3.0 = significant)
2. If flips found: read the flagged articles before tuning
3. Distinguish: model wrong vs. model stricter (both possible)
4. Tune only what's clearly wrong — don't encode ambiguous cases in prompt
5. Re-run A/B after each tuning round
6. Test temperature separately once model/prompt are validated
7. Always test on stable connection
8. Confirm feedback loop still works (profile injection unaffected)
9. Document everything — model, temperature, prompt version, and reasoning
```
