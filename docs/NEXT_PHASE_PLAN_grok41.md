# Next Phase Plan: Grok 4.1 Fast + Surprise Prompt Integration

**Date**: March 2026
**Status**: 📋 Saved for implementation — do not build yet
**Flag**: Verify exact model name `grok-4-1-fast-reasoning` against live xAI API model list before implementing — may need correction.

**Goal**: Upgrade scoring/ranking from grok-3-mini → grok-4-1-fast-reasoning for better nuance, surprise, and agentic potential. Keep cost ~same or lower. Add creative "Sonnet-like" spark without losing personalization.

---

## 1. Model Switch (Primary Recommendation)

- **Target model**: `grok-4-1-fast-reasoning` (xAI API or via OpenRouter in OpenClaw)
  - 2M context → can inject full profile + more articles/history
  - Strong reasoning traces → better at patterns/contradictions
  - Native tool use → future-proof for wider sources/synthesis
  - Pricing: ~$0.20/M input, $0.50/M output (competitive with grok-3-mini for your volume)
- **Fallback**: grok-4-1-fast-non-reasoning (for speed in loops)
- **Pre-filter**: Keep Haiku (cheap 400→50 cut)

- **Implementation steps**:
  1. Verify exact model name against xAI API model list (`GET /v1/models`)
  2. Update model selector in `curator_rss_v2.py` (or `curator_config.py`) to support `"grok-4-1-fast-reasoning"`
  3. Add xAI API key handling (use `credential_manager.py` pattern)
  4. Test with `--dry-run` and `--model=grok-4-1-fast-reasoning`
  5. Monitor costs via `cost_report.py` — expect similar or slightly higher than grok-3-mini
  6. If using OpenClaw: Set provider=`xai`, model=`grok-4-1-fast-reasoning` for scoring tasks

---

## 2. Injecting "Surprise" / Creative Prompting

**Goal**: Make outputs less literal, more bold/unexpected (closer to Sonnet flair).

**Injection location**: Dispatcher / model-call preparation, likely the scoring function in `curator_rss_v2.py`.
- Prepend a system prompt **before** the injected user profile
- Keep profile injection model-agnostic

**Recommended Surprise System Prompt** (copy-paste ready):

```
You are a contrarian, high-variance intelligence co-pilot who knows the user deeply.
Core rules:
- ALWAYS respect the injected USER PROFILE (learned preferences, boosts, avoids).
- BUT go beyond literal ranking: be surprising, creative, and bold.
- Surface unexpected angles, counterintuitive implications, contrarian takes,
  overlooked risks/opportunities, or heretical observations.
- Avoid safe, obvious, or echo-chamber summaries — if something feels too
  straightforward, twist it with a high-impact insight.
- Think like a mad-scientist collaborator: push boundaries without hallucinating.
- Output format: Keep structured (ranked list + brief rationale), but infuse
  rationale with surprise.
```

**How to add it**:

```python
SURPRISE_PROMPT = """[paste above]"""

full_prompt = (
    SURPRISE_PROMPT
    + "\n\nUSER PROFILE:\n" + profile_str
    + "\n\nARTICLES TO RANK:\n" + articles_str
)
```

- Define as constant in code or load from file (e.g. `surprise_prompt.md`)
- Toggle via config flag (e.g. `enable_surprise_mode: true` in `curator_config.py`)

**Options for where exactly to inject**:

1. **Best spot**: In the scoring/ranking function inside `curator_rss_v2.py`, where the input to the model is assembled. Look for where `grok-3-mini` is called — add the system prompt block before the profile.

2. **Cleaner/more flexible**: Add a config key in `curator_config.py` (e.g. `SCORING_SYSTEM_PROMPT`) or a separate `scoring_surprise_prompt.md` file, loaded and prepended dynamically. Makes A/B testing easy.

3. **OpenClaw integration** (future): Set as persistent system prompt for the scoring task in the OpenClaw agent config. OpenClaw maintains it across calls; dynamic profile + articles fed each run.

**Testing tips**:
- Run dry-runs side-by-side (old grok-3-mini vs new)
- Check if rankings feel more "alive" / less predictable
- If too wild → dial back with "Balance surprise with accuracy" in prompt

---

## 3. Quick Validation Checklist

```
□ Verify model name against xAI API model list before any code changes
□ Model switch works (API call succeeds, outputs parse correctly)
□ Surprise prompt injected → sample briefings show bolder insights
□ Costs stay <$50/mo (track week 1)
□ Profile injection still works (feedback loop unaffected)
□ Ready for Phase 3D (image analysis) — grok-4.1-fast has vision variants if needed
```

---

## Notes

- This plan was generated with input from Grok during the 2026-03-05 session
- Do not implement until model name is verified
- Reference this file in OpenClaw chats: *"Follow NEXT_PHASE_PLAN_grok41.md guidelines"*
- Share sample outputs after first runs for prompt tweaks
