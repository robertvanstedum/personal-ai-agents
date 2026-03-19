# Case Study: Tuning grok-4-1-fast-reasoning for Personal Curator (v1.0 – 2026-03-06)

**Author**: vanstedum + Claude Code + OpenClaw + Grok
**Status**: ✅ Complete — shipped to production

> Upgraded from grok-3-mini to grok-4-1-fast-reasoning with targeted prompt tuning and temperature 0.7; restored profile deference while gaining reasoning depth. Shipped after 3 tuning rounds + temperature calibration.

---

## Context

Personal AI curator — multi-stage LLM pipeline:
- **Stage 1**: Claude Haiku pre-filter (400 → ~50 articles, cheap cut)
- **Stage 2**: xAI Grok scoring/ranking (final personalised ranking)
- **Delivery**: Telegram morning briefing via `rvsopenbot`

Learned user profile (`curator_preferences.json`) injected into Stage 2 as a dispatcher concern — model-agnostic, passed to whatever LLM is scoring. **Baseline**: `grok-3-mini` at `temperature=0`.

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

## Round 1: Baseline A/B — ❌ HOLD

| Metric | grok-3-mini | grok-4-1-fast-reasoning |
|---|---|---|
| Average score | 6.33 | 5.07 |
| Significant rank flips (δ≥3) | — | 4 (all downward, δ 3–4) |

**Problem articles:**
1. "Brazil's soy industry" (Deutsche Welle) — Grok-3: +5, Grok-4-1: +1 ("off-topic")
2. "10 Thursday AM Reads" (The Big Picture) — Grok-3: +6, Grok-4-1: +3 ("generic")
3. UN genocide allegations (Die Welt) — Grok-3: +7, Grok-4-1: +4 ("tangential")
4. "10 Tuesday AM Reads" (The Big Picture) — same pattern as #2

**Root cause**: `grok-4-1-fast-reasoning`'s stronger reasoning overrode the injected user profile with independent categorical judgments ("this is generic content"). Smarter but less deferential — applying its own content quality model instead of respecting learned preferences.

---

## Round 2: Prompt Tuning — ⚠️ YELLOW LIGHT

Added explicit profile-weight framing to the system prompt:

```
The USER PROFILE below is ground truth.
Source preferences OVERRIDE your content judgment.
If a source appears in the user's boost list, score it UP
regardless of whether the content seems generic or off-topic.
The user has chosen these sources deliberately.
```

Fixed 2/4 problem articles. 2 edge cases remained.

---

## Manual Article Review (pivotal step)

Before further prompt tuning, read both remaining articles directly. This was the most important diagnostic step of the entire process.

**Brazil soy article** — *Extremely relevant*. Brazil exports 70% of soybeans to China; Europe importing less due to stricter standards; rainforest clearance accelerating. Classic commodities × geopolitics × supply-chain signal — exactly what the curator is designed to surface. **`grok-4-1-fast-reasoning` was wrong.**

**UK Ad article** — *Genuinely borderline*. More politics than finance, but signals a European ideological shift (away from "victim-only" framing). Too subtle to encode as a prompt rule — handled via Like feedback loop instead.

**Decision**:
- Brazil/soy → domain clarification in prompt (clear model error)
- UK article → feedback loop, not prompt engineering (ambiguous cases age badly as rules)

---

## Round 3: Domain Clarification — ✅ GREEN LIGHT

Added explicit domain guidance:

```
- ZeroHedge sources = trusted risk/contrarian angles
- Commodity/supply-chain disruptions = geopolitical risk signals
- Treat source trust from user profile as categorical, not content-dependent
```

Brazil/soy article restored to appropriate ranking. Profile deference solid. Rationale noticeably richer than `grok-3-mini` — less literal, more signal-aware.

---

## Temperature Testing

With model and prompt validated, tested temperature settings:

| Temperature | Signal Quality | Noise Level | Reactivity to Breaking News | Verdict |
|---|---|---|---|---|
| 0.0 | Very high | Minimal | Low | Too flat |
| 0.7 | High | Low | Moderate | ✅ Sweet spot |
| 1.1 | Moderate | Medium | High | Too noisy |

**Key insight**: For this use case, temperature is a *signal-to-noise dial*, not just a creativity dial. At 1.1, the model gravitates toward what's broadly trending (headline volume, the same news everyone sees). At 0.7, it maintains analytical sharpness while finding non-obvious angles.

**Note**: temperature=0.7 initially appeared to fail — diagnosed as airport WiFi timeouts, not temperature-related. Confirmed clean on stable connection (phone hotspot). Always test on stable connection before attributing failures to model/config changes.

---

## Infrastructure: `--temperature` CLI Flag

`temperature` was hardcoded in the xAI API call, requiring code patches during testing. Added proper CLI support:

```bash
python3 curator_rss_v2.py --dry-run --model=grok-4-1-fast-reasoning --temperature=0.7
```

Wired through `main()` → `curate()` → `score_entries_xai()` → xAI API call. Default remains `0.0` for production stability.

**Commit**: `70e1571` (added `--temperature` flag, wired to xAI API call)

---

## Final Production Configuration

```
Model:        grok-4-1-fast-reasoning
Temperature:  0.7
Pre-filter:   Claude Haiku (unchanged)
Prompt:       Profile-weight framing (ground truth rule)
              + domain clarifications (ZeroHedge, commodity/geopolitical)
```

**Estimated cost impact**: ~neutral to +10–15% (larger context but fewer overrides → similar token burn vs. grok-3-mini)

---

## Lessons Learned

1. **Stronger models aren't automatically better** — intelligence without profile deference is the wrong trade-off for a personalisation system. Optimise for *fit*, not raw capability.

2. **Read the articles** — A/B tests tell you *that* something changed; human review tells you *who was right*. The Brazil/soy call couldn't have been made from metrics alone.

3. **Prompt bloat is a risk** — each tuning round adds rules. The feedback loop (Like/Dislike) handles subtle preferences better than encoded rules, and generalises better over time.

4. **Temperature is a signal-to-noise dial** — for curation, the right question is "does this surface signal or noise?" not "is this more or less creative?"

5. **Environment matters for testing** — airport WiFi timeouts were a confounding variable. Always confirm on stable connection before attributing failures to model or config changes.

6. **CLI flags over code edits** — `--temperature` was hardcoded, forcing direct code patches during testing. A CLI flag makes experiments clean, repeatable, and safe.

---

## Reusable Protocol for Future Model Upgrades

```
[ ] 1. Run A/B on same article batch; flag rank flips (δ ≥ 3.0)
[ ] 2. Read flagged articles directly before any tuning
[ ] 3. Distinguish: model wrong vs. model stricter (both are possible)
[ ] 4. Tune only clear errors — let feedback loop handle ambiguous cases
[ ] 5. Re-run A/B after each tuning round
[ ] 6. Test temperature separately once model/prompt are validated
[ ] 7. Always test on stable connection
[ ] 8. Confirm feedback loop still works post-upgrade (profile injection unaffected)
[ ] 9. Document: model name, temperature, prompt version, reasoning, commit hash
```

---

*Co-authored with Claude Code, OpenClaw, and Grok. Built on airport WiFi and a phone hotspot.*
