# Spec #125: Domain Standardization Phase 2 — Model Name Centralization
**File:** `spec_125_model_standardization_2026-07-05.md`
**Status:** Backlog
**Date:** 2026-07-05
**Build queue:** #125
**GitHub issue:** #78
**Author:** Claude.ai design session
**Prerequisite:** #120 Domain Standardization Phase 1 complete
**Grok review:** Required before build

---

## Intent

Architecture Principle #4 states: *Model names never hardcoded in domain functions.* Today, 16 files across every domain violate this principle. Switching LLM providers or model versions requires hunting down hardcoded strings across the entire codebase — a fragile, error-prone process that creates vendor lock-in at the code level.

This spec centralizes all LLM model names to a single config file (`config/models.json`). Every domain reads from this config at runtime. Switching models becomes a one-line config change with no code change and no ECR push required.

This is a high-risk change — it touches every domain in production. A full regression test is required before any prod deploy. This spec is standalone, not folded into #120, specifically because the regression test requirement demands its own deliberate build and review cycle.

**Motivated by:** Investigation 2026-07-05 which found 16 files with hardcoded model strings. Immediate hotfix (#77) fixed one stale string (`deep_dive.py:197`). The remaining 15 files are addressed here.

---

## Scope — Full Violation Inventory

| File | Lines | Hardcoded strings | Domain |
|---|---|---|---|
| `curator_intelligence.py` | 46-47 | claude-haiku-4-5, claude-sonnet-4-5 | Curator |
| `curator_rss_v2.py` | 577, 768 | claude-haiku-4-5 | Curator |
| `curator_rss_v2.py` | 896 | claude-sonnet-4-5 | Curator |
| `curator_rss_v2.py` | 947, 2097 | grok-3-mini, grok-4-1-fast-reasoning | Curator |
| `curator_deepdive.py` | 210 | claude-sonnet-4-5 | Curator |
| `curator_feedback.py` | 287, 1232 | claude-sonnet-4-5 | Curator |
| `curator_utils.py` | 255 | claude-haiku-4-5-20251001 | Curator |
| `signal_store.py` | 347 | grok-3-mini | Curator |
| `research_routes.py` | 2757 | claude-sonnet-4-5 | Curator |
| `domains/german/german_domain.py` | 104-106, 1874 | grok-4-1-fast, claude-haiku-4-5-20251001, claude-sonnet-4-6 | German |
| `domains/german/providers/review_router.py` | 88, 110, 136, 222, 237, 252 | grok-4.3, gpt-4o, claude-sonnet-4-6, gpt-4o-mini | German |
| `domains/portuguese/html_server.py` | 419, 460 | claude-haiku-4-5-20251001 | Portuguese |
| `domains/portuguese/review_router.py` | 120 | grok-4.3 | Portuguese |
| `domains/guild/agents/chief_of_staff.py` | 697, 759 | grok-4-1-fast-reasoning | Guild |
| `domains/guild/agents/loops/cos_novelty_watch.py` | 147 | grok-4-1-fast-reasoning | Guild |
| `domains/guild/agents/loops/cos_job_search.py` | 424, 476 | grok-4-1-fast-reasoning | Guild |

**Already correct (gold standard pattern):**
- `domains/german/reviewer.py` — reads from `data/config/domain.json` at runtime ✅
- `domains/guild/config/challenger_config.json` — model names in config ✅

---

## The Gold Standard Pattern

```python
# Load models from central config at startup
import json
from pathlib import Path

def load_models():
    config_path = Path(__file__).parent.parent / 'config' / 'models.json'
    with open(config_path) as f:
        return json.load(f)

MODELS = load_models()

# Use in code:
model = MODELS['curator']['fast']      # replaces hardcoded "claude-haiku-4-5"
model = MODELS['curator']['smart']     # replaces hardcoded "claude-sonnet-4-6"
model = MODELS['curator']['reasoning'] # replaces hardcoded "grok-4-1-fast-reasoning"
```

---

## `config/models.json` Design

Single file at repo root `config/models.json`. All domains read from it.

```json
{
  "_comment": "Central model configuration. Change here, not in code. No ECR push needed for model swaps.",
  "_updated": "2026-07-05",
  "curator": {
    "fast": "claude-haiku-4-5",
    "smart": "claude-sonnet-4-6",
    "reasoning": "grok-4-1-fast-reasoning",
    "scoring": "grok-3-mini"
  },
  "german": {
    "fast": "claude-haiku-4-5",
    "smart": "claude-sonnet-4-6",
    "reviewer": "grok-4.3",
    "review_fast": "gpt-4o-mini",
    "review_smart": "gpt-4o"
  },
  "portuguese": {
    "fast": "claude-haiku-4-5",
    "smart": "claude-sonnet-4-6",
    "reviewer": "grok-4.3"
  },
  "guild": {
    "fast": "grok-4-1-fast-reasoning",
    "smart": "claude-sonnet-4-6"
  }
}
```

**Naming convention:**
- `fast` — lightweight, high-volume tasks (scoring, enrichment, simple summaries)
- `smart` — complex reasoning, quality output
- `reasoning` — explicit reasoning/chain-of-thought tasks
- `reviewer` — code or content review tasks
- Domain-specific keys where needed (e.g. `scoring` for Curator)

**Volume-mounted on EC2** — `config/models.json` is already synced via `sync_docs.sh`. Model changes deploy without ECR push or container restart. This is the key operational benefit.

---

## Regression Test Plan

This is the highest-risk part of this spec. Every domain is touched. A full regression test is required on dev before any prod deploy.

### Pre-build baseline
Claude Code documents current model behavior for each test case before making any changes. This is the baseline to compare against.

### Test suite — by domain

**Curator:**
- [ ] Daily briefing runs end-to-end — articles scored and ranked
- [ ] AI observations generate correctly
- [ ] Deeper dive completes with summary output
- [ ] Feedback processing works
- [ ] RSS enrichment runs without errors
- [ ] Signal store writes correctly

**German (Mein Deutsch):**
- [ ] Lesen article loads with translation working
- [ ] Gespräche KI-Persona session starts and responds
- [ ] Schreiben correction returns feedback
- [ ] Wörter drill works end-to-end
- [ ] Reviewer generates feedback on a transcript

**Portuguese (Meu Português):**
- [ ] Leitura article loads with translation working
- [ ] Conversas persona session starts and responds
- [ ] Escrita correction returns feedback
- [ ] Palavras drill works end-to-end

**Guild:**
- [ ] CoS chief_of_staff.py responds to a test query
- [ ] Novelty watch loop runs without error
- [ ] Job search loop runs without error

### Test process
1. Run baseline on dev before any code changes — document outputs
2. Apply model centralization changes to dev
3. Re-run full test suite on dev — compare against baseline
4. Any regression → stop, diagnose, fix before proceeding
5. Grok reviews test results before prod deploy
6. Robert approves prod deploy explicitly

### What counts as a regression
- Any domain that previously worked now returns an error
- Model response quality noticeably degraded (subjective — Robert's judgment)
- Any hardcoded model string remaining in codebase after the change

---

## Build Phases

### Phase A — Config file and loader (low risk)
1. Create `config/models.json` with full model map
2. Write `config/model_loader.py` — shared utility all domains import
3. No changes to domain files yet
4. Commit to dev, confirm file loads correctly

### Phase B — Curator (highest volume, most violations)
1. Update all 9 Curator files to use `MODELS['curator'][...]`
2. Run Curator regression tests on dev
3. Confirm baseline matches — no regressions
4. Commit to dev

### Phase C — German and Portuguese
1. Update German files (3 files, 8 violations)
2. Update Portuguese files (2 files, 3 violations)
3. Run German and Portuguese regression tests on dev
4. Commit to dev

### Phase D — Guild
1. Update Guild files (3 files, 4 violations)
2. Run Guild regression tests
3. Commit to dev

### Phase E — Full regression + prod deploy
1. Run complete test suite across all domains on dev
2. Grok reviews test results
3. Robert approves
4. Single ECR push — all containers rebuilt with centralized model config
5. Verify all domains healthy on prod
6. Confirm `config/models.json` volume-mounted and readable

---

## Definition of Done

- [ ] `config/models.json` created and volume-mounted on EC2
- [ ] `config/model_loader.py` written and importable by all domains
- [ ] Zero hardcoded model strings remaining in codebase (grep confirms)
- [ ] All 16 violation files updated to read from config
- [ ] Full regression test suite passed on dev — no regressions
- [ ] Grok has reviewed test results
- [ ] Single ECR push — all containers healthy on prod
- [ ] Model swap verified: change one value in `config/models.json`, confirm change takes effect without code change or ECR push
- [ ] `docs/architecture/DOMAIN_TEMPLATE.md` updated — model config pattern documented as required

---

## Commit

Single PR on dev covering all phases. One ECR push after full regression passes.

```
feat(all-domains): centralize LLM model names to config/models.json (#125)

- config/models.json: single source of truth for all model names
- config/model_loader.py: shared loader utility
- 16 files updated across Curator, German, Portuguese, Guild
- Zero hardcoded model strings remaining
- Regression tested across all domains on dev before deploy
- Closes #78
```

---

## Notes for Grok Review

- Phase sequence (A→B→C→D→E) is deliberate — stop if any phase has regressions
- Curator has the most violations and highest call volume — test most thoroughly
- German `review_router.py` uses GPT-4o in addition to Claude and Grok — confirm OpenAI key available in SSM
- `config/models.json` must be volume-mounted on EC2 for the "no ECR push for model swaps" benefit to work — confirm this in docker-compose.prod.yml
- Regression baseline must be documented before any code changes — untested migration is not a migration

---

*Spec · 2026-07-05 · Claude.ai design session · Status: Backlog*
*Prerequisite: #120 Phase 1 complete*
*Standalone spec — not folded into #120 due to regression test requirement*
*Grok review required before build*
